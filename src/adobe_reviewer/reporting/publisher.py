"""Finishes a review: saves the reports and moves the image, in a failure-safe order.

Order of operations:
    1. Pick a free target name (no overwriting of images OR reports).
    2. Write the reports under that final name.
    3. Move the image.

If step 2 fails the image has not moved, so it simply stays in ``Incoming/``.
If step 3 fails the freshly written reports are deleted again, so retrying later
starts clean and no orphan reports are left behind.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from adobe_reviewer.config.manager import ConfigManager
from adobe_reviewer.engine.review_engine import ReviewOutcome
from adobe_reviewer.exceptions import FileMoveError
from adobe_reviewer.io.file_mover import FileMover
from adobe_reviewer.reporting.report_generator import ReportGenerator

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PublishedResult:
    """Where everything ended up."""

    image_path: Path
    report_paths: tuple[Path, ...]


class ReviewPublisher:
    """Saves a review's reports and moves the image to its decided folder.

    Args:
        config: The application's :class:`ConfigManager`.
        reports: The :class:`ReportGenerator`.
        mover: The :class:`FileMover`.
    """

    def __init__(self, config: ConfigManager, reports: ReportGenerator, mover: FileMover) -> None:
        self._config = config
        self._reports = reports
        self._mover = mover

    @classmethod
    def create(cls, config: ConfigManager) -> "ReviewPublisher":
        """Build a publisher with the standard components."""
        return cls(config, ReportGenerator(config), FileMover(config))

    def publish(self, image_path: Path, outcome: ReviewOutcome) -> PublishedResult:
        """Save the reports and move ``image_path`` to the folder chosen by the decision.

        Raises:
            ReportWriteError: If the reports cannot be written (the image is not moved).
            FileMoveError: If the image cannot be moved (the reports are removed again).
        """
        image_path = Path(image_path)
        destination_dir = self._config.paths.destination_dir(outcome.destination)

        target = self._mover.unique_target(
            image_path,
            outcome.destination,
            is_taken=lambda stem: self._reports.reports_exist(stem, destination_dir),
        )
        if target.name != image_path.name:
            logger.info("%s already exists in %s; saving as %s", image_path.name, destination_dir.name, target.name)

        # The report must name the file as it will actually be stored.
        report = outcome.report.model_copy(update={"image_file": target.name})
        report_paths = self._reports.save(report, stem=target.stem, image_dir=destination_dir)

        try:
            final_path = self._mover.move(image_path, target)
        except FileMoveError:
            self._reports.delete(report_paths)
            raise

        logger.info("Filed %s in %s with %d report file(s)", final_path.name, destination_dir.name, len(report_paths))
        return PublishedResult(image_path=final_path, report_paths=tuple(report_paths))
