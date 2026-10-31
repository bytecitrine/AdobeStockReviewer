"""Tests for extract_json_object."""

import pytest

from adobe_reviewer.clients.json_utils import extract_json_object
from adobe_reviewer.exceptions import ReviewParseError


def test_plain_json():
    assert extract_json_object('{"a": 1}') == {"a": 1}


def test_markdown_fence_and_chatter():
    text = 'Here is my review:\n```json\n{"a": {"b": [1, 2]}}\n```\nHope this helps!'
    assert extract_json_object(text) == {"a": {"b": [1, 2]}}


def test_think_block_is_ignored():
    text = '<think>maybe {"x": 0} is wrong</think>{"approved": false}'
    assert extract_json_object(text) == {"approved": False}


def test_truncated_json_raises_with_raw_response():
    with pytest.raises(ReviewParseError) as info:
        extract_json_object('{"approved": false, "issues": [{"title": "cut')
    assert "cut" in info.value.raw_response


def test_no_json_raises():
    with pytest.raises(ReviewParseError):
        extract_json_object("I cannot review this image.")
