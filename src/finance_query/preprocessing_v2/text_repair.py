"""Auditable text cleanup for a canonical view of immutable source text.

This module deliberately avoids dictionary-based spelling correction.  Only
Unicode/HTML/whitespace repairs and explicitly configured OCR replacements are
applied.  Every change is returned to the caller for the repair ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import html
import re
import unicodedata
from typing import Any, Iterable

from bs4 import BeautifulSoup


ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")
SPACE_RE = re.compile(r"\s+")
PAGE_RE = re.compile(r"={3,}\s*PAGE\s*\d+\s*={3,}", re.IGNORECASE)
# Avoid flagging valid Vietnamese strings such as ``LÃI`` or ``ÂN``.  These
# sequences are characteristic of an extra UTF-8/Latin-1 decode layer.
MOJIBAKE_RE = re.compile(r"(?:Ãƒ|Ã‚|Â |â€|â€™|â€œ|â€\x9d|\ufffd)")
NUMERIC_TOKEN_RE = re.compile(
    r"(?<!\w)[+-]?(?:\d{1,3}(?:[.,]\d{3})+|\d+)(?:[.,]\d+)?%?(?!\w)"
)


@dataclass(frozen=True, slots=True)
class RepairRule:
    rule_id: str
    source: str
    replacement: str
    confidence: float

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "RepairRule":
        return cls(
            rule_id=str(value["id"]),
            source=str(value["source"]),
            replacement=str(value["replacement"]),
            confidence=float(value.get("confidence", 1.0)),
        )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def numeric_tokens(value: str) -> list[str]:
    return NUMERIC_TOKEN_RE.findall(value)


def _event(kind: str, before: str, after: str, **extra: Any) -> dict[str, Any]:
    return {
        "repair_type": kind,
        "before_sha256": sha256_text(before),
        "after_sha256": sha256_text(after),
        "before_excerpt": before[:180],
        "after_excerpt": after[:180],
        "canonical_only": True,
        **extra,
    }


def _strip_controls(value: str) -> str:
    return "".join(
        character
        for character in value
        if character in "\n\r\t" or unicodedata.category(character) != "Cc"
    )


def _has_joined_words(value: str) -> bool:
    """Detect a lowercase-to-uppercase boundary without unreliable ranges."""
    return any(left.islower() and right.isupper() for left, right in zip(value, value[1:]))


def normalize_canonical_text(
    value: object,
    *,
    rules: Iterable[RepairRule] = (),
    strip_html: bool = False,
    strip_page_markers: bool = False,
) -> tuple[str, list[dict[str, Any]], list[str]]:
    """Return canonical text, repair events and unresolved-quality flags.

    Numeric tokens are an invariant.  A configured rule that changes them is
    not applied and is reported as ``numeric_mutation_blocked``.
    """
    current = str(value or "")
    events: list[dict[str, Any]] = []
    flags: list[str] = []

    normalized = unicodedata.normalize("NFC", current)
    if normalized != current:
        events.append(_event("unicode_nfc", current, normalized, confidence=1.0))
        current = normalized

    decoded = html.unescape(current)
    if strip_html:
        decoded = BeautifulSoup(decoded, "lxml").get_text(" ", strip=True)
    if decoded != current:
        events.append(
            _event(
                "html_to_text" if strip_html else "html_entity_decode",
                current,
                decoded,
                confidence=1.0,
            )
        )
        current = decoded

    cleaned = ZERO_WIDTH_RE.sub("", _strip_controls(current))
    if cleaned != current:
        events.append(_event("control_character_removal", current, cleaned, confidence=1.0))
        current = cleaned

    if strip_page_markers:
        cleaned = PAGE_RE.sub(" ", current)
        if cleaned != current:
            events.append(_event("page_marker_removal", current, cleaned, confidence=1.0))
            current = cleaned

    collapsed = SPACE_RE.sub(" ", current).strip()
    if collapsed != current:
        events.append(_event("whitespace_collapse", current, collapsed, confidence=1.0))
        current = collapsed

    for rule in rules:
        if rule.source not in current:
            continue
        candidate = current.replace(rule.source, rule.replacement)
        if numeric_tokens(candidate) != numeric_tokens(current):
            flags.append("numeric_mutation_blocked")
            continue
        events.append(
            _event(
                "allowlisted_ocr_repair",
                current,
                candidate,
                rule_id=rule.rule_id,
                confidence=rule.confidence,
            )
        )
        current = candidate

    if _has_joined_words(current):
        flags.append("possible_concatenated_words")
    if MOJIBAKE_RE.search(current):
        flags.append("possible_mojibake")
    return current, events, sorted(set(flags))
