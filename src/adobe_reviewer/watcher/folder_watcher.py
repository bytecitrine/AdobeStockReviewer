"""Finds images in ``Incoming/`` that are ready to be reviewed.

The watcher is a simple poller: each call to :meth:`FolderWatcher.poll` lists the
folder and returns the images that are safe to process now. Polling beats
file-system event libraries for this job: it never reports a half-copied file, it
works on network drives, and there is nothing to go out of sync.

An image is *ready* when:
    * its extension is one of ``image.extensions``,
    * its size and modification time have not changed for ``stable_wait_seconds``
      (so it has finished copying),
    * it can be opened for reading (Windows locks files that are still being written),
    * it is not cooling down after a failed review.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from adobe_reviewer.config.manager import ConfigManager

logger = logging.getLogger(__name__)

#: Suffixes used by browsers, cloud-sync tools and copy utilities for unfinished files.
TEMP_SUFFIXES = frozenset({".tmp", ".part", ".partial", ".crdownload", ".download", ".filepart"})

Signature = tuple[int, int]  # (size in bytes, modification time in ns)


@dataclass
class _Seen:
    """When a file was last observed to have a particular size/mtime."""

    signature: Signature
    since: float


class FolderWatcher:
    """Reports which images in ``Incoming/`` are ready for review.

    Args:
        config: The application's :class:`ConfigManager` (settings are read live).
        clock: Monotonic clock in seconds (replaceable in tests).
    """

    def __init__(self, config: ConfigManager, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._config = config
        self._clock = clock
        self._seen: dict[Path, _Seen] = {}
        self._cooldown: dict[Path, tuple[Signature, float]] = {}  # path -> (signature, retry_at)
        self._baseline: dict[Path, Signature] = {}  # files ignored because they pre-date startup
        self._warned: set[Path] = set()
        self._first_poll_done = False
        self._unstable = 0

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def poll(self) -> list[Path]:
        """Return the images ready to process now, oldest first."""
        now = self._clock()
        settings = self._config.settings.watcher
        candidates = self._list_candidates()
        present = {path for path, _ in candidates}

        # Forget anything that has disappeared (processed, deleted or moved by the user).
        for table in (self._seen, self._cooldown, self._baseline):
            for path in [p for p in table if p not in present]:
                del table[path]

        if not self._first_poll_done:
            self._first_poll_done = True
            if not settings.process_existing_on_start:
                self._baseline = dict(candidates)
                logger.info("Ignoring %d image(s) already in Incoming at startup", len(self._baseline))

        ready: list[tuple[Path, Signature]] = []
        unstable = 0
        for path, signature in candidates:
            if self._baseline.get(path) == signature:
                continue
            self._baseline.pop(path, None)  # it changed since startup: treat it as new

            cooling = self._cooldown.get(path)
            if cooling:
                cooled_signature, retry_at = cooling
                if cooled_signature == signature and now < retry_at:
                    continue
                self._cooldown.pop(path, None)  # cool-down over, or the file was replaced

            seen = self._seen.get(path)
            if seen is None or seen.signature != signature:
                self._seen[path] = _Seen(signature, now)
                unstable += 1
                continue
            if now - seen.since < settings.stable_wait_seconds or not self._can_open(path):
                unstable += 1
                continue
            ready.append((path, signature))

        self._unstable = unstable
        ready.sort(key=lambda item: (item[1][1], item[0].name))
        return [path for path, _ in ready]

    def has_unstable(self) -> bool:
        """True if the last poll saw images that are still being copied (not ready yet)."""
        return self._unstable > 0

    def mark_done(self, path: Path) -> None:
        """Forget ``path`` after it has been processed successfully."""
        self._seen.pop(path, None)
        self._cooldown.pop(path, None)
        self._baseline.pop(path, None)

    def mark_failed(self, path: Path) -> None:
        """Keep ``path`` in Incoming but do not retry it until the cool-down has passed."""
        signature = self._signature(path)
        if signature is None:
            return
        delay = self._config.settings.watcher.failure_retry_cooldown_seconds
        self._cooldown[path] = (signature, self._clock() + delay)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _list_candidates(self) -> list[tuple[Path, Signature]]:
        """Supported image files currently in Incoming, with their size/mtime signature."""
        incoming = self._config.paths.incoming
        extensions = set(self._config.settings.image.extensions)
        found: list[tuple[Path, Signature]] = []
        try:
            entries = list(incoming.iterdir())
        except OSError as exc:
            logger.warning("Cannot read the Incoming folder %s: %s", incoming, exc)
            return found

        for path in entries:
            suffix = path.suffix.lower()
            if path.name.startswith((".", "~")) or suffix in TEMP_SUFFIXES:
                continue
            signature = self._signature(path)
            if signature is None or not path.is_file():
                continue
            if suffix not in extensions:
                if path not in self._warned:
                    self._warned.add(path)
                    logger.warning(
                        "Ignoring %s: unsupported file type (supported: %s)", path.name, ", ".join(sorted(extensions))
                    )
                continue
            found.append((path, signature))
        return found

    @staticmethod
    def _signature(path: Path) -> Signature | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return (stat.st_size, stat.st_mtime_ns)

    @staticmethod
    def _can_open(path: Path) -> bool:
        """True if the file can be opened for reading (not locked by a program still writing it)."""
        try:
            with path.open("rb") as handle:
                handle.read(1)
        except OSError:
            return False
        return True
