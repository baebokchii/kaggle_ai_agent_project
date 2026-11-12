"""에이전트 지시문(Instruction Provider).

문자열 템플릿 대신 함수로 만들어 세션 state를 직접 읽어 프롬프트를 조립한다.
JSON 중괄호가 템플릿 변수로 오인되는 문제도 없고, 누락된 키도 안전하게 처리된다.
"""

from __future__ import annotations

import json
from typing import Any

from google.adk.agents.readonly_context import ReadonlyContext

from .budget import DAILY_GUIDE, ROOM_GUIDE
from .routes import route_summary


def _get(ctx: ReadonlyContext, key: str, default: Any = "") -> Any:
  value = ctx.state.get(key)
  return default if value is None else value


def _json(value: Any) -> str:
  return json.dumps(value, ensure_ascii=False, indent=1)


def challenge_brief(ch: dict[str, Any]) -> str:
  dest = ch["destination"]
  lines = [
      f"- 다트가 꽂힌 곳: {dest['full_name']} (위도 {dest['lat']}, 경도 {dest['lon']})",
      f"- 여행 중심: {dest['name']} ({dest['province']})",
      f"- 출발지: {ch['departure']} (직선거리 약 {ch['distance_km']}km)",
      f"- 기간: {ch['duration_label']} (총 {ch['days']}일)",
      f"- 인원: {ch['travelers']}명",
      f"- 예산 스타일: {ch['budget_level']}",
  ]
  if ch.get("start_date"):
    lines.append(f"- 출발일: {ch['start_date']}")
  return "\n".join(lines)


_COMMON = (
    "모든 답변은 한국어로 작성하세요. 사용자는 지도에 다트를 던져 무작위로 여행지를 정했습니다. "
    "다트가 꽂힌 읍·면·동을 여행의 중심으로 삼되, 그곳만으로 볼거리가 부족하면 같은 시·군과 "
    "차로 1시간 이내의 인접 지역까지 넓혀도 됩니다. 다른 도시로 여행지를 바꾸지는 마세요.\n\n"
)


def _research_header(ctx: ReadonlyContext) -> str:
  return _COMMON + "[챌린지 정보]\n" + challenge_brief(_get(ctx, "challenge", {})) + "\n\n"


def spots_instruction(ctx: ReadonlyContext) -> str:
  return _research_header(ctx) + (
      "당신은 국내 여행지 리서처입니다. google_search 도구로 이 여행지의 "
      "**가볼 만한 장소와 동네**를 조사하세요.\n"
      "- 대표 명소뿐 아니라 최근 인기 있는 숨은 명소도 포함 (총 8~12곳)\n"
      "- 각 장소: 이름 / 위치(동네) / 추천 이유 한 줄 / 소요 시간 / 입장료(알면) / 휴무일(알면)\n"
      "- 서로 가까운 장소끼리 동네별로 묶어서 정리\n"
      "- 실내 명소(비 올 때 대안)도 2곳 이상\n"
      "검색으로 확인되지 않은 장소는 지어내지 마세요. 장소마다 한 줄, 전체 25줄 이내의 간결한 불릿 목록으로 답하세요."
  )


def food_instruction(ctx: ReadonlyContext) -> str:
  return _research_header(ctx) + (
      "당신은 국내 맛집 리서처입니다. google_search 도구로 이 여행지의 "
      "**대표 음식과 맛집·카페**를 조사하세요.\n"
      "- 꼭 먹어야 할 향토 음식 3~5가지\n"
      "- 맛집 6~10곳: 이름 / 동네 / 대표 메뉴 / 1인 가격대 / 웨이팅·휴무 정보(알면)\n"
      "- 분위기 좋은 카페·디저트 3~5곳\n"
      "- 예산 스타일에 맞는 곳 위주로\n"
      "검색으로 확인되지 않은 가게는 지어내지 마세요. 가게마다 한 줄, 전체 25줄 이내의 간결한 불릿 목록으로 답하세요."
  )


def logistics_instruction(ctx: ReadonlyContext) -> str:
  return _research_header(ctx) + (
      "당신은 교통·숙박 리서처입니다. google_search 도구로 다음을 조사하세요.\n"
      "- 출발지에서 여행지까지 이동 방법별(KTX/SRT, 고속버스, 자가용, 항공, 배 등) "
      "소요 시간과 1인 편도 요금\n"
      "- 현지 이동 수단(시내버스, 택시, 렌터카, 도보, 자전거 등)과 대략 비용\n"
      "- 숙박하기 좋은 지역 2~3곳과 예산 스타일별 1박 객실 가격대\n"
      "- 계절 이슈나 축제, 주의사항(배편 결항, 휴무 시즌 등)\n"
      "전체 20줄 이내의 간결한 불릿 목록으로 답하세요."
  )


