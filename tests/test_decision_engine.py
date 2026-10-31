"""Tests for DecisionEngine routing rules."""

import pytest

from adobe_reviewer.config.manager import ConfigManager
from adobe_reviewer.engine.decision_engine import DecisionEngine
from adobe_reviewer.models.schemas import Destination, Issue, ReviewReport, ReviewVerdict, Severity

READY, EDIT, REJECT = Destination.READY_TO_UPLOAD, Destination.NEEDS_EDITING, Destination.REJECTED


@pytest.fixture()
def engine(project) -> DecisionEngine:
    return DecisionEngine(ConfigManager(project / "config" / "settings.json"))


def report(score=95, *, approved=True, confidence=0.9, issues=()) -> ReviewReport:
    verdict = ReviewVerdict(
        approved=approved, overall_score=score, commercial_value=score, technical_quality=score,
        issues=list(issues), confidence=confidence,
    )
    return ReviewReport.from_verdict(verdict, image_file="x.jpg", reviewed_by=["a"], models_used=["m"])


@pytest.mark.parametrize(
    "score, approved, expected",
    [(100, True, READY), (90, True, READY), (89, False, EDIT), (75, False, EDIT), (74, False, REJECT), (0, False, REJECT)],
)
def test_score_thresholds(engine, score, approved, expected):
    assert engine.decide(report(score, approved=approved)).destination is expected


def test_hard_fail_rejects_even_a_perfect_score(engine):
    logo = Issue(title="Visible Nike logo", severity=Severity.CRITICAL, hard_fail=True)
    decision = engine.decide(report(99, issues=[logo]))
    assert decision.destination is REJECT and "Visible Nike logo" in decision.reasons[0]


def test_high_score_without_approval_is_downgraded(engine):
    decision = engine.decide(report(95, approved=False))
    assert decision.destination is EDIT and "did not approve" in decision.reasons[-1]


@pytest.mark.parametrize("severity", [Severity.CRITICAL, Severity.MAJOR])
def test_serious_issue_blocks_ready(engine, severity):
    decision = engine.decide(report(94, issues=[Issue(title="Blurry hand", severity=severity)]))
    assert decision.destination is EDIT and "Blurry hand" in decision.reasons[-1]


def test_minor_issue_does_not_block_ready(engine):
    assert engine.decide(report(92, issues=[Issue(title="Slight noise", severity=Severity.MINOR)])).destination is READY


def test_low_confidence_blocks_ready(engine):
    decision = engine.decide(report(95, confidence=0.4))
    assert decision.destination is EDIT and "confidence 0.40" in decision.reasons[-1]


def test_low_confidence_does_not_change_other_bands(engine):
    assert engine.decide(report(80, approved=False, confidence=0.1)).destination is EDIT
    assert engine.decide(report(50, approved=False, confidence=0.1)).destination is REJECT


def test_approved_flag_cannot_override_a_low_score(engine):
    decision = engine.decide(report(82, approved=True))
    assert decision.destination is EDIT and "score decides" in decision.reasons[-1]
