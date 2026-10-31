"""Absolute project paths resolved from the relative values in ``settings.json``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from adobe_reviewer.config.models import PathSettings
from adobe_reviewer.models.schemas import Destination


def _resolve(root: Path, value: str) -> Path:
    """Resolve ``value`` against ``root`` unless it is already absolute."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else (root / path).resolve()


@dataclass(frozen=True)
class ProjectPaths:
    """All folders the application reads from or writes to, as absolute paths."""

    root: Path
    incoming: Path
    needs_editing: Path
    ready_to_upload: Path
    rejected: Path
    reports_json: Path
    reports_txt: Path
    logs: Path
    prompts: Path

    @classmethod
    def from_settings(cls, root: Path, settings: PathSettings) -> "ProjectPaths":
        """Build absolute paths from the ``paths`` section of the settings."""
        return cls(
            root=root,
            incoming=_resolve(root, settings.incoming),
            needs_editing=_resolve(root, settings.needs_editing),
            ready_to_upload=_resolve(root, settings.ready_to_upload),
            rejected=_resolve(root, settings.rejected),
            reports_json=_resolve(root, settings.reports_json),
            reports_txt=_resolve(root, settings.reports_txt),
            logs=_resolve(root, settings.logs),
            prompts=_resolve(root, settings.prompts),
        )

    def destination_dir(self, destination: Destination) -> Path:
        """Return the folder an image routed to ``destination`` is moved into."""
        mapping = {
            Destination.READY_TO_UPLOAD: self.ready_to_upload,
            Destination.NEEDS_EDITING: self.needs_editing,
            Destination.REJECTED: self.rejected,
        }
        return mapping[destination]

    def all_directories(self) -> tuple[Path, ...]:
        """Every folder that must exist for the application to run."""
        return (
            self.incoming,
            self.needs_editing,
            self.ready_to_upload,
            self.rejected,
            self.reports_json,
            self.reports_txt,
            self.logs,
            self.prompts,
        )

    def ensure(self) -> None:
        """Create any missing folders (idempotent)."""
        for directory in self.all_directories():
            directory.mkdir(parents=True, exist_ok=True)
