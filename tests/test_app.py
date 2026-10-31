"""Tests for ReviewService (engine and publisher are faked; watcher and clock are real/fake)."""

import threading
from pathlib import Path

import pytest

from adobe_reviewer.app import ReviewService, SessionStats
from adobe_reviewer.config.manager import ConfigManager
from adobe_reviewer.engine.review_engine import ReviewOutcome
from adobe_reviewer.exceptions import ConfigError, FileMoveError, OpenRouterError
from adobe_reviewer.models.schemas import Decision, Destination, ReviewReport, ReviewVerdict
from adobe_reviewer.reporting.publisher import PublishedResult
from adobe_reviewer.watcher.folder_watcher import FolderWatcher


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_outcome(destination=Destination.REJECTED, cost=0.02, ai_called=True) -> ReviewOutcome:
    verdict = ReviewVerdict(approved=False, overall_score=60, commercial_value=60, technical_quality=60, confidence=0.9)
    decision = Decision(destination, ("test",))
    report = ReviewReport.from_verdict(verdict, image_file="x.jpg", reviewed_by=["a"], models_used=["m"]).with_decision(decision)
    return ReviewOutcome(report=report, decision=decision, cost_usd=cost, ai_called=ai_called)


class FakeEngine:
    """Each call takes the next scripted behaviour: an exception to raise or an outcome to return."""

    def __init__(self, *script) -> None:
        self.script = list(script)
        self.reviewed: list[Path] = []
        self.on_review = None

    def review(self, path: Path):
        self.reviewed.append(path)
        if self.on_review:
            self.on_review()
        step = self.script.pop(0) if self.script else make_outcome()
        if isinstance(step, Exception):
            raise step
        return step


class FakePublisher:
    """'Files' the image by deleting it from Incoming, like the real mover does."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def publish(self, path: Path, outcome):
        if self.error:
            raise self.error
        path.unlink()
        return PublishedResult(image_path=path, report_paths=())


@pytest.fixture()
def env(project):
    config = ConfigManager(project / "config" / "settings.json")
    config.ensure_directories()
    clock = Clock()
    watcher = FolderWatcher(config, clock=clock)

    def build(engine, publisher=None):
        def wait(stop: threading.Event, seconds: float) -> bool:
            clock.advance(seconds)
            return stop.is_set()

        return ReviewService(config, engine, publisher or FakePublisher(), watcher, clock=clock, wait=wait)

    return config, clock, build


def put(config: ConfigManager, name: str) -> Path:
    path = config.paths.incoming / name
    path.write_bytes(b"x" * 100)
    return path


def settle(service: ReviewService, clock: Clock) -> int:
    """Poll once to register new files, wait long enough to be stable, then run for real."""
    service.run_once()
    clock.advance(3)
    return service.run_once()


def test_stable_image_is_reviewed_filed_and_counted(env):
    config, clock, build = env
    path = put(config, "a.jpg")
    engine = FakeEngine(make_outcome(Destination.READY_TO_UPLOAD, cost=0.03))
    service = build(engine)
    assert service.run_once() == 0 and engine.reviewed == []   # not stable yet
    clock.advance(3)
    assert service.run_once() == 1 and engine.reviewed == [path] and not path.exists()
    assert service.stats.processed == 1 and service.stats.cost_usd == pytest.approx(0.03)


def test_api_failure_keeps_the_image_and_retries_after_the_cooldown(env):
    config, clock, build = env
    path = put(config, "a.jpg")
    engine = FakeEngine(OpenRouterError("HTTP 500", status_code=500), make_outcome())
    service = build(engine)
    assert settle(service, clock) == 1
    assert path.exists() and service.stats.failed == 1 and service.stats.processed == 0
    clock.advance(10)
    assert service.run_once() == 0                       # cooling down
    clock.advance(300)
    assert service.run_once() == 1 and not path.exists() and service.stats.processed == 1


def test_one_failure_does_not_stop_the_next_image(env):
    config, clock, build = env
    first, second = put(config, "a.jpg"), put(config, "b.jpg")
    engine = FakeEngine(OpenRouterError("HTTP 500", status_code=500), make_outcome())
    service = build(engine)
    assert settle(service, clock) == 2
    assert len(engine.reviewed) == 2 and service.stats.failed == 1 and service.stats.processed == 1


def test_unexpected_exception_is_contained(env):
    config, clock, build = env
    path = put(config, "a.jpg")
    service = build(FakeEngine(RuntimeError("boom")))
    assert settle(service, clock) == 1
    assert path.exists() and service.stats.failed == 1


def test_publish_failure_is_contained_and_image_stays(env):
    config, clock, build = env
    path = put(config, "a.jpg")
    service = build(FakeEngine(make_outcome()), FakePublisher(error=FileMoveError("locked")))
    assert settle(service, clock) == 1
    assert path.exists() and service.stats.failed == 1 and service.stats.processed == 0


@pytest.mark.parametrize("error", [OpenRouterError("HTTP 402", status_code=402), ConfigError("no key")])
def test_systemic_errors_pause_processing(env, error):
    config, clock, build = env
    put(config, "a.jpg")
    second = put(config, "b.jpg")
    engine = FakeEngine(error, make_outcome())
    service = build(engine)
    assert settle(service, clock) == 1 and len(engine.reviewed) == 1   # b.jpg was not even tried
    clock.advance(30)
    assert service.run_once() == 0 and len(engine.reviewed) == 1       # still paused
    clock.advance(31)
    assert service.run_once() == 1 and engine.reviewed[-1] == second and not second.exists()


def test_run_until_idle_waits_for_unstable_files_then_returns(env):
    config, clock, build = env
    path = put(config, "a.jpg")
    engine = FakeEngine(make_outcome())
    service = build(engine)
    service.run_until_idle(threading.Event())
    assert engine.reviewed == [path] and not path.exists()


def test_run_stops_when_asked(env):
    config, clock, build = env
    put(config, "a.jpg")
    stop = threading.Event()
    engine = FakeEngine(make_outcome())
    engine.on_review = stop.set
    service = build(engine)
    service.run(stop)
    assert len(engine.reviewed) == 1


def test_run_returns_immediately_if_already_stopped(env):
    _, _, build = env
    stop = threading.Event()
    stop.set()
    build(FakeEngine()).run(stop)


def test_summary_text():
    stats = SessionStats()
    stats.record(make_outcome(Destination.REJECTED, cost=0.01))
    stats.record(make_outcome(Destination.READY_TO_UPLOAD, cost=0.02))
    stats.failed = 3
    text = stats.summary()
    assert "2 image(s) filed" in text and "1 ready" in text and "1 rejected" in text
    assert "3 failed" in text and "$0.0300" in text
