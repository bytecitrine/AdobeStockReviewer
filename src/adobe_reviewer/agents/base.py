"""Base class for reviewer agents.

An *agent* is one AI reviewer: it turns a :class:`ReviewContext` (image + measurements
+ earlier agents' findings) into an :class:`AgentResult` containing a validated
:class:`ReviewVerdict`.

All the hard parts live here once (calling the model, extracting and validating the
JSON, one automatic repair retry when the model replies badly, token/cost tracking),
so a concrete agent only has to say *which prompts to send*. Version 2's Technical
Inspector, Commercial Inspector and Final Judge are therefore tiny subclasses.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod

from pydantic import ValidationError

from adobe_reviewer.analysis.metrics_formatter import format_metrics_block
from adobe_reviewer.clients.json_utils import extract_json_object
from adobe_reviewer.clients.openrouter import OpenRouterClient, build_vision_messages
from adobe_reviewer.config.manager import ConfigManager
from adobe_reviewer.config.models import AgentSettings
from adobe_reviewer.exceptions import ConfigError, ReviewParseError
from adobe_reviewer.models.schemas import AgentResult, ReviewContext, ReviewVerdict
from adobe_reviewer.prompts.manager import PromptManager

logger = logging.getLogger(__name__)

NO_MEASUREMENTS_TEXT = "- (no local measurements are available for this image)"
MAX_REPAIR_TOKENS = 16384


def parse_verdict(text: str) -> ReviewVerdict:
    """Turn a model reply into a validated :class:`ReviewVerdict`.

    Also accepts the verdict wrapped inside one extra object (for example
    ``{"review": {...}}``), which some models like to do.

    Raises:
        ReviewParseError: If the reply has no JSON, or the JSON does not match the schema.
    """
    data = extract_json_object(text)
    if "overall_score" not in data:
        for value in data.values():
            if isinstance(value, dict) and "overall_score" in value:
                data = value
                break
    try:
        return ReviewVerdict.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:6])
        raise ReviewParseError(f"The JSON does not match the required format ({problems})", raw_response=text) from exc


def _sum_optional(first: int | float | None, second: int | float | None) -> int | float | None:
    """Add two optional numbers; ``None`` only if both are ``None``."""
    if first is None and second is None:
        return None
    return (first or 0) + (second or 0)


class BaseAgent(ABC):
    """Template for every reviewer agent.

    Subclasses implement :meth:`build_prompts`. Settings (model, temperature, prompt
    name) are looked up by agent name from the live configuration on every run, so
    editing ``settings.json`` changes the model without restarting.

    Args:
        name: The agent's name as written in ``settings.json`` (``pipeline.agents[].name``).
        config: The application's :class:`ConfigManager`.
        client: The :class:`OpenRouterClient` used to call the model.
        prompts: The :class:`PromptManager` used to load prompt files.
    """

    #: How many times a malformed reply is sent back to the model for correction.
    MAX_REPAIR_ATTEMPTS = 1

    def __init__(self, name: str, *, config: ConfigManager, client: OpenRouterClient, prompts: PromptManager) -> None:
        self.name = name
        self._config = config
        self._client = client
        self._prompts = prompts

    # ------------------------------------------------------------------ #
    # What subclasses provide
    # ------------------------------------------------------------------ #
    @abstractmethod
    def build_prompts(self, context: ReviewContext, settings: AgentSettings) -> tuple[str, str]:
        """Return ``(system_prompt, user_text)`` for this agent and image."""

    # ------------------------------------------------------------------ #
    # Shared behaviour
    # ------------------------------------------------------------------ #
    @property
    def settings(self) -> AgentSettings:
        """This agent's current settings (re-read from the live configuration)."""
        for agent in self._config.settings.pipeline.agents:
            if agent.name == self.name:
                return agent
        raise ConfigError(f"Agent '{self.name}' is not defined in settings.json (pipeline.agents)")

    def run(self, context: ReviewContext) -> AgentResult:
        """Review one image.

        Raises:
            OpenRouterError: If the API call fails after retries.
            ReviewParseError: If the model never produced a valid verdict.
            PromptError: If the prompt files are missing or malformed.
        """
        settings = self.settings
        system_prompt, user_text = self.build_prompts(context, settings)
        return self._query(context, settings, system_prompt, user_text)

    @staticmethod
    def metrics_text(context: ReviewContext) -> str:
        """The measurements block for prompts (engine-provided text, else formatted here)."""
        if context.metrics_block:
            return context.metrics_block
        if context.metrics is not None:
            return format_metrics_block(context.metrics)
        return NO_MEASUREMENTS_TEXT

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _query(self, context: ReviewContext, settings: AgentSettings, system_prompt: str, user_text: str) -> AgentResult:
        """Call the model; if the reply is unusable, ask it to correct itself (bounded)."""
        messages = build_vision_messages(system_prompt, user_text, context.image_data_url)
        json_schema = ReviewVerdict.model_json_schema() if settings.response_format == "json_schema" else None
        max_tokens = settings.max_tokens

        started = time.monotonic()
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        cost: float | None = None
        last_error: ReviewParseError | None = None

        for attempt in range(self.MAX_REPAIR_ATTEMPTS + 1):
            chat = self._client.complete(
                messages=messages,
                model=settings.model,
                temperature=settings.temperature,
                max_tokens=max_tokens,
                response_format=settings.response_format,
                json_schema=json_schema,
            )
            prompt_tokens = _sum_optional(prompt_tokens, chat.prompt_tokens)
            completion_tokens = _sum_optional(completion_tokens, chat.completion_tokens)
            cost = _sum_optional(cost, chat.cost_usd)

            try:
                verdict = parse_verdict(chat.content)
            except ReviewParseError as exc:
                last_error = exc
                if attempt == self.MAX_REPAIR_ATTEMPTS:
                    break
                logger.warning("Agent '%s' gave an unusable reply (%s). Asking it to correct itself.", self.name, exc)
                messages = messages + [
                    {"role": "assistant", "content": chat.content},
                    {"role": "user", "content": self._repair_request(exc)},
                ]
                if chat.finish_reason == "length":  # reply was cut off: allow a longer one
                    max_tokens = min(max_tokens * 2, MAX_REPAIR_TOKENS)
                continue

            logger.info(
                "Agent '%s' verdict: score %d, approved=%s, %d issue(s), confidence %.2f",
                self.name,
                verdict.overall_score,
                verdict.approved,
                len(verdict.issues),
                verdict.confidence,
            )
            return AgentResult(
                agent_name=self.name,
                model=chat.model,
                verdict=verdict,
                raw_response=chat.content,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=cost,
                elapsed_seconds=time.monotonic() - started,
            )

        assert last_error is not None  # the loop only exits here after a parse failure
        raise last_error

    @staticmethod
    def _repair_request(error: ReviewParseError) -> str:
        """The message sent back to the model after an unusable reply."""
        return (
            f"Your previous reply could not be used: {str(error)[:300]}. "
            "Reply again with ONE valid JSON object only, in exactly the required format, "
            "with no markdown, no code fences and no other text."
        )
