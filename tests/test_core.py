"""다트·파서·예산·날씨 등 LLM과 무관한 순수 로직 테스트."""

from __future__ import annotations

import datetime as dt
import random

import pytest

from travel_roulette.budget import calculate_budget
from travel_roulette import dart
from travel_roulette.dart import DEPARTURES, MAX_NIGHTS, haversine_km, range_for
from travel_roulette.request_parser import (
    TripRequest, merge_request, parse_budget_level, parse_departure, parse_nights,
    parse_start_date, parse_travelers)
from travel_roulette.weather import describe_weather, parse_forecast


# ---- 다트 ----
@pytest.mark.parametrize("departure", list(DEPARTURES))
@pytest.mark.parametrize("nights", range(MAX_NIGHTS + 1))
def test_samples_stay_in_range(departure, nights):
  dep = DEPARTURES[departure]
  lo, hi = range_for(nights)
  rng = random.Random(nights)
  for _ in range(200):
    lat, lon = dart.sample_point(dep, nights, rng)
    d = haversine_km(dep.lat, dep.lon, lat, lon)
    assert d >= lo - 1 and (hi is None or d <= hi + 1)


def test_land_prefilter():
  for lat, lon in [(37.5665, 126.978), (37.75, 128.88), (34.81, 126.39), (33.38, 126.55), (37.49, 130.86)]:
    assert dart.maybe_land(lat, lon)
  for lat, lon in [(36.0, 125.5), (37.5, 130.0), (34.0, 127.5), (39.0, 127.5)]:
    assert not dart.maybe_land(lat, lon)


# 실제 Nominatim 응답(2026-11)에서 가져온 주소
NOMINATIM = {
    "seoul": {"quarter": "을지로1가", "suburb": "명동", "borough": "중구", "city": "서울특별시", "country_code": "kr"},
    "yeongyang": {"village": "동부리", "town": "영양읍", "county": "영양군", "province": "경상북도", "country_code": "kr"},
    "jeju": {"city_district": "아라동", "city": "제주시", "province": "제주특별자치도", "country_code": "kr"},
    "sea": {"county": "부안군", "province": "전북특별자치도", "country_code": "kr"},
    "north": {"county": "창도군", "state": "강원도", "country_code": "kp"},
}


def test_parse_landing():
  lm, _ = dart.parse_landing(37.5, 127.0, {"address": NOMINATIM["seoul"]})
  assert (lm.name, lm.sido_short, lm.full_name) == ("중구 명동", "서울", "서울특별시 중구 명동")
  lm, _ = dart.parse_landing(36.6, 129.1, {"address": NOMINATIM["yeongyang"]})
  assert (lm.name, lm.village) == ("영양군 영양읍", "동부리")
  lm, _ = dart.parse_landing(33.4, 126.5, {"address": NOMINATIM["jeju"]})
  assert (lm.name, lm.sido_short) == ("제주시 아라동", "제주")
  assert dart.parse_landing(35.5, 126.0, {"address": NOMINATIM["sea"]}) == (None, "빈 땅")
  assert dart.parse_landing(38.6, 127.6, {"address": NOMINATIM["north"]}) == (None, "국외")
  assert dart.parse_landing(36.0, 125.5, None) == (None, "빈 땅")


def test_short_sido_handles_new_province_names():
  assert dart.short_sido("경상북도") == "경북"
  assert dart.short_sido("전남광주통합특별시") == "전남광주"
  assert dart.short_sido("세종특별자치시") == "세종"
  assert dart.short_sido("어딘가") == "어딘가"


def test_island_rules():
  jeju, _ = dart.parse_landing(33.4, 126.5, {"address": NOMINATIM["jeju"]})
  assert dart.trip_rule(jeju, 0) and not dart.trip_rule(jeju, 1)


@pytest.mark.asyncio
async def test_throw_dart_records_misses_and_is_reproducible(fake_geo):
  calls = []

  async def flaky(lat, lon):
    calls.append((lat, lon))
    return None if len(calls) <= 2 else await fake_geo(lat, lon)  # 처음 두 번은 바다/빈 땅

  a = await dart.throw_dart(1, "대전", rng=random.Random(5), geocoder=flaky)
  assert len(a.misses) == 2 and all(m["reason"] == "빈 땅" for m in a.misses)
  lo, hi = range_for(1)
  assert lo <= a.distance_km <= hi
  c = await dart.throw_dart(1, "대전", rng=random.Random(5))
  d = await dart.throw_dart(1, "대전", rng=random.Random(5))
  assert (c.landing.lat, c.landing.lon) == (d.landing.lat, d.landing.lon)  # 같은 시드 → 같은 지점
  e = await dart.throw_dart(1, "대전", rng=random.Random(5), exclude=[c.landing.name])
  assert e.landing.name != c.landing.name


@pytest.mark.asyncio
async def test_throw_dart_gives_up_on_all_sea():
  async def ocean(lat, lon):
    return None

  with pytest.raises(dart.GeocodeError):
    await dart.throw_dart(0, "서울", rng=random.Random(0), geocoder=ocean, max_lookups=3)


@pytest.mark.asyncio
async def test_throw_dart_rejects_unknown_departure():
  with pytest.raises(ValueError):
    await dart.throw_dart(1, "평양")


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
