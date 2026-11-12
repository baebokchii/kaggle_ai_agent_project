"""추첨·파서·예산·날씨 등 LLM과 무관한 순수 로직 테스트."""

from __future__ import annotations

import datetime as dt
import random

import pytest

from travel_roulette.budget import calculate_budget
from travel_roulette.destinations import (
    DEPARTURES, DESTINATIONS_BY_NAME, MAX_NIGHTS, candidates_for, draw_destination, haversine_km)
from travel_roulette.request_parser import (
    TripRequest, merge_request, parse_budget_level, parse_departure, parse_nights,
    parse_start_date, parse_travelers)
from travel_roulette.weather import describe_weather, parse_forecast


# ---- 추첨 ----
@pytest.mark.parametrize("departure", list(DEPARTURES))
@pytest.mark.parametrize("nights", range(MAX_NIGHTS + 1))
def test_every_departure_and_duration_has_candidates(departure, nights):
  assert candidates_for(nights, departure), (departure, nights)


def test_day_trip_stays_close_and_skips_islands():
  for dest, dist in candidates_for(0, "서울"):
    assert dist <= 170
    assert dest.access == "육로"
  names = {d.name for d, _ in candidates_for(0, "서울")}
  assert "제주" not in names and "부산" not in names and "서울" not in names


def test_island_rules():
  assert "제주" not in {d.name for d, _ in candidates_for(0, "서울")}
  assert "제주" in {d.name for d, _ in candidates_for(1, "서울")}
  assert "울릉도" not in {d.name for d, _ in candidates_for(1, "서울")}
  assert "울릉도" in {d.name for d, _ in candidates_for(2, "서울")}


def test_draw_is_reproducible_with_seed_and_respects_exclude():
  a = draw_destination(2, "서울", rng=random.Random(1))
  b = draw_destination(2, "서울", rng=random.Random(1))
  assert a.destination == b.destination
  c = draw_destination(2, "서울", exclude=[a.destination.name], rng=random.Random(1))
  assert c.destination != a.destination


def test_draw_relaxes_when_everything_excluded():
  everything = list(DESTINATIONS_BY_NAME)
  r = draw_destination(0, "서울", exclude=everything, rng=random.Random(0))
  assert r.relaxed


def test_draw_rejects_unknown_departure():
  with pytest.raises(ValueError):
    draw_destination(1, "평양")


def test_haversine_seoul_busan():
  s, b = DEPARTURES["서울"], DEPARTURES["부산"]
  assert 310 < haversine_km(s.lat, s.lon, b.lat, b.lon) < 340


# ---- 요청 파싱 ----
@pytest.mark.parametrize("text,expected", [
    ("당일치기로 다녀올래", 0), ("1박 2일", 1), ("2박3일 여행", 2), ("이박삼일", 2),
    ("3일 일정", 2), ("4박 5일 가자", 4), ("10박", MAX_NIGHTS), ("어디든 좋아", None),
])
def test_parse_nights(text, expected):
  assert parse_nights(text) == expected


def test_parse_misc():
  assert parse_departure("대구에서 출발해요") == "대구"
  assert parse_departure("부산 출발") == "부산"
  assert parse_departure("서울 근교") is None
  assert parse_travelers("4명이서") == 4
  assert parse_travelers("혼자 갈래") == 1
  assert parse_travelers("커플 여행") == 2
  assert parse_budget_level("가성비 좋게") == "알뜰"
  assert parse_budget_level("호캉스 느낌") == "여유"
  assert parse_budget_level("그냥") is None


def test_parse_start_date():
  today = dt.date(2026, 11, 12)
  assert parse_start_date("11월 20일 출발", today) == dt.date(2026, 11, 20)
  assert parse_start_date("1월 3일", today) == dt.date(2027, 1, 3)  # 지난 날짜는 내년
  assert parse_start_date("2026-12-24", today) == dt.date(2026, 12, 24)
  assert parse_start_date("내일", today) == dt.date(2026, 11, 13)


def test_merge_request_keeps_previous_values():
  prev = TripRequest(nights=3, departure="광주", travelers=4).model_dump(mode="json")
  merged = merge_request("다시 돌려줘", prev)
  assert (merged.nights, merged.departure, merged.travelers) == (3, "광주", 4)
  merged = merge_request("이번엔 당일치기", prev)
  assert merged.nights == 0 and merged.departure == "광주"


def test_trip_request_validation():
  with pytest.raises(ValueError):
    TripRequest(departure="도쿄")
  with pytest.raises(ValueError):
    TripRequest(budget_level="무제한")
  with pytest.raises(ValueError):
    TripRequest(nights=30)


# ---- 예산 ----
def test_calculate_budget():
  est = {
      "items": [
          {"category": "교통", "description": "KTX 왕복", "unit_cost_krw": 25000, "quantity": 2, "per_person": True},
          {"category": "숙박", "description": "객실", "unit_cost_krw": 90000, "quantity": 2, "per_person": False},
          {"category": "식비", "description": "식사", "unit_cost_krw": -5, "quantity": 3, "per_person": True},
      ],
      "assumptions": ["가정"],
  }
  b = calculate_budget(est, travelers=3)
  assert b["subtotal_krw"] == 25000 * 2 * 3 + 90000 * 2  # 음수 단가는 0 처리
  assert b["contingency_krw"] == 33000
  assert b["total_krw"] == 363_000
  assert b["per_person_krw"] == 121_000
  assert [c["category"] for c in b["by_category"]] == ["교통", "숙박"]


# ---- 날씨 ----
def test_weather_parse_and_describe():
  payload = {"daily": {
      "time": ["2026-11-20"], "weather_code": [61], "temperature_2m_max": [12.1],
      "temperature_2m_min": [3.4], "precipitation_probability_max": [70]}}
  fc = parse_forecast(payload)
  assert fc[0]["summary"] == "약한 비"
  assert "강수확률 70%" in describe_weather(fc, dt.date(2026, 11, 20))
  assert "가을" in describe_weather(None, dt.date(2026, 10, 1))
