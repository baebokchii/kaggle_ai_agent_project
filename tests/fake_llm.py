"""API 키 없이 워크플로 전체를 돌려보기 위한 가짜 LLM.

지시문(system instruction)의 내용으로 어떤 에이전트가 호출했는지 판별해
미리 준비한 응답을 돌려준다.
"""

from __future__ import annotations

import json
from typing import AsyncGenerator

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types


def _itinerary(days: int) -> dict:
  return {
      "title": "테스트 여행",
      "summary": "테스트용 일정입니다.",
      "getting_there": "KTX로 2시간",
      "local_transport": "시내버스",
      "stay_area": "시내 중심가" if days > 1 else "",
      "days": [
          {
              "day": d,
              "theme": f"{d}일차 테마",
              "activities": [
                  {"time": "10:00", "title": "명소 방문", "place": "테스트 명소",
                   "category": "관광", "description": "둘러보기", "tip": ""},
                  {"time": "12:30", "title": "점심", "place": "테스트 식당",
                   "category": "식사", "description": "향토 음식", "tip": "웨이팅 주의"},
              ],
          }
          for d in range(1, days + 1)
      ],
      "rainy_day_backup": "박물관",
      "tips": ["편한 신발"],
  }


BUDGET = {
    "items": [
        {"category": "교통", "description": "왕복 KTX", "unit_cost_krw": 30000, "quantity": 2, "per_person": True},
        {"category": "숙박", "description": "호텔", "unit_cost_krw": 100000, "quantity": 1, "per_person": False},
        {"category": "식비", "description": "식사", "unit_cost_krw": 12000, "quantity": 4, "per_person": True},
    ],
    "assumptions": ["2인 1실"],
}


class FakeLlm(BaseLlm):
  """critic은 첫 검수에서 반려하고, 두 번째에 승인한다."""

  days: int = 2
  calls: list[str] = []
  critic_rejections: int = 1

  @classmethod
  def supported_models(cls) -> list[str]:
    return [r"gemini-fake.*"]

  def _role(self, req: LlmRequest) -> str:
    si = req.config.system_instruction if req.config else ""
    text = si if isinstance(si, str) else json.dumps(str(si), ensure_ascii=False)
    for marker, role in [
        ("가볼 만한 장소", "spots"), ("대표 음식과 맛집", "food"), ("교통·숙박 리서처", "logistics"),
        ("깐깐한 여행 일정 검수자", "critic"), ("검수자의 피드백을 반영", "reviser"),
        ("여행 일정 플래너입니다. 아래 리서치", "planner"), ("여행 예산 플래너", "budget"),
    ]:
      if marker in text:
        return role
    return "unknown"

  async def generate_content_async(
      self, llm_request: LlmRequest, stream: bool = False
  ) -> AsyncGenerator[LlmResponse, None]:
    role = self._role(llm_request)
    self.calls.append(role)
    grounding = None
    if role in ("spots", "food", "logistics"):
      text = f"- {role} 리서치 결과"
      grounding = types.GroundingMetadata(grounding_chunks=[
          types.GroundingChunk(web=types.GroundingChunkWeb(
              uri=f"https://example.com/{role}", title=f"{role} 출처"))
      ])
    elif role in ("planner", "reviser"):
      text = json.dumps(_itinerary(self.days), ensure_ascii=False)
    elif role == "critic":
      rejected = self.calls.count("critic") <= self.critic_rejections
      text = json.dumps({
          "approved": not rejected,
          "issues": ["동선이 비효율적"] if rejected else [],
          "feedback": "동선을 정리하세요" if rejected else "",
      }, ensure_ascii=False)
    elif role == "budget":
      text = json.dumps(BUDGET, ensure_ascii=False)
    else:
      text = "?"
    yield LlmResponse(
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        grounding_metadata=grounding,
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=100, candidates_token_count=20, thoughts_token_count=5),
    )
