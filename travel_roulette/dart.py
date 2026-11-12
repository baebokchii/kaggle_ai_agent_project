"""지도에 다트 던지기: 기간에 맞는 거리 안의 '육지'에서 완전히 무작위로 한 점을 뽑는다.

1. 출발지를 중심으로 기간별 거리 범위(도넛 모양) 안에서 면적이 고르게 좌표를 뽑는다.
2. 대략적인 남한 육지 윤곽(_LAND)으로 확실한 바다는 API 호출 없이 거른다.
3. OpenStreetMap Nominatim 역지오코딩으로 그 좌표의 읍·면·동을 찾는다.
   - 군 단위만 나오고 읍·면·동·리가 없으면 바다(연안 해역)나 사람 없는 산속으로 보고 다시 던진다.
   - 북한(kp)이나 해외는 다시 던진다.

Nominatim 이용 정책: 초당 1회 이하, 앱을 알 수 있는 User-Agent, 결과 캐싱, OSM 출처 표시.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import logging
import math
import os
import random
import time
from typing import Any, Awaitable, Callable
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 출발지·기간
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Departure:
  name: str
  lat: float
  lon: float


DEPARTURES: dict[str, Departure] = {
    d.name: d
    for d in [
        Departure("서울", 37.5665, 126.9780),
        Departure("인천", 37.4563, 126.7052),
        Departure("대전", 36.3504, 127.3845),
        Departure("대구", 35.8714, 128.6014),
        Departure("광주", 35.1595, 126.8526),
        Departure("부산", 35.1796, 129.0756),
    ]
}

DURATION_LABELS = {0: "당일치기", 1: "1박 2일", 2: "2박 3일", 3: "3박 4일", 4: "4박 5일", 5: "5박 6일", 6: "6박 7일"}
MAX_NIGHTS = max(DURATION_LABELS)

# 기간별 직선거리 범위(km). (최소, 최대) — 최대 None이면 남한 어디든
RANGES_KM: dict[int, tuple[float, float | None]] = {
    0: (20, 130),   # 당일치기: 도로로 편도 2시간 안팎
    1: (40, 300),   # 1박 2일
    2: (60, 450),   # 2박 3일
}
_LONG_TRIP = (80, None)  # 3박 이상: 전국


def duration_label(nights: int) -> str:
  return DURATION_LABELS.get(nights, f"{nights}박 {nights + 1}일")


def range_for(nights: int) -> tuple[float, float | None]:
  return RANGES_KM.get(nights, _LONG_TRIP)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
  r = 6371.0
  p1, p2 = math.radians(lat1), math.radians(lat2)
  dp, dl = p2 - p1, math.radians(lon2 - lon1)
  a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
  return 2 * r * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# 대략적인 남한 육지 윤곽 (경도, 위도). 바다를 API 호출 없이 거르는 1차 필터일 뿐이고,
# 최종 판정은 Nominatim이 한다. 해안선 근처 바다가 일부 포함되는 것은 의도된 것.
# 북쪽 경계는 휴전선보다 약 10km 남쪽으로 잡아 민간인 통제구역(민통선 이북)을 피한다.
# ---------------------------------------------------------------------------
_MAINLAND: tuple[tuple[float, float], ...] = (
    (126.55, 37.70), (126.72, 37.86), (127.10, 38.24), (127.60, 38.26), (128.10, 38.24),
    (128.36, 38.52), (128.62, 38.20), (128.95, 37.76), (129.34, 37.25), (129.43, 36.80),
    (129.46, 36.20), (129.58, 36.02), (129.47, 35.50), (129.24, 35.10), (128.62, 34.84),
    (128.05, 34.80), (127.55, 34.56), (127.02, 34.44), (126.55, 34.29), (126.26, 34.58),
    (126.30, 35.02), (126.40, 35.62), (126.52, 35.96), (126.12, 36.42), (126.10, 36.80),
    (126.48, 36.92), (126.72, 37.02), (126.56, 37.30), (126.38, 37.46), (126.40, 37.70),
)
_JEJU = (126.55, 33.38, 0.38, 0.19)       # 중심 경도, 위도, 경도 반경, 위도 반경 (타원)
_ULLEUNG = (130.87, 37.50, 0.06, 0.05)

KOREA_BBOX = (33.05, 124.6, 38.65, 131.0)  # 위도 최소, 경도 최소, 위도 최대, 경도 최대


def _in_polygon(lon: float, lat: float, poly: tuple[tuple[float, float], ...]) -> bool:
  inside = False
  j = len(poly) - 1
  for i, (xi, yi) in enumerate(poly):
    xj, yj = poly[j]
    if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
      inside = not inside
    j = i
  return inside


def _in_ellipse(lon: float, lat: float, e: tuple[float, float, float, float]) -> bool:
  cx, cy, rx, ry = e
  return ((lon - cx) / rx) ** 2 + ((lat - cy) / ry) ** 2 <= 1


def maybe_land(lat: float, lon: float) -> bool:
  return _in_polygon(lon, lat, _MAINLAND) or _in_ellipse(lon, lat, _JEJU) or _in_ellipse(lon, lat, _ULLEUNG)


def sample_point(dep: Departure, nights: int, rng: random.Random) -> tuple[float, float]:
  """출발지 기준 거리 범위 안에서 면적이 고르게 한 점을 뽑는다."""
  lo, hi = range_for(nights)
  hi = hi or 700.0
  while True:
    r = math.sqrt(rng.random() * (hi**2 - lo**2) + lo**2)
    theta = rng.random() * 2 * math.pi
    lat = dep.lat + (r * math.sin(theta)) / 111.32
    lon = dep.lon + (r * math.cos(theta)) / (111.32 * math.cos(math.radians(dep.lat)))
    if KOREA_BBOX[0] <= lat <= KOREA_BBOX[2] and KOREA_BBOX[1] <= lon <= KOREA_BBOX[3]:
      return round(lat, 5), round(lon, 5)


# ---------------------------------------------------------------------------
# Nominatim 역지오코딩
# ---------------------------------------------------------------------------
NOMINATIM_URL = os.getenv("TRAVEL_NOMINATIM_URL", "https://nominatim.openstreetmap.org/reverse")
USER_AGENT = os.getenv("TRAVEL_GEOCODER_UA", "travel-roulette-dart/1.0")
_MIN_INTERVAL = 1.1  # 초 — 공용 Nominatim 정책(초당 1회)보다 살짝 여유
_lock = asyncio.Lock()
_last_call = 0.0
_cache: dict[tuple[float, float], dict[str, Any] | None] = {}


class GeocodeError(RuntimeError):
  pass


def _fetch_sync(url: str) -> dict[str, Any]:
  req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "ko"})
  with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 (고정 URL)
    return json.loads(resp.read().decode("utf-8"))


async def reverse_geocode(lat: float, lon: float) -> dict[str, Any] | None:
  """좌표 → Nominatim 결과(jsonv2). 해당 위치에 아무것도 없으면 None."""
  global _last_call
  key = (round(lat, 3), round(lon, 3))
  if key in _cache:
    return _cache[key]
  query = urllib.parse.urlencode({
      "format": "jsonv2", "lat": lat, "lon": lon, "zoom": 14,
      "addressdetails": 1, "accept-language": "ko",
  })
  async with _lock:  # 서버 전체에서 초당 1회
    wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
    if wait > 0:
      await asyncio.sleep(wait)
    try:
      data = await asyncio.to_thread(_fetch_sync, f"{NOMINATIM_URL}?{query}")
    except Exception as e:
      raise GeocodeError(f"주소 변환 실패: {e}") from e
    finally:
      _last_call = time.monotonic()
  result = None if data.get("error") else data
  _cache[key] = result
  return result


# ---------------------------------------------------------------------------
# 결과 해석
# ---------------------------------------------------------------------------
_SIDO_SHORT = {
    "서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천",
    "광주광역시": "광주", "대전광역시": "대전", "울산광역시": "울산", "세종특별자치시": "세종",
    "경기도": "경기", "강원특별자치도": "강원", "강원도": "강원", "충청북도": "충북", "충청남도": "충남",
    "전북특별자치도": "전북", "전라북도": "전북", "전라남도": "전남", "경상북도": "경북",
    "경상남도": "경남", "제주특별자치도": "제주",
}
_SIDO_SUFFIXES = ("통합특별시", "특별자치도", "특별자치시", "특별시", "광역시", "도")


def short_sido(sido: str) -> str:
  """'경상북도' → '경북'. 표에 없는 새 행정구역('전남광주통합특별시' 등)은 접미사를 떼서 줄인다."""
  if sido in _SIDO_SHORT:
    return _SIDO_SHORT[sido]
  for suffix in _SIDO_SUFFIXES:
    if sido.endswith(suffix) and len(sido) > len(suffix):
      return sido[: -len(suffix)]
  return sido


# 읍·면·동 → 리 순으로 '사람이 사는 단위'를 찾는다. 하나도 없으면 바다나 사람 없는 산속('빈 땅').
_LOCAL_KEYS = ("town", "suburb", "city_district", "quarter", "village", "hamlet", "neighbourhood")


@dataclass
class Landing:
  lat: float
  lon: float
  sido: str
  sido_short: str
  sigungu: str
  dong: str
  village: str
  full_name: str

  @property
  def name(self) -> str:
    return " ".join(x for x in (self.sigungu, self.dong) if x) or self.sido_short


def parse_landing(lat: float, lon: float, data: dict[str, Any] | None) -> tuple[Landing | None, str]:
  """Nominatim 결과를 Landing으로. 실패하면 (None, 이유)."""
  if not data:
    return None, "빈 땅"
  addr = data.get("address") or {}
  if addr.get("country_code") != "kr":
    return None, "국외"
  sido = addr.get("province") or addr.get("state") or addr.get("city") or ""
  city = addr.get("city") if addr.get("province") else ""
  county = addr.get("county") or ""
  borough = addr.get("borough") or ""
  sigungu = " ".join(x for x in (city or county, borough) if x)
  if not sigungu and addr.get("city"):  # 서울·부산 등 광역시: city가 곧 시도
    sigungu = borough
  local = [addr[k] for k in _LOCAL_KEYS if addr.get(k)]
  if not local:
    return None, "빈 땅"
  dong = local[0]
  village = addr.get("village") if addr.get("village") and addr.get("village") != dong else ""
  short = short_sido(sido)
  full = " ".join(x for x in (sido, sigungu, dong, village) if x)
  return Landing(lat, lon, sido, short, sigungu, dong, village, full), ""


def trip_rule(landing: Landing, nights: int) -> str:
  """기간상 갈 수 없는 섬이면 이유를 돌려준다."""
  if landing.sido_short == "제주" and nights < 1:
    return "당일치기로는 제주에 갈 수 없어요"
  if "울릉" in landing.sigungu and nights < 2:
    return "울릉도는 2박 이상이 필요해요"
  return ""


# ---------------------------------------------------------------------------
# 다트 던지기
# ---------------------------------------------------------------------------
@dataclass
class DartThrow:
  landing: Landing
  departure: Departure
  nights: int
  distance_km: float
  misses: list[dict[str, Any]] = field(default_factory=list)

  def to_dict(self) -> dict[str, Any]:
    lo, hi = range_for(self.nights)
    lm = self.landing
    return {
        "destination": {
            "name": lm.name, "province": lm.sido_short, "full_name": lm.full_name,
            "sido": lm.sido, "sigungu": lm.sigungu, "dong": lm.dong, "village": lm.village,
            "lat": lm.lat, "lon": lm.lon,
        },
        "departure": self.departure.name,
        "departure_coords": [self.departure.lat, self.departure.lon],
        "nights": self.nights,
        "days": self.nights + 1,
        "duration_label": duration_label(self.nights),
        "distance_km": round(self.distance_km),
        "range_km": [lo, hi],
        "misses": self.misses,
    }


Geocoder = Callable[[float, float], Awaitable[dict[str, Any] | None]]


async def throw_dart(
    nights: int,
    departure: str = "서울",
    exclude: list[str] | None = None,
    rng: random.Random | None = None,
    geocoder: Geocoder | None = None,
    max_lookups: int = 12,
) -> DartThrow:
  if departure not in DEPARTURES:
    raise ValueError(f"지원하지 않는 출발지입니다: {departure}")
  nights = max(0, min(int(nights), MAX_NIGHTS))
  rng = rng or random.SystemRandom()
  geocode = geocoder or reverse_geocode
  dep = DEPARTURES[departure]
  excluded = set(exclude or [])
  misses: list[dict[str, Any]] = []

  for _ in range(max_lookups):
    lat, lon = sample_point(dep, nights, rng)
    tries = 0
    while not maybe_land(lat, lon) and tries < 400:  # 확실한 바다는 API 없이 다시
      lat, lon = sample_point(dep, nights, rng)
      tries += 1
    landing, reason = parse_landing(lat, lon, await geocode(lat, lon))
    if landing and not reason:
      reason = trip_rule(landing, nights) or ("이미 나온 곳" if landing.name in excluded else "")
    if landing and not reason:
      dist = haversine_km(dep.lat, dep.lon, lat, lon)
      return DartThrow(landing, dep, nights, dist, misses)
    misses.append({"lat": lat, "lon": lon, "reason": reason})
  raise GeocodeError("다트가 계속 바다나 빈 땅에 꽂혔어요. 다시 던져 주세요.")
