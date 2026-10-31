"""Decides which folder a reviewed image belongs in.

The AI proposes a score; this class applies YOUR rules in code, so an over-generous
model can never push a risky image into ``Ready_To_Upload``.

Rules, in order:
    1. Any hard-fail issue (logo, watermark, resolution out of range, ...) -> Rejected.
    2. Otherwise the overall score decides (thresholds come from ``settings.json``):
       ``>= ready`` -> Ready_To_Upload, ``>= needs_editing`` -> Needs_Editing, else Rejected.
    3. Safeguards: an image that would be Ready_To_Upload is downgraded to
       Needs_Editing if the reviewer did not approve it, if it still has critical or
       major issues, or if the reviewer's confidence is below ``min_confidence``.
"""

from __future__ import annotations

from adobe_reviewer.config.manager import ConfigManager
from adobe_reviewer.models.schemas import Decision, Destination, ReviewReport, Severity


class DecisionEngine:
    """Turns a :class:`ReviewReport` into a routing :class:`Decision`.

    Args:
        config: The application's :class:`ConfigManager` (thresholds are read live).
    """

    def __init__(self, config: ConfigManager) -> None:
        self._config = config

    def decide(self, report: ReviewReport) -> Decision:
        """Return the destination folder and the human-readable reasons for it."""
        thresholds = self._config.settings.thresholds

        hard_fails = [issue.title for issue in report.issues if issue.hard_fail]
        if hard_fails:
            return Decision(
                Destination.REJECTED,
                (f"Automatic rejection because of hard-fail issue(s): {'; '.join(hard_fails)}",),
            )

        score = report.overall_score
        reasons: list[str] = []
        if score >= thresholds.ready:
            destination = Destination.READY_TO_UPLOAD
            reasons.append(f"Overall score {score} is at least {thresholds.ready}")
        elif score >= thresholds.needs_editing:
            destination = Destination.NEEDS_EDITING
            reasons.append(f"Overall score {score} is between {thresholds.needs_editing} and {thresholds.ready - 1}")
        else:
            destination = Destination.REJECTED
            reasons.append(f"Overall score {score} is below {thresholds.needs_editing}")

        if destination is Destination.READY_TO_UPLOAD:
            concerns: list[str] = []
            if not report.approved:
                concerns.append("the reviewer did not approve the image")
            serious = [i.title for i in report.issues if i.severity in (Severity.CRITICAL, Severity.MAJOR)]
            if serious:
                concerns.append(f"it still has critical/major issue(s): {'; '.join(serious)}")
            if report.confidence < thresholds.min_confidence:
                concerns.append(
                    f"the reviewer's confidence {report.confidence:.2f} is below {thresholds.min_confidence:.2f}"
                )
            if concerns:
                destination = Destination.NEEDS_EDITING
                reasons.append("Downgraded from Ready_To_Upload because " + "; ".join(concerns))
        elif report.approved:
            reasons.append("The reviewer marked the image approved, but the score is below the ready threshold; the score decides")

        return Decision(destination, tuple(reasons))
