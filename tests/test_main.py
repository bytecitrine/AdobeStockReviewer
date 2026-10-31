"""Tests for the command-line entry point (no network: --check and --once on an empty folder)."""

import logging

import pytest

from adobe_reviewer.main import main


@pytest.fixture(autouse=True)
def close_app_logging():
    """main() installs log handlers on the package logger; remove them so tests stay independent."""
    yield
    logger = logging.getLogger("adobe_reviewer")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.propagate = True


def args(project, *extra):
    return [*extra, "--config", str(project / "config" / "settings.json")]


def test_check_succeeds_with_valid_setup(project, monkeypatch, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    assert main(args(project, "--check")) == 0
    out = capsys.readouterr().out
    assert "Settings OK" in out and "agent 'adobe_reviewer'" in out and "sk-or-test" not in out


def test_missing_api_key_is_a_friendly_error(project, monkeypatch, capsys):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert main(args(project, "--check")) == 2
    assert "OPENROUTER_API_KEY" in capsys.readouterr().err


def test_broken_settings_file_is_a_friendly_error(project, monkeypatch, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    (project / "config" / "settings.json").write_text("{ nope")
    assert main(args(project, "--check")) == 2
    assert "not valid JSON" in capsys.readouterr().err


def test_missing_prompt_file_is_a_friendly_error(project, monkeypatch, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    (project / "prompts" / "v1_reviewer.user.md").unlink()
    assert main(args(project, "--check")) == 2
    assert "v1_reviewer" in capsys.readouterr().err


def test_once_with_an_empty_incoming_folder_exits_cleanly(project, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    assert main(args(project, "--once")) == 0
    assert (project / "images" / "Incoming").is_dir()  # folders are created on first run
