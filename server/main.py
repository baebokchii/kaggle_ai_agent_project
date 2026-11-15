"""랜덤 여행 챌린지 웹 서비스 (FastAPI).

- GET  /                       웹 UI
- GET  /api/meta               선택지(기간·출발지·예산 스타일)와 여행지 목록
- POST /api/darts                    다트만 던진다 (LLM 없음). 꽂힌 곳과 세션 ID를 돌려준다
- POST /api/challenges/{id}/plan     그 다트로 일정을 짠다 (Server-Sent Events로 진행 상황 스트리밍)
- POST /api/challenges               던지기부터 일정까지 한 번에 (SSE)
- GET  /api/challenges/{id}          꽂힌 곳과, 완성됐다면 일정 조회 (공유 링크용)
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
from travel_roulette.dart import DEPARTURES, DURATION_LABELS, GeocodeError, geocoder_name, range_for  # noqa: E402
from travel_roulette.render import found_items  # noqa: E402
from travel_roulette.request_parser import BUDGET_LEVELS, TripRequest  # noqa: E402

logger = logging.getLogger("travel_roulette.server")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

STATIC_DIR = Path(__file__).parent / "static"
USER_ID = "web"  # 공유 링크로 누구나 결과를 볼 수 있게 단일 사용자 공간 사용
MAX_CONCURRENT_RUNS = int(os.getenv("MAX_CONCURRENT_RUNS", "4"))

# 노드가 끝나면(이벤트를 내면) 시작되는 다음 단계.
# LLM 노드는 응답이 끝나야 이벤트가 나오므로 '완료된 노드 → 다음 단계 진행 중'으로 매핑한다.
STAGES: dict[str, tuple[str, str]] = {
    "setup_challenge": ("research", "주변 명소·맛집·교통 리서치"),
    "prepare_planning": ("plan", "일정 짜는 중"),
    "itinerary_planner": ("review", "일정 검수"),
    "review_gate": ("budget", "예산 계산"),  # approved 라우트일 때만
    "after_revision": ("budget", "예산 계산"),  # done 라우트일 때만
    "present_plan": ("done", "완성"),
}


# 특정 라우트로 나갈 때만 다음 단계로 치는 노드
_ROUTE_GATED = {"review_gate": "approved", "after_revision": "done"}


def make_session_service() -> BaseSessionService:
  url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/sessions.db")
  if url == "memory":
    return InMemorySessionService()
  if url.startswith("sqlite"):
    db_path = url.split(":///", 1)[-1]
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
  return DatabaseSessionService(db_url=url)


class _Job:
  """일정 짜기 한 번의 실행. 브라우저 연결과 따로 돌아서, 창을 닫아도 끝까지 만들고
  나중에 같은 링크로 오면 지금까지의 진행 상황부터 이어서 볼 수 있다."""

  def __init__(self) -> None:
    self.events: list[dict[str, Any]] = []
    self.done = False
    self.changed = asyncio.Condition()
    self.task: asyncio.Task[None] | None = None

  async def push(self, event: dict[str, Any] | None = None, *, done: bool = False) -> None:
    async with self.changed:
      if event:
        self.events.append(event)
      self.done = self.done or done
      self.changed.notify_all()

  async def stream(self) -> AsyncIterator[str]:
    sent = 0
    while True:
      async with self.changed:
        await self.changed.wait_for(lambda: len(self.events) > sent or self.done)
        batch, finished = self.events[sent:], self.done
      for event in batch:
        yield _sse(event)
      sent += len(batch)
      if finished:
        return


class _State:
  runner: Runner
  session_service: BaseSessionService
  semaphore: asyncio.Semaphore
  jobs: dict[str, _Job]


state = _State()


@asynccontextmanager
async def lifespan(_: FastAPI):
  state.session_service = make_session_service()
  state.runner = Runner(app=travel_agent.app, session_service=state.session_service)
  state.semaphore = asyncio.Semaphore(MAX_CONCURRENT_RUNS)
  state.jobs = {}
  yield
  for job in list(state.jobs.values()):
    if job.task:
      job.task.cancel()
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
      "durations": [
          {"nights": n, "label": label, "range_km": list(range_for(n))}
          for n, label in DURATION_LABELS.items() if n <= 4
      ],
      "departures": [{"name": d.name, "lat": d.lat, "lon": d.lon} for d in DEPARTURES.values()],
      "budget_levels": list(BUDGET_LEVELS),
      # 네이버 지도 Client ID: 등록한 도메인에서만 동작하는 공개용 키라 브라우저에 줘도 된다.
      # 없으면 프론트엔드가 OpenStreetMap 지도로 대신 보여 준다.
      "naver_map_key": os.getenv("NAVER_MAP_KEY_ID") or None,
      # 다트 주소 검색에 쓰는 곳 ("naver" 또는 "nominatim"): 화면 아래 출처 표기를 맞춘다
      "geocoder": geocoder_name(),
  }


# 리서치 메모가 state에 들어오는 대로 '찾은 곳'으로 먼저 보여 준다 (교통 메모는 장소 목록이 아니라 제외)
_FOUND_KEYS = {"spots_notes": "spots", "food_notes": "food"}


async def _run_job(session_id: str, job: _Job) -> None:
  await job.push({"type": "session", "id": session_id})
  if state.semaphore.locked():
    await job.push({"type": "queued", "message": "요청이 많아 잠시 대기 중이에요…"})
  try:
    async with state.semaphore:
      seen_stages: set[str] = set()
      sent_challenge = False
      plan: dict[str, Any] | None = None
      invocation_id = ""
      message = types.Content(role="user", parts=[types.Part(text="랜덤 여행 챌린지 시작")])
      async for event in state.runner.run_async(
          user_id=USER_ID, session_id=session_id, new_message=message
      ):
        node = _node_name(event)
        if node in _ROUTE_GATED and getattr(event.actions, "route", None) != _ROUTE_GATED[node]:
          node = None  # 수정 루프 안에 있는 중
        if node:
          stage, label = STAGES[node]
          if stage not in seen_stages:
            seen_stages.add(stage)
            await job.push({"type": "stage", "stage": stage, "label": label})

        delta = event.actions.state_delta if event.actions else {}
        if not sent_challenge and delta.get("challenge"):
          sent_challenge = True
          await job.push({"type": "challenge", "challenge": delta["challenge"],
                          "weather": delta.get("weather")})
        for key, kind in _FOUND_KEYS.items():
          items = found_items(delta[key]) if isinstance(delta.get(key), str) else []
          if items:
            await job.push({"type": "found", "kind": kind, "items": items})
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
        await job.push({"type": "result", "id": session_id, "plan": plan, "stats": stats})
      else:
        await job.push({"type": "error", "message": "일정을 완성하지 못했어요. 다시 시도해 주세요."})
  except Exception as e:  # 사용자에게는 일반 메시지만
    logger.exception("챌린지 실행 실패 (session=%s)", session_id)
    await job.push({"type": "error", "message": _user_message(e)})
  finally:
    state.jobs.pop(session_id, None)
    await asyncio.shield(job.push(done=True))


def _start_job(session_id: str) -> _Job:
  """이미 돌고 있으면 그 실행에 붙고, 아니면 새로 시작한다."""
  job = state.jobs.get(session_id)
  if job is None:
    job = state.jobs[session_id] = _Job()
    job.task = asyncio.create_task(_run_job(session_id, job))
  return job


def _stream(events: AsyncIterator[str]) -> StreamingResponse:
  return StreamingResponse(
      events, media_type="text/event-stream",
      headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
  )


async def _create_session(initial: dict[str, Any]) -> str:
  session_id = uuid.uuid4().hex[:12]
  await state.session_service.create_session(
      app_name=APP_NAME, user_id=USER_ID, session_id=session_id, state=initial)
  return session_id


async def _get_session(session_id: str) -> Any:
  if not session_id.isalnum() or len(session_id) > 64:
    raise HTTPException(status_code=400, detail="잘못된 ID입니다.")
  session = await state.session_service.get_session(
      app_name=APP_NAME, user_id=USER_ID, session_id=session_id
  )
  if not session or not session.state.get("challenge"):
    raise HTTPException(status_code=404, detail="결과를 찾을 수 없어요.")
  return session


def _user_message(error: BaseException) -> str:
  seen: set[int] = set()
  err: BaseException | None = error
  while err is not None and id(err) not in seen:
    seen.add(id(err))
    if isinstance(err, GeocodeError):
      return f"{err} 지도 서비스가 잠시 응답하지 않을 수도 있어요."
    err = err.__cause__ or err.__context__
  return "AI 호출 중 문제가 생겼어요. 잠시 후 다시 시도해 주세요."


@app.post("/api/darts")
async def create_dart(req: TripRequest) -> dict[str, Any]:
  try:
    drawn = await travel_agent.draw_challenge(req)
  except GeocodeError as e:
    raise HTTPException(status_code=503, detail=_user_message(e)) from e
  session_id = await _create_session(
      {"trip_request": req.model_dump(mode="json"), **drawn, "prethrown": True})
  return {"id": session_id, "challenge": drawn["challenge"], "weather": drawn["weather"]}


@app.post("/api/challenges/{session_id}/plan")
async def plan_challenge(session_id: str) -> StreamingResponse:
  session = await _get_session(session_id)
  plan = session.state.get("final_plan")
  if plan and session_id not in state.jobs:

    async def finished() -> AsyncIterator[str]:
      yield _sse({"type": "result", "id": session_id, "plan": plan, "stats": {}})

    return _stream(finished())
  return _stream(_start_job(session_id).stream())


@app.post("/api/challenges")
async def create_challenge(req: TripRequest) -> StreamingResponse:
  session_id = await _create_session({"trip_request": req.model_dump(mode="json")})
  return _stream(_start_job(session_id).stream())


@app.get("/api/challenges/{session_id}")
async def get_challenge(session_id: str) -> dict[str, Any]:
  session = await _get_session(session_id)
  return {
      "id": session_id,
      "challenge": session.state["challenge"],
      "weather": session.state.get("weather"),
      "plan": session.state.get("final_plan"),
      "planning": session_id in state.jobs,
  }
