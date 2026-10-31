"""Writes review reports: ``<name>_review.json`` and ``<name>_review.txt``.

Where they go depends on ``report_placement`` in ``settings.json``:

    "both"          next to the image (in its destination folder) AND in reports/json + reports/txt
    "beside_image"  next to the image only
    "reports_dir"   in reports/json and reports/txt only

Files are written atomically (temp file, then rename), so a crash can never leave a
half-written report.
"""

from __future__ import annotations

import json
import logging
import os
import textwrap
from collections.abc import Iterable
from pathlib import Path

from adobe_reviewer.config.manager import ConfigManager
from adobe_reviewer.exceptions import ReportWriteError
from adobe_reviewer.models.schemas import Destination, LocalMetrics, ReviewReport

logger = logging.getLogger(__name__)

JSON_SUFFIX = "_review.json"
TEXT_SUFFIX = "_review.txt"
_WRAP_WIDTH = 92

_RESULT_LABELS = {
    Destination.READY_TO_UPLOAD: "PASS - ready to upload",
    Destination.NEEDS_EDITING: "NEEDS EDITING - fix the issues below, then submit again",
    Destination.REJECTED: "FAIL - rejected",
}


class ReportGenerator:
    """Renders and saves :class:`ReviewReport` objects.

    Args:
        config: The application's :class:`ConfigManager` (placement and folders are read live).
    """

    def __init__(self, config: ConfigManager) -> None:
        self._config = config

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #
    @staticmethod
    def render_json(report: ReviewReport) -> str:
        """The report as pretty-printed JSON (a superset of the format in the project brief)."""
        return json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"

    @staticmethod
    def render_text(report: ReviewReport) -> str:
        """The report as a readable plain-text document."""
        lines: list[str] = ["ADOBE STOCK AI REVIEW", "=" * 21, ""]
        lines.append(f"Image:          {report.image_file or 'unknown'}")
        if report.reviewed_at:
            lines.append(f"Reviewed:       {report.reviewed_at.strftime('%Y-%m-%d %H:%M UTC')}")
        if report.reviewed_by:
            by = ", ".join(report.reviewed_by)
            models = f" ({', '.join(report.models_used)})" if report.models_used else ""
            lines.append(f"Reviewed by:    {by}{models}")

        result = _RESULT_LABELS.get(report.destination, "NOT ROUTED") if report.destination else "NOT ROUTED"
        lines += ["", f"RESULT:         {result}"]
        if report.destination:
            lines.append(f"Folder:         {report.destination.value}/")
        lines.append(f"Approved:       {'YES' if report.approved else 'NO'}")
        lines += [
            f"Overall score:  {report.overall_score}/100",
            f"Technical:      {report.technical_quality}/100",
            f"Commercial:     {report.commercial_value}/100",
            f"Confidence:     {round(report.confidence * 100)}%",
        ]

        if report.decision_reasons:
            lines += ["", "WHY THIS FOLDER"]
            lines += [_bullet(reason) for reason in report.decision_reasons]

        if report.summary:
            lines += ["", "SUMMARY", textwrap.fill(report.summary, _WRAP_WIDTH)]

        lines += ["", f"ISSUES ({len(report.issues)})"]
        if not report.issues:
            lines.append("No issues found.")
        for number, issue in enumerate(report.issues, start=1):
            flag = "   *** HARD FAIL ***" if issue.hard_fail else ""
            lines.append(f"{number}. [{issue.severity.value.upper()}] {issue.category.value} - {issue.title}{flag}")
            if issue.explanation:
                lines.append(_labelled("Problem:", issue.explanation))
            if issue.correction:
                lines.append(_labelled("Fix:", issue.correction))
            lines.append("")
        if report.issues:
            lines.pop()  # drop the trailing blank line

        if report.recommended_fixes:
            lines += ["", "RECOMMENDED FIXES (most important first)"]
            lines += [
                textwrap.fill(fix, _WRAP_WIDTH, initial_indent=f"{n}. ", subsequent_indent="   ")
                for n, fix in enumerate(report.recommended_fixes, start=1)
            ]

        if report.local_metrics:
            lines += ["", "MEASURED FACTS (computed locally on the original file)"]
            lines += _metric_lines(report.local_metrics)

        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------ #
    # Saving
    # ------------------------------------------------------------------ #
    def save(self, report: ReviewReport, *, stem: str, image_dir: Path) -> list[Path]:
        """Write the report files according to ``report_placement``.

        Args:
            report: The finished report.
            stem: File name without extension of the (final) image; files are ``<stem>_review.*``.
            image_dir: The folder the image is being moved to (used for "beside the image").

        Returns:
            The paths written.

        Raises:
            ReportWriteError: If any file cannot be written. Files already written by
                this call are removed again.
        """
        placement = self._config.settings.report_placement
        paths = self._config.paths
        json_text, text = self.render_json(report), self.render_text(report)

        targets: list[tuple[Path, str]] = []
        if placement in ("both", "reports_dir"):
            targets += [(paths.reports_json / f"{stem}{JSON_SUFFIX}", json_text)]
            targets += [(paths.reports_txt / f"{stem}{TEXT_SUFFIX}", text)]
        if placement in ("both", "beside_image"):
            targets += [(image_dir / f"{stem}{JSON_SUFFIX}", json_text)]
            targets += [(image_dir / f"{stem}{TEXT_SUFFIX}", text)]

        written: list[Path] = []
        try:
            for path, content in targets:
                self._write_atomic(path, content)
                written.append(path)
        except ReportWriteError:
            self.delete(written)
            raise
        logger.debug("Saved %d report file(s) for %s", len(written), stem)
        return written

    def reports_exist(self, stem: str, image_dir: Path) -> bool:
        """True if saving a report for ``stem`` would overwrite an existing report."""
        placement = self._config.settings.report_placement
        paths = self._config.paths
        candidates: list[Path] = []
        if placement in ("both", "reports_dir"):
            candidates += [paths.reports_json / f"{stem}{JSON_SUFFIX}", paths.reports_txt / f"{stem}{TEXT_SUFFIX}"]
        if placement in ("both", "beside_image"):
            candidates += [image_dir / f"{stem}{JSON_SUFFIX}", image_dir / f"{stem}{TEXT_SUFFIX}"]
        return any(path.exists() for path in candidates)

    @staticmethod
    def delete(paths: Iterable[Path]) -> None:
        """Best-effort removal of report files (used to undo a partly finished save)."""
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Could not remove %s: %s", path, exc)

    @staticmethod
    def _write_atomic(path: Path, content: str) -> None:
        """Write via a temporary file and rename, so readers never see a partial report."""
        temp = path.with_name(path.name + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp.write_text(content, encoding="utf-8")
            os.replace(temp, path)
        except OSError as exc:
            temp.unlink(missing_ok=True)
            raise ReportWriteError(f"Cannot write report {path}: {exc}") from exc


# ---------------------------------------------------------------------- #
# Text helpers
# ---------------------------------------------------------------------- #
def _bullet(text: str) -> str:
    return textwrap.fill(text, _WRAP_WIDTH, initial_indent="- ", subsequent_indent="  ")


def _labelled(label: str, text: str) -> str:
    prefix = f"   {label:<9} "
    return textwrap.fill(text, _WRAP_WIDTH, initial_indent=prefix, subsequent_indent=" " * len(prefix))


def _metric_lines(m: LocalMetrics) -> list[str]:
    lines = [
        f"- Resolution:   {m.width} x {m.height} px ({m.megapixels:.2f} MP)",
        f"- File:         {m.image_format or 'unknown'}, {m.color_mode or 'unknown'}, {m.file_size_bytes / 1_048_576:.2f} MB",
    ]
    if m.jpeg_quality_estimate is not None:
        lines.append(f"- JPEG quality: about {m.jpeg_quality_estimate}")
    if m.sharpness_score is not None:
        lines.append(f"- Sharpness:    {m.sharpness_score:g} (higher is sharper)")
    if m.noise_level is not None:
        lines.append(f"- Noise:        {m.noise_level:g} (lower is cleaner)")
    return lines
