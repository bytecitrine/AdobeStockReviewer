"""Client for OpenRouter's OpenAI-compatible chat-completions API.

The client knows nothing about Adobe Stock: it sends messages (optionally with an
image) to *whatever model it is told to use* and returns the text plus usage data.
Because the model id is a plain argument, switching models never requires code changes.

Reliability features:
    * Retries with exponential backoff and jitter on timeouts, network errors and
      HTTP 408/429/500/502/503/504 (honouring ``Retry-After``).
    * Detects OpenRouter's "HTTP 200 with an error body" and empty replies.
    * Clear, actionable messages for 400/401/402/403/404.
    * Never logs or includes the API key or image data in messages.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from adobe_reviewer.config.manager import ConfigManager
from adobe_reviewer.config.models import OpenRouterSettings
from adobe_reviewer.exceptions import OpenRouterError

logger = logging.getLogger(__name__)

ResponseFormat = Literal["json_schema", "json_object", "none"]

RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
BACKOFF_BASE_SECONDS = 2.0
BACKOFF_MAX_SECONDS = 30.0
RETRY_AFTER_CAP_SECONDS = 60.0

_STATUS_HINTS: dict[int, str] = {
    400: "bad request. The model may not accept images or this response_format "
    "(try response_format 'none' for this agent in settings.json)",
    401: "invalid or missing API key",
    402: "insufficient OpenRouter credits",
    403: "request blocked (input moderation, or a key/provider restriction)",
    404: "model or endpoint not found. Check the model id, and that the model supports image input",
}


@dataclass(frozen=True)
class ChatResult:
    """A successful model reply plus bookkeeping."""

    content: str
    model: str
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None
    elapsed_seconds: float = 0.0


def build_vision_messages(system_prompt: str, user_text: str, image_data_url: str) -> list[dict[str, Any]]:
    """Build an OpenAI-style message list containing one image.

    Args:
        system_prompt: The fixed instructions.
        user_text: The per-image text.
        image_data_url: ``data:image/jpeg;base64,...`` string.
    """
    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ],
        },
    ]


class OpenRouterClient:
    """Thin, reliable wrapper around ``POST /chat/completions``.

    Settings (base URL, timeout, retries) are read from the live configuration on
    every call, so a config reload takes effect immediately.

    Args:
        config: The application's :class:`ConfigManager`.
        http_client: Optional pre-built ``httpx.Client`` (used by tests).
        sleep: Function used to wait between retries (replaceable in tests).
    """

    def __init__(
        self,
        config: ConfigManager,
        *,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._http = http_client or httpx.Client()
        self._owns_http = http_client is None
        self._sleep = sleep

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def close(self) -> None:
        """Close the underlying HTTP client if this object created it."""
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> "OpenRouterClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        response_format: ResponseFormat = "json_object",
        json_schema: dict[str, Any] | None = None,
    ) -> ChatResult:
        """Send a chat completion request and return the reply.

        Args:
            messages: Chat messages (see :func:`build_vision_messages`).
            model: Any OpenRouter model id.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens in the reply.
            response_format: How to request JSON ("json_schema", "json_object" or "none").
            json_schema: Required when ``response_format`` is "json_schema".

        Raises:
            ConfigError: If the API key is not set.
            OpenRouterError: If the request ultimately fails (after retries).
        """
        settings = self._config.settings.openrouter
        payload = self._build_payload(messages, model, temperature, max_tokens, response_format, json_schema)
        headers = self._build_headers(self._config.get_api_key(), settings)
        url = f"{settings.base_url.rstrip('/')}/chat/completions"
        attempts = settings.max_retries + 1

        for attempt in range(attempts):
            try:
                result = self._send_once(url, headers, payload, settings.timeout_seconds, model)
            except OpenRouterError as exc:
                if not exc.retryable or attempt == attempts - 1:
                    raise
                delay = self._backoff_delay(attempt, exc.retry_after)
                logger.warning(
                    "OpenRouter request failed (attempt %d/%d): %s. Retrying in %.1fs",
                    attempt + 1,
                    attempts,
                    exc,
                    delay,
                )
                self._sleep(delay)
                continue

            logger.info(
                "OpenRouter reply from %s in %.1fs (tokens in/out: %s/%s, cost: %s)",
                result.model,
                result.elapsed_seconds,
                result.prompt_tokens,
                result.completion_tokens,
                f"${result.cost_usd:.4f}" if result.cost_usd is not None else "n/a",
            )
            return result

        raise OpenRouterError("OpenRouter request failed")  # pragma: no cover (loop always returns/raises)

    # ------------------------------------------------------------------ #
    # Request construction
    # ------------------------------------------------------------------ #
    @staticmethod
    def _build_payload(
        messages: list[dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
        response_format: ResponseFormat,
        json_schema: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "usage": {"include": True},  # ask OpenRouter to report the cost
        }
        if response_format == "json_object":
            payload["response_format"] = {"type": "json_object"}
        elif response_format == "json_schema":
            if json_schema is None:
                raise ValueError("json_schema is required when response_format is 'json_schema'")
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "review_verdict", "strict": False, "schema": json_schema},
            }
        return payload

    @staticmethod
    def _build_headers(api_key: str, settings: OpenRouterSettings) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        if settings.app_referer:
            headers["HTTP-Referer"] = settings.app_referer
        if settings.app_title:
            headers["X-Title"] = settings.app_title
        return headers

    # ------------------------------------------------------------------ #
    # One attempt
    # ------------------------------------------------------------------ #
    def _send_once(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
        requested_model: str,
    ) -> ChatResult:
        started = time.monotonic()
        try:
            response = self._http.post(url, headers=headers, json=payload, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise OpenRouterError(f"Request timed out after {timeout:.0f}s", retryable=True) from exc
        except httpx.TransportError as exc:
            raise OpenRouterError(f"Network error: {type(exc).__name__}: {exc}", retryable=True) from exc

        if response.status_code >= 400:
            raise self._error_from_response(response)

        try:
            data = response.json()
        except ValueError as exc:
            raise OpenRouterError("OpenRouter returned a non-JSON response", retryable=True) from exc

        return self._parse_success(data, requested_model, time.monotonic() - started)

    @staticmethod
    def _parse_success(data: Any, requested_model: str, elapsed: float) -> ChatResult:
        """Turn a 200 response body into a :class:`ChatResult`."""
        if not isinstance(data, dict):
            raise OpenRouterError("Unexpected response shape from OpenRouter", retryable=True)

        # OpenRouter can answer HTTP 200 with {"error": {...}} when an upstream provider fails.
        error = data.get("error")
        if error and not data.get("choices"):
            message = error.get("message", "unknown error") if isinstance(error, dict) else str(error)
            code = error.get("code") if isinstance(error, dict) else None
            status = code if isinstance(code, int) else None
            retryable = status is None or status in RETRYABLE_STATUS_CODES
            raise OpenRouterError(f"Provider error: {message}", status_code=status, retryable=retryable)

        choices = data.get("choices") or []
        if not choices:
            raise OpenRouterError("OpenRouter returned no choices", retryable=True)

        choice = choices[0]
        content = (choice.get("message") or {}).get("content")
        if isinstance(content, list):  # some providers return a list of content parts
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if not isinstance(content, str) or not content.strip():
            raise OpenRouterError("The model returned an empty reply", retryable=True)

        finish_reason = choice.get("finish_reason")
        if finish_reason == "length":
            logger.warning("The model reply was cut off (max_tokens reached); increase max_tokens in settings.json")

        usage = data.get("usage") or {}
        return ChatResult(
            content=content,
            model=str(data.get("model") or requested_model),
            finish_reason=finish_reason,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            cost_usd=usage.get("cost"),
            elapsed_seconds=elapsed,
        )

    # ------------------------------------------------------------------ #
    # Errors and backoff
    # ------------------------------------------------------------------ #
    @staticmethod
    def _error_from_response(response: httpx.Response) -> OpenRouterError:
        """Build a descriptive error for an HTTP status >= 400."""
        status = response.status_code
        detail = ""
        try:
            body = response.json()
            error = body.get("error") if isinstance(body, dict) else None
            if isinstance(error, dict):
                detail = str(error.get("message", ""))
        except ValueError:
            detail = response.text[:200]

        hint = _STATUS_HINTS.get(status, "request failed")
        message = f"HTTP {status}: {hint}"
        if detail:
            message += f" ({detail[:300]})"

        retry_after: float | None = None
        raw_retry_after = response.headers.get("Retry-After")
        if raw_retry_after:
            try:
                retry_after = min(float(raw_retry_after), RETRY_AFTER_CAP_SECONDS)
            except ValueError:
                retry_after = None

        return OpenRouterError(
            message,
            status_code=status,
            retryable=status in RETRYABLE_STATUS_CODES,
            retry_after=retry_after,
        )

    @staticmethod
    def _backoff_delay(attempt: int, retry_after: float | None) -> float:
        """Exponential backoff with jitter; never shorter than the server's Retry-After."""
        delay = min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * (2**attempt))
        delay *= random.uniform(0.8, 1.2)
        if retry_after is not None:
            delay = max(delay, retry_after)
        return delay
