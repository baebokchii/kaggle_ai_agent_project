"""예산 합계 계산. LLM은 단가만 추정하고 산수는 전부 파이썬이 한다."""

from __future__ import annotations

from typing import Any

from .schemas import BudgetEstimate

CONTINGENCY_RATE = 0.1  # 예비비 10%
CATEGORY_ORDER = ("교통", "숙박", "식비", "입장료·체험", "카페·간식", "기타")

# 예산 스타일별 1인 1일 가이드(숙박 제외, 원) — 프롬프트와 결과 비교에 사용
DAILY_GUIDE = {
    "알뜰": (50_000, 80_000),
    "보통": (80_000, 130_000),
    "여유": (130_000, 250_000),
}
# 1박 객실 가이드(원)
ROOM_GUIDE = {
    "알뜰": (40_000, 80_000),
    "보통": (90_000, 160_000),
    "여유": (180_000, 400_000),
}


def _round_up(value: float, unit: int = 1000) -> int:
  return int(-(-value // unit) * unit)


def calculate_budget(estimate: BudgetEstimate | dict, travelers: int) -> dict[str, Any]:
  if isinstance(estimate, dict):
    estimate = BudgetEstimate.model_validate(estimate)
  travelers = max(1, travelers)

  lines = []
  by_cat: dict[str, int] = {c: 0 for c in CATEGORY_ORDER}
  for item in estimate.items:
    unit = max(0, item.unit_cost_krw)
    qty = max(0, min(item.quantity, 100))
    mult = travelers if item.per_person else 1
    subtotal = unit * qty * mult
    by_cat[item.category] = by_cat.get(item.category, 0) + subtotal
    lines.append({
        "category": item.category,
        "description": item.description,
        "unit_cost_krw": unit,
        "quantity": qty,
        "per_person": item.per_person,
        "subtotal_krw": subtotal,
    })

  total = sum(by_cat.values())
  contingency = _round_up(total * CONTINGENCY_RATE)
  grand_total = total + contingency
  return {
      "travelers": travelers,
      "items": lines,
      "by_category": [
          {"category": c, "amount_krw": by_cat[c]} for c in CATEGORY_ORDER if by_cat.get(c)
      ],
      "subtotal_krw": total,
      "contingency_krw": contingency,
      "total_krw": grand_total,
      "per_person_krw": _round_up(grand_total / travelers),
      "assumptions": list(estimate.assumptions),
  }


def format_krw(value: int) -> str:
  return f"{value:,}원"
