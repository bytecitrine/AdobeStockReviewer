"""Tests for ReportGenerator."""

import json
from pathlib import Path

import pytest

from adobe_reviewer.config.manager import ConfigManager
from adobe_reviewer.exceptions import ReportWriteError
from adobe_reviewer.models.schemas import (
    Decision, Destination, Issue, IssueCategory, LocalMetrics, ReviewReport, ReviewVerdict, Severity,
)
from adobe_reviewer.reporting.report_generator import ReportGenerator


def sample_report(*, issues=None, destination=Destination.NEEDS_EDITING) -> ReviewReport:
    if issues is None:
        issues = [
            Issue(category=IssueCategory.AI_ARTIFACTS, severity=Severity.MAJOR, title="Malformed fingers",
                  explanation="The right hand has six fingers.", correction="Inpaint the hand."),
            Issue(category=IssueCategory.LOGO, severity=Severity.CRITICAL, title="Visible brand logo",
                  explanation="A swoosh is on the shoe.", correction="Remove it.", hard_fail=True),
        ]
    verdict = ReviewVerdict(
        approved=False, overall_score=73, commercial_value=84, technical_quality=65, issues=issues,
        recommended_fixes=["Inpaint the hand.", "Remove the logo."], confidence=0.94, summary="Needs work.",
    )
    metrics = LocalMetrics(width=4000, height=3000, megapixels=12.0, aspect_ratio=1.333, file_size_bytes=3_000_000,
                           image_format="JPEG", color_mode="RGB", sharpness_score=210.5, noise_level=1.2,
                           jpeg_quality_estimate=90)
    report = ReviewReport.from_verdict(verdict, image_file="forest.jpg", reviewed_by=["adobe_reviewer"],
                                       models_used=["google/gemini-2.5-pro"], local_metrics=metrics)
    return report.with_decision(Decision(destination, ("Overall score 73 is between 75 and 89",)))


def make_generator(project: Path, placement: str | None = None) -> tuple[ReportGenerator, ConfigManager]:
    settings_file = project / "config" / "settings.json"
    if placement:
        data = json.loads(settings_file.read_text())
        data["report_placement"] = placement
        settings_file.write_text(json.dumps(data))
    config = ConfigManager(settings_file)
    config.ensure_directories()
    return ReportGenerator(config), config


def test_json_keeps_the_briefs_field_order_and_round_trips():
    text = ReportGenerator.render_json(sample_report())
    data = json.loads(text)
    assert list(data)[:7] == ["approved", "overall_score", "commercial_value", "technical_quality",
                              "issues", "recommended_fixes", "confidence"]
    assert data["destination"] == "Needs_Editing" and data["issues"][0]["severity"] == "major"
    assert ReviewReport.model_validate_json(text).overall_score == 73


def test_text_report_contains_everything_a_human_needs():
    text = ReportGenerator.render_text(sample_report())
    for expected in ("forest.jpg", "NEEDS EDITING", "Needs_Editing/", "73/100", "94%", "WHY THIS FOLDER",
                     "[MAJOR] ai_artifacts - Malformed fingers", "*** HARD FAIL ***", "Problem:", "Fix:",
                     "RECOMMENDED FIXES", "4000 x 3000 px", "JPEG quality: about 90"):
        assert expected in text, expected


def test_text_report_without_issues():
    assert "No issues found." in ReportGenerator.render_text(sample_report(issues=[]))


def test_both_placement_writes_four_files(project):
    generator, config = make_generator(project, "both")
    image_dir = config.paths.needs_editing
    written = generator.save(sample_report(), stem="forest", image_dir=image_dir)
    expected = {
        config.paths.reports_json / "forest_review.json", config.paths.reports_txt / "forest_review.txt",
        image_dir / "forest_review.json", image_dir / "forest_review.txt",
    }
    assert set(written) == expected and all(p.is_file() for p in expected)
    assert not list(project.rglob("*.tmp"))


def test_reports_dir_placement_writes_only_to_reports(project):
    generator, config = make_generator(project, "reports_dir")
    generator.save(sample_report(), stem="forest", image_dir=config.paths.needs_editing)
    assert (config.paths.reports_json / "forest_review.json").is_file()
    assert not (config.paths.needs_editing / "forest_review.json").exists()


def test_beside_image_placement_writes_only_beside(project):
    generator, config = make_generator(project, "beside_image")
    generator.save(sample_report(), stem="forest", image_dir=config.paths.needs_editing)
    assert (config.paths.needs_editing / "forest_review.txt").is_file()
    assert not (config.paths.reports_json / "forest_review.json").exists()


def test_reports_exist_follows_placement(project):
    generator, config = make_generator(project, "both")
    image_dir = config.paths.rejected
    assert not generator.reports_exist("forest", image_dir)
    generator.save(sample_report(), stem="forest", image_dir=image_dir)
    assert generator.reports_exist("forest", image_dir)
    assert generator.reports_exist("forest", config.paths.needs_editing)  # the reports/ copy is enough


def test_failed_save_cleans_up_files_already_written(project, monkeypatch):
    generator, config = make_generator(project, "both")
    original = ReportGenerator._write_atomic
    calls = {"n": 0}

    def flaky(path, content):
        calls["n"] += 1
        if calls["n"] == 3:
            raise ReportWriteError("disk full")
        original(path, content)

    monkeypatch.setattr(ReportGenerator, "_write_atomic", staticmethod(flaky))
    with pytest.raises(ReportWriteError):
        generator.save(sample_report(), stem="forest", image_dir=config.paths.needs_editing)
    assert not list(config.paths.reports_json.glob("*")) and not list(config.paths.reports_txt.glob("*"))


def test_unicode_is_preserved(project):
    generator, config = make_generator(project, "reports_dir")
    report = sample_report().model_copy(update={"summary": "Schöne Landschaft, aber unscharf – 風景"})
    generator.save(report, stem="bild", image_dir=config.paths.needs_editing)
    assert "風景" in (config.paths.reports_json / "bild_review.json").read_text(encoding="utf-8")
