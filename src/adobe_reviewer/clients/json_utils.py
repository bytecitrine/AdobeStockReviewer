"""Helpers for turning a model's text reply into a Python dict."""

from __future__ import annotations

import json
import re
from typing import Any

from adobe_reviewer.exceptions import ReviewParseError

# Some reasoning models wrap their thoughts in <think>...</think> before the answer.
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def extract_json_object(text: str) -> dict[str, Any]:
    """Extract the first JSON object from ``text``.

    Tolerates the usual model habits: markdown code fences, a sentence before or
    after the JSON, and ``<think>`` blocks. The JSON itself must be valid.

    Raises:
        ReviewParseError: If no valid JSON object can be found (including when the
            reply was cut off mid-way).
    """
    cleaned = _THINK_BLOCK.sub("", text or "")
    start = cleaned.find("{")
    if start == -1:
        raise ReviewParseError("The model reply contains no JSON object", raw_response=text)

    try:
        value, _ = json.JSONDecoder().raw_decode(cleaned, start)
    except json.JSONDecodeError as exc:
        raise ReviewParseError(f"The model reply is not valid JSON: {exc}", raw_response=text) from exc

    if not isinstance(value, dict):
        raise ReviewParseError("The model reply JSON is not an object", raw_response=text)
    return value
