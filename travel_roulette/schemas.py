"""LLM 구조화 출력(output_schema) 스키마.

웹 UI가 그대로 렌더링할 수 있도록 모든 LLM 결과를 JSON으로 받는다.
Gemini 스키마 호환을 위해 기본값·Optional 필드는 쓰지 않는다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ActivityCategory = Literal["관광", "식사", "카페", "체험", "쇼핑", "휴식", "이동", "숙소"]
BudgetCategory = Literal["교통", "숙박", "식비", "입장료·체험", "카페·간식", "기타"]


class Activity(BaseModel):
  time: str = Field(description="시작 시각 (HH:MM, 24시간제)")
  title: str = Field(description="활동 제목 (짧게)")
  place: str = Field(description="실제 장소명")
  area: str = Field(description="장소가 속한 동네·권역 이름 (예: 시내, 경포, 주문진). 같은 권역은 항상 같은 이름으로")
  category: ActivityCategory
  description: str = Field(description="무엇을 하는지 한 문장 (40자 이내)")
  tip: str = Field(description="꿀팁 (25자 이내). 꼭 필요할 때만, 없으면 빈 문자열")


class DayPlan(BaseModel):
  day: int = Field(description="1부터 시작하는 일차")
  theme: str = Field(description="그날의 테마 한 줄")
  activities: list[Activity]


class Itinerary(BaseModel):
  title: str = Field(description="여행 일정 제목 (재치 있게)")
  summary: str = Field(description="여행 전체 요약 2문장")
  getting_there: str = Field(description="출발지에서 가는 방법과 소요 시간")
  local_transport: str = Field(description="현지 이동 수단 추천")
  stay_area: str = Field(description="추천 숙소 지역과 이유. 당일치기면 빈 문자열")
  days: list[DayPlan]
  rainy_day_backup: str = Field(description="비 올 때 대체 코스")
  tips: list[str] = Field(description="여행 꿀팁 3개 (각 40자 이내)")


class Review(BaseModel):
  approved: bool = Field(description="수정 없이 사용해도 되면 true")
  issues: list[str] = Field(description="발견한 문제 목록. 없으면 빈 배열")
  feedback: str = Field(description="수정 지시사항. 승인이면 빈 문자열")


class BudgetItem(BaseModel):
  category: BudgetCategory
  description: str = Field(description="항목 설명 (예: 서울→강릉 KTX 왕복)")
  unit_cost_krw: int = Field(description="단가 (원)")
  quantity: int = Field(description="수량 (횟수, 박 수, 끼니 수 등)")
  per_person: bool = Field(description="true면 인원수만큼 곱한다. 숙박(객실)·렌터카·주유는 false")


class BudgetEstimate(BaseModel):
  items: list[BudgetItem]
  assumptions: list[str] = Field(description="추정에 사용한 가정")
