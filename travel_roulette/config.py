"""환경 변수 기반 설정값."""

from __future__ import annotations

import os

from google.genai import types

APP_NAME = "travel_roulette"

# 메인 추론용 모델 (일정 작성·검토·예산 추정)
MODEL = os.getenv("TRAVEL_MODEL", "gemini-3.5-flash")
# 검색 리서치용 모델 (병렬로 3번 호출되므로 가벼운 모델 권장)
RESEARCH_MODEL = os.getenv("TRAVEL_RESEARCH_MODEL", MODEL)

# 사고(thinking) 수준: 검색 요약·검수·단가 추정은 깊은 추론이 필요 없어 'low'로 토큰 절약.
# 일정 작성·수정은 품질이 중요해 모델 기본값(빈 값)을 쓴다.
LIGHT_THINKING = os.getenv("TRAVEL_LIGHT_THINKING", "low") or None
PLAN_THINKING = os.getenv("TRAVEL_PLAN_THINKING") or None


def gen_config(temperature: float, thinking_level: str | None) -> types.GenerateContentConfig:
  return types.GenerateContentConfig(
      temperature=temperature,
      thinking_config=types.ThinkingConfig(thinking_level=thinking_level) if thinking_level else None,
  )


# 일정 검토 → 수정 루프의 최대 수정 횟수
MAX_REVISIONS = int(os.getenv("TRAVEL_MAX_REVISIONS", "1"))

# ADK LoggingPlugin으로 모든 콜백을 콘솔에 출력 (디버깅용)
VERBOSE_LOGGING = os.getenv("TRAVEL_VERBOSE_LOGGING", "").lower() in ("1", "true", "yes")

# 날씨 API 타임아웃(초)
WEATHER_TIMEOUT = float(os.getenv("TRAVEL_WEATHER_TIMEOUT", "5"))

RETRY_OPTIONS = types.HttpRetryOptions(
    attempts=5,
    exp_base=2,
    initial_delay=1,
    http_status_codes=[429, 500, 503, 504],
)