def planner_instruction(ctx: ReadonlyContext) -> str:
  ch = _get(ctx, "challenge", {})
  days = ch.get("days", 1)
  day_rule = (
      "당일치기이므로 Day 1 하나만 작성하고, 아침 출발 → 저녁 귀가 흐름으로 짜세요."
      if days == 1
      else f"정확히 Day 1 ~ Day {days}까지 {days}일치를 작성하세요. "
      "Day 1은 출발지에서 이동해 도착하는 시간을, 마지막 날은 귀가 이동 시간을 반영하세요."
  )
  return _COMMON + (
      "당신은 국내 여행 일정 플래너입니다. 아래 리서치를 바탕으로 현실적인 일정을 만드세요.\n\n"
      f"[챌린지 정보]\n{challenge_brief(ch)}\n\n"
      f"[날씨]\n{_get(ctx, 'weather_text', '정보 없음')}\n\n"
      f"[명소 리서치]\n{_get(ctx, 'spots_notes', '없음')}\n\n"
      f"[맛집 리서치]\n{_get(ctx, 'food_notes', '없음')}\n\n"
      f"[교통·숙박 리서치]\n{_get(ctx, 'logistics_notes', '없음')}\n\n"
      "[규칙]\n"
      f"1. {day_rule}\n"
      "2. 하루 주요 방문지는 3~5곳. 하루는 1~2개 권역 안에서 움직이고, 권역 사이를 오가는 "
      "왕복(A→B→A)은 만들지 마세요. 각 활동의 area에 권역 이름을 일관되게 적으세요.\n"
      "3. 점심·저녁 식사는 매일 포함하고, 리서치에 나온 실제 가게를 우선 사용하세요.\n"
      "4. 시간(time)은 HH:MM 형식으로 오름차순. 이동 시간도 고려하세요.\n"
      "5. 비 예보가 있는 날은 실내 일정을 늘리세요.\n"
      "6. 리서치에 없는 장소를 지어내지 마세요.\n"
      "7. 출력 전에 스스로 점검하세요: 일차 수, 첫날 도착·마지막 날 귀가 시간, 매일 점심·저녁, 권역 왕복 여부."
  )


def critic_instruction(ctx: ReadonlyContext) -> str:
  ch = _get(ctx, "challenge", {})
  return _COMMON + (
      "당신은 깐깐한 여행 일정 검수자입니다. 사용자 메시지로 받은 일정(JSON)이 "
      "실제로 따라 할 수 있는지 검토하세요.\n\n"
      f"[챌린지 정보]\n{challenge_brief(ch)}\n\n"
      f"[날씨]\n{_get(ctx, 'weather_text', '정보 없음')}\n\n"
      f"[코드가 정리한 일자별 권역 순서]\n{route_summary(_get(ctx, 'itinerary', {}))}\n\n"
      "[체크리스트]\n"
      f"- 일차 수가 정확히 {ch.get('days', 1)}일인가?\n"
      "- 첫날 도착·마지막 날 귀가 이동 시간이 현실적인가?\n"
      "- 하루 일정이 너무 빡빡하거나(이동만 하다 끝남) 너무 비어 있지 않은가?\n"
      "- 매일 점심·저녁 식사가 있는가?\n"
      "- 동선: 위 권역 순서에 왕복(A→B→A)이 있거나, 권역 이름이 실제 위치와 맞지 않으면 지적하세요. 시간 순서도 맞는가?\n"
      "- 날씨·계절을 고려했는가?\n\n"
      "사소한 표현 문제는 무시하고, 여행을 망칠 만한 문제만 지적하세요. "
      "문제가 없으면 approved=true, issues=[], feedback=\"\"로 답하세요."
  )


def reviser_instruction(ctx: ReadonlyContext) -> str:
  review = _get(ctx, "review", {})
  return _COMMON + (
      "당신은 여행 일정 플래너입니다. 검수자의 피드백을 반영해 일정을 수정하세요. "
      "지적되지 않은 부분은 최대한 유지하고, 전체 일정을 다시 출력하세요.\n\n"
      f"[챌린지 정보]\n{challenge_brief(_get(ctx, 'challenge', {}))}\n\n"
      f"[기존 일정]\n{_json(_get(ctx, 'itinerary', {}))}\n\n"
      f"[발견된 문제]\n{_json(review.get('issues', []))}\n\n"
      f"[수정 지시]\n{review.get('feedback', '')}\n\n"
      f"[현재 일자별 권역 순서]\n{route_summary(_get(ctx, 'itinerary', {}))}\n\n"
      f"[참고: 명소 리서치]\n{_get(ctx, 'spots_notes', '없음')}\n\n"
      f"[참고: 맛집 리서치]\n{_get(ctx, 'food_notes', '없음')}"
  )


def budget_instruction(ctx: ReadonlyContext) -> str:
  ch = _get(ctx, "challenge", {})
  level = ch.get("budget_level", "보통")
  d_lo, d_hi = DAILY_GUIDE.get(level, DAILY_GUIDE["보통"])
  r_lo, r_hi = ROOM_GUIDE.get(level, ROOM_GUIDE["보통"])
  return _COMMON + (
      "당신은 여행 예산 플래너입니다. 확정된 일정의 비용 항목을 **단가와 수량**으로 나열하세요. "
      "합계는 계산하지 마세요 — 합산은 시스템이 코드로 정확히 처리합니다.\n\n"
      f"[챌린지 정보]\n{challenge_brief(ch)}\n\n"
      f"[확정 일정]\n{_json(_get(ctx, 'itinerary', {}))}\n\n"
      f"[교통·숙박 리서치]\n{_get(ctx, 'logistics_notes', '없음')}\n\n"
      "[작성 규칙]\n"
      "- 단가는 원(KRW) 정수. 리서치에 나온 실제 요금을 우선 사용\n"
      "- 왕복 교통(출발지↔여행지), 현지 교통, 숙박, 매 끼니 식비, 입장료·체험, 카페·간식을 빠짐없이\n"
      "- per_person: 1인당 드는 비용(기차표, 식사, 입장료)은 true, "
      "인원과 무관한 비용(객실, 렌터카, 주유, 통행료, 택시)은 false\n"
      f"- 예산 스타일 '{level}' 가이드: 숙박 제외 1인 1일 {d_lo:,}~{d_hi:,}원, "
      f"객실 1박 {r_lo:,}~{r_hi:,}원\n"
      f"- 인원 {ch.get('travelers', 2)}명 기준으로 객실 수를 정하세요 (예: 2명=1실)\n"
      "- assumptions에 주요 가정을 3~6개 적으세요"
  )
