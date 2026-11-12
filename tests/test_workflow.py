"""가짜 LLM으로 Workflow 그래프 전체를 끝까지 실행해 본다."""

from __future__ import annotations

from google.adk.runners import InMemoryRunner
from google.genai import types
import pytest

from travel_roulette import agent as travel_agent
from travel_roulette.config import APP_NAME


async def _run(runner, session_id: str, text: str):
  events = []
  async for ev in runner.run_async(
      user_id="u",
      session_id=session_id,
      new_message=types.Content(role="user", parts=[types.Part(text=text)]),
  ):
    events.append(ev)
  session = await runner.session_service.get_session(
      app_name=APP_NAME, user_id="u", session_id=session_id
  )
  return events, session


@pytest.mark.asyncio
async def test_full_pipeline_with_review_loop(fake_llm):
  fake_llm.days = 3
  runner = InMemoryRunner(app=travel_agent.app)
  await runner.session_service.create_session(
      app_name=APP_NAME, user_id="u", session_id="s1", state={"seed": 7}
  )
  events, session = await _run(runner, "s1", "2박 3일 부산에서 둘이 알뜰하게")
  st = session.state

  # 조건 파싱
  assert st["trip_request"]["nights"] == 2
  assert st["trip_request"]["departure"] == "부산"
  assert st["trip_request"]["budget_level"] == "알뜰"
  assert st["challenge"]["destination"]["name"] != "부산"

  # 병렬 리서치 3개 + 출처 수집
  for key in ("spots_notes", "food_notes", "logistics_notes"):
    assert st[key]
  assert {s["uri"] for s in st["sources"]} == {
      "https://example.com/spots", "https://example.com/food", "https://example.com/logistics"}

  # 검수 루프: critic 1회 반려 → reviser → (수정 기회 소진이라 재검수 생략) → 예산
  assert fake_llm.calls.count("critic") == 1
  assert fake_llm.calls.count("reviser") == 1
  assert st["revision_count"] == 1

  # 예산은 코드로 계산: (30000*2*2) + 100000 + (12000*4*2) = 316000, 예비비 31600→32000
  assert st["budget"]["subtotal_krw"] == 316_000
  assert st["budget"]["total_krw"] == 348_000
  assert st["budget"]["per_person_krw"] == 174_000

  # 최종 결과
  plan = st["final_plan"]
  assert len(plan["itinerary"]["days"]) == 3
  final_texts = [
      p.text for ev in events if ev.content for p in ev.content.parts or [] if p.text
  ]
  assert any("랜덤 여행 챌린지" in t for t in final_texts)
  assert st["visited"] == [plan["challenge"]["destination"]["name"]]


@pytest.mark.asyncio
async def test_reroll_excludes_previous_destination(fake_llm):
  fake_llm.days = 1
  fake_llm.critic_rejections = 0
  runner = InMemoryRunner(app=travel_agent.app)
  await runner.session_service.create_session(app_name=APP_NAME, user_id="u", session_id="s2")
  _, s = await _run(runner, "s2", "당일치기")
  first = s.state["challenge"]["destination"]["name"]
  _, s = await _run(runner, "s2", "다시")
  second = s.state["challenge"]["destination"]["name"]
  assert first != second
  assert s.state["trip_request"]["nights"] == 0  # 이전 조건 유지
  assert s.state["visited"] == [first, second]


@pytest.mark.asyncio
async def test_gate_forces_revision_when_day_count_wrong(fake_llm):
  fake_llm.days = 1  # 2박 3일인데 1일치만 작성 → 코드 검사로 반려
  fake_llm.critic_rejections = 0  # LLM 검수는 승인
  runner = InMemoryRunner(app=travel_agent.app)
  await runner.session_service.create_session(app_name=APP_NAME, user_id="u", session_id="s3")
  _, s = await _run(runner, "s3", "2박 3일")
  assert fake_llm.calls.count("reviser") == 1
  assert s.state["revision_count"] == 1
  assert "final_plan" in s.state  # 최대 수정 횟수 후에는 그대로 진행


def test_hard_check_itinerary():
  it = {"days": [{"activities": [{}]}, {"activities": []}]}
  issues = travel_agent.hard_check_itinerary(it, 3)
  assert any("일차 수" in i for i in issues)
  assert any("Day 2" in i for i in issues)
  assert travel_agent.hard_check_itinerary({"days": [{"activities": [{}]}]}, 1) == []


