"""End-to-end tests: real files, real analyzer, engine, agent, reports, mover, watcher and loop.

Only the AI is faked (a scripted client that answers per file name), so these tests
cost nothing and show that all the modules work together.
"""

import json
import shutil
import threading
from pathlib import Path

import pytest

from adobe_reviewer.app import ReviewService
from adobe_reviewer.clients.openrouter import ChatResult
from adobe_reviewer.config.manager import ConfigManager
from adobe_reviewer.engine.review_engine import ReviewEngine
from adobe_reviewer.exceptions import OpenRouterError
from adobe_reviewer.prompts.manager import PromptManager
from adobe_reviewer.reporting.publisher import ReviewPublisher
from adobe_reviewer.watcher.folder_watcher import FolderWatcher

GOOD = {
    "approved": True, "overall_score": 95, "commercial_value": 92, "technical_quality": 96,
    "issues": [], "recommended_fixes": [], "confidence": 0.9, "summary": "Clean image.",
}
FLAWED = {
    "approved": False, "overall_score": 80, "commercial_value": 85, "technical_quality": 74,
    "issues": [{
        "category": "ai_artifacts", "severity": "major", "title": "Malformed fingers",
        "explanation": "The right hand has six fingers.", "correction": "Inpaint the hand.", "hard_fail": False,
    }],
    "recommended_fixes": ["Inpaint the hand."], "confidence": 0.9, "summary": "Fixable.",
}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class ScriptedClient:
    """Fake OpenRouter client. ``replies`` maps a file name to a dict, a raw string, an
    exception, or a list of those (used one after another)."""

    def __init__(self, replies: dict) -> None:
        self.replies = replies
        self.calls: list[str] = []

    def complete(self, **kwargs) -> ChatResult:
        user_text = kwargs["messages"][1]["content"][0]["text"]
        name = next(n for n in self.replies if f"File name: {n}" in user_text)
        self.calls.append(name)
        reply = self.replies[name]
        if isinstance(reply, list):
            reply = reply.pop(0)
        if isinstance(reply, Exception):
            raise reply
        content = reply if isinstance(reply, str) else json.dumps(reply)
        return ChatResult(
            content=content, model="fake/model", finish_reason="stop",
            prompt_tokens=1500, completion_tokens=400, cost_usd=0.02,
        )


def build_app(project: Path, replies: dict):
    config = ConfigManager(project / "config" / "settings.json")
    config.ensure_directories()
    clock = Clock()
    client = ScriptedClient(replies)
    service = ReviewService(
        config,
        ReviewEngine.create(config, client, PromptManager(config)),
        ReviewPublisher.create(config),
        FolderWatcher(config, clock=clock),
        clock=clock,
        wait=lambda stop, seconds: (setattr(clock, "now", clock.now + seconds), stop.is_set())[1],
    )
    return config, service, client


def drop(config: ConfigManager, make_image, name: str, size=(2000, 2000)) -> None:
    shutil.copy(make_image(name, size), config.paths.incoming / name)


def test_three_images_are_filed_with_reports(project, make_image):
    config, service, client = build_app(project, {"good.jpg": GOOD, "flawed.jpg": FLAWED, "small.jpg": GOOD})
    drop(config, make_image, "good.jpg")
    drop(config, make_image, "flawed.jpg")
    drop(config, make_image, "small.jpg", size=(800, 600))  # under 4 MP: rejected without asking the AI

    service.run_until_idle(threading.Event())

    paths = config.paths
    assert (paths.ready_to_upload / "good.jpg").is_file()
    assert (paths.needs_editing / "flawed.jpg").is_file()
    assert (paths.rejected / "small.jpg").is_file()
    assert list(paths.incoming.glob("*")) == []
    assert sorted(client.calls) == ["flawed.jpg", "good.jpg"]  # the tiny image never reached the AI

    for stem, folder in (("good", paths.ready_to_upload), ("flawed", paths.needs_editing), ("small", paths.rejected)):
        assert (paths.reports_json / f"{stem}_review.json").is_file()
        assert (paths.reports_txt / f"{stem}_review.txt").is_file()
        assert (folder / f"{stem}_review.json").is_file() and (folder / f"{stem}_review.txt").is_file()

    flawed = json.loads((paths.reports_json / "flawed_review.json").read_text(encoding="utf-8"))
    assert flawed["destination"] == "Needs_Editing" and flawed["approved"] is False
    assert flawed["issues"][0]["title"] == "Malformed fingers"
    assert flawed["local_metrics"]["width"] == 2000 and flawed["models_used"] == ["fake/model"]

    small_text = (paths.reports_txt / "small_review.txt").read_text(encoding="utf-8")
    assert "FAIL" in small_text and "No AI call" in small_text and "Resolution too low" in small_text

    assert (service.stats.processed, service.stats.ai_reviews, service.stats.failed) == (3, 2, 0)
    assert service.stats.cost_usd == pytest.approx(0.04)


def test_api_failure_leaves_the_image_and_does_not_block_others(project, make_image):
    config, service, client = build_app(project, {"good.jpg": GOOD, "bad.jpg": OpenRouterError("HTTP 500", status_code=500)})
    drop(config, make_image, "good.jpg")
    drop(config, make_image, "bad.jpg")

    service.run_until_idle(threading.Event())

    assert (config.paths.ready_to_upload / "good.jpg").is_file()
    assert (config.paths.incoming / "bad.jpg").is_file()
    assert not list(config.paths.reports_json.glob("bad*"))
    assert service.stats.processed == 1 and service.stats.failed == 1


def test_messy_model_reply_is_repaired_automatically(project, make_image):
    config, service, client = build_app(project, {"good.jpg": ["Sorry, here you go: not json", GOOD]})
    drop(config, make_image, "good.jpg")

    service.run_until_idle(threading.Event())

    assert client.calls == ["good.jpg", "good.jpg"]  # the second call is the automatic correction
    assert (config.paths.ready_to_upload / "good.jpg").is_file()
    assert service.stats.cost_usd == pytest.approx(0.04)  # both calls are counted
