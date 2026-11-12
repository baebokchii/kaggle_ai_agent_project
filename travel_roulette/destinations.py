"""국내 여행지 데이터와 랜덤 추첨 로직.

LLM은 '무작위 선택'을 잘 못하기 때문에(늘 유명한 곳만 고름) 추첨은
순수 파이썬 코드로 처리한다. 여행 기간과 출발지에서의 거리를 기준으로
현실적으로 다녀올 수 있는 후보만 남긴 뒤 가중치 랜덤으로 뽑는다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
import random
from typing import Literal

Access = Literal["육로", "항공", "배"]


@dataclass(frozen=True)
class Destination:
  name: str
  province: str
  lat: float
  lon: float
  tags: tuple[str, ...]
  pitch: str  # 한 줄 소개
  access: Access = "육로"
  min_nights: int = 0

  def to_dict(self) -> dict:
    d = asdict(self)
    d["tags"] = list(self.tags)
    return d


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

DESTINATIONS: tuple[Destination, ...] = (
    # 강원
    Destination("강릉", "강원", 37.7519, 128.8761, ("바다", "카페", "먹거리"), "커피 거리와 동해 바다, 초당순두부의 도시"),
    Destination("속초", "강원", 38.2070, 128.5918, ("바다", "산", "시장"), "설악산과 바다, 중앙시장 먹거리를 한 번에"),
    Destination("춘천", "강원", 37.8813, 127.7298, ("호수", "먹거리", "자연"), "호반의 도시, 닭갈비와 막국수"),
    Destination("양양", "강원", 38.0754, 128.6190, ("바다", "서핑", "카페"), "서핑과 감성 해변의 성지"),
    Destination("평창", "강원", 37.3705, 128.3903, ("산", "자연", "목장"), "해발 700m 고원의 목장과 숲"),
    Destination("정선", "강원", 37.3807, 128.6608, ("시장", "자연", "레일바이크"), "5일장과 아리랑의 산골 마을"),
    Destination("삼척", "강원", 37.4499, 129.1652, ("바다", "동굴", "드라이브"), "해안 절벽과 거대한 석회 동굴"),
    # 경기·인천
    Destination("가평", "경기", 37.8315, 127.5105, ("자연", "수상레저", "캠핑"), "북한강 따라 숲과 물놀이"),
    Destination("수원", "경기", 37.2636, 127.0286, ("역사", "먹거리", "산책"), "수원화성 성곽길과 통닭 거리"),
    Destination("양평", "경기", 37.4917, 127.4876, ("자연", "카페", "자전거"), "두물머리 물안개와 강변 카페"),
    Destination("파주", "경기", 37.7600, 126.7800, ("책", "예술", "카페"), "출판도시와 헤이리 예술마을"),
    Destination("강화도", "인천", 37.7473, 126.4878, ("역사", "섬", "노을"), "고인돌부터 노을 맛집까지, 가까운 섬 여행"),
    Destination("인천", "인천", 37.4738, 126.6216, ("근대", "먹거리", "바다"), "개항장 골목과 차이나타운"),
    # 충청
    Destination("공주", "충남", 36.4465, 127.1190, ("역사", "백제", "산책"), "백제 왕도의 공산성과 금강"),
    Destination("부여", "충남", 36.2757, 126.9098, ("역사", "백제", "연꽃"), "궁남지 연꽃과 백제의 마지막 수도"),
    Destination("태안", "충남", 36.7456, 126.2980, ("바다", "해변", "노을"), "꽃지 해변 낙조와 해안 국립공원"),
    Destination("보령", "충남", 36.3333, 126.6127, ("바다", "머드", "섬"), "대천 해수욕장과 머드의 고장"),
    Destination("단양", "충북", 36.9845, 128.3655, ("자연", "액티비티", "강"), "도담삼봉과 패러글라이딩"),
    Destination("청주", "충북", 36.6424, 127.4890, ("역사", "먹거리", "산책"), "직지의 도시, 수암골 벽화마을"),
    Destination("제천", "충북", 37.1326, 128.1910, ("호수", "자연", "약초"), "청풍호 케이블카와 의림지"),
    Destination("대전", "대전", 36.3504, 127.3845, ("빵", "과학", "도시"), "빵지순례의 성지와 과학 도시"),
    # 전라
    Destination("전주", "전북", 35.8242, 127.1480, ("한옥", "먹거리", "전통"), "한옥마을과 비빔밥, 막걸리 골목"),
    Destination("군산", "전북", 35.9676, 126.7366, ("근대", "먹거리", "골목"), "시간이 멈춘 근대 골목과 빵집"),
    Destination("남원", "전북", 35.4164, 127.3904, ("전통", "자연", "산"), "광한루와 지리산 자락"),
    Destination("여수", "전남", 34.7604, 127.6622, ("바다", "야경", "먹거리"), "밤바다 낭만과 해상 케이블카"),
    Destination("순천", "전남", 34.9507, 127.4872, ("정원", "습지", "자연"), "순천만 갈대숲과 국가정원"),
    Destination("목포", "전남", 34.8118, 126.3922, ("바다", "근대", "먹거리"), "유달산 케이블카와 근대 골목"),
    Destination("담양", "전남", 35.3211, 126.9882, ("대나무", "자연", "먹거리"), "죽녹원 대숲과 떡갈비"),
    Destination("보성", "전남", 34.7715, 127.0800, ("녹차", "자연", "바다"), "초록 계단식 녹차밭"),
    Destination("광주", "광주", 35.1595, 126.8526, ("예술", "역사", "먹거리"), "아시아문화전당과 양림동 골목"),
    # 경상
    Destination("경주", "경북", 35.8562, 129.2247, ("역사", "야경", "산책"), "지붕 없는 박물관, 황리단길"),
    Destination("안동", "경북", 36.5684, 128.7294, ("한옥", "전통", "먹거리"), "하회마을과 안동찜닭"),
    Destination("포항", "경북", 36.0190, 129.3435, ("바다", "일출", "먹거리"), "호미곶 일출과 죽도시장 물회"),
    Destination("영덕", "경북", 36.4150, 129.3653, ("바다", "대게", "드라이브"), "대게와 블루로드 해안길"),
    Destination("문경", "경북", 36.5867, 128.1867, ("산", "역사", "레포츠"), "문경새재 옛길과 단산 모노레일"),
    Destination("대구", "대구", 35.8714, 128.6014, ("도시", "먹거리", "근대"), "근대골목과 막창, 서문시장"),
    Destination("통영", "경남", 34.8544, 128.4331, ("바다", "섬", "예술"), "동양의 나폴리, 동피랑 벽화마을"),
    Destination("거제", "경남", 34.8806, 128.6211, ("바다", "섬", "드라이브"), "바람의 언덕과 외도 보타니아"),
    Destination("남해", "경남", 34.8377, 127.8924, ("바다", "마을", "드라이브"), "다랭이마을과 독일마을"),
    Destination("진주", "경남", 35.1800, 128.1076, ("역사", "야경", "먹거리"), "진주성과 남강 유등"),
    Destination("하동", "경남", 35.0674, 127.7514, ("녹차", "강", "자연"), "섬진강과 화개장터"),
    Destination("울산", "울산", 35.5384, 129.3114, ("자연", "바다", "정원"), "태화강 국가정원과 간절곶"),
    Destination("부산", "부산", 35.1796, 129.0756, ("바다", "야경", "먹거리"), "해운대부터 영도까지, 바다 도시"),
    Destination("서울", "서울", 37.5665, 126.9780, ("도시", "쇼핑", "궁궐"), "궁궐과 골목, 트렌드의 수도"),
    # 섬
    Destination("제주", "제주", 33.4996, 126.5312, ("섬", "자연", "바다"), "오름과 바다, 말 그대로 섬 여행", access="항공", min_nights=1),
    Destination("울릉도", "경북", 37.4844, 130.9057, ("섬", "자연", "트레킹"), "배 타고 가는 신비의 섬", access="배", min_nights=2),
)

DESTINATIONS_BY_NAME = {d.name: d for d in DESTINATIONS}

DURATION_LABELS = {
    0: "당일치기",
    1: "1박 2일",
    2: "2박 3일",
    3: "3박 4일",
    4: "4박 5일",
    5: "5박 6일",
    6: "6박 7일",
}
MAX_NIGHTS = max(DURATION_LABELS)


def duration_label(nights: int) -> str:
  return DURATION_LABELS.get(nights, f"{nights}박 {nights + 1}일")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
  r = 6371.0
  p1, p2 = math.radians(lat1), math.radians(lat2)
  dp = p2 - p1
  dl = math.radians(lon2 - lon1)
  a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
  return 2 * r * math.asin(math.sqrt(a))


# 기간별 '육로 여행지'의 직선거리 범위(km). (최소, 최대)
_MAINLAND_RANGE: dict[int, tuple[float, float]] = {
    0: (25, 170),  # 당일치기: 편도 2시간 안팎
    1: (50, 360),  # 1박 2일: 너무 가깝지 않고, KTX/자가용으로 반나절 이내
}
_LONG_TRIP_MIN_KM = 80  # 2박 이상이면 어느 정도는 멀리 떠나기


@dataclass
class DrawResult:
  destination: Destination
  departure: Departure
  nights: int
  distance_km: float
  candidates: int
  relaxed: bool = False
  excluded: list[str] = field(default_factory=list)

  def to_dict(self) -> dict:
    return {
        "destination": self.destination.to_dict(),
        "departure": self.departure.name,
        "nights": self.nights,
        "days": self.nights + 1,
        "duration_label": duration_label(self.nights),
        "distance_km": round(self.distance_km),
        "candidates": self.candidates,
        "relaxed": self.relaxed,
    }


def _is_feasible(dest: Destination, dep: Departure, nights: int, dist: float) -> bool:
  if dest.name == dep.name or dist < 25:
    return False
  if nights < dest.min_nights:
    return False
  if dest.access != "육로":
    return True  # 섬은 min_nights만 충족하면 OK
  if nights in _MAINLAND_RANGE:
    lo, hi = _MAINLAND_RANGE[nights]
    return lo <= dist <= hi
  return dist >= _LONG_TRIP_MIN_KM


def _weight(dest: Destination, nights: int, dist: float) -> float:
  if nights <= 1:
    return 1.0
  # 긴 여행일수록 먼 곳·섬에 가중치
  w = min(max(dist / 150, 0.5), 2.0)
  if dest.access != "육로":
    w *= 1.5
  return w


def candidates_for(
    nights: int, departure: str, exclude: list[str] | None = None
) -> list[tuple[Destination, float]]:
  dep = DEPARTURES[departure]
  excluded = set(exclude or [])
  out = []
  for dest in DESTINATIONS:
    if dest.name in excluded:
      continue
    dist = haversine_km(dep.lat, dep.lon, dest.lat, dest.lon)
    if _is_feasible(dest, dep, nights, dist):
      out.append((dest, dist))
  return out


def draw_destination(
    nights: int,
    departure: str = "서울",
    exclude: list[str] | None = None,
    rng: random.Random | None = None,
) -> DrawResult:
  """기간·출발지 조건에 맞는 국내 여행지를 무작위로 하나 뽑는다."""
  if departure not in DEPARTURES:
    raise ValueError(f"지원하지 않는 출발지입니다: {departure}")
  nights = max(0, min(int(nights), MAX_NIGHTS))
  rng = rng or random.SystemRandom()
  exclude = list(exclude or [])

  pool = candidates_for(nights, departure, exclude)
  relaxed = False
  if not pool:
    # 제외 목록 때문에 후보가 바닥나면 제외 목록을 비운다.
    pool = candidates_for(nights, departure)
    relaxed = True

  weights = [_weight(dest, nights, dist) for dest, dist in pool]
  dest, dist = rng.choices(pool, weights=weights, k=1)[0]
  return DrawResult(
      destination=dest,
      departure=DEPARTURES[departure],
      nights=nights,
      distance_km=dist,
      candidates=len(pool),
      relaxed=relaxed,
      excluded=exclude,
  )
