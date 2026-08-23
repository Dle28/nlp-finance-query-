"""Deterministic hierarchy-aware scoring for retrieval candidates.

The score is deliberately a candidate-discovery signal.  It may reorder the
bounded lexical/dense pool, but it never selects a source cell or authorizes an
operand.  Only source-derived table structure is inspected.
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Mapping


HIERARCHY_SCORING_PROTOCOL = "vifinqa_hierarchy_candidate_score_v1"
_TOKEN_RE = re.compile(r"[a-z0-9%]+", re.IGNORECASE)
_STOPWORDS = frozenset(
    {
        "bao",
        "cua",
        "cho",
        "co",
        "da",
        "duoc",
        "gi",
        "la",
        "nam",
        "nao",
        "nhu",
        "tai",
        "the",
        "theo",
        "trong",
        "va",
        "voi",
    }
)


def _fold(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(character for character in normalized if not unicodedata.combining(character))


def _tokens(value: object) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall(_fold(value))
        if len(token) > 1 and token not in _STOPWORDS
    }


def _json_value(value: object, default: object) -> object:
    if not isinstance(value, str):
        return value if value is not None else default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _mapping_text(value: object) -> str:
    mapping = _json_value(value, {})
    if not isinstance(mapping, Mapping):
        return ""
    return " ".join(
        str(mapping.get(key) or "")
        for key in ("kind", "label", "title", "source_title", "statement_type")
    )


def _row_label_text(value: object) -> str:
    rows = _json_value(value, [])
    if not isinstance(rows, list):
        return ""
    labels: list[str] = []
    for row in rows:
        if not isinstance(row, list):
            continue
        # Financial row labels can span the first two columns after HTML span
        # expansion.  Numeric cells are intentionally excluded here.
        for cell in row[:2]:
            text = str(cell or "").strip()
            if text and not any(character.isdigit() for character in text):
                labels.append(text)
    return " ".join(labels)


def hierarchy_candidate_score(question: str, asset: Mapping[str, Any]) -> float:
    """Return a bounded structural relevance score in ``[0, 1]``.

    This is a soft rank feature only.  A zero score never removes a candidate.
    """

    query = _tokens(question)
    if not query:
        return 0.0
    fields = (
        (_mapping_text(asset.get("table_function_json") or asset.get("table_function")), 3.0),
        (_mapping_text(asset.get("table_section_json") or asset.get("table_section")), 3.0),
        (_mapping_text(asset.get("table_purpose_json") or asset.get("table_purpose")), 2.0),
        (_json_value(asset.get("headers_json") or asset.get("headers"), []), 2.0),
        (_row_label_text(asset.get("rows_json") or asset.get("rows")), 1.0),
    )
    weighted_overlap = 0.0
    weight_total = 0.0
    for text, weight in fields:
        field_tokens = _tokens(text)
        if not field_tokens:
            continue
        weighted_overlap += weight * (len(query & field_tokens) / len(query))
        weight_total += weight
    return min(1.0, weighted_overlap / weight_total) if weight_total else 0.0


def rank_hierarchy_candidates(
    question: str,
    assets: Mapping[str, Mapping[str, Any]],
) -> list[tuple[str, float]]:
    """Rank only the caller's already-bounded candidate pool."""

    scored = [
        (uid, hierarchy_candidate_score(question, asset))
        for uid, asset in assets.items()
    ]
    return sorted(
        ((uid, score) for uid, score in scored if score > 0),
        key=lambda item: (-item[1], item[0]),
    )

