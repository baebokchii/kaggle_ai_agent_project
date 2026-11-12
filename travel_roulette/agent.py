"""🎲 랜덤 국내 여행 챌린지 — ADK Workflow 그래프 정의.

    START
      │
    setup_challenge        (코드) 조건 파싱 → 여행지 무작위 추첨 → 날씨 조회
      ├──────────────┬──────────────┐
    spot_researcher  food_researcher  logistics_researcher   (병렬, Google 검색)
      └──────────────┴──────────────┘
    research_join          (3개 리서치가 모두 끝날 때까지 대기)
      │
    prepare_planning       (코드) 출처 수집
      │
    itinerary_planner      일정 초안 (구조화 JSON)
      │
    itinerary_critic  ◀────────┐   검수 (구조화 JSON)
      │                        │
    review_gate  ── revise ──▶ itinerary_reviser ─▶ after_revision
      │ approved                  (수정 기회 남음 → 재검수 / 소진 → 예산으로)
    budget_estimator       항목별 단가·수량만 추정 (LLM)
      │
    budget_calculator      (코드) 합계·예비비·1인당 계산
      │
    present_plan           (코드) 최종 결과 조립 + 마크다운 응답
"""

from __future__ import annotations

import datetime as dt
import logging
import random
import time
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.agents.context import Context
from google.adk.apps import App
from google.adk.events.event import Event
from google.adk.models.google_llm import Gemini
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.logging_plugin import LoggingPlugin
from google.adk.tools import google_search
from google.adk.workflow import START, FunctionNode, JoinNode, Workflow
from google.genai import types

from . import config, prompts
from .budget import calculate_budget
from .destinations import draw_destination
from .observability import RunMetricsPlugin
from .render import render_markdown
from .routes import route_issues
from .request_parser import TripRequest, merge_request
from .schemas import BudgetEstimate, Itinerary, Review
from .weather import describe_weather, fetch_forecast

logger = logging.getLogger(__name__)


def _model(name: str) -> Gemini:
  return Gemini(model=name, retry_options=config.RETRY_OPTIONS)


def _content_text(value: Any) -> str:
  if isinstance(value, types.Content):
    return "".join(p.text or "" for p in value.parts or [])
  return "" if value is None else str(value)


# ---------------------------------------------------------------------------
# 1) 챌린지 세팅: 조건 파싱 + 랜덤 추첨 + 날씨 (LLM 없음)
# ---------------------------------------------------------------------------
async def setup_challenge(ctx: Context, node_input: Any) -> str:
  message = _content_text(node_input)
  req: TripRequest = merge_request(message, ctx.state.get("trip_request"))

  # 같은 세션에서 "다시"를 외치면 이전에 나온 곳은 제외
  visited = list(ctx.state.get("visited") or [])
  exclude = list(dict.fromkeys([*req.exclude, *visited]))

  seed = ctx.state.get("seed")  # 평가·테스트 재현용 (같은 시드 → 같은 여행지)
  rng = random.Random(seed) if seed is not None else None
  draw = draw_destination(req.nights, req.departure, exclude=exclude, rng=rng)
  dest = draw.destination

  forecast = None
  if req.start_date:
    forecast = await fetch_forecast(
        dest.lat, dest.lon, req.start_date, req.days, timeout=config.WEATHER_TIMEOUT
    )

  challenge = {
      **draw.to_dict(),
      "travelers": req.travelers,
      "budget_level": req.budget_level,
      "start_date": req.start_date.isoformat() if req.start_date else None,
      "drawn_at": dt.datetime.now().isoformat(timespec="seconds"),
  }
  ctx.state["trip_request"] = req.model_dump(mode="json")
  ctx.state["challenge"] = challenge
  ctx.state["weather"] = forecast
  ctx.state["weather_text"] = describe_weather(forecast, req.start_date)
  ctx.state["revision_count"] = 0
  logger.info("추첨 결과: %s (%s, 후보 %d곳)", dest.name, draw.departure.name, draw.candidates)
  return "다음 랜덤 여행 챌린지를 리서치하세요.\n" + prompts.challenge_brief(challenge)


