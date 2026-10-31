"""Shared data models for the Adobe Stock AI Reviewer.

Design notes:
    * :class:`ReviewVerdict` is the *contract* the AI model must return (JSON only).
      It is deliberately lenient when parsing (e.g. 87.4 -> 87, "critical" or
      "Critical" -> Severity.CRITICAL) because LLM output is rarely perfectly typed.
    * :class:`ReviewReport` is the verdict plus metadata added by Python (file name,
      timestamp, models used, local measurements, routing decision). This is what
      gets saved as ``<name>_review.json``.
    * :class:`ReviewContext` / :class:`AgentResult` are the hand-off objects between
      agents, so Version 2 can chain Technical -> Commercial -> Judge without changes
      to the engine.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class _LenientEnum(str, Enum):
    """String enum that normalises input and falls back instead of raising.

    Subclasses override :meth:`_fallback` to say what an unrecognised value maps to.
    """

    @classmethod
    def _missing_(cls, value: object) -> "_LenientEnum | None":
        if not isinstance(value, str):
            return None
        normalized = value.strip().lower().replace(" ", "_").replace("-", "_")
        for member in cls:
            if member.value == normalized:
                return member
        return cls._fallback()

    @classmethod
    def _fallback(cls) -> "_LenientEnum | None":
        return None


class Severity(_LenientEnum):
    """How serious an issue is for Adobe Stock acceptance."""

    CRITICAL = "critical"  # almost certain rejection
    MAJOR = "major"  # likely rejection
    MINOR = "minor"  # may be tolerated, hurts the score
    INFO = "info"  # suggestion only

    @classmethod
    def _fallback(cls) -> "Severity":
        return cls.MINOR

    @property
    def rank(self) -> int:
        """Sort key: 0 for the most severe level, 3 for the least."""
        return list(type(self)).index(self)


class IssueCategory(_LenientEnum):
    """What aspect of the image an issue relates to."""

    TECHNICAL = "technical"
    SHARPNESS = "sharpness"
    FOCUS = "focus"
    RESOLUTION = "resolution"
    NOISE = "noise"
    COMPRESSION = "compression"
    AI_ARTIFACTS = "ai_artifacts"
    LIGHTING = "lighting"
    COMPOSITION = "composition"
    COLOR = "color"
    COPY_SPACE = "copy_space"
    COMMERCIAL = "commercial"
    INTELLECTUAL_PROPERTY = "intellectual_property"
    LOGO = "logo"
    TRADEMARK = "trademark"
    FACE = "face"
    TEXT = "text"
    WATERMARK = "watermark"
    STOCK_DEMAND = "stock_demand"
    OTHER = "other"

    @classmethod
    def _fallback(cls) -> "IssueCategory":
        return cls.OTHER


class Destination(str, Enum):
    """Where an image is routed after review. Values are the folder names."""

    READY_TO_UPLOAD = "Ready_To_Upload"
    NEEDS_EDITING = "Needs_Editing"
    REJECTED = "Rejected"


# --------------------------------------------------------------------------- #
# Local (Python-measured) image facts
# --------------------------------------------------------------------------- #
class LocalMetrics(BaseModel):
    """Objective measurements computed locally, not guessed by the AI."""

    width: int
    height: int
    megapixels: float
    aspect_ratio: float
    file_size_bytes: int
    image_format: str | None = None
    color_mode: str | None = None
    has_alpha: bool = False
    sharpness_score: float | None = None  # e.g. variance of Laplacian (higher = sharper)
    noise_level: float | None = None  # estimated noise sigma (higher = noisier)
    jpeg_quality_estimate: int | None = None  # 1-100, JPEG only


# --------------------------------------------------------------------------- #
# The AI contract
# --------------------------------------------------------------------------- #
class Issue(BaseModel):
    """One problem found in the image."""

    model_config = ConfigDict(extra="ignore")

    category: IssueCategory = IssueCategory.OTHER
    severity: Severity = Severity.MINOR
    title: str
    explanation: str = ""
    correction: str = ""
    hard_fail: bool = Field(
        default=False,
        description="True if this issue alone makes Adobe Stock reject the image "
        "(e.g. visible trademark, watermark, recognisable real person).",
    )


class ReviewVerdict(BaseModel):
    """The JSON object an AI reviewer must return. Scores are 0-100."""

    model_config = ConfigDict(extra="ignore")

    approved: bool
    overall_score: int = Field(ge=0, le=100)
    commercial_value: int = Field(ge=0, le=100)
    technical_quality: int = Field(ge=0, le=100)
    issues: list[Issue] = Field(default_factory=list)
    recommended_fixes: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str = ""

    # ---- lenient coercion of typical LLM sloppiness ----
    @field_validator("overall_score", "commercial_value", "technical_quality", mode="before")
    @classmethod
    def _coerce_score(cls, value: Any) -> Any:
        """Accept 87.4, "87", "87%" and round to an int."""
        if isinstance(value, bool):
            raise ValueError("score must be a number, not a boolean")
        if isinstance(value, str):
            try:
                value = float(value.strip().rstrip("%"))
            except ValueError as exc:
                raise ValueError(f"score {value!r} is not numeric") from exc
        if isinstance(value, (int, float)):
            return int(round(value))
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, value: Any) -> Any:
        """Accept 0.94, "0.94", 94 and "94%" (treated as a percentage)."""
        if isinstance(value, bool):
            raise ValueError("confidence must be a number, not a boolean")
        if isinstance(value, str):
            try:
                value = float(value.strip().rstrip("%"))
            except ValueError as exc:
                raise ValueError(f"confidence {value!r} is not numeric") from exc
        if isinstance(value, (int, float)) and 1 < value <= 100:
            return value / 100
        return value

    @field_validator("issues", mode="before")
    @classmethod
    def _coerce_issues(cls, value: Any) -> Any:
        """Accept null, and plain strings in place of issue objects."""
        if value is None:
            return []
        if isinstance(value, list):
            return [{"title": item} if isinstance(item, str) else item for item in value]
        return value

    @field_validator("recommended_fixes", mode="before")
    @classmethod
    def _coerce_fixes(cls, value: Any) -> Any:
        """Accept null, a single string, and objects in place of strings."""
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [item if isinstance(item, str) else json.dumps(item, ensure_ascii=False) for item in value]
        return value

    @property
    def has_hard_fail(self) -> bool:
        """True if any issue is flagged as an automatic rejection."""
        return any(issue.hard_fail for issue in self.issues)


# --------------------------------------------------------------------------- #
# Decision + final saved report
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Decision:
    """Routing outcome produced by the DecisionEngine."""

    destination: Destination
    reasons: tuple[str, ...] = ()


class ReviewReport(ReviewVerdict):
    """The verdict plus Python-side metadata. Saved as ``<name>_review.json``.

    It is a strict superset of the example JSON in the project brief.
    """

    image_file: str | None = None
    reviewed_at: datetime | None = None
    reviewed_by: list[str] = Field(default_factory=list, description="Agent names that contributed.")
    models_used: list[str] = Field(default_factory=list)
    local_metrics: LocalMetrics | None = None
    destination: Destination | None = None
    decision_reasons: list[str] = Field(default_factory=list)

    @classmethod
    def from_verdict(
        cls,
        verdict: ReviewVerdict,
        *,
        image_file: str,
        reviewed_by: list[str],
        models_used: list[str],
        local_metrics: LocalMetrics | None = None,
    ) -> "ReviewReport":
        """Wrap an AI verdict with metadata (timestamp is set to now, UTC)."""
        return cls(
            **verdict.model_dump(),
            image_file=image_file,
            reviewed_at=datetime.now(timezone.utc),
            reviewed_by=reviewed_by,
            models_used=models_used,
            local_metrics=local_metrics,
        )

    def with_decision(self, decision: Decision) -> "ReviewReport":
        """Return a copy that records where the image was routed and why."""
        return self.model_copy(
            update={"destination": decision.destination, "decision_reasons": list(decision.reasons)}
        )


# --------------------------------------------------------------------------- #
# Multi-agent hand-off objects
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AgentResult:
    """What a single agent produced, plus bookkeeping for logs and cost tracking."""

    agent_name: str
    model: str
    verdict: ReviewVerdict
    raw_response: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None
    elapsed_seconds: float = 0.0


@dataclass
class ReviewContext:
    """Everything an agent may need to review one image.

    ``previous_results`` lets later agents (Version 2's Final Judge) read the
    findings of earlier ones, keyed by agent name.
    """

    image_path: Path
    image_data_url: str  # "data:image/jpeg;base64,..." (already resized for the API)
    metrics: LocalMetrics | None = None
    metrics_block: str = ""  # prompt-ready measurements text (engine fills it; includes the downscale note)
    previous_results: dict[str, AgentResult] = field(default_factory=dict)
