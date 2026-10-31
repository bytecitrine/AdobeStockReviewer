"""Tests for FolderWatcher using a fake clock."""

import json
import os
from pathlib import Path

import pytest

from adobe_reviewer.config.manager import ConfigManager
from adobe_reviewer.watcher.folder_watcher import FolderWatcher


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def setup(project: Path, **watcher_overrides):
    settings_file = project / "config" / "settings.json"
    if watcher_overrides:
        data = json.loads(settings_file.read_text())
        data["watcher"].update(watcher_overrides)
        settings_file.write_text(json.dumps(data))
    config = ConfigManager(settings_file)
    config.ensure_directories()
    clock = Clock()
    return config, clock, FolderWatcher(config, clock=clock)


def put(config: ConfigManager, name: str, content: bytes = b"x" * 100, mtime: float | None = None) -> Path:
    path = config.paths.incoming / name
    path.write_bytes(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_new_file_becomes_ready_only_after_it_is_stable(project):
    config, clock, watcher = setup(project)  # stable_wait_seconds = 2.0
    path = put(config, "a.jpg")
    assert watcher.poll() == [] and watcher.has_unstable()   # first sight
    clock.advance(1)
    assert watcher.poll() == []                               # unchanged, but not long enough
    clock.advance(1.5)
    assert watcher.poll() == [path] and not watcher.has_unstable()


def test_growing_file_restarts_the_wait(project):
    config, clock, watcher = setup(project)
    path = put(config, "a.jpg", b"x" * 100)
    watcher.poll()
    clock.advance(3)
    path.write_bytes(b"x" * 5000)  # still being copied
    assert watcher.poll() == []
    clock.advance(3)
    assert watcher.poll() == [path]


def test_unsupported_and_temporary_files_are_ignored(project):
    config, clock, watcher = setup(project)
    for name in ("notes.txt", "photo.jpg.crdownload", "half.tmp", ".hidden.jpg", "~lock.png"):
        put(config, name)
    (config.paths.incoming / "subfolder.jpg").mkdir()
    watcher.poll()
    clock.advance(5)
    assert watcher.poll() == [] and not watcher.has_unstable()


def test_extensions_are_case_insensitive(project):
    config, clock, watcher = setup(project)
    path = put(config, "SHOUT.JPG")
    watcher.poll()
    clock.advance(3)
    assert watcher.poll() == [path]


def test_ready_files_are_returned_oldest_first(project):
    config, clock, watcher = setup(project)
    newer = put(config, "a_new.jpg", mtime=2_000_000_000)
    older = put(config, "z_old.jpg", mtime=1_500_000_000)
    watcher.poll()
    clock.advance(3)
    assert watcher.poll() == [older, newer]


def test_failed_file_waits_for_the_cooldown_then_is_retried(project):
    config, clock, watcher = setup(project, failure_retry_cooldown_seconds=300)
    path = put(config, "a.jpg")
    watcher.poll()
    clock.advance(3)
    assert watcher.poll() == [path]
    watcher.mark_failed(path)
    clock.advance(100)
    assert watcher.poll() == []
    clock.advance(201)
    assert watcher.poll() == [path]


def test_replacing_a_failed_file_clears_its_cooldown(project):
    config, clock, watcher = setup(project)
    path = put(config, "a.jpg", b"x" * 100)
    watcher.poll()
    clock.advance(3)
    watcher.poll()
    watcher.mark_failed(path)
    path.write_bytes(b"y" * 250)  # user dropped a fixed version with the same name
    watcher.poll()
    clock.advance(3)
    assert watcher.poll() == [path]


def test_mark_done_forgets_the_file(project):
    config, clock, watcher = setup(project)
    path = put(config, "a.jpg")
    watcher.poll()
    clock.advance(3)
    watcher.poll()
    watcher.mark_done(path)
    path.unlink()
    assert watcher.poll() == []
    put(config, "a.jpg")           # a new file with the same name must wait for stability again
    assert watcher.poll() == [] and watcher.has_unstable()


def test_existing_files_are_ignored_when_process_existing_is_off(project):
    config, clock, watcher = setup(project, process_existing_on_start=False)
    old = put(config, "old.jpg")
    watcher.poll()
    clock.advance(3)
    assert watcher.poll() == []
    new = put(config, "new.jpg")
    watcher.poll()
    clock.advance(3)
    assert watcher.poll() == [new] and old.exists()


def test_existing_file_that_changes_is_treated_as_new(project):
    config, clock, watcher = setup(project, process_existing_on_start=False)
    old = put(config, "old.jpg", b"x" * 100)
    watcher.poll()
    old.write_bytes(b"x" * 999)
    watcher.poll()
    clock.advance(3)
    assert watcher.poll() == [old]


def test_missing_incoming_folder_does_not_crash(project):
    config, clock, watcher = setup(project)
    config.paths.incoming.rmdir()
    assert watcher.poll() == []
