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
  assert data["durations"][0] == {"nights": 0, "label": "당일치기"}
  assert "서울" in data["departures"] and "제주" in data["destinations"]


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
  assert challenge["destination"]["name"] not in ("전주", "대전")

  sid = events[-1]["id"]
  shared = client.get(f"/api/challenges/{sid}").json()
  assert shared["plan"]["challenge"]["destination"]["name"] == challenge["destination"]["name"]
  assert shared["plan"]["budget"]["travelers"] == 3
  assert events[-1]["stats"]["llm_calls"] == 7


def test_invalid_request(client):
  assert client.post("/api/challenges", json={"nights": 1, "departure": "도쿄"}).status_code == 422
  assert client.get("/api/challenges/nope").status_code == 404
  assert client.get("/api/challenges/../../etc").status_code in (400, 404)


def test_index_served(client):
  r = client.get("/")
  assert r.status_code == 200 and "랜덤 여행 챌린지" in r.text
