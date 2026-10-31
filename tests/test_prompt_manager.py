"""Tests for PromptManager, including the shipped v1 reviewer prompts."""

import os
from pathlib import Path

import pytest

from adobe_reviewer.config.manager import ConfigManager
from adobe_reviewer.exceptions import PromptError
from adobe_reviewer.prompts.manager import PromptManager

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_shipped_v1_prompts_load_and_render():
    manager = PromptManager(ConfigManager(PROJECT_ROOT / "config" / "settings.json"))
    prompt = manager.get("v1_reviewer")
    assert "Adobe Stock" in prompt.system and "JSON" in prompt.system
    text = manager.render_user("v1_reviewer", image_name="forest.jpg", metrics_block="- megapixels: 6.0")
    assert "forest.jpg" in text and "megapixels: 6.0" in text and "${" not in text


def test_missing_variable_raises(project: Path):
    manager = PromptManager(ConfigManager(project / "config" / "settings.json"))
    with pytest.raises(PromptError, match="metrics_block"):
        manager.render_user("v1_reviewer", image_name="x.jpg")


def test_missing_prompt_files_raise(project: Path):
    manager = PromptManager(ConfigManager(project / "config" / "settings.json"))
    with pytest.raises(PromptError):
        manager.get("does_not_exist")


def test_empty_prompt_file_raises(project: Path):
    (project / "prompts" / "v1_reviewer.system.md").write_text("   \n")
    manager = PromptManager(ConfigManager(project / "config" / "settings.json"))
    with pytest.raises(PromptError, match="empty"):
        manager.get("v1_reviewer")


def test_edits_are_picked_up_without_restart(project: Path):
    manager = PromptManager(ConfigManager(project / "config" / "settings.json"))
    system_file = project / "prompts" / "v1_reviewer.system.md"
    assert "SENTINEL" not in manager.get("v1_reviewer").system
    system_file.write_text("SENTINEL strict rules")
    # make sure the modification time visibly changes even on coarse filesystems
    stat = system_file.stat()
    os.utime(system_file, (stat.st_atime, stat.st_mtime + 5))
    assert manager.get("v1_reviewer").system == "SENTINEL strict rules"
