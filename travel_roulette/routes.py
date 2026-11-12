"""일정 동선(권역 순서) 분석.

LLM 검수자는 "남부 → 시내 → 남서부 → 시내" 같은 왕복 동선을 자주 놓친다.
플래너가 활동마다 권역(area)을 적게 하고, 코드가 권역 순서를 뽑아
왕복을 결정적으로 찾아낸다. 결과는 검수 게이트와 검수·수정 프롬프트에 쓰인다.
"""

from __future__ import annotations

from typing import Any

# 권역 판단에서 빼는 카테고리 (이동 자체·숙소 복귀는 동선 왕복으로 보지 않는다)
_SKIP = {"이동", "숙소"}


def _norm(area: str) -> str:
  return "".join((area or "").split())


def area_sequence(day: dict[str, Any]) -> list[str]:
  """연속 중복을 합친 하루의 권역 순서."""
  seq: list[str] = []
  for a in day.get("activities") or []:
    if a.get("category") in _SKIP:
      continue
    area = _norm(a.get("area", ""))
    if area and (not seq or seq[-1] != area):
      seq.append(area)
  return seq


def backtracks(seq: list[str]) -> list[str]:
  """이미 떠났던 권역으로 되돌아간 경우. 하루를 시작한 권역으로 마지막에 돌아오는 것은 허용."""
  seen: set[str] = set()
  out = []
  for i, area in enumerate(seq):
    if area in seen and not (i == len(seq) - 1 and area == seq[0]):
      out.append(area)
    seen.add(area)
  return out


def route_issues(itinerary: dict[str, Any]) -> list[str]:
  issues = []
  for day in itinerary.get("days") or []:
    seq = area_sequence(day)
    back = backtracks(seq)
    if back:
      issues.append(
          f"Day {day.get('day')} 동선이 왕복합니다 ({' → '.join(seq)}). "
          f"'{', '.join(dict.fromkeys(back))}' 권역 일정을 한 번에 묶어 주세요."
      )
  return issues


def route_summary(itinerary: dict[str, Any]) -> str:
  lines = []
  for day in itinerary.get("days") or []:
    seq = area_sequence(day)
    back = backtracks(seq)
    mark = f"  ⚠️ 왕복: {', '.join(dict.fromkeys(back))}" if back else ""
    lines.append(f"Day {day.get('day')}: {' → '.join(seq) or '(정보 없음)'}{mark}")
  return "\n".join(lines) or "(일정 없음)"