# ---------------------------------------------------------------------------
# 2) 병렬 리서치 (Google 검색 그라운딩)
# ---------------------------------------------------------------------------
def _collect_sources(state_key: str):
  """검색 그라운딩 메타데이터에서 출처 URL을 state에 모은다."""

  def callback(callback_context: Context, llm_response: LlmResponse) -> None:
    meta = llm_response.grounding_metadata
    if not meta or not meta.grounding_chunks:
      return None
    sources = list(callback_context.state.get(state_key) or [])
    seen = {s["uri"] for s in sources}
    for chunk in meta.grounding_chunks:
      web = chunk.web
      if web and web.uri and web.uri not in seen:
        sources.append({"title": web.title or web.uri, "uri": web.uri})
        seen.add(web.uri)
    callback_context.state[state_key] = sources
    return None

  return callback


# --- 검색 할당량 소진 대비 ---------------------------------------------------
# Google 검색 그라운딩은 일반 호출과 별도 할당량이라 먼저 바닥날 수 있다.
# 429가 나면 검색 없이 모델 지식으로 답하게 하고(fallback), 일정 시간 동안은
# 아예 검색을 건너뛰어(circuit breaker) 매 요청이 재시도로 시간을 버리지 않게 한다.
SEARCH_COOLDOWN_SECONDS = 600
_search_blocked_until = 0.0
_NO_SEARCH_NOTE = (
    "\n\n[알림] 지금은 검색 도구를 쓸 수 없습니다. 알고 있는 정보로 작성하되, "
    "최근에 바뀌었을 수 있는 정보(가격·영업시간·신규 가게)는 '(확인 필요)'라고 표시하세요."
)


def _is_quota_error(error: Exception) -> bool:
  return getattr(error, "code", None) == 429 or "RESOURCE_EXHAUSTED" in str(error)


def _strip_search(llm_request: LlmRequest) -> LlmRequest:
  req = llm_request.model_copy(deep=True)
  if req.config:
    req.config.tools = None
  req.tools_dict = {}
  req.append_instructions([_NO_SEARCH_NOTE])
  return req


def search_available() -> bool:
  return time.monotonic() >= _search_blocked_until


def _skip_search_if_blocked(callback_context: Context, llm_request: LlmRequest) -> None:
  """차단 중이면 요청에서 검색 도구를 빼고 진행한다."""
  if search_available():
    return None
  stripped = _strip_search(llm_request)
  llm_request.config = stripped.config
  llm_request.tools_dict = {}
  callback_context.state["search_degraded"] = True
  return None


def _search_fallback(model: Gemini):
  async def callback(
      callback_context: Context, llm_request: LlmRequest, error: Exception
  ) -> LlmResponse | None:
    global _search_blocked_until
    if not _is_quota_error(error) or not (llm_request.config and llm_request.config.tools):
      return None  # 다른 오류는 그대로 전파
    _search_blocked_until = time.monotonic() + SEARCH_COOLDOWN_SECONDS
    logger.warning("검색 할당량 소진 → %d초간 검색 없이 진행", SEARCH_COOLDOWN_SECONDS)
    callback_context.state["search_degraded"] = True
    final: LlmResponse | None = None
    async for resp in callback.model.generate_content_async(_strip_search(llm_request)):
      if not resp.partial:
        final = resp
    return final

  callback.model = model  # 테스트에서 교체할 수 있도록 속성으로 둔다
  return callback


def _researcher(name: str, instruction, output_key: str, sources_key: str) -> LlmAgent:
  model = _model(config.RESEARCH_MODEL)
  return LlmAgent(
      name=name,
      model=model,
      instruction=instruction,
      tools=[google_search],
      include_contents="none",
      output_key=output_key,
      before_model_callback=_skip_search_if_blocked,
      after_model_callback=_collect_sources(sources_key),
      on_model_error_callback=_search_fallback(model),
      generate_content_config=config.gen_config(0.4, config.LIGHT_THINKING),
  )


