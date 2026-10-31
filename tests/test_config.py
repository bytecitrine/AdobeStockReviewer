"""Tests for settings validation and the ConfigManager."""

import json
from pathlib import Path

import pytest

from adobe_reviewer.config.manager import ConfigManager
from adobe_reviewer.exceptions import ConfigError
from adobe_reviewer.models.schemas import Destination

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def read(project: Path) -> dict:
    return json.loads((project / "config" / "settings.json").read_text())


def write(project: Path, data: dict) -> None:
    (project / "config" / "settings.json").write_text(json.dumps(data))


def test_shipped_settings_are_valid():
    manager = ConfigManager(PROJECT_ROOT / "config" / "settings.json")
    assert manager.settings.thresholds.ready == 90
    assert manager.settings.pipeline.agents[0].model


def test_paths_resolve_under_root_and_can_be_created(project: Path):
    manager = ConfigManager(project / "config" / "settings.json")
    manager.ensure_directories()
    assert manager.paths.incoming == (project / "images" / "Incoming").resolve()
    assert manager.paths.destination_dir(Destination.REJECTED).is_dir()


def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(ConfigError):
        ConfigManager(tmp_path / "config" / "settings.json")


def test_unknown_key_is_rejected(project: Path):
    data = read(project)
    data["thresolds"] = {}
    write(project, data)
    with pytest.raises(ConfigError):
        ConfigManager(project / "config" / "settings.json")


def test_thresholds_must_be_ordered(project: Path):
    data = read(project)
    data["thresholds"] = {"ready": 70, "needs_editing": 80}
    write(project, data)
    with pytest.raises(ConfigError, match="greater than"):
        ConfigManager(project / "config" / "settings.json")


def test_reload_picks_up_model_change(project: Path):
    manager = ConfigManager(project / "config" / "settings.json")
    data = read(project)
    data["pipeline"]["agents"][0]["model"] = "anthropic/claude-sonnet-4.5"
    write(project, data)
    manager.reload()
    assert manager.settings.pipeline.agents[0].model == "anthropic/claude-sonnet-4.5"


def test_bad_reload_keeps_previous_settings(project: Path):
    manager = ConfigManager(project / "config" / "settings.json")
    original = manager.settings.pipeline.agents[0].model
    (project / "config" / "settings.json").write_text("{ not json")
    with pytest.raises(ConfigError):
        manager.reload()
    assert manager.settings.pipeline.agents[0].model == original


def test_api_key_comes_from_environment(project: Path, monkeypatch: pytest.MonkeyPatch):
    manager = ConfigManager(project / "config" / "settings.json")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="OPENROUTER_API_KEY"):
        manager.get_api_key()
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    assert manager.get_api_key() == "sk-or-test"
