"""Tests for ReviewPublisher (reports + move, in a failure-safe order)."""

import shutil
from pathlib import Path

import pytest

from adobe_reviewer.config.manager import ConfigManager
from adobe_reviewer.engine.review_engine import ReviewOutcome
from adobe_reviewer.exceptions import FileMoveError, ReportWriteError
from adobe_reviewer.io.file_mover import FileMover
from adobe_reviewer.models.schemas import Decision, Destination, ReviewReport, ReviewVerdict
from adobe_reviewer.reporting.publisher import ReviewPublisher
from adobe_reviewer.reporting.report_generator import ReportGenerator

EDIT = Destination.NEEDS_EDITING


@pytest.fixture()
def parts(project):
    config = ConfigManager(project / "config" / "settings.json")
    config.ensure_directories()
    reports = ReportGenerator(config)
    mover = FileMover(config, sleep=lambda s: None)
    return config, reports, mover, ReviewPublisher(config, reports, mover)


def outcome(destination: Destination = EDIT, name: str = "forest.jpg") -> ReviewOutcome:
    verdict = ReviewVerdict(approved=False, overall_score=80, commercial_value=80, technical_quality=80, confidence=0.9)
    decision = Decision(destination, ("test",))
    report = ReviewReport.from_verdict(verdict, image_file=name, reviewed_by=["a"], models_used=["m"]).with_decision(decision)
    return ReviewOutcome(report=report, decision=decision, cost_usd=None, ai_called=True)


def put_in_incoming(config: ConfigManager, make_image, name: str = "forest.jpg") -> Path:
    path = config.paths.incoming / name
    shutil.copy(make_image(name, (300, 200)), path)
    return path


def test_image_is_moved_and_reports_saved_in_both_places(parts, make_image):
    config, _, _, publisher = parts
    source = put_in_incoming(config, make_image)
    result = publisher.publish(source, outcome())
    assert result.image_path == config.paths.needs_editing / "forest.jpg" and result.image_path.is_file()
    assert not source.exists()
    names = {p.relative_to(config.root).as_posix() for p in result.report_paths}
    assert names == {
        "reports/json/forest_review.json", "reports/txt/forest_review.txt",
        "images/Needs_Editing/forest_review.json", "images/Needs_Editing/forest_review.txt",
    }


def test_second_image_with_same_name_gets_a_new_name_and_matching_reports(parts, make_image):
    config, _, _, publisher = parts
    publisher.publish(put_in_incoming(config, make_image), outcome())
    second = publisher.publish(put_in_incoming(config, make_image), outcome())
    assert second.image_path.name == "forest_1.jpg"
    json_path = config.paths.reports_json / "forest_1_review.json"
    assert json_path.is_file() and '"image_file": "forest_1.jpg"' in json_path.read_text(encoding="utf-8")
    assert (config.paths.reports_json / "forest_review.json").is_file()  # the first report is untouched


def test_same_stem_in_another_folder_does_not_overwrite_reports(parts, make_image):
    config, _, _, publisher = parts
    publisher.publish(put_in_incoming(config, make_image), outcome(Destination.REJECTED))
    result = publisher.publish(put_in_incoming(config, make_image), outcome(EDIT))
    assert result.image_path.name == "forest_1.jpg"


def test_failed_move_removes_the_reports_it_wrote(parts, make_image, monkeypatch):
    config, _, mover, publisher = parts
    source = put_in_incoming(config, make_image)
    monkeypatch.setattr(mover, "move", lambda s, t: (_ for _ in ()).throw(FileMoveError("locked")))
    with pytest.raises(FileMoveError):
        publisher.publish(source, outcome())
    assert source.exists()
    assert not list(config.paths.reports_json.glob("*")) and not list(config.paths.needs_editing.glob("*"))


def test_failed_report_write_leaves_the_image_in_incoming(parts, make_image, monkeypatch):
    config, reports, _, publisher = parts
    source = put_in_incoming(config, make_image)
    monkeypatch.setattr(reports, "save", lambda *a, **k: (_ for _ in ()).throw(ReportWriteError("disk full")))
    with pytest.raises(ReportWriteError):
        publisher.publish(source, outcome())
    assert source.exists() and not list(config.paths.needs_editing.glob("*"))
