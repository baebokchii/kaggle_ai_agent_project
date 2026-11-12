"""Open-Meteo(무료, API 키 불필요)로 여행 기간 날씨 예보를 가져온다."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
FORECAST_HORIZON_DAYS = 15

# WMO weather code → 한국어
_WMO = {
    0: "맑음", 1: "대체로 맑음", 2: "구름 조금", 3: "흐림",
    45: "안개", 48: "안개",
    51: "약한 이슬비", 53: "이슬비", 55: "강한 이슬비",
    61: "약한 비", 63: "비", 65: "강한 비",
    66: "어는 비", 67: "어는 비",
    71: "약한 눈", 73: "눈", 75: "많은 눈", 77: "싸락눈",
    80: "소나기", 81: "소나기", 82: "강한 소나기",
    85: "눈 소나기", 86: "강한 눈 소나기",
    95: "뇌우", 96: "뇌우·우박", 99: "뇌우·우박",
}

_SEASON = {
    12: "겨울", 1: "겨울", 2: "겨울", 3: "봄", 4: "봄", 5: "봄",
    6: "여름", 7: "여름", 8: "여름", 9: "가을", 10: "가을", 11: "가을",
}


def season_of(day: dt.date) -> str:
  return _SEASON[day.month]


def parse_forecast(payload: dict) -> list[dict]:
  daily = payload.get("daily") or {}
  out = []
  for i, date in enumerate(daily.get("time", [])):
    code = (daily.get("weather_code") or [None])[i]
    out.append({
        "date": date,
        "summary": _WMO.get(code, "정보 없음"),
        "temp_max": (daily.get("temperature_2m_max") or [None])[i],
        "temp_min": (daily.get("temperature_2m_min") or [None])[i],
        "precip_prob": (daily.get("precipitation_probability_max") or [None])[i],
    })
  return out


def _fetch_sync(url: str, timeout: float) -> dict:
  req = urllib.request.Request(url, headers={"User-Agent": "travel-roulette/1.0"})
  with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (고정 URL)
    return json.loads(resp.read().decode("utf-8"))


async def fetch_forecast(
    lat: float, lon: float, start: dt.date, days: int, timeout: float = 5.0
) -> list[dict] | None:
  """예보 범위(약 2주) 안이면 일별 예보를, 아니면 None을 돌려준다."""
  today = dt.date.today()
  end = start + dt.timedelta(days=days - 1)
  if start < today or end > today + dt.timedelta(days=FORECAST_HORIZON_DAYS):
    return None
  query = urllib.parse.urlencode({
      "latitude": lat,
      "longitude": lon,
      "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
      "timezone": "Asia/Seoul",
      "start_date": start.isoformat(),
      "end_date": end.isoformat(),
  })
  try:
    payload = await asyncio.to_thread(_fetch_sync, f"{FORECAST_URL}?{query}", timeout)
  except Exception as e:  # 네트워크 실패는 치명적이지 않다
    logger.warning("날씨 조회 실패: %s", e)
    return None
  return parse_forecast(payload) or None


def describe_weather(forecast: list[dict] | None, start: dt.date | None) -> str:
  """프롬프트에 넣을 날씨 요약 문장."""
  if forecast:
    lines = []
    for f in forecast:
      prob = f"강수확률 {f['precip_prob']}%" if f.get("precip_prob") is not None else ""
      lines.append(
          f"- {f['date']}: {f['summary']}, {f['temp_min']}~{f['temp_max']}°C {prob}".rstrip()
      )
    return "여행 기간 날씨 예보:\n" + "\n".join(lines)
  ref = start or dt.date.today()
  when = f"{ref.month}월" if start else f"지금({ref.month}월)"
  return f"예보 정보 없음. {when}은 {season_of(ref)}이니 계절 특성을 고려하세요."
