"""Tests for PreflightChecker."""

import json

import pytest

from adobe_reviewer.analysis.preflight import PreflightChecker
from adobe_reviewer.config.manager import ConfigManager
from adobe_reviewer.models.schemas import IssueCategory, LocalMetrics, Severity


def metrics(**overrides) -> LocalMetrics:
    base = dict(
        width=4000, height=3000, megapixels=12.0, aspect_ratio=1.333, file_size_bytes=3_000_000,
        image_format="JPEG", color_mode="RGB", jpeg_quality_estimate=92,
    )
    base.update(overrides)
    return LocalMetrics(**base)


@pytest.fixture()
def checker(project) -> PreflightChecker:
    return PreflightChecker(ConfigManager(project / "config" / "settings.json"))


def test_clean_image_has_no_issues(checker):
    assert checker.check(metrics()) == []


def test_below_minimum_resolution_is_hard_fail_with_target_size(checker):
    issues = checker.check(metrics(width=1600, height=1200, megapixels=1.92))
    assert issues[0].category is IssueCategory.RESOLUTION and issues[0].hard_fail
    assert issues[0].severity is Severity.CRITICAL
    assert "2310 x 1733" in issues[0].correction  # sqrt(4 / 1.92) = 1.443
    assert checker.has_hard_fail(issues)


def test_above_maximum_resolution_is_hard_fail(checker):
    issues = checker.check(metrics(width=12000, height=10000, megapixels=120.0))
    assert issues[0].hard_fail and "Downscale" in issues[0].correction


def test_below_minimum_is_not_hard_fail_when_disabled(project):
    path = project / "config" / "settings.json"
    data = json.loads(path.read_text())
    data["image"]["reject_below_min_resolution"] = False
    path.write_text(json.dumps(data))
    issues = PreflightChecker(ConfigManager(path)).check(metrics(megapixels=2.0, width=1600, height=1250))
    assert not issues[0].hard_fail and issues[0].severity is Severity.MAJOR


def test_non_jpeg_is_info_only(checker):
    issues = checker.check(metrics(image_format="PNG", jpeg_quality_estimate=None))
    assert [i.severity for i in issues] == [Severity.INFO] and not checker.has_hard_fail(issues)


def test_cmyk_and_alpha_are_flagged(checker):
    titles = " ".join(i.title for i in checker.check(metrics(color_mode="CMYK", has_alpha=True)))
    assert "CMYK" in titles and "transparency" in titles


@pytest.mark.parametrize("quality, severity", [(65, Severity.MAJOR), (75, Severity.MINOR)])
def test_low_jpeg_quality(checker, quality, severity):
    issues = checker.check(metrics(jpeg_quality_estimate=quality))
    assert issues[0].category is IssueCategory.COMPRESSION and issues[0].severity is severity


def test_issues_are_sorted_most_severe_first(checker):
    issues = checker.check(metrics(megapixels=2.0, width=1600, height=1250, image_format="PNG", jpeg_quality_estimate=None))
    assert issues[0].severity is Severity.CRITICAL and issues[-1].severity is Severity.INFO
