"""Loads prompt files from the ``prompts/`` folder.

A *prompt set* named ``v1_reviewer`` consists of two files:

    prompts/v1_reviewer.system.md   fixed instructions (sent as-is)
    prompts/v1_reviewer.user.md     per-image template using ``${placeholders}``

Prompts are plain files so you can tune the reviewer's strictness without touching
Python. Files are re-read automatically when they change on disk.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from string import Template

from adobe_reviewer.config.manager import ConfigManager
from adobe_reviewer.exceptions import PromptError

logger = logging.getLogger(__name__)

SYSTEM_SUFFIX = ".system.md"
USER_SUFFIX = ".user.md"


@dataclass(frozen=True)
class PromptSet:
    """A loaded pair of prompts."""

    name: str
    system: str
    user_template: str


class PromptManager:
    """Loads, caches and renders prompt sets.

    The prompts folder is read from the live configuration on every call, so a
    config reload that points to a different folder is respected.

    Args:
        config: The application's :class:`ConfigManager`.
    """

    def __init__(self, config: ConfigManager) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._cache: dict[Path, tuple[tuple[float, float], PromptSet]] = {}

    def get(self, name: str) -> PromptSet:
        """Return the prompt set called ``name``.

        Raises:
            PromptError: If a file is missing, unreadable, or empty.
        """
        directory = self._config.paths.prompts
        system_path = directory / f"{name}{SYSTEM_SUFFIX}"
        user_path = directory / f"{name}{USER_SUFFIX}"

        try:
            stamp = (system_path.stat().st_mtime, user_path.stat().st_mtime)
        except FileNotFoundError as exc:
            raise PromptError(
                f"Prompt set '{name}' needs both {system_path.name} and {user_path.name} in {directory}"
            ) from exc

        with self._lock:
            cached = self._cache.get(system_path)
            if cached and cached[0] == stamp:
                return cached[1]

            prompt_set = PromptSet(
                name=name,
                system=self._read(system_path),
                user_template=self._read(user_path),
            )
            self._cache[system_path] = (stamp, prompt_set)
            logger.debug("Loaded prompt set '%s'", name)
            return prompt_set

    def render_user(self, name: str, **variables: object) -> str:
        """Fill the user template of prompt set ``name``.

        Args:
            name: Prompt set name.
            **variables: Values for the template's ``${placeholders}``.

        Raises:
            PromptError: If a placeholder has no value, or the template is malformed.
        """
        template = Template(self.get(name).user_template)
        values = {key: str(value) for key, value in variables.items()}
        try:
            return template.substitute(values).strip()
        except KeyError as exc:
            raise PromptError(f"Prompt '{name}' needs a value for placeholder {exc}") from exc
        except ValueError as exc:
            raise PromptError(f"Prompt '{name}' has a malformed placeholder: {exc}") from exc

    @staticmethod
    def _read(path: Path) -> str:
        """Read a prompt file, rejecting empty files."""
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise PromptError(f"Cannot read prompt file {path}: {exc}") from exc
        if not text:
            raise PromptError(f"Prompt file {path.name} is empty")
        return text
