"""Loads and serves application settings.

The model name, thresholds and everything else live in ``config/settings.json``;
the OpenRouter API key lives in an environment variable (optionally loaded from
``config/.env``) so it never ends up in a file you might share or commit.

Changing the model is a one-line edit in ``settings.json``. Call :meth:`reload`
(the application does this before every review) and no restart is needed.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from pydantic import ValidationError

from adobe_reviewer.config.models import Settings
from adobe_reviewer.config.paths import ProjectPaths
from adobe_reviewer.exceptions import ConfigError

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = "config/settings.json"


class ConfigManager:
    """Thread-safe access to validated settings and resolved folder paths.

    The project root is assumed to be the parent of the folder containing the
    settings file (``<root>/config/settings.json``).

    Args:
        config_path: Path to ``settings.json``.

    Raises:
        ConfigError: If the file is missing, is not valid JSON, or fails validation.
    """

    def __init__(self, config_path: str | Path = DEFAULT_CONFIG_PATH) -> None:
        self._config_path = Path(config_path).expanduser().resolve()
        self._root = self._config_path.parent.parent
        self._lock = threading.RLock()
        self._load_env_file()
        self._settings = self._read_settings()
        self._paths = ProjectPaths.from_settings(self._root, self._settings.paths)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    @property
    def settings(self) -> Settings:
        """The current validated settings."""
        with self._lock:
            return self._settings

    @property
    def paths(self) -> ProjectPaths:
        """The current absolute folder paths."""
        with self._lock:
            return self._paths

    @property
    def root(self) -> Path:
        """The project root directory."""
        return self._root

    def reload(self) -> Settings:
        """Re-read ``settings.json``.

        If the new file is invalid, the previous settings stay active and
        :class:`ConfigError` is raised, so a typo made while the app is running
        cannot take it down.
        """
        new_settings = self._read_settings()
        with self._lock:
            old_model = [a.model for a in self._settings.pipeline.agents]
            self._settings = new_settings
            self._paths = ProjectPaths.from_settings(self._root, new_settings.paths)
            new_model = [a.model for a in new_settings.pipeline.agents]
        if old_model != new_model:
            logger.info("Model configuration changed: %s -> %s", old_model, new_model)
        return new_settings

    def get_api_key(self) -> str:
        """Return the OpenRouter API key from the environment.

        Raises:
            ConfigError: If the variable named by ``openrouter.api_key_env`` is unset or empty.
        """
        env_name = self.settings.openrouter.api_key_env
        key = os.environ.get(env_name, "").strip()
        if not key:
            raise ConfigError(
                f"Environment variable {env_name} is not set. "
                f"Copy config/.env.example to config/.env and add your OpenRouter key."
            )
        return key

    def ensure_directories(self) -> None:
        """Create every folder the application needs."""
        self.paths.ensure()

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _read_settings(self) -> Settings:
        """Read, parse and validate the settings file."""
        try:
            raw = self._config_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ConfigError(f"Settings file not found: {self._config_path}") from exc
        except OSError as exc:
            raise ConfigError(f"Cannot read settings file {self._config_path}: {exc}") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{self._config_path.name} is not valid JSON: {exc}") from exc

        try:
            return Settings.model_validate(data)
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()
            )
            raise ConfigError(f"Invalid settings in {self._config_path.name}: {details}") from exc

    def _load_env_file(self) -> None:
        """Load ``config/.env`` into the environment if python-dotenv and the file exist."""
        env_file = self._config_path.parent / ".env"
        if not env_file.is_file():
            return
        try:
            from dotenv import load_dotenv
        except ImportError:
            logger.warning("python-dotenv is not installed; ignoring %s", env_file)
            return
        load_dotenv(env_file, override=False)
