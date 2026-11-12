from __future__ import annotations

import pytest

from google.adk.agents import LlmAgent

from travel_roulette import agent as travel_agent

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
  return llm
