"""관측성(Observability) 플러그인 — Kaggle Day 4a 예제를 실서비스용으로 확장.

예제의 CountInvocationPlugin은 인스턴스 변수로 횟수를 셌지만, 서버에서는 여러
요청이 동시에 돌기 때문에 값이 섞인다. 여기서는 invocation_id별로 따로 집계한다.

실행 1회마다 다음을 모아 구조화 로그(JSON 한 줄)로 남긴다.
- 전체 소요 시간, 노드별 완료 시점(시작 후 몇 초)
- 에이전트별 LLM 호출 수·응답 시간·토큰(입력/출력/사고)
- 모델 오류 수
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass, field
import json
import logging
import time
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events.event import Event
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin

logger = logging.getLogger("travel_roulette.metrics")


@dataclass
class AgentStats:
  calls: int = 0
  errors: int = 0
  seconds: float = 0.0
  prompt_tokens: int = 0
  output_tokens: int = 0
  thought_tokens: int = 0


@dataclass
class RunMetrics:
  invocation_id: str
  session_id: str
  started: float
  duration_s: float | None = None
  milestones: dict[str, float] = field(default_factory=dict)  # 노드 → 완료 시점(초)
  agents: dict[str, AgentStats] = field(default_factory=dict)
  _pending: dict[str, list[float]] = field(default_factory=dict, repr=False)

  def elapsed(self) -> float:
    return round(time.perf_counter() - self.started, 2)

  def summary(self) -> dict[str, Any]:
    totals = AgentStats()
    for s in self.agents.values():
      totals.calls += s.calls
      totals.errors += s.errors
      totals.prompt_tokens += s.prompt_tokens
      totals.output_tokens += s.output_tokens
      totals.thought_tokens += s.thought_tokens
    return {
        "invocation_id": self.invocation_id,
        "session_id": self.session_id,
        "duration_s": self.duration_s,
        "llm_calls": totals.calls,
        "llm_errors": totals.errors,
        "tokens": {
            "prompt": totals.prompt_tokens,
            "output": totals.output_tokens,
            "thought": totals.thought_tokens,
            "total": totals.prompt_tokens + totals.output_tokens + totals.thought_tokens,
        },
        "milestones": self.milestones,
        "agents": {
            name: {**asdict(s), "seconds": round(s.seconds, 2)} for name, s in self.agents.items()
        },
    }


def _node_name(event: Event) -> str | None:
  path = getattr(getattr(event, "node_info", None), "path", "") or ""
  last = path.rsplit("/", 1)[-1]
  return last.split("@", 1)[0] or None


class RunMetricsPlugin(BasePlugin):
  """실행(invocation)별 LLM 호출·토큰·소요 시간을 집계하는 플러그인."""

  def __init__(self, max_runs: int = 500) -> None:
    super().__init__(name="run_metrics")
    self._runs: OrderedDict[str, RunMetrics] = OrderedDict()
    self._max_runs = max_runs

  # ---- 조회 API ----
  def get(self, invocation_id: str) -> dict[str, Any] | None:
    run = self._runs.get(invocation_id)
    return run.summary() if run else None

  def latest(self) -> dict[str, Any] | None:
    return next(reversed(self._runs.values())).summary() if self._runs else None

  # ---- 내부 ----
  def _run(self, invocation_id: str) -> RunMetrics | None:
    return self._runs.get(invocation_id)

  def _agent(self, ctx: CallbackContext) -> tuple[RunMetrics | None, str]:
    return self._run(ctx.invocation_id), ctx.agent_name or "unknown"

  # ---- 콜백 ----
  async def before_run_callback(self, *, invocation_context: InvocationContext) -> None:
    self._runs[invocation_context.invocation_id] = RunMetrics(
        invocation_id=invocation_context.invocation_id,
        session_id=invocation_context.session.id,
        started=time.perf_counter(),
    )
    while len(self._runs) > self._max_runs:
      self._runs.popitem(last=False)
    return None

  async def before_model_callback(
      self, *, callback_context: CallbackContext, llm_request: LlmRequest
  ) -> None:
    run, name = self._agent(callback_context)
    if run:
      run._pending.setdefault(name, []).append(time.perf_counter())
    return None

  async def after_model_callback(
      self, *, callback_context: CallbackContext, llm_response: LlmResponse
  ) -> None:
    run, name = self._agent(callback_context)
    if not run or llm_response.partial:
      return None
    stats = run.agents.setdefault(name, AgentStats())
    starts = run._pending.get(name) or []
    if starts:
      stats.seconds += time.perf_counter() - starts.pop()
    stats.calls += 1
    usage = llm_response.usage_metadata
    if usage:
      stats.prompt_tokens += usage.prompt_token_count or 0
      stats.output_tokens += usage.candidates_token_count or 0
      stats.thought_tokens += usage.thoughts_token_count or 0
    return None

  async def on_model_error_callback(
      self, *, callback_context: CallbackContext, llm_request: LlmRequest, error: Exception
  ) -> None:
    # 시작 시각은 남겨 둔다: 에이전트의 오류 콜백이 대체 응답(fallback)을 만들면
    # after_model_callback에서 재시도·대체 호출까지 포함한 실제 소요 시간으로 기록된다.
    run, name = self._agent(callback_context)
    if run:
      run.agents.setdefault(name, AgentStats()).errors += 1
    return None

  async def on_event_callback(
      self, *, invocation_context: InvocationContext, event: Event
  ) -> None:
    run = self._run(invocation_context.invocation_id)
    node = _node_name(event)
    if run and node and event.author != "user":
      run.milestones[node] = run.elapsed()  # 마지막 이벤트 시점 = 노드 완료 시점
    return None

  async def after_run_callback(self, *, invocation_context: InvocationContext) -> None:
    run = self._run(invocation_context.invocation_id)
    if not run:
      return None
    run.duration_s = run.elapsed()
    logger.info("run_metrics %s", json.dumps(run.summary(), ensure_ascii=False))
    return None
