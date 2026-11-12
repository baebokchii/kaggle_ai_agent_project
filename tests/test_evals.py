"""평가 세트·커스텀 지표 테스트 (Kaggle Day 4b 응용).

루브릭 LLM 심사는 실제 Gemini가 필요하므로, 여기서는 코드 채점 지표만으로
ADK 평가 파이프라인(AgentEvaluator)을 가짜 LLM 위에서 끝까지 돌려 본다.
"""

from __future__ import annotations

from pathlib import Path

from google.adk.evaluation.agent_evaluator import AgentEvaluator
from google.adk.evaluation.eval_config import EvalConfig
from google.adk.evaluation.eval_set import EvalSet
import pytest

from travel_roulette import agent as travel_agent
from travel_roulette.evals.metrics import check_plan
from travel_roulette.render import render_markdown

EVAL_DIR = Path(travel_agent.__file__).parent / "evals"


def _sample_plan(days: int, departure: str = "서울", travelers: int = 2) -> dict:
  from fake_llm import BUDGET, _itinerary
  from travel_roulette.budget import calculate_budget

  return {
      "challenge": {"destination": {"name": "강릉", "province": "강원"}, "departure": departure,
                    "duration_label": f"{days - 1}박 {days}일", "travelers": travelers,
                    "budget_level": "보통", "distance_km": 160},
      "itinerary": _itinerary(days),
      "budget": calculate_budget(BUDGET, travelers),
      "weather_text": "맑음",
      "sources": [],
  }


def test_check_plan_passes_matching_response():
  md = render_markdown(_sample_plan(3, "대구", 4))
  assert all(check_plan("2박 3일 대구에서 4명", md).values())


def test_check_plan_catches_wrong_day_count_and_departure():
  md = render_markdown(_sample_plan(2))
  checks = check_plan("2박 3일 부산에서 출발", md)
  assert not checks["일차 수 일치"]
  assert not checks["출발지 반영"]
  assert checks["예산 합계 표시"]


def test_eval_files_are_valid():
  es = EvalSet.model_validate_json((EVAL_DIR / "challenge.evalset.json").read_text())
  cfg = EvalConfig.model_validate_json((EVAL_DIR / "test_config.json").read_text())
  assert len(es.eval_cases) >= 5
  assert "rubric_based_final_response_quality_v1" in cfg.criteria
  assert "plan_structure_score" in cfg.custom_metrics


@pytest.mark.asyncio
async def test_adk_eval_pipeline_with_fake_llm(fake_llm):
  fake_llm.critic_rejections = 0
  es = EvalSet.model_validate_json((EVAL_DIR / "challenge.evalset.json").read_text())
  # 당일치기 케이스만: 가짜 LLM은 days=1 일정을 돌려준다
  es.eval_cases = [c for c in es.eval_cases if c.eval_id == "day_trip_seoul"]
  fake_llm.days = 1
  full = EvalConfig.model_validate_json((EVAL_DIR / "test_config.json").read_text())
  cfg = EvalConfig(criteria={"plan_structure_score": 1.0}, custom_metrics=full.custom_metrics)
  await AgentEvaluator.evaluate_eval_set(
      agent_module=travel_agent.app, eval_set=es, eval_config=cfg, num_runs=1,
      print_detailed_results=False,
  )


def test_check_plan_ignores_source_urls_for_language():
  md = render_markdown(_sample_plan(1)) + "\n" + "\n".join(
      f"- [site{i}](https://vertexaisearch.cloud.google.com/grounding-api-redirect/{'A' * 200})" for i in range(8))
  assert check_plan("당일치기", md)["한국어 응답"]
