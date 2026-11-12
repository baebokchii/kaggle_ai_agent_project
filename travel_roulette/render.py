"""최종 결과를 채팅용 마크다운으로 렌더링한다 (adk web / CLI 출력용)."""

from __future__ import annotations

from typing import Any

from .budget import format_krw

_ICONS = {"관광": "📍", "식사": "🍽️", "카페": "☕", "체험": "🎟️", "쇼핑": "🛍️",
          "휴식": "🛌", "이동": "🚆", "숙소": "🏨"}


def render_markdown(plan: dict[str, Any]) -> str:
  ch = plan["challenge"]
  dest = ch["destination"]
  it = plan["itinerary"]
  budget = plan["budget"]
  out: list[str] = []

  out.append(f"# 🎲 랜덤 여행 챌린지: **{dest['name']}** ({dest['province']})")
  out.append(
      f"> {ch['departure']} 출발 · {ch['duration_label']} · {ch['travelers']}명 · "
      f"예산 스타일 '{ch['budget_level']}' · 직선거리 약 {ch['distance_km']}km"
  )
  if plan.get("search_degraded"):
    out.append("\n> ⚠️ 지금은 실시간 검색을 쓸 수 없어 AI가 알고 있는 정보로 작성했어요. "
               "가게·가격·영업시간은 꼭 다시 확인하세요.")
  out.append(f"\n## {it['title']}\n\n{it['summary']}")
  out.append(f"\n**가는 법** — {it['getting_there']}")
  out.append(f"\n**현지 이동** — {it['local_transport']}")
  if it.get("stay_area"):
    out.append(f"\n**숙소 추천 지역** — {it['stay_area']}")
  out.append(f"\n**날씨** — {plan['weather_text']}")

  for day in it["days"]:
    out.append(f"\n### Day {day['day']} · {day['theme']}")
    for a in day["activities"]:
      icon = _ICONS.get(a["category"], "•")
      line = f"- `{a['time']}` {icon} **{a['title']}** ({a['place']}) — {a['description']}"
      if a.get("tip"):
        line += f"  \n  💡 {a['tip']}"
      out.append(line)

  out.append(f"\n☔ **비 오는 날 플랜B** — {it['rainy_day_backup']}")

  out.append("\n## 💰 예상 예산")
  out.append("| 항목 | 금액 |\n|---|---:|")
  for c in budget["by_category"]:
    out.append(f"| {c['category']} | {format_krw(c['amount_krw'])} |")
  out.append(f"| 예비비 (10%) | {format_krw(budget['contingency_krw'])} |")
  out.append(f"| **합계** | **{format_krw(budget['total_krw'])}** |")
  out.append(f"\n1인당 약 **{format_krw(budget['per_person_krw'])}**")
  if budget["assumptions"]:
    out.append("\n<details><summary>예산 가정</summary>\n")
    out.extend(f"- {a}" for a in budget["assumptions"])
    out.append("\n</details>")

  if it["tips"]:
    out.append("\n## ✅ 꿀팁")
    out.extend(f"- {t}" for t in it["tips"])

  if plan.get("sources"):
    out.append("\n## 🔎 참고한 출처")
    out.extend(f"- [{s['title']}]({s['uri']})" for s in plan["sources"][:8])

  out.append("\n---\n다른 곳으로 다시 돌리려면 **\"다시\"** 라고 입력하세요.")
  return "\n".join(out)
