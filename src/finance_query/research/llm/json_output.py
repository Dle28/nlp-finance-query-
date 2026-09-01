"""Strict, portable parsing for one JSON object emitted by a model.

The Qwen adapter and the standalone Kaggle feedback worker both consume model
text.  They must not silently select the first object from an ambiguous
response: a second object can contain an instruction, a different decision,
or an attempted authority upgrade.  This module has no project imports so it
can be copied into a Kaggle input dataset and used there as a small runtime
dependency.
"""

from __future__ import annotations

import json
from typing import Any


MAX_JSON_OUTPUT_CHARS = 64_000


def _reject_non_standard_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def _remove_complete_json_fences(text: str) -> str:
    """Remove only complete line-based ``json`` fences.

    Open-weight models occasionally wrap a valid object in a markdown fence.
    The model contract prefers plain JSON, but accepting a complete
    `````json`` wrapper is a safe compatibility measure.  Unsupported or
    unclosed fences remain invalid instead of being guessed at.
    """

    lines = text.splitlines(keepends=True)
    if not any(line.strip().startswith("```") for line in lines):
        return text

    output: list[str] = []
    fence_count = 0
    for line in lines:
        content = line.rstrip("\r\n")
        stripped = content.strip()
        if not stripped.startswith("```"):
            output.append(line)
            continue
        language = stripped[3:].strip()
        if language.casefold() not in {"", "json"}:
            raise ValueError("unsupported markdown code fence; expected json")
        fence_count += 1
        output.append(" " * len(content) + line[len(content) :])

    if fence_count % 2:
        raise ValueError("unclosed markdown code fence")
    return "".join(output)


def _object_spans(text: str) -> list[tuple[int, int, dict[str, Any]]]:
    """Find every complete JSON-object span, including nested objects."""

    decoder = json.JSONDecoder(parse_constant=_reject_non_standard_json_constant)
    spans: list[tuple[int, int, dict[str, Any]]] = []
    for start, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, relative_end = decoder.raw_decode(text[start:])
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(value, dict):
            spans.append((start, start + relative_end, value))
    return spans


def _outermost_spans(
    spans: list[tuple[int, int, dict[str, Any]]],
) -> list[tuple[int, int, dict[str, Any]]]:
    outermost: list[tuple[int, int, dict[str, Any]]] = []
    for candidate in spans:
        start, end, _ = candidate
        if any(
            other_start <= start
            and end <= other_end
            and (other_start, other_end) != (start, end)
            for other_start, other_end, _ in spans
        ):
            continue
        outermost.append(candidate)
    return sorted(outermost, key=lambda item: item[0])


def parse_one_json_object(raw_response: object) -> dict[str, Any]:
    """Parse exactly one JSON object from model output.

    Leading/trailing plain prose is tolerated for compatibility with small
    models.  Multiple objects, malformed brace-delimited output, arrays,
    unsupported fences and non-standard JSON constants are rejected.  This is
    intentionally a generic parser; the feedback module performs the stricter
    domain schema and authority validation afterwards.
    """

    if not isinstance(raw_response, str):
        raise ValueError("LLM output must be a JSON text string")
    if len(raw_response) > MAX_JSON_OUTPUT_CHARS:
        raise ValueError("LLM output is too long")
    text = raw_response.strip()
    if not text:
        raise ValueError("LLM output is empty")
    text = _remove_complete_json_fences(text)
    spans = _outermost_spans(_object_spans(text))
    if not spans:
        raise ValueError("LLM did not return a JSON object")
    if len(spans) != 1:
        raise ValueError("LLM output must contain exactly one JSON object")

    start, end, value = spans[0]
    outside = text[:start] + text[end:]
    if any(character in outside for character in "{}[]"):
        raise ValueError("LLM output contains extra structured delimiters")
    return value


__all__ = ["MAX_JSON_OUTPUT_CHARS", "parse_one_json_object"]
