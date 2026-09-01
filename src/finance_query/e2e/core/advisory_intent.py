"""High-precision Vietnamese advisory-intent abstention policy."""

from __future__ import annotations

import re
import unicodedata


_EXACT_ADVISORY_ACTION_RE = re.compile(r"\bco nen\s+(?:mua|ban|giu|dau tu)\b")


def _ascii_fold(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text.casefold().replace("đ", "d"))
    value = "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Mn"
    )
    return re.sub(r"\s+", " ", value).strip()


def is_explicit_single_entity_advisory(question: str, *, entity_count: int) -> bool:
    """Return true only for the frozen single-entity ``có nên`` action form.

    This policy does not answer the advisory question. It routes the request
    out of the numeric financial-report planner before family inference,
    retrieval, or arithmetic can fabricate a numeric program.
    """
    if entity_count != 1:
        return False
    return _EXACT_ADVISORY_ACTION_RE.search(_ascii_fold(question)) is not None
