from __future__ import annotations

import pytest

from google.adk.agents import LlmAgent

from travel_roulette import agent as travel_agent
from travel_roulette import dart

from fake_llm import FakeLlm


def graph_llm_agents() -> list[LlmAgent]:
  # Workflow는 그래프를 만들 때 노드를 복사하므로, 그래프 안의 인스턴스를 패치해야 한다.
  return [n for n in travel_agent.root_agent.graph.nodes if isinstance(n, LlmAgent)]


@pytest.fixture
def fake_llm(monkeypatch):
  """그래프 안의 모든 LlmAgent 모델을 가짜 LLM으로 바꾸고 날씨 API를 막는다."""
  llm = FakeLlm(model="gemini-fake", calls=[])
  agents = graph_llm_agents()
  assert len(agents) == 7
  for a in agents:
    monkeypatch.setattr(a, "model", llm)

  async def no_weather(*args, **kwargs):
    return None

  monkeypatch.setattr(travel_agent, "fetch_forecast", no_weather)
  monkeypatch.setattr(dart, "reverse_geocode", fake_geocoder)
  return llm


async def fake_geocoder(lat: float, lon: float):
  """네트워크 없이 Nominatim 응답을 흉내 낸다: 육지 윤곽 안이면 가짜 읍, 밖이면 바다."""
  if not dart.maybe_land(lat, lon):
    return None
  return {"address": {"country_code": "kr", "province": "경상북도", "county": "테스트군",
                      "town": f"다트{lat:.3f}읍"}}


@pytest.fixture
def fake_geo(monkeypatch):
  monkeypatch.setattr(dart, "reverse_geocode", fake_geocoder)
  return fake_geocoder
