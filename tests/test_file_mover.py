"""Tests for FileMover."""

import shutil
from pathlib import Path

import pytest

from adobe_reviewer.config.manager import ConfigManager
from adobe_reviewer.exceptions import FileMoveError
from adobe_reviewer.io.file_mover import FileMover
from adobe_reviewer.models.schemas import Destination


@pytest.fixture()
def setup(project):
    config = ConfigManager(project / "config" / "settings.json")
    config.ensure_directories()
    sleeps: list[float] = []
    return config, FileMover(config, sleep=sleeps.append), sleeps


def incoming_file(config: ConfigManager, name: str = "forest.jpg", content: bytes = b"data") -> Path:
    path = config.paths.incoming / name
    path.write_bytes(content)
    return path


def test_moves_file_and_removes_source(setup):
    config, mover, _ = setup
    source = incoming_file(config)
    target = mover.unique_target(source, Destination.READY_TO_UPLOAD)
    assert target == config.paths.ready_to_upload / "forest.jpg"
    assert mover.move(source, target) == target
    assert target.read_bytes() == b"data" and not source.exists()


def test_name_collisions_get_a_numeric_suffix(setup):
    config, mover, _ = setup
    (config.paths.rejected / "forest.jpg").write_bytes(b"old")
    (config.paths.rejected / "forest_1.jpg").write_bytes(b"old")
    target = mover.unique_target(incoming_file(config), Destination.REJECTED)
    assert target.name == "forest_2.jpg"


def test_is_taken_callback_forces_a_new_name(setup):
    config, mover, _ = setup
    target = mover.unique_target(incoming_file(config), Destination.REJECTED, is_taken=lambda stem: stem == "forest")
    assert target.name == "forest_1.jpg"


def test_existing_files_are_never_overwritten(setup):
    config, mover, _ = setup
    source = incoming_file(config)
    existing = config.paths.rejected / "forest.jpg"
    existing.write_bytes(b"precious")
    with pytest.raises(FileMoveError, match="overwrite"):
        mover.move(source, existing)
    assert existing.read_bytes() == b"precious" and source.exists()


def test_missing_source_raises(setup):
    config, mover, _ = setup
    with pytest.raises(FileMoveError, match="no longer exists"):
        mover.move(config.paths.incoming / "ghost.jpg", config.paths.rejected / "ghost.jpg")


def test_locked_file_is_retried_then_succeeds(setup, monkeypatch):
    config, mover, sleeps = setup
    source = incoming_file(config)
    real_move, calls = shutil.move, {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("file in use")
        return real_move(src, dst)

    monkeypatch.setattr(shutil, "move", flaky)
    target = config.paths.rejected / "forest.jpg"
    assert mover.move(source, target) == target
    assert calls["n"] == 3 and len(sleeps) == 2


def test_permanently_locked_file_raises_and_stays_put(setup, monkeypatch):
    config, mover, _ = setup
    source = incoming_file(config)
    monkeypatch.setattr(shutil, "move", lambda s, d: (_ for _ in ()).throw(PermissionError("in use")))
    with pytest.raises(FileMoveError, match="locked"):
        mover.move(source, config.paths.rejected / "forest.jpg")
    assert source.exists()
