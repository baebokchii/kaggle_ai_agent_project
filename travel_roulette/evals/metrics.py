"""결정적(코드 기반) 평가 지표.

여행지가 매번 랜덤이라 Day 4b 예제의 `response_match_score`(정답 문장과의 유사도)는
쓸 수 없다. 대신 "요청한 조건이 결과에 제대로 반영됐는가"를 코드로 채점한다.
품질(동선·현실성 등)은 test_config.json의 루브릭 LLM 심사가 맡는다.
"""

from __future__ import annotations

import re
from typing import Optional

from google.adk.evaluation.eval_case import Invocation
from google.adk.evaluation.eval_metrics import EvalMetric
from google.adk.evaluation.evaluator import EvalStatus, EvaluationResult, PerInvocationResult
from google.genai import types

from ..request_parser import merge_request

PASS_THRESHOLD = 1.0  # 모든 체크를 통과해야 PASSED


def _text(content: Optional[types.Content]) -> str:
  if not content:
    return ""
  return "".join(p.text or "" for p in content.parts or [])


def check_plan(user_text: str, response: str) -> dict[str, bool]:
  """최종 마크다운 응답이 요청 조건을 지켰는지 항목별로 확인한다."""
  req = merge_request(user_text, None)
  day_numbers = [int(n) for n in re.findall(r"^### Day (\d+)", response, flags=re.M)]
  day_sections = re.split(r"^### Day \d+", response, flags=re.M)[1:]
  prose = re.sub(r"https?://\S+", "", response)  # 출처 URL은 언어 판단에서 제외
  hangul = len(re.findall(r"[가-힣]", prose))
  letters = len(re.findall(r"[A-Za-z가-힣]", prose)) or 1
  return {
      "일차 수 일치": day_numbers == list(range(1, req.days + 1)),
      "출발지 반영": f"{req.departure} 출발" in response,
      "인원 반영": f"{req.travelers}명" in response,
      "매일 식사 포함": bool(day_sections) and all("[식사]" in s for s in day_sections),
      "예산 합계 표시": "**합계**" in response and "1인당" in response,
      "한국어 응답": hangul / letters > 0.5,
  }


def plan_structure_score(
    eval_metric: EvalMetric,
    actual_invocations: list[Invocation],
    expected_invocations: Optional[list[Invocation]] = None,
    conversation_scenario=None,
) -> EvaluationResult:
  per_invocation = []
  for inv in actual_invocations:
    checks = check_plan(_text(inv.user_content), _text(inv.final_response))
    score = sum(checks.values()) / len(checks)
    per_invocation.append(PerInvocationResult(
        actual_invocation=inv,
        score=score,
        eval_status=EvalStatus.PASSED if score >= PASS_THRESHOLD else EvalStatus.FAILED,
    ))
  if not per_invocation:
    return EvaluationResult()
  overall = sum(r.score for r in per_invocation) / len(per_invocation)
  return EvaluationResult(
      overall_score=overall,
      overall_eval_status=EvalStatus.PASSED if overall >= PASS_THRESHOLD else EvalStatus.FAILED,
      per_invocation_results=per_invocation,
  )
