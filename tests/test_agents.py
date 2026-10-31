"""Tests for BaseAgent behaviour, AdobeReviewerAgent and the agent registry (model calls are faked)."""

import json
from pathlib import Path

import pytest

from adobe_reviewer.agents import AdobeReviewerAgent, available_agent_types, create_agent
from adobe_reviewer.clients.openrouter import ChatResult
from adobe_reviewer.config.manager import ConfigManager
from adobe_reviewer.config.models import AgentSettings
from adobe_reviewer.exceptions import ConfigError, OpenRouterError, ReviewParseError
from adobe_reviewer.models.schemas import LocalMetrics, ReviewContext
from adobe_reviewer.prompts.manager import PromptManager

VALID = {
    "approved": False,
    "overall_score": 73,
    "commercial_value": 84,
    "technical_quality": 65,
    "issues": [{"category": "ai_artifacts", "severity": "major", "title": "Extra finger", "hard_fail": False}],
    "recommended_fixes": ["Inpaint the hand"],
    "confidence": 0.9,
}


def chat(content, *, prompt=1000, completion=200, cost=0.01, finish="stop", model="actual/model") -> ChatResult:
    return ChatResult(
        content=content if isinstance(content, str) else json.dumps(content),
        model=model,
        finish_reason=finish,
        prompt_tokens=prompt,
        completion_tokens=completion,
        cost_usd=cost,
    )


class FakeClient:
    """Stands in for OpenRouterClient: returns scripted results and records every call."""

    def __init__(self, *results) -> None:
        self.results = list(results)
        self.calls: list[dict] = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture()
def config(project: Path) -> ConfigManager:
    return ConfigManager(project / "config" / "settings.json")


def make_agent(config: ConfigManager, client: FakeClient) -> AdobeReviewerAgent:
    return AdobeReviewerAgent("adobe_reviewer", config=config, client=client, prompts=PromptManager(config))


def context(**overrides) -> ReviewContext:
    base = dict(
        image_path=Path("forest.jpg"),
        image_data_url="data:image/jpeg;base64,AAAA",
        metrics=LocalMetrics(
            width=4000, height=3000, megapixels=12.0, aspect_ratio=1.333, file_size_bytes=3_000_000, image_format="JPEG"
        ),
        metrics_block="- Original resolution: 4000 x 3000 px (12.00 megapixels)",
    )
    base.update(overrides)
    return ReviewContext(**base)


def test_successful_review(config):
    client = FakeClient(chat(VALID))
    result = make_agent(config, client).run(context())
    assert result.verdict.overall_score == 73 and result.verdict.issues[0].title == "Extra finger"
    assert result.agent_name == "adobe_reviewer" and result.model == "actual/model"
    assert (result.prompt_tokens, result.completion_tokens, result.cost_usd) == (1000, 200, 0.01)


def test_request_contains_image_filename_measurements_and_configured_model(config):
    client = FakeClient(chat(VALID))
    make_agent(config, client).run(context())
    call = client.calls[0]
    configured = config.settings.pipeline.agents[0]
    assert call["model"] == configured.model and call["response_format"] == configured.response_format
    system, user = call["messages"]
    assert "Adobe Stock" in system["content"]
    text_part, image_part = user["content"]
    assert "forest.jpg" in text_part["text"] and "12.00 megapixels" in text_part["text"]
    assert image_part["image_url"]["url"] == "data:image/jpeg;base64,AAAA"


def test_metrics_are_formatted_when_engine_gave_no_block(config):
    client = FakeClient(chat(VALID))
    make_agent(config, client).run(context(metrics_block=""))
    assert "4000 x 3000" in client.calls[0]["messages"][1]["content"][0]["text"]


def test_missing_metrics_are_stated(config):
    client = FakeClient(chat(VALID))
    make_agent(config, client).run(context(metrics=None, metrics_block=""))
    assert "no local measurements" in client.calls[0]["messages"][1]["content"][0]["text"]


