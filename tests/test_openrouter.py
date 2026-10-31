"""Tests for OpenRouterClient using httpx's mock transport (no network)."""

import json
from pathlib import Path

import httpx
import pytest

from adobe_reviewer.clients.openrouter import OpenRouterClient, build_vision_messages
from adobe_reviewer.config.manager import ConfigManager
from adobe_reviewer.exceptions import OpenRouterError

KEY = "sk-or-test-secret"
OK_BODY = {
    "model": "google/gemini-2.5-pro",
    "choices": [{"message": {"content": '{"approved": false}'}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1200, "completion_tokens": 300, "cost": 0.0123},
}


class Recorder:
    """Scripted server: returns the queued responses in order and records requests."""

    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0)


def make_client(project: Path, monkeypatch: pytest.MonkeyPatch, *responses: httpx.Response):
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    recorder = Recorder(*responses)
    sleeps: list[float] = []
    http = httpx.Client(transport=httpx.MockTransport(recorder))
    config = ConfigManager(project / "config" / "settings.json")
    client = OpenRouterClient(config, http_client=http, sleep=sleeps.append)
    return client, recorder, sleeps


def call(client: OpenRouterClient, **overrides):
    args = dict(
        messages=build_vision_messages("system", "user", "data:image/jpeg;base64,AAAA"),
        model="google/gemini-2.5-pro",
    )
    args.update(overrides)
    return client.complete(**args)


def test_success_returns_content_usage_and_cost(project, monkeypatch):
    client, recorder, _ = make_client(project, monkeypatch, httpx.Response(200, json=OK_BODY))
    result = call(client)
    assert result.content == '{"approved": false}'
    assert (result.prompt_tokens, result.completion_tokens, result.cost_usd) == (1200, 300, 0.0123)


def test_request_shape(project, monkeypatch):
    client, recorder, _ = make_client(project, monkeypatch, httpx.Response(200, json=OK_BODY))
    call(client, model="qwen/qwen2.5-vl-72b-instruct")
    request = recorder.requests[0]
    body = json.loads(request.content)
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    assert body["model"] == "qwen/qwen2.5-vl-72b-instruct"
    assert body["response_format"] == {"type": "json_object"}
    assert body["usage"] == {"include": True}
    user_parts = body["messages"][1]["content"]
    assert user_parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_response_format_none_omits_field(project, monkeypatch):
    client, recorder, _ = make_client(project, monkeypatch, httpx.Response(200, json=OK_BODY))
    call(client, response_format="none")
    assert "response_format" not in json.loads(recorder.requests[0].content)


def test_json_schema_requires_a_schema(project, monkeypatch):
    client, _, _ = make_client(project, monkeypatch)
    with pytest.raises(ValueError):
        call(client, response_format="json_schema")


def test_retries_on_503_then_succeeds(project, monkeypatch):
    client, recorder, sleeps = make_client(
        project, monkeypatch, httpx.Response(503, json={"error": {"message": "busy"}}), httpx.Response(200, json=OK_BODY)
    )
    assert call(client).content
    assert len(recorder.requests) == 2 and len(sleeps) == 1


def test_429_honours_retry_after(project, monkeypatch):
    client, _, sleeps = make_client(
        project,
        monkeypatch,
        httpx.Response(429, headers={"Retry-After": "7"}, json={"error": {"message": "slow down"}}),
        httpx.Response(200, json=OK_BODY),
    )
    call(client)
    assert sleeps[0] >= 7


def test_401_is_not_retried_and_hides_key(project, monkeypatch):
    client, recorder, sleeps = make_client(
        project, monkeypatch, httpx.Response(401, json={"error": {"message": "No auth"}})
    )
    with pytest.raises(OpenRouterError) as info:
        call(client)
    assert info.value.status_code == 401 and not info.value.retryable
    assert len(recorder.requests) == 1 and sleeps == []
    assert KEY not in str(info.value)


def test_error_inside_200_is_retried(project, monkeypatch):
    client, recorder, _ = make_client(
        project,
        monkeypatch,
        httpx.Response(200, json={"error": {"message": "upstream down", "code": 502}}),
        httpx.Response(200, json=OK_BODY),
    )
    call(client)
    assert len(recorder.requests) == 2


def test_empty_reply_is_retried(project, monkeypatch):
    empty = {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]}
    client, recorder, _ = make_client(project, monkeypatch, httpx.Response(200, json=empty), httpx.Response(200, json=OK_BODY))
    call(client)
    assert len(recorder.requests) == 2


def test_gives_up_after_max_retries(project, monkeypatch):
    # settings.json has max_retries = 3  ->  4 attempts in total
    responses = [httpx.Response(500, json={"error": {"message": "boom"}}) for _ in range(4)]
    client, recorder, sleeps = make_client(project, monkeypatch, *responses)
    with pytest.raises(OpenRouterError) as info:
        call(client)
    assert len(recorder.requests) == 4 and len(sleeps) == 3
    assert info.value.status_code == 500


def test_timeout_is_retryable(project, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json=OK_BODY)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = OpenRouterClient(ConfigManager(project / "config" / "settings.json"), http_client=http, sleep=lambda s: None)
    assert call(client).content
    assert calls["n"] == 2
