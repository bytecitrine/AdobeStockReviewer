"""The application loop: watch Incoming, review each image, file it, repeat until stopped.

Guarantees:
    * One bad image or one failed API call never stops the loop.
    * A failed image stays in ``Incoming/`` and is retried after the configured cool-down.
    * Problems that would fail EVERY image (bad API key, no credits, missing prompt) pause
      processing for a minute instead of hammering the API and flooding the log.
    * Stopping (Ctrl+C) lets the current image finish first.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from adobe_reviewer.config.manager import ConfigManager
from adobe_reviewer.engine.review_engine import ReviewEngine, ReviewOutcome
from adobe_reviewer.exceptions import AdobeReviewerError, ConfigError, OpenRouterError, PromptError
from adobe_reviewer.models.schemas import Destination
from adobe_reviewer.reporting.publisher import ReviewPublisher
from adobe_reviewer.watcher.folder_watcher import FolderWatcher

logger = logging.getLogger(__name__)

#: HTTP statuses that mean "every request will fail until you fix something".
SYSTEMIC_HTTP_STATUSES = frozenset({401, 402, 403, 404})


def _default_wait(stop: threading.Event, seconds: float) -> bool:
    """Sleep up to ``seconds`` but wake immediately when ``stop`` is set. Returns True if stopping."""
    return stop.wait(seconds)


@dataclass
class SessionStats:
    """Counters for one run of the application."""

    by_destination: Counter[Destination] = field(default_factory=Counter)
    failed: int = 0
    ai_reviews: int = 0
    cost_usd: float = 0.0

    @property
    def processed(self) -> int:
        """Images successfully reviewed and filed."""
        return sum(self.by_destination.values())

    def record(self, outcome: ReviewOutcome) -> None:
        """Count one successfully filed image."""
        self.by_destination[outcome.destination] += 1
        if outcome.ai_called:
            self.ai_reviews += 1
        if outcome.cost_usd:
            self.cost_usd += outcome.cost_usd

    def summary(self) -> str:
        """One-line, human-readable summary."""
        ready = self.by_destination[Destination.READY_TO_UPLOAD]
        edit = self.by_destination[Destination.NEEDS_EDITING]
        rejected = self.by_destination[Destination.REJECTED]
        return (
            f"Session summary: {self.processed} image(s) filed ({ready} ready, {edit} needs editing, "
            f"{rejected} rejected), {self.failed} failed attempt(s), {self.ai_reviews} AI review(s), "
            f"cost ${self.cost_usd:.4f}"
        )


class ReviewService:
    """Runs the review loop.

    Args:
        config: The application's :class:`ConfigManager`.
        engine: Reviews one image.
        publisher: Saves reports and moves the image.
        watcher: Finds images that are ready.
        clock: Monotonic clock in seconds (replaceable in tests).
        wait: ``wait(stop_event, seconds) -> stopping`` (replaceable in tests).
    """

    FATAL_PAUSE_SECONDS = 60.0

    def __init__(
        self,
        config: ConfigManager,
        engine: ReviewEngine,
        publisher: ReviewPublisher,
        watcher: FolderWatcher,
        *,
        clock: Callable[[], float] = time.monotonic,
        wait: Callable[[threading.Event, float], bool] = _default_wait,
    ) -> None:
        self._config = config
        self._engine = engine
        self._publisher = publisher
        self._watcher = watcher
        self._clock = clock
        self._wait = wait
        self._paused_until = 0.0
        self.stats = SessionStats()

    # ------------------------------------------------------------------ #
    # Loops
    # ------------------------------------------------------------------ #
    def run(self, stop: threading.Event) -> None:
        """Watch ``Incoming/`` until ``stop`` is set."""
        while not stop.is_set():
            if self.run_once(stop):
                continue  # something was handled: look again straight away
            if self._wait(stop, self._config.settings.watcher.poll_interval_seconds):
                break

    def run_until_idle(self, stop: threading.Event) -> None:
        """Process everything currently in ``Incoming/`` (waiting for copies to finish), then return."""
        while not stop.is_set():
            if self.run_once(stop):
                continue
            if self._is_paused() or not self._watcher.has_unstable():
                break
            if self._wait(stop, self._config.settings.watcher.poll_interval_seconds):
                break

    def run_once(self, stop: threading.Event | None = None) -> int:
        """Poll once and process every ready image. Returns how many were attempted."""
        if self._is_paused():
            return 0
        attempted = 0
        for path in self._watcher.poll():
            if stop is not None and stop.is_set():
                break
            attempted += 1
            if not self.process_one(path) and self._is_paused():
                break  # a systemic problem: do not try the remaining images now
        return attempted

    # ------------------------------------------------------------------ #
    # One image
    # ------------------------------------------------------------------ #
    def process_one(self, path: Path) -> bool:
        """Review and file one image. Never raises. Returns True on success."""
        try:
            outcome = self._engine.review(path)
            self._publisher.publish(path, outcome)
        except AdobeReviewerError as exc:
            self._handle_failure(path, exc)
            return False
        except Exception:  # noqa: BLE001 - the loop must survive anything
            logger.exception("Unexpected error while processing %s", path.name)
            self._handle_failure(path, None)
            return False

        self._watcher.mark_done(path)
        self.stats.record(outcome)
        return True

    # ------------------------------------------------------------------ #
    # Failure handling
    # ------------------------------------------------------------------ #
    def _handle_failure(self, path: Path, error: AdobeReviewerError | None) -> None:
        self.stats.failed += 1
        self._watcher.mark_failed(path)
        cooldown = int(self._config.settings.watcher.failure_retry_cooldown_seconds)
        if error is not None:
            logger.error("Could not finish %s: %s. It stays in Incoming and will be retried in %ds.", path.name, error, cooldown)
        if error is not None and self._is_systemic(error):
            self._paused_until = self._clock() + self.FATAL_PAUSE_SECONDS
            logger.error(
                "This problem will affect every image, so processing is paused for %ds. "
                "Fix it (API key, credits, model name or prompt files) and the app will carry on by itself.",
                int(self.FATAL_PAUSE_SECONDS),
            )

    @staticmethod
    def _is_systemic(error: AdobeReviewerError) -> bool:
        if isinstance(error, OpenRouterError):
            return error.status_code in SYSTEMIC_HTTP_STATUSES
        return isinstance(error, (ConfigError, PromptError))

    def _is_paused(self) -> bool:
        return self._clock() < self._paused_until