def test_model_change_in_settings_applies_without_new_agent(config, project):
    client = FakeClient(chat(VALID), chat(VALID))
    agent = make_agent(config, client)
    original_model = config.settings.pipeline.agents[0].model
    agent.run(context())
    path = project / "config" / "settings.json"
    data = json.loads(path.read_text())
    data["pipeline"]["agents"][0]["model"] = "qwen/qwen2.5-vl-72b-instruct"
    path.write_text(json.dumps(data))
    config.reload()
    agent.run(context())
    assert [c["model"] for c in client.calls] == [original_model, "qwen/qwen2.5-vl-72b-instruct"]


def test_sloppy_but_usable_reply_is_accepted(config):
    sloppy = dict(VALID, overall_score="73%", confidence=90, issues=["Soft focus"])
    fenced = "Here you go:\n```json\n" + json.dumps(sloppy) + "\n```"
    result = make_agent(config, FakeClient(chat(fenced))).run(context())
    assert result.verdict.overall_score == 73 and result.verdict.confidence == pytest.approx(0.9)


def test_wrapped_verdict_is_unwrapped(config):
    result = make_agent(config, FakeClient(chat({"review": VALID}))).run(context())
    assert result.verdict.overall_score == 73


def test_bad_json_is_repaired_once_and_usage_is_summed(config):
    client = FakeClient(chat("Sorry, I cannot do that.", prompt=1000, cost=0.01), chat(VALID, prompt=1100, cost=0.02))
    result = make_agent(config, client).run(context())
    assert len(client.calls) == 2 and result.verdict.overall_score == 73
    assert result.prompt_tokens == 2100 and result.cost_usd == pytest.approx(0.03)
    roles = [m["role"] for m in client.calls[1]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert "could not be used" in client.calls[1]["messages"][-1]["content"]


def test_schema_violation_triggers_repair(config):
    client = FakeClient(chat(dict(VALID, overall_score=140)), chat(VALID))
    assert make_agent(config, client).run(context()).verdict.overall_score == 73
    assert "overall_score" in client.calls[1]["messages"][-1]["content"]


def test_gives_up_after_one_repair(config):
    client = FakeClient(chat("nope"), chat("still nope"))
    with pytest.raises(ReviewParseError) as info:
        make_agent(config, client).run(context())
    assert len(client.calls) == 2 and info.value.raw_response == "still nope"


def test_truncated_reply_gets_more_room_on_retry(config):
    client = FakeClient(chat('{"approved": false, "overall', finish="length"), chat(VALID))
    make_agent(config, client).run(context())
    assert client.calls[1]["max_tokens"] == client.calls[0]["max_tokens"] * 2


def test_api_errors_propagate_without_repair(config):
    client = FakeClient(OpenRouterError("HTTP 402: insufficient OpenRouter credits", status_code=402))
    with pytest.raises(OpenRouterError):
        make_agent(config, client).run(context())
    assert len(client.calls) == 1


def test_json_schema_mode_sends_the_schema(config, project):
    path = project / "config" / "settings.json"
    data = json.loads(path.read_text())
    data["pipeline"]["agents"][0]["response_format"] = "json_schema"
    path.write_text(json.dumps(data))
    config.reload()
    client = FakeClient(chat(VALID))
    make_agent(config, client).run(context())
    assert client.calls[0]["response_format"] == "json_schema"
    assert "overall_score" in client.calls[0]["json_schema"]["properties"]


def test_agent_removed_from_settings_raises(config, project):
    agent = make_agent(config, FakeClient())
    path = project / "config" / "settings.json"
    data = json.loads(path.read_text())
    data["pipeline"]["agents"][0]["name"] = "renamed"
    path.write_text(json.dumps(data))
    config.reload()
    with pytest.raises(ConfigError, match="adobe_reviewer"):
        agent.run(context())


def test_registry_builds_registered_type(config):
    settings = config.settings.pipeline.agents[0]
    agent = create_agent(settings, config=config, client=FakeClient(), prompts=PromptManager(config))
    assert isinstance(agent, AdobeReviewerAgent) and "adobe_reviewer" in available_agent_types()


def test_registry_rejects_unknown_type(config):
    bogus = AgentSettings(name="x", type="does_not_exist", model="m/m", prompt="v1_reviewer")
    with pytest.raises(ConfigError, match="Unknown agent type"):
        create_agent(bogus, config=config, client=FakeClient(), prompts=PromptManager(config))
