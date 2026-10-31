"""Tests for AgentPipeline (the model client is faked)."""

import json
from pathlib import Path

import pytest

from adobe_reviewer.clients.openrouter import ChatResult
from adobe_reviewer.config.manager import ConfigManager
from adobe_reviewer.engine.pipeline import AgentPipeline
from adobe_reviewer.exceptions import ConfigError
from adobe_reviewer.models.schemas import ReviewContext
from adobe_reviewer.prompts.manager import PromptManager

REPLY = json.dumps(
    {"approved": True, "overall_score": 93, "commercial_value": 90, "technical_quality": 95, "confidence": 0.9}
)


class FakeClient:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, **kwargs):
        self.calls += 1
        return ChatResult(content=REPLY, model="actual/model", prompt_tokens=100, completion_tokens=50, cost_usd=0.01)


def context() -> ReviewContext:
    return ReviewContext(image_path=Path("a.jpg"), image_data_url="data:image/jpeg;base64,AAAA")


def test_single_mode_runs_the_one_agent(project):
    config = ConfigManager(project / "config" / "settings.json")
    client = FakeClient()
    result = AgentPipeline(config, client, PromptManager(config)).run(context())
    assert client.calls == 1 and result.verdict.overall_score == 93
    assert result.reviewed_by == ["adobe_reviewer"] and result.models_used == ["actual/model"]
    assert result.total_cost_usd == pytest.approx(0.01)


def test_unavailable_mode_gives_a_clear_error(project):
    path = project / "config" / "settings.json"
    data = json.loads(path.read_text())
    data["pipeline"]["mode"] = "sequential"
    path.write_text(json.dumps(data))
    config = ConfigManager(path)
    with pytest.raises(ConfigError, match="not available"):
        AgentPipeline(config, FakeClient(), PromptManager(config)).run(context())


def test_unknown_agent_type_is_reported(project):
    path = project / "config" / "settings.json"
    data = json.loads(path.read_text())
    data["pipeline"]["agents"][0]["type"] = "nonexistent"
    path.write_text(json.dumps(data))
    config = ConfigManager(path)
    with pytest.raises(ConfigError, match="Unknown agent type"):
        AgentPipeline(config, FakeClient(), PromptManager(config)).run(context())
