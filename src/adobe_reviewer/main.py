"""Command-line entry point.

    uv run python -m adobe_reviewer            watch Incoming/ until Ctrl+C
    uv run python -m adobe_reviewer --once     review what is in Incoming/ now, then exit
    uv run python -m adobe_reviewer --check    verify settings, API key and prompts; no API call

Exit codes: 0 = normal, 2 = a configuration problem (message printed to stderr).
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from collections.abc import Sequence
from pathlib import Path

from adobe_reviewer import __version__
from adobe_reviewer.app import ReviewService
from adobe_reviewer.clients.openrouter import OpenRouterClient
from adobe_reviewer.config.manager import ConfigManager
from adobe_reviewer.engine.review_engine import ReviewEngine
from adobe_reviewer.exceptions import AdobeReviewerError
from adobe_reviewer.logging_setup import setup_logging
from adobe_reviewer.prompts.manager import PromptManager
from adobe_reviewer.reporting.publisher import ReviewPublisher
from adobe_reviewer.watcher.folder_watcher import FolderWatcher

logger = logging.getLogger(__name__)


def default_config_path() -> Path:
    """``<project root>/config/settings.json``, found from this file so the CWD does not matter."""
    return Path(__file__).resolve().parents[2] / "config" / "settings.json"


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="adobe_reviewer",
        description="Review AI-generated images like a strict Adobe Stock reviewer.",
    )
    parser.add_argument("--config", type=Path, default=default_config_path(), help="path to settings.json")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="review the images now in Incoming, then exit")
    mode.add_argument("--check", action="store_true", help="verify settings, API key and prompts, then exit")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the application. Returns the process exit code."""
    args = _parse_args(argv)

    try:
        config = ConfigManager(args.config)
        config.ensure_directories()
        setup_logging(config.settings.logging, config.paths.logs)
        config.get_api_key()  # fail early, with a friendly message
        prompts = PromptManager(config)
        for agent in config.settings.pipeline.agents:  # fail early if a prompt file is missing
            prompts.get(agent.prompt)
    except AdobeReviewerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.check:
        _print_check(config)
        return 0

    client = OpenRouterClient(config)
    service = ReviewService(
        config,
        ReviewEngine.create(config, client, prompts),
        ReviewPublisher.create(config),
        FolderWatcher(config),
    )
    stop = threading.Event()
    _log_startup(config)

    try:
        if args.once:
            service.run_until_idle(stop)
        else:
            _install_signal_handlers(stop)
            logger.info("Watching %s. Press Ctrl+C to stop.", config.paths.incoming)
            service.run(stop)
    except KeyboardInterrupt:
        logger.info("Forced stop.")
    finally:
        client.close()
        logger.info(service.stats.summary())
    return 0


# ---------------------------------------------------------------------- #
def _install_signal_handlers(stop: threading.Event) -> None:
    """Ctrl+C asks for a clean stop (current image finishes); a second Ctrl+C forces it."""

    def handler(signum: int, frame: object) -> None:
        if stop.is_set():
            raise KeyboardInterrupt
        stop.set()
        logger.info("Stopping after the current image... (press Ctrl+C again to force quit)")

    for name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):  # not running in the main thread
            pass


def _log_startup(config: ConfigManager) -> None:
    settings = config.settings
    for agent in settings.pipeline.agents:
        logger.info("Agent '%s' uses model %s (prompt set '%s')", agent.name, agent.model, agent.prompt)
    t = settings.thresholds
    logger.info(
        "Routing: >= %d Ready_To_Upload, %d-%d Needs_Editing, < %d Rejected",
        t.ready, t.needs_editing, t.ready - 1, t.needs_editing,
    )


def _print_check(config: ConfigManager) -> None:
    settings = config.settings
    print(f"Settings OK: {config.root / 'config' / 'settings.json'}")
    print(f"API key found in {settings.openrouter.api_key_env}")
    print(f"Pipeline mode: {settings.pipeline.mode}")
    for agent in settings.pipeline.agents:
        print(f"  agent '{agent.name}': model {agent.model}, prompt set '{agent.prompt}' (found)")
    print(f"Incoming folder: {config.paths.incoming}")
    print(f"Report placement: {settings.report_placement}")
    print("Everything looks good. Run without --check to start.")
