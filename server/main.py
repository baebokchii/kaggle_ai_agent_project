"""랜덤 여행 챌린지 웹 서비스 (FastAPI).

- GET  /                       웹 UI
- GET  /api/meta               선택지(기간·출발지·예산 스타일)와 여행지 목록
- POST /api/challenges         챌린지 실행 (Server-Sent Events로 진행 상황 스트리밍)
- GET  /api/challenges/{id}    저장된 결과 조회 (공유 링크용)
- GET  /healthz                헬스 체크

실행:  uvicorn server.main:app --reload
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import logging
import os
from pathlib import Path
from typing import Any, AsyncIterator
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService, DatabaseSessionService, InMemorySessionService
from google.genai import types

load_dotenv()

from travel_roulette import agent as travel_agent  # noqa: E402  (.env 로드 이후 import)
from travel_roulette.config import APP_NAME  # noqa: E402
from travel_roulette.destinations import DEPARTURES, DESTINATIONS, DURATION_LABELS  # noqa: E402
from travel_roulette.request_parser import BUDGET_LEVELS, TripRequest  # noqa: E402

logger = logging.getLogger("travel_roulette.server")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

STATIC_DIR = Path(__file__).parent / "static"
USER_ID = "web"  # 공유 링크로 누구나 결과를 볼 수 있게 단일 사용자 공간 사용
MAX_CONCURRENT_RUNS = int(os.getenv("MAX_CONCURRENT_RUNS", "4"))

# 노드가 끝나면(이벤트를 내면) 시작되는 다음 단계.
# LLM 노드는 응답이 끝나야 이벤트가 나오므로 '완료된 노드 → 다음 단계 진행 중'으로 매핑한다.
STAGES: dict[str, tuple[str, str]] = {
    "setup_challenge": ("research", "명소·맛집·교통 리서치"),
    "prepare_planning": ("plan", "일정 짜는 중"),
    "itinerary_planner": ("review", "일정 검수"),
    "review_gate": ("budget", "예산 계산"),  # approved 라우트일 때만
    "present_plan": ("done", "완성"),
}


def make_session_service() -> BaseSessionService:
  url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/sessions.db")
  if url == "memory":
    return InMemorySessionService()
  if url.startswith("sqlite"):
    db_path = url.split(":///", 1)[-1]
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
  return DatabaseSessionService(db_url=url)


class _State:
  runner: Runner
  session_service: BaseSessionService
  semaphore: asyncio.Semaphore


state = _State()


@asynccontextmanager
async def lifespan(_: FastAPI):
  state.session_service = make_session_service()
  state.runner = Runner(app=travel_agent.app, session_service=state.session_service)
  state.semaphore = asyncio.Semaphore(MAX_CONCURRENT_RUNS)
  yield
  await state.runner.close()


app = FastAPI(title="랜덤 여행 챌린지", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _sse(data: dict[str, Any]) -> str:
  return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _node_name(event: Any) -> str | None:
  """이벤트의 node path(예: 'travel_roulette@1/spot_researcher@1')에서 노드 이름을 뽑는다."""
  path = getattr(getattr(event, "node_info", None), "path", "") or ""
  for segment in reversed(path.split("/")):
    name = segment.split("@", 1)[0]
    if name in STAGES:
      return name
  return None


@app.get("/")
async def index() -> FileResponse:
  return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
  return {"status": "ok"}


@app.get("/api/meta")
async def meta() -> dict[str, Any]:
  return {
      "durations": [{"nights": n, "label": label} for n, label in DURATION_LABELS.items() if n <= 4],
      "departures": list(DEPARTURES),
      "budget_levels": list(BUDGET_LEVELS),
      "destinations": [d.name for d in DESTINATIONS],
  }


async def _run_challenge(req: TripRequest) -> AsyncIterator[str]:
  session_id = uuid.uuid4().hex[:12]
  await state.session_service.create_session(
      app_name=APP_NAME,
      user_id=USER_ID,
      session_id=session_id,
      state={"trip_request": req.model_dump(mode="json")},
  )
  yield _sse({"type": "session", "id": session_id})

  if state.semaphore.locked():
    yield _sse({"type": "queued", "message": "요청이 많아 잠시 대기 중이에요…"})

  async with state.semaphore:
    seen_stages: set[str] = set()
    sent_challenge = False
    plan: dict[str, Any] | None = None
    invocation_id = ""
    try:
      message = types.Content(role="user", parts=[types.Part(text="랜덤 여행 챌린지 시작")])
      async for event in state.runner.run_async(
          user_id=USER_ID, session_id=session_id, new_message=message
      ):
        node = _node_name(event)
        if node == "review_gate" and getattr(event.actions, "route", None) != "approved":
          node = None  # 수정 루프로 돌아가는 중
        if node:
          stage, label = STAGES[node]
          if stage not in seen_stages:
            seen_stages.add(stage)
            yield _sse({"type": "stage", "stage": stage, "label": label})

        delta = event.actions.state_delta if event.actions else {}
        if not sent_challenge and delta.get("challenge"):
          sent_challenge = True
          yield _sse({"type": "challenge", "challenge": delta["challenge"],
                      "weather": delta.get("weather")})
        invocation_id = event.invocation_id or invocation_id
        if delta.get("final_plan"):
          plan = delta["final_plan"]

      # 실행이 끝까지 돌아야 플러그인의 after_run 콜백(지표 집계)이 호출된다.
      if plan is None:
        session = await state.session_service.get_session(
            app_name=APP_NAME, user_id=USER_ID, session_id=session_id
        )
        plan = session.state.get("final_plan") if session else None
      if plan:
        metrics = travel_agent.metrics_plugin.get(invocation_id) or {}
        stats = {
            "duration_s": metrics.get("duration_s"),
            "llm_calls": metrics.get("llm_calls"),
            "total_tokens": (metrics.get("tokens") or {}).get("total"),
        }
        yield _sse({"type": "result", "id": session_id, "plan": plan, "stats": stats})
      else:
        yield _sse({"type": "error", "message": "일정을 완성하지 못했어요. 다시 시도해 주세요."})
    except Exception:  # 사용자에게는 일반 메시지만
      logger.exception("챌린지 실행 실패 (session=%s)", session_id)
      yield _sse({"type": "error", "message": "AI 호출 중 문제가 생겼어요. 잠시 후 다시 시도해 주세요."})


@app.post("/api/challenges")
async def create_challenge(req: TripRequest) -> StreamingResponse:
  return StreamingResponse(
      _run_challenge(req),
      media_type="text/event-stream",
      headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
  )


@app.get("/api/challenges/{session_id}")
async def get_challenge(session_id: str) -> dict[str, Any]:
  if not session_id.isalnum() or len(session_id) > 64:
    raise HTTPException(status_code=400, detail="잘못된 ID입니다.")
  session = await state.session_service.get_session(
      app_name=APP_NAME, user_id=USER_ID, session_id=session_id
  )
  if not session or not session.state.get("final_plan"):
    raise HTTPException(status_code=404, detail="결과를 찾을 수 없어요.")
  return {"id": session_id, "plan": session.state["final_plan"]}
