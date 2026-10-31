"""Tests for ReviewEngine using real image analysis and a fake pipeline."""

from pathlib import Path

import pytest

from adobe_reviewer.analysis.image_analyzer import ImageAnalyzer
from adobe_reviewer.analysis.image_processor import ImageProcessor
from adobe_reviewer.analysis.preflight import PreflightChecker
from adobe_reviewer.config.manager import ConfigManager
from adobe_reviewer.engine.decision_engine import DecisionEngine
from adobe_reviewer.engine.pipeline import PipelineResult
from adobe_reviewer.engine.review_engine import ReviewEngine
from adobe_reviewer.exceptions import OpenRouterError
from adobe_reviewer.models.schemas import (
    AgentResult, Destination, Issue, IssueCategory, ReviewVerdict, Severity,
)

READY, EDIT, REJECT = Destination.READY_TO_UPLOAD, Destination.NEEDS_EDITING, Destination.REJECTED


def verdict(score=95, *, approved=True, issues=(), fixes=()) -> ReviewVerdict:
    return ReviewVerdict(
        approved=approved, overall_score=score, commercial_value=score, technical_quality=score,
        issues=list(issues), recommended_fixes=list(fixes), confidence=0.9, summary="ok",
    )


class FakePipeline:
    """Returns a scripted verdict (or raises) and records the contexts it was given."""

    def __init__(self, result: ReviewVerdict | Exception) -> None:
        self.result = result
        self.contexts = []

    def run(self, context):
        self.contexts.append(context)
        if isinstance(self.result, Exception):
            raise self.result
        agent_result = AgentResult("adobe_reviewer", "actual/model", self.result, cost_usd=0.02)
        return PipelineResult(verdict=self.result, agent_results=(agent_result,))


def make_engine(project: Path, pipeline: FakePipeline) -> ReviewEngine:
    config = ConfigManager(project / "config" / "settings.json")
    return ReviewEngine(
        config=config,
        analyzer=ImageAnalyzer(),
        processor=ImageProcessor(config),
        preflight=PreflightChecker(config),
        pipeline=pipeline,
        decision_engine=DecisionEngine(config),
    )


def test_good_image_goes_to_ready_with_full_report(project, make_image):
    pipeline = FakePipeline(verdict(95))
    outcome = make_engine(project, pipeline).review(make_image("forest.jpg", (2000, 2000)))
    report = outcome.report
    assert outcome.destination is READY and outcome.ai_called and outcome.cost_usd == pytest.approx(0.02)
    assert report.image_file == "forest.jpg" and report.reviewed_by == ["adobe_reviewer"]
    assert report.models_used == ["actual/model"] and report.local_metrics.megapixels == 4.0
    assert report.destination is READY and report.approved is True and report.reviewed_at is not None


def test_ai_receives_image_copy_and_measurements(project, make_image):
    pipeline = FakePipeline(verdict(95))
    make_engine(project, pipeline).review(make_image("forest.jpg", (2000, 2000)))
    context = pipeline.contexts[0]
    assert context.image_path.name == "forest.jpg" and context.image_data_url.startswith("data:image/jpeg;base64,")
    assert "4.00 megapixels" in context.metrics_block and context.metrics.width == 2000


def test_low_resolution_is_rejected_without_calling_the_ai(project, make_image):
    pipeline = FakePipeline(verdict(95))
    outcome = make_engine(project, pipeline).review(make_image("small.jpg", (800, 600)))
    assert pipeline.contexts == [] and not outcome.ai_called and outcome.cost_usd is None
    assert outcome.destination is REJECT and outcome.report.reviewed_by == ["preflight"]
    assert outcome.report.overall_score == 0 and outcome.report.approved is False
    assert any(i.category is IssueCategory.RESOLUTION and i.hard_fail for i in outcome.report.issues)
    assert outcome.report.recommended_fixes and "No AI call" in outcome.report.summary


def test_ai_hard_fail_is_capped_and_rejected(project, make_image):
    logo = Issue(category=IssueCategory.LOGO, severity=Severity.CRITICAL, title="Visible Nike logo", hard_fail=True)
    outcome = make_engine(project, FakePipeline(verdict(92, approved=True, issues=[logo]))).review(
        make_image("a.jpg", (2000, 2000))
    )
    assert outcome.destination is REJECT
    assert outcome.report.overall_score == 59 and outcome.report.approved is False


def test_preflight_findings_are_merged_and_sorted(project, make_image):
    ai_issue = Issue(severity=Severity.MINOR, title="Soft background")
    outcome = make_engine(project, FakePipeline(verdict(80, approved=False, issues=[ai_issue], fixes=["Sharpen background"]))).review(
        make_image("a.png", (2000, 2000))
    )
    report = outcome.report
    assert [i.title for i in report.issues] == ["Soft background", "File is PNG, not JPEG"]
    assert report.recommended_fixes[0] == "Sharpen background" and any("JPEG" in fix for fix in report.recommended_fixes)
    assert outcome.destination is EDIT


def test_approved_flag_in_report_matches_routing(project, make_image):
    outcome = make_engine(project, FakePipeline(verdict(82, approved=True))).review(make_image("a.jpg", (2000, 2000)))
    assert outcome.destination is EDIT and outcome.report.approved is False


def test_corrupt_file_is_rejected_without_calling_the_ai(project, tmp_path):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not an image")
    pipeline = FakePipeline(verdict(95))
    outcome = make_engine(project, pipeline).review(bad)
    assert pipeline.contexts == [] and outcome.destination is REJECT
    assert outcome.report.issues[0].title == "File cannot be opened as an image" and outcome.report.local_metrics is None


def test_api_failure_propagates_so_the_image_stays_put(project, make_image):
    engine = make_engine(project, FakePipeline(OpenRouterError("HTTP 500", status_code=500)))
    with pytest.raises(OpenRouterError):
        engine.review(make_image("a.jpg", (2000, 2000)))


def test_invalid_settings_edit_does_not_stop_a_review(project, make_image):
    engine = make_engine(project, FakePipeline(verdict(95)))
    (project / "config" / "settings.json").write_text("{ broken")
    assert engine.review(make_image("a.jpg", (2000, 2000))).destination is READY
