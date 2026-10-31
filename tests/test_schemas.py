"""Tests for the AI-contract models: lenient parsing and metadata wrapping."""

import pytest
from pydantic import ValidationError

from adobe_reviewer.models.schemas import (
    Decision,
    Destination,
    Issue,
    IssueCategory,
    ReviewReport,
    ReviewVerdict,
    Severity,
)

BASE = {
    "approved": False,
    "overall_score": 73,
    "commercial_value": 84,
    "technical_quality": 65,
    "issues": [],
    "recommended_fixes": [],
    "confidence": 0.94,
}


def make(**overrides):
    return ReviewVerdict.model_validate({**BASE, **overrides})


def test_parses_example_from_brief():
    verdict = make()
    assert verdict.overall_score == 73
    assert verdict.confidence == 0.94
    assert verdict.has_hard_fail is False


def test_float_and_string_scores_are_rounded():
    verdict = make(overall_score=87.6, commercial_value="84", technical_quality="65%")
    assert (verdict.overall_score, verdict.commercial_value, verdict.technical_quality) == (88, 84, 65)


def test_confidence_percentage_is_normalised():
    assert make(confidence=94).confidence == pytest.approx(0.94)
    assert make(confidence="94%").confidence == pytest.approx(0.94)


def test_out_of_range_score_is_rejected():
    with pytest.raises(ValidationError):
        make(overall_score=140)


def test_string_issues_and_null_fixes_are_accepted():
    verdict = make(issues=["Soft focus on the subject"], recommended_fixes=None)
    assert verdict.issues[0].title == "Soft focus on the subject"
    assert verdict.recommended_fixes == []


def test_unknown_enum_values_fall_back():
    issue = Issue.model_validate({"title": "x", "category": "Weird Thing", "severity": "SEVERE?"})
    assert issue.category is IssueCategory.OTHER
    assert issue.severity is Severity.MINOR


def test_enum_normalisation():
    issue = Issue.model_validate({"title": "x", "category": "Copy Space", "severity": "Critical"})
    assert issue.category is IssueCategory.COPY_SPACE
    assert issue.severity is Severity.CRITICAL


def test_hard_fail_detection():
    verdict = make(issues=[{"title": "Visible Nike logo", "category": "logo", "hard_fail": True}])
    assert verdict.has_hard_fail is True


def test_extra_keys_from_model_are_ignored():
    assert make(some_new_field="whatever").approved is False


def test_report_wraps_verdict_and_records_decision():
    report = ReviewReport.from_verdict(
        make(), image_file="forest.jpg", reviewed_by=["adobe_reviewer"], models_used=["m/x"]
    )
    assert report.reviewed_at is not None and report.destination is None
    decided = report.with_decision(Decision(Destination.NEEDS_EDITING, ("score 73",)))
    assert decided.destination is Destination.NEEDS_EDITING
    assert decided.decision_reasons == ["score 73"]
    assert report.destination is None  # original untouched