@pytest.mark.asyncio
async def test_metrics_plugin_collects_per_run_stats(fake_llm):
  fake_llm.days = 2
  runner = InMemoryRunner(app=travel_agent.app)
  await runner.session_service.create_session(app_name=APP_NAME, user_id="u", session_id="m1")
  events, _ = await _run(runner, "m1", "1박 2일")
  m = travel_agent.metrics_plugin.get(events[-1].invocation_id)
  # 리서치 3 + 일정 1 + 검수 1 + 수정 1 + 예산 1 (수정 후 재검수 생략)
  assert m["llm_calls"] == 7
  assert m["agents"]["itinerary_critic"]["calls"] == 1
  assert m["tokens"] == {"prompt": 700, "output": 140, "thought": 35, "total": 875}
  assert m["duration_s"] is not None
  # 노드 완료 시점은 실행 순서대로 늘어난다
  ms = m["milestones"]
  assert ms["setup_challenge"] <= ms["research_join"] <= ms["itinerary_planner"] <= ms["present_plan"]


@pytest.mark.asyncio
async def test_search_quota_fallback_and_circuit_breaker(fake_llm, monkeypatch):
  """검색 그라운딩이 429면 검색 없이 답하고, 이후 요청은 검색을 건너뛴다."""
  from google.genai.errors import ClientError

  monkeypatch.setattr(travel_agent, "_search_blocked_until", 0.0)
  real = type(fake_llm).generate_content_async
  seen_tools: list[bool] = []

  async def flaky(self, llm_request, stream=False):
    has_search = bool(llm_request.config and llm_request.config.tools)
    if self._role(llm_request) in ("spots", "food", "logistics"):
      seen_tools.append(has_search)
      if has_search:
        raise ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}})
    async for r in real(self, llm_request, stream):
      yield r

  monkeypatch.setattr(type(fake_llm), "generate_content_async", flaky)
  # 대체(검색 없는) 호출도 가짜 LLM으로
  for node in travel_agent.root_agent.graph.nodes:
    if getattr(node, "on_model_error_callback", None):
      monkeypatch.setattr(node.on_model_error_callback, "model", fake_llm)

  fake_llm.critic_rejections = 0
  runner = InMemoryRunner(app=travel_agent.app)
  await runner.session_service.create_session(app_name=APP_NAME, user_id="u", session_id="q1")
  _, s = await _run(runner, "q1", "1박 2일")
  assert s.state["final_plan"]["search_degraded"] is True
  assert s.state["spots_notes"]
  assert not travel_agent.search_available()

  # 두 번째 실행: 차단 중이라 처음부터 검색 없이 요청
  seen_tools.clear()
  await runner.session_service.create_session(app_name=APP_NAME, user_id="u", session_id="q2")
  _, s2 = await _run(runner, "q2", "당일치기")
  assert seen_tools and not any(seen_tools)
  assert s2.state["final_plan"]["search_degraded"] is True


def test_route_backtrack_detection():
  from travel_roulette.routes import area_sequence, backtracks, route_issues

  def day(*areas, cats=None):
    return {"day": 1, "activities": [
        {"area": a, "category": (cats or {}).get(i, "관광")} for i, a in enumerate(areas)]}

  # 삼척 사례: 남부 → 시내 → 남서부 → 시내 (왕복)
  assert backtracks(area_sequence(day("남부", "시내", "남서부", "시내"))) == ["시내"]
  # 연속 중복은 하나로, 시작 권역으로 마지막에 돌아오는 건 허용
  assert backtracks(area_sequence(day("시내", "시내", "경포", "시내"))) == []
  # 이동·숙소는 동선 판단에서 제외
  assert area_sequence(day("시내", "고속도로", "경포", cats={1: "이동"})) == ["시내", "경포"]
  # 띄어쓰기 차이는 같은 권역
  assert backtracks(area_sequence(day("삼척 시내", "장호", "삼척시내", "장호"))) == ["삼척시내", "장호"]
  issues = route_issues({"days": [day("남", "북", "남", "북")]})
  assert len(issues) == 1 and "왕복" in issues[0]


@pytest.mark.asyncio
async def test_after_revision_rechecks_when_budget_allows(fake_llm, monkeypatch):
  monkeypatch.setattr(travel_agent.config, "MAX_REVISIONS", 2)
  fake_llm.critic_rejections = 1
  runner = InMemoryRunner(app=travel_agent.app)
  await runner.session_service.create_session(app_name=APP_NAME, user_id="u", session_id="r1")
  _, s = await _run(runner, "r1", "1박 2일")
  # 반려 → 수정 → (기회 남음) 재검수 승인
  assert fake_llm.calls.count("critic") == 2
  assert fake_llm.calls.count("reviser") == 1
  assert "final_plan" in s.state