spot_researcher = _researcher("spot_researcher", prompts.spots_instruction, "spots_notes", "sources_spots")
food_researcher = _researcher("food_researcher", prompts.food_instruction, "food_notes", "sources_food")
logistics_researcher = _researcher(
    "logistics_researcher", prompts.logistics_instruction, "logistics_notes", "sources_logistics"
)

research_join = JoinNode(name="research_join")


def prepare_planning(ctx: Context) -> str:
  sources: list[dict] = []
  seen: set[str] = set()
  for key in ("sources_spots", "sources_food", "sources_logistics"):
    for s in ctx.state.get(key) or []:
      # 그라운딩 URI는 리다이렉트 링크라 같은 사이트도 매번 달라진다 → 제목(사이트명)으로도 중복 제거
      if s["uri"] not in seen and s["title"] not in seen:
        sources.append(s)
        seen.update((s["uri"], s["title"]))
  ctx.state["sources"] = sources
  return "리서치가 끝났습니다. 지시문의 정보를 바탕으로 여행 일정을 작성하세요."


# ---------------------------------------------------------------------------
# 3) 일정 작성 → 검수 → (필요시) 수정 루프
# ---------------------------------------------------------------------------
itinerary_planner = LlmAgent(
    name="itinerary_planner",
    model=_model(config.MODEL),
    instruction=prompts.planner_instruction,
    include_contents="none",
    output_schema=Itinerary,
    output_key="itinerary",
    generate_content_config=config.gen_config(0.7, config.PLAN_THINKING),
)

itinerary_critic = LlmAgent(
    name="itinerary_critic",
    model=_model(config.MODEL),
    instruction=prompts.critic_instruction,
    include_contents="none",
    output_schema=Review,
    output_key="review",
    generate_content_config=config.gen_config(0.1, config.LIGHT_THINKING),
)

itinerary_reviser = LlmAgent(
    name="itinerary_reviser",
    model=_model(config.MODEL),
    instruction=prompts.reviser_instruction,
    include_contents="none",
    output_schema=Itinerary,
    output_key="itinerary",
    # 수정은 구체적인 피드백을 반영하는 작업이라 깊은 추론이 필요 없다 → low
    generate_content_config=config.gen_config(0.4, config.LIGHT_THINKING),
)


def hard_check_itinerary(itinerary: dict[str, Any], expected_days: int) -> list[str]:
  """LLM 검수와 별개로 코드로 확인하는 필수 조건."""
  issues = []
  days = itinerary.get("days") or []
  if len(days) != expected_days:
    issues.append(f"일차 수가 {len(days)}일입니다. 정확히 {expected_days}일로 맞춰야 합니다.")
  for i, day in enumerate(days, 1):
    if not day.get("activities"):
      issues.append(f"Day {i}에 활동이 없습니다.")
  issues.extend(route_issues(itinerary))
  return issues


def review_gate(ctx: Context, node_input: Any) -> str:
  review = Review.model_validate(node_input or ctx.state.get("review") or {
      "approved": True, "issues": [], "feedback": ""})
  expected = (ctx.state.get("challenge") or {}).get("days", 1)
  hard_issues = hard_check_itinerary(ctx.state.get("itinerary") or {}, expected)
  count = int(ctx.state.get("revision_count") or 0)

  if (review.approved and not hard_issues) or count >= config.MAX_REVISIONS:
    ctx.route = "approved"
    return "확정된 일정의 예산 항목을 작성하세요."

  merged = review.model_copy(update={
      "approved": False,
      "issues": [*hard_issues, *review.issues],
      "feedback": " ".join(filter(None, [*hard_issues, review.feedback])),
  })
  ctx.state["review"] = merged.model_dump()
  ctx.state["revision_count"] = count + 1
  ctx.route = "revise"
  return "검수 피드백을 반영해 일정을 수정하세요."


