"""FastAPI 서버 + SSE 스트리밍 테스트 (가짜 LLM, 인메모리 세션)."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
import pytest


@pytest.fixture(params=["memory", "sqlite"])
def client(request, fake_llm, monkeypatch, tmp_path):
  url = "memory" if request.param == "memory" else f"sqlite+aiosqlite:///{tmp_path}/s.db"
  monkeypatch.setenv("DATABASE_URL", url)
  from server.main import app

  with TestClient(app) as c:
    yield c


def _events(resp) -> list[dict]:
  return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


def test_meta(client):
  data = client.get("/api/meta").json()
  assert data["durations"][0]["label"] == "당일치기"
  assert data["departures"][0]["name"] == "서울" and "lat" in data["departures"][0]
  assert data["durations"][0]["range_km"] == [20, 130]


def test_challenge_stream_and_share_link(client, fake_llm):
  fake_llm.days = 2
  resp = client.post("/api/challenges", json={
      "nights": 1, "departure": "대전", "travelers": 3, "budget_level": "여유",
      "exclude": ["전주"]})
  assert resp.status_code == 200
  events = _events(resp)
  types_ = [e["type"] for e in events]
  assert types_[0] == "session"
  assert "challenge" in types_ and types_[-1] == "result"
  stages = [e["stage"] for e in events if e["type"] == "stage"]
  assert stages == ["research", "plan", "review", "budget", "done"]

  challenge = next(e for e in events if e["type"] == "challenge")["challenge"]
  assert challenge["departure"] == "대전" and challenge["travelers"] == 3
  assert 40 <= challenge["distance_km"] <= 300  # 1박 2일 다트 범위
  assert "lat" in challenge["destination"]

  sid = events[-1]["id"]
  shared = client.get(f"/api/challenges/{sid}").json()
  assert shared["plan"]["challenge"]["destination"]["name"] == challenge["destination"]["name"]
  assert shared["plan"]["budget"]["travelers"] == 3
  assert events[-1]["stats"]["llm_calls"] == 7


def test_throw_first_then_plan_on_request(client, fake_llm):
  fake_llm.days = 2
  thrown = client.post("/api/darts", json={"nights": 1, "departure": "대전", "travelers": 3}).json()
  sid, dest = thrown["id"], thrown["challenge"]["destination"]["name"]
  assert 40 <= thrown["challenge"]["distance_km"] <= 300
  assert fake_llm.calls == []  # 던지기만으로는 LLM을 부르지 않는다

  # 일정을 짜기 전에도 공유 링크는 꽂힌 곳을 보여 준다
  before = client.get(f"/api/challenges/{sid}").json()
  assert before["plan"] is None and before["planning"] is False
  assert before["challenge"]["destination"]["name"] == dest

  events = _events(client.post(f"/api/challenges/{sid}/plan"))
  assert events[-1]["type"] == "result"
  assert "challenge" not in [e["type"] for e in events]  # 다시 던지지 않는다
  assert events[-1]["plan"]["challenge"]["destination"]["name"] == dest
  assert events[-1]["plan"]["budget"]["travelers"] == 3
  found = {e["kind"]: e["items"] for e in events if e["type"] == "found"}
  assert found["spots"] == [{"name": "테스트 spots", "note": "시내 · 가 볼 만함"}]
  assert "food" in found and "logistics" not in found

  # 완성된 뒤 다시 요청하면 새로 만들지 않고 저장된 일정을 돌려준다
  calls = len(fake_llm.calls)
  again = _events(client.post(f"/api/challenges/{sid}/plan"))
  assert [e["type"] for e in again] == ["result"] and len(fake_llm.calls) == calls
  assert client.get(f"/api/challenges/{sid}").json()["plan"]["challenge"]["destination"]["name"] == dest


def test_dart_failure_is_reported(client, monkeypatch):
  from travel_roulette import dart

  async def nothing_there(lat, lon):
    return None

  monkeypatch.setattr(dart, "reverse_geocode", nothing_there)
  resp = client.post("/api/darts", json={"nights": 1})
  assert resp.status_code == 503 and "다시 던져" in resp.json()["detail"]
  assert client.post("/api/challenges/nope/plan").status_code == 404


def test_invalid_request(client):
  assert client.post("/api/challenges", json={"nights": 1, "departure": "도쿄"}).status_code == 422
  assert client.get("/api/challenges/nope").status_code == 404
  assert client.get("/api/challenges/../../etc").status_code in (400, 404)


def test_index_served(client):
  r = client.get("/")
  assert r.status_code == 200 and "랜덤 여행 챌린지" in r.text


async def test_job_replays_progress_to_late_subscribers():
  """창을 닫았다 다시 온 사람도 지금까지의 진행 상황부터 이어서 본다."""
  import asyncio

  from server.main import _Job

  job = _Job()
  await job.push({"type": "stage", "stage": "research"})

  async def collect():
    return [json.loads(chunk[6:]) for chunk in [c async for c in job.stream()]]

  late = asyncio.create_task(collect())
  await asyncio.sleep(0)
  await job.push({"type": "result"}, done=True)
  assert [e["type"] for e in await late] == ["stage", "result"]
  assert [e["type"] for e in await collect()] == ["stage", "result"]  # 끝난 뒤에 붙어도 전부 받는다
