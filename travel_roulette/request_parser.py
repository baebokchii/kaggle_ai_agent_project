"""채팅 메시지("2박 3일 부산에서 둘이 알뜰하게")에서 여행 조건을 뽑아낸다.

간단한 정규식 기반이라 LLM 호출 없이 빠르고 결정적이다.
웹 API는 구조화된 값을 바로 넘기므로 이 파서는 채팅(adk web) 경로용이다.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

from .destinations import DEPARTURES, MAX_NIGHTS

BUDGET_LEVELS = ("알뜰", "보통", "여유")

_KOR_NUM = {"무": 0, "일": 1, "한": 1, "이": 2, "두": 2, "삼": 3, "세": 3, "사": 4, "네": 4,
            "오": 5, "육": 6, "륙": 6, "칠": 7}


class TripRequest(BaseModel):
  """여행 조건. 웹 API 요청 본문과 세션 state에 그대로 저장된다."""

  nights: int = Field(default=1, ge=0, le=MAX_NIGHTS, description="숙박 일수 (0=당일치기)")
  departure: str = Field(default="서울", description="출발 도시")
  travelers: int = Field(default=2, ge=1, le=10, description="인원")
  budget_level: str = Field(default="보통", description="예산 스타일: 알뜰/보통/여유")
  start_date: dt.date | None = Field(default=None, description="출발일 (선택)")
  exclude: list[str] = Field(default_factory=list, description="추첨에서 제외할 여행지")

  @field_validator("departure")
  @classmethod
  def _check_departure(cls, v: str) -> str:
    if v not in DEPARTURES:
      raise ValueError(f"출발지는 {', '.join(DEPARTURES)} 중 하나여야 합니다.")
    return v

  @field_validator("budget_level")
  @classmethod
  def _check_budget(cls, v: str) -> str:
    if v not in BUDGET_LEVELS:
      raise ValueError(f"예산 스타일은 {', '.join(BUDGET_LEVELS)} 중 하나여야 합니다.")
    return v

  @property
  def days(self) -> int:
    return self.nights + 1


def _to_int(token: str) -> int | None:
  if token.isdigit():
    return int(token)
  return _KOR_NUM.get(token)


def parse_nights(text: str) -> int | None:
  t = text.replace(" ", "")
  if "당일" in t or "무박" in t:
    return 0
  m = re.search(r"(\d+|[일이삼사오육륙칠])박", t)
  if m:
    n = _to_int(m.group(1))
    if n is not None:
      return min(n, MAX_NIGHTS)
  m = re.search(r"(\d+)일(?:짜리|간|동안|여행|코스|일정)", t)
  if m:
    return min(max(int(m.group(1)) - 1, 0), MAX_NIGHTS)
  m = re.search(r"(\d+)days?", t.lower())
  if m:
    return min(max(int(m.group(1)) - 1, 0), MAX_NIGHTS)
  return None


def parse_departure(text: str) -> str | None:
  for name in DEPARTURES:
    if re.search(rf"{name}\s*(?:에서|출발|발)", text):
      return name
  return None


def parse_travelers(text: str) -> int | None:
  m = re.search(r"(\d+)\s*(?:명|인)", text)
  if m:
    return max(1, min(int(m.group(1)), 10))
  if re.search(r"혼자|나홀로|혼행|1인", text):
    return 1
  if re.search(r"둘이|커플|연인|부부|친구랑|2인", text):
    return 2
  return None


def parse_budget_level(text: str) -> str | None:
  if re.search(r"알뜰|가성비|저렴|싸게|짠내|아껴", text):
    return "알뜰"
  if re.search(r"럭셔리|여유|호캉스|고급|플렉스|넉넉", text):
    return "여유"
  return None


def parse_start_date(text: str, today: dt.date | None = None) -> dt.date | None:
  today = today or dt.date.today()
  m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
  if m:
    try:
      return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
      return None
  m = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", text)
  if m:
    try:
      d = dt.date(today.year, int(m.group(1)), int(m.group(2)))
    except ValueError:
      return None
    return d if d >= today else d.replace(year=today.year + 1)
  if "내일" in text:
    return today + dt.timedelta(days=1)
  if "모레" in text:
    return today + dt.timedelta(days=2)
  return None


def merge_request(
    message: str, previous: dict[str, Any] | None, today: dt.date | None = None
) -> TripRequest:
  """메시지에서 읽은 값 > 이전 요청값 > 기본값 순으로 합친다."""
  base = TripRequest.model_validate(previous) if previous else TripRequest()
  updates: dict[str, Any] = {}
  if (n := parse_nights(message)) is not None:
    updates["nights"] = n
  if dep := parse_departure(message):
    updates["departure"] = dep
  if (t := parse_travelers(message)) is not None:
    updates["travelers"] = t
  if b := parse_budget_level(message):
    updates["budget_level"] = b
  if d := parse_start_date(message, today):
    updates["start_date"] = d
  return base.model_copy(update=updates)