def after_revision(ctx: Context) -> str:
  """수정 후: 남은 수정 기회가 있으면 재검수, 없으면 재검수 없이 예산 단계로.

  수정 횟수를 다 썼다면 재검수 결과와 상관없이 게이트가 통과시키므로
  재검수 LLM 호출은 시간·토큰 낭비다.
  """
  count = int(ctx.state.get("revision_count") or 0)
  if count < config.MAX_REVISIONS:
    ctx.route = "recheck"
    return "수정된 일정을 다시 검토하세요."
  ctx.route = "done"
  return "확정된 일정의 예산 항목을 작성하세요."


# ---------------------------------------------------------------------------
# 4) 예산: LLM은 단가·수량만, 합계는 코드로
# ---------------------------------------------------------------------------
budget_estimator = LlmAgent(
    name="budget_estimator",
    model=_model(config.MODEL),
    instruction=prompts.budget_instruction,
    include_contents="none",
    output_schema=BudgetEstimate,
    output_key="budget_estimate",
    generate_content_config=config.gen_config(0.2, config.LIGHT_THINKING),
)


def budget_calculator(ctx: Context, node_input: Any) -> dict[str, Any]:
  estimate = node_input or ctx.state.get("budget_estimate")
  travelers = (ctx.state.get("challenge") or {}).get("travelers", 2)
  budget = calculate_budget(estimate, travelers)
  ctx.state["budget"] = budget
  return budget


# ---------------------------------------------------------------------------
# 5) 결과 조립
# ---------------------------------------------------------------------------
def build_final_plan(state: Any) -> dict[str, Any]:
  return {
      "challenge": state.get("challenge"),
      "weather": state.get("weather"),
      "weather_text": state.get("weather_text"),
      "itinerary": state.get("itinerary"),
      "budget": state.get("budget"),
      "review": {
          "revisions": state.get("revision_count") or 0,
          "last": state.get("review"),
      },
      "sources": state.get("sources") or [],
      "search_degraded": bool(state.get("search_degraded")),
  }


def present_plan(ctx: Context):
  plan = build_final_plan(ctx.state)
  ctx.state["final_plan"] = plan
  visited = list(ctx.state.get("visited") or [])
  name = plan["challenge"]["destination"]["name"]
  if name not in visited:
    visited.append(name)
  ctx.state["visited"] = visited
  yield Event(
      content=types.Content(role="model", parts=[types.Part(text=render_markdown(plan))]),
      output=plan,
  )


# ---------------------------------------------------------------------------
# Workflow 그래프
# ---------------------------------------------------------------------------
setup_node = FunctionNode(func=setup_challenge, name="setup_challenge", timeout=30)
prepare_node = FunctionNode(func=prepare_planning, name="prepare_planning")
gate_node = FunctionNode(func=review_gate, name="review_gate")
after_revision_node = FunctionNode(func=after_revision, name="after_revision")
calculator_node = FunctionNode(func=budget_calculator, name="budget_calculator")
present_node = FunctionNode(func=present_plan, name="present_plan")

root_agent = Workflow(
    name="travel_roulette",
    description="기간만 고르면 국내 여행지를 랜덤 추첨해 일정과 예산을 짜주는 여행 챌린지 에이전트",
    edges=[
        (
            START,
            setup_node,
            (spot_researcher, food_researcher, logistics_researcher),
            research_join,
            prepare_node,
            itinerary_planner,
            itinerary_critic,
            gate_node,
        ),
        (gate_node, {"revise": itinerary_reviser, "approved": budget_estimator}),
        (itinerary_reviser, after_revision_node),
        (after_revision_node, {"recheck": itinerary_critic, "done": budget_estimator}),
        (budget_estimator, calculator_node, present_node),
    ],
)

metrics_plugin = RunMetricsPlugin()


def _plugins() -> list:
  plugins: list = [metrics_plugin]
  if config.VERBOSE_LOGGING:
    # Kaggle Day 4a: 모든 콜백을 콘솔에 찍는 ADK 기본 플러그인 (디버깅용, 출력이 많음)
    plugins.append(LoggingPlugin())
  return plugins


app = App(name=config.APP_NAME, root_agent=root_agent, plugins=_plugins())
