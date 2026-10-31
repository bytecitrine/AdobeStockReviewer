"""Moves reviewed images into their destination folder without ever overwriting anything."""

from __future__ import annotations

import logging
import shutil
import time
from collections.abc import Callable
from pathlib import Path

from adobe_reviewer.config.manager import ConfigManager
from adobe_reviewer.exceptions import FileMoveError
from adobe_reviewer.models.schemas import Destination

logger = logging.getLogger(__name__)


class FileMover:
    """Chooses a safe target name and moves a file there.

    Args:
        config: The application's :class:`ConfigManager` (folders are read live).
        sleep: Function used to wait between retries (replaceable in tests).
    """

    MAX_NAME_ATTEMPTS = 10_000
    MOVE_ATTEMPTS = 3  # Windows can briefly lock a file (preview pane, antivirus)
    RETRY_DELAY_SECONDS = 0.5

    def __init__(self, config: ConfigManager, *, sleep: Callable[[float], None] = time.sleep) -> None:
        self._config = config
        self._sleep = sleep

    def unique_target(
        self,
        source: Path,
        destination: Destination,
        is_taken: Callable[[str], bool] | None = None,
    ) -> Path:
        """Pick where ``source`` will go, without touching anything.

        The name is ``forest.jpg``, or ``forest_1.jpg``, ``forest_2.jpg`` ... if that name is
        already used in the destination folder or ``is_taken(stem)`` says so (the publisher
        uses this to avoid overwriting an existing report).

        Raises:
            FileMoveError: If no free name is found.
        """
        folder = self._config.paths.destination_dir(destination)
        stem, suffix = source.stem, source.suffix
        for number in range(self.MAX_NAME_ATTEMPTS):
            candidate_stem = stem if number == 0 else f"{stem}_{number}"
            candidate = folder / f"{candidate_stem}{suffix}"
            if not candidate.exists() and not (is_taken and is_taken(candidate_stem)):
                return candidate
        raise FileMoveError(f"Could not find a free file name for {source.name} in {folder}")

    def move(self, source: Path, target: Path) -> Path:
        """Move ``source`` to ``target`` (never overwriting).

        Raises:
            FileMoveError: If the source is gone, the target exists, or the file stays
                locked / unmovable after a few retries.
        """
        if not source.is_file():
            raise FileMoveError(f"{source.name} no longer exists")
        if target.exists():
            raise FileMoveError(f"Refusing to overwrite existing file {target}")

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise FileMoveError(f"Cannot create folder {target.parent}: {exc}") from exc

        last_error: PermissionError | None = None
        for attempt in range(self.MOVE_ATTEMPTS):
            try:
                shutil.move(str(source), str(target))
                logger.debug("Moved %s -> %s", source.name, target)
                return target
            except PermissionError as exc:
                last_error = exc
                if attempt < self.MOVE_ATTEMPTS - 1:
                    logger.warning("%s is locked; retrying shortly", source.name)
                    self._sleep(self.RETRY_DELAY_SECONDS)
            except OSError as exc:
                raise FileMoveError(f"Cannot move {source.name} to {target}: {exc}") from exc
        raise FileMoveError(f"{source.name} is locked by another program and could not be moved") from last_error
