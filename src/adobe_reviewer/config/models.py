"""Typed, validated representation of ``config/settings.json``.

All models are frozen (immutable) and reject unknown keys, so a typo such as
``"thresolds"`` fails loudly at startup instead of being silently ignored.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Section(BaseModel):
    """Base class for a settings section: immutable, no unknown keys."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class OpenRouterSettings(_Section):
    """Connection settings for the OpenRouter API (the key itself lives in the environment)."""

    base_url: str = "https://openrouter.ai/api/v1"
    api_key_env: str = "OPENROUTER_API_KEY"
    timeout_seconds: float = Field(default=120.0, gt=0)
    max_retries: int = Field(default=3, ge=0, le=10)
    app_title: str | None = "Adobe Stock AI Reviewer"
    app_referer: str | None = None


class AgentSettings(_Section):
    """Configuration of one reviewer agent. Each agent can use a different model."""

    name: str = Field(min_length=1)
    type: str = Field(
        default="adobe_reviewer",
        min_length=1,
        description="Which agent class runs this entry (see agents/registry.py).",
    )
    model: str = Field(min_length=1, description="Any OpenRouter model id, e.g. 'google/gemini-2.5-pro'.")
    prompt: str = Field(min_length=1, description="Prompt set name; files live in prompts/<name>.*.md")
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, gt=0)
    response_format: Literal["json_schema", "json_object", "none"] = Field(
        default="json_object",
        description="How to ask the model for JSON. Use 'none' for models that reject response_format.",
    )


class PipelineSettings(_Section):
    """Which agents run and how. Version 1 implements ``single`` only."""

    mode: Literal["single", "sequential", "parallel_judge"] = "single"
    agents: list[AgentSettings] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_agents(self) -> "PipelineSettings":
        names = [agent.name for agent in self.agents]
        if len(names) != len(set(names)):
            raise ValueError("agent names must be unique")
        if self.mode == "single" and len(self.agents) != 1:
            raise ValueError("pipeline.mode 'single' requires exactly one agent")
        return self


class ThresholdSettings(_Section):
    """Score cut-offs used by the DecisionEngine."""

    ready: int = Field(default=90, ge=0, le=100)
    needs_editing: int = Field(default=75, ge=0, le=100)
    min_confidence: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Below this confidence an otherwise 'ready' image is downgraded to Needs_Editing.",
    )

    @model_validator(mode="after")
    def _validate_order(self) -> "ThresholdSettings":
        if self.ready <= self.needs_editing:
            raise ValueError("thresholds.ready must be greater than thresholds.needs_editing")
        return self


class ImageSettings(_Section):
    """Image handling and Adobe Stock technical limits."""

    max_side_px: int = Field(default=2048, ge=256, description="Longest side of the copy sent to the AI.")
    upload_jpeg_quality: int = Field(default=90, ge=50, le=100)
    extensions: list[str] = Field(default_factory=lambda: [".jpg", ".jpeg", ".png", ".webp"])
    min_megapixels: float = Field(default=4.0, ge=0)  # Adobe Stock minimum is 4 MP
    max_megapixels: float = Field(default=100.0, gt=0)  # Adobe Stock maximum is 100 MP
    reject_below_min_resolution: bool = True

    @field_validator("extensions")
    @classmethod
    def _normalise_extensions(cls, value: list[str]) -> list[str]:
        """Lower-case and ensure each extension starts with a dot."""
        return [ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in value]


class WatcherSettings(_Section):
    """Folder-watching behaviour."""

    poll_interval_seconds: float = Field(default=2.0, gt=0)
    stable_wait_seconds: float = Field(default=2.0, ge=0, description="File size must stay unchanged this long.")
    process_existing_on_start: bool = True
    use_watchdog: bool = Field(default=True, description="Unused: the watcher polls. Kept so older settings files stay valid.")
    failure_retry_cooldown_seconds: float = Field(
        default=300.0,
        ge=0,
        description="Images that failed review stay in Incoming and are retried after this delay.",
    )


class PathSettings(_Section):
    """Folder locations, relative to the project root unless absolute."""

    incoming: str = "images/Incoming"
    needs_editing: str = "images/Needs_Editing"
    ready_to_upload: str = "images/Ready_To_Upload"
    rejected: str = "images/Rejected"
    reports_json: str = "reports/json"
    reports_txt: str = "reports/txt"
    logs: str = "logs"
    prompts: str = "prompts"


class LoggingSettings(_Section):
    """Log file and verbosity."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    file_name: str = "reviewer.log"
    max_bytes: int = Field(default=5 * 1024 * 1024, gt=0)
    backup_count: int = Field(default=5, ge=0)


class Settings(_Section):
    """Root of ``settings.json``."""

    openrouter: OpenRouterSettings = Field(default_factory=OpenRouterSettings)
    pipeline: PipelineSettings
    thresholds: ThresholdSettings = Field(default_factory=ThresholdSettings)
    report_placement: Literal["both", "beside_image", "reports_dir"] = "both"
    image: ImageSettings = Field(default_factory=ImageSettings)
    watcher: WatcherSettings = Field(default_factory=WatcherSettings)
    paths: PathSettings = Field(default_factory=PathSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
