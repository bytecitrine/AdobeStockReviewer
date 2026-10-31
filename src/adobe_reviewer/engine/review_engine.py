"""Reviews ONE image from file to final, routed report.

Flow:
    reload settings -> measure the file locally -> rule-based preflight
    -> (hard fail? reject now, no AI call) -> prepare the API copy
    -> run the agent pipeline -> merge preflight findings -> enforce hard-fail rules
    -> DecisionEngine -> final :class:`ReviewReport`.

The engine does not touch folders or write reports: it only returns the outcome.
Moving files and saving reports are the next modules' jobs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from adobe_reviewer.analysis.image_analyzer import ImageAnalyzer
from adobe_reviewer.analysis.image_processor import ImageProcessor
from adobe_reviewer.analysis.metrics_formatter import format_metrics_block
from adobe_reviewer.analysis.preflight import PreflightChecker
from adobe_reviewer.clients.openrouter import OpenRouterClient
from adobe_reviewer.config.manager import ConfigManager
from adobe_reviewer.engine.decision_engine import DecisionEngine
from adobe_reviewer.engine.pipeline import AgentPipeline
from adobe_reviewer.exceptions import ConfigError, ImageAnalysisError
from adobe_reviewer.models.schemas import (
    Decision,
    Destination,
    Issue,
    IssueCategory,
    LocalMetrics,
    ReviewContext,
    ReviewReport,
    ReviewVerdict,
    Severity,
)
from adobe_reviewer.prompts.manager import PromptManager

logger = logging.getLogger(__name__)

HARD_FAIL_SCORE_CAP = 59  # matches the reviewer prompt: a hard fail can never score above this
PREFLIGHT_AGENT_NAME = "preflight"


@dataclass(frozen=True)
class ReviewOutcome:
    """Everything the rest of the application needs after one review."""

    report: ReviewReport
    decision: Decision
    cost_usd: float | None
    ai_called: bool

    @property
    def destination(self) -> Destination:
        """The folder the image should be moved to."""
        return self.decision.destination


class ReviewEngine:
    """Coordinates analysis, the AI pipeline and the decision for one image.

    Args:
        config: The application's :class:`ConfigManager`.
        analyzer: Measures the original file.
        processor: Prepares the downscaled copy sent to the AI.
        preflight: Rule-based checks that need no AI.
        pipeline: Runs the reviewer agent(s). Any object with ``run(context)`` works.
        decision_engine: Applies the routing rules.
    """

    def __init__(
        self,
        *,
        config: ConfigManager,
        analyzer: ImageAnalyzer,
        processor: ImageProcessor,
        preflight: PreflightChecker,
        pipeline: AgentPipeline,
        decision_engine: DecisionEngine,
    ) -> None:
        self._config = config
        self._analyzer = analyzer
        self._processor = processor
        self._preflight = preflight
        self._pipeline = pipeline
        self._decision = decision_engine

    @classmethod
    def create(cls, config: ConfigManager, client: OpenRouterClient, prompts: PromptManager) -> "ReviewEngine":
        """Build an engine with the standard components."""
        return cls(
            config=config,
            analyzer=ImageAnalyzer(),
            processor=ImageProcessor(config),
            preflight=PreflightChecker(config),
            pipeline=AgentPipeline(config, client, prompts),
            decision_engine=DecisionEngine(config),
        )

    # ------------------------------------------------------------------ #
    def review(self, image_path: Path) -> ReviewOutcome:
        """Review one image.

        Unreadable files and preflight hard fails are returned as ordinary rejections.

        Raises:
            OpenRouterError: If the API call failed even after retries.
            ReviewParseError: If the model never produced a valid verdict.
            PromptError / ConfigError: If prompts or settings are unusable.
        In all of these cases nothing is decided: the caller leaves the image where it is.
        """
        image_path = Path(image_path)
        self._reload_config()

        try:
            metrics = self._analyzer.analyze(image_path)
        except ImageAnalysisError as exc:
            return self._reject_unreadable(image_path, exc)

        preflight_issues = self._preflight.check(metrics)
        if self._preflight.has_hard_fail(preflight_issues):
            logger.info("%s fails preflight; skipping the AI review", image_path.name)
            return self._finish(self._preflight_rejection(image_path, metrics, preflight_issues), ai_called=False)

        try:
            prepared = self._processor.prepare(image_path)
        except ImageAnalysisError as exc:
            return self._reject_unreadable(image_path, exc)

        context = ReviewContext(
            image_path=image_path,
            image_data_url=prepared.data_url,
            metrics=metrics,
            metrics_block=format_metrics_block(metrics, prepared),
        )
        result = self._pipeline.run(context)

        verdict = self._merge_preflight(result.verdict, preflight_issues)
        report = ReviewReport.from_verdict(
            verdict,
            image_file=image_path.name,
            reviewed_by=result.reviewed_by,
            models_used=result.models_used,
            local_metrics=metrics,
        )
        return self._finish(report, ai_called=True, cost_usd=result.total_cost_usd)

    # ------------------------------------------------------------------ #
    def _reload_config(self) -> None:
        """Pick up edits to settings.json; keep the old settings if the new file is invalid."""
        try:
            self._config.reload()
        except ConfigError as exc:
            logger.error("settings.json is invalid; continuing with the previous settings: %s", exc)

    def _finish(self, report: ReviewReport, *, ai_called: bool, cost_usd: float | None = None) -> ReviewOutcome:
        """Enforce hard-fail rules, decide the destination and make the report consistent with it."""
        if report.has_hard_fail:
            report = report.model_copy(
                update={"approved": False, "overall_score": min(report.overall_score, HARD_FAIL_SCORE_CAP)}
            )
        decision = self._decision.decide(report)
        final = report.with_decision(decision).model_copy(
            update={"approved": decision.destination is Destination.READY_TO_UPLOAD}
        )
        logger.info(
            "%s -> %s (score %d, %s)",
            final.image_file,
            decision.destination.value,
            final.overall_score,
            "AI reviewed" if ai_called else "no AI call",
        )
        return ReviewOutcome(report=final, decision=decision, cost_usd=cost_usd, ai_called=ai_called)

    @staticmethod
    def _merge_preflight(verdict: ReviewVerdict, preflight_issues: list[Issue]) -> ReviewVerdict:
        """Add the objective (non-fatal) preflight findings to the AI's verdict."""
        known_titles = {issue.title for issue in verdict.issues}
        extra = [issue for issue in preflight_issues if issue.title not in known_titles]
        if not extra:
            return verdict

        issues = sorted([*verdict.issues, *extra], key=lambda issue: issue.severity.rank)  # stable sort
        fixes = list(verdict.recommended_fixes)
        for issue in extra:
            if issue.correction and issue.correction not in fixes:
                fixes.append(issue.correction)
        return verdict.model_copy(update={"issues": issues, "recommended_fixes": fixes})

    def _preflight_rejection(self, path: Path, metrics: LocalMetrics, issues: list[Issue]) -> ReviewReport:
        """A report for an image rejected by the rule-based checks alone."""
        titles = "; ".join(issue.title for issue in issues if issue.hard_fail)
        return self._report_without_ai(
            path,
            issues,
            summary=f"Rejected automatically before the AI review: {titles}. No AI call was made, so the image is not scored.",
            metrics=metrics,
        )

    def _reject_unreadable(self, path: Path, error: ImageAnalysisError) -> ReviewOutcome:
        """A rejection for a file that cannot be opened as an image."""
        logger.warning("%s cannot be read as an image: %s", path.name, error)
        issue = Issue(
            category=IssueCategory.TECHNICAL,
            severity=Severity.CRITICAL,
            title="File cannot be opened as an image",
            explanation=str(error),
            correction="Re-export the image from its original source and make sure the file is a complete JPEG, PNG or WEBP.",
            hard_fail=True,
        )
        report = self._report_without_ai(
            path,
            [issue],
            summary="Rejected automatically: the file is corrupted, incomplete or not a supported image. No AI call was made.",
            metrics=None,
        )
        return self._finish(report, ai_called=False)

    @staticmethod
    def _report_without_ai(path: Path, issues: list[Issue], *, summary: str, metrics: LocalMetrics | None) -> ReviewReport:
        """Build a report from rule-based findings only. Scores are 0 because nothing was scored."""
        verdict = ReviewVerdict(
            approved=False,
            overall_score=0,
            commercial_value=0,
            technical_quality=0,
            issues=issues,
            recommended_fixes=[issue.correction for issue in issues if issue.correction],
            confidence=1.0,
            summary=summary,
        )
        return ReviewReport.from_verdict(
            verdict,
            image_file=path.name,
            reviewed_by=[PREFLIGHT_AGENT_NAME],
            models_used=[],
            local_metrics=metrics,
        )
