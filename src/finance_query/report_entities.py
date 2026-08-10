"""Source-bound report-entity normalization.

Financial-report OCR frequently repeats a company name in table bodies (for
example as a customer, vendor, or related party).  Such occurrences must not
be used to decide which issuer a question concerns.  This module extracts an
entity only when it is present at the *start of a source page*, before the
report-title marker.  The resulting sidecar is navigation metadata: it is not
numeric evidence and cannot train or promote a review label.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from bs4 import BeautifulSoup

from .financial_metrics import fold_text
from .report_segments import PAGE_RE
from .table_structure import normalize_space, sha256_file


REPORT_ENTITY_VERSION = 1
REPORT_ENTITY_POLICY = "source_page_initial_entity_before_report_marker_v1"
REPORT_ENTITY_RESOLUTION_POLICY = "unique_source_title_entity_alias_question_match_v1"
MAX_SOURCE_TITLE_CHARS = 240

# An issuer title must begin with an explicit organisation marker.  This keeps
# a company mentioned halfway through a table from becoming an entity alias.
ENTITY_START_RE = re.compile(
    r"^\s*(?:"
    r"c[oô]ng\s+ty\s+(?:c[oổ]\s+phần|tr[aá]ch\s+nhiệm\s+hữu\s+hạn|tnhh|liên\s+doanh)"
    r"|t[oổ]ng\s+c[oô]ng\s+ty|t[âa]p\s+đo[aà]n|ng[aâ]n\s+h[aà]ng|quỹ)\b",
    re.IGNORECASE,
)
REPORT_MARKER_RE = re.compile(
    r"\b(?:thuyết\s+minh|báo\s+cáo|bảng\s+cân\s+đối|m[ẫaãâ]u\s+số|b\s*0?\d{1,2}\s*[-–])\b",
    re.IGNORECASE,
)
ADDRESS_BREAK_RE = re.compile(
    r"\s+\b(?:số\s+\d|tầng\s+\d|tang\s+\d|lô\s+\d|tòa\s+|toa\s+|phường\s+|quận\s+|địa\s+chỉ\b)",
    re.IGNORECASE,
)
TOKEN_RE = re.compile(r"[a-z0-9]+")
CORPORATE_ABBREVIATIONS = {
    "ctcp": "công ty cổ phần",
    "cty cp": "công ty cổ phần",
    "tcp": "tổng công ty",
    "tct": "tổng công ty",
    "tnhh": "trách nhiệm hữu hạn",
    "tmcp": "thương mại cổ phần",
}
LEGAL_FORM_TOKENS = {
    "cong",
    "ty",
    "co",
    "phan",
    "trach",
    "nhiem",
    "huu",
    "han",
    "tong",
    "tap",
    "doan",
    "ngan",
    "hang",
    "thuong",
    "mai",
    "quy",
}


def _source_page_text(raw_context: object) -> str:
    """Read only the current source page; never join preceding pages."""
    page = PAGE_RE.split(str(raw_context or ""))[-1]
    return normalize_space(BeautifulSoup(page, "lxml").get_text(" ", strip=True))


def source_report_entity(raw_context: object) -> str:
    """Return a literal issuer title only from the safe title zone.

    A marker is required after the initial organisation name.  If OCR starts
    midway through a page, or the page has only transaction text, extraction
    fails closed and returns an empty string.
    """
    page = _source_page_text(raw_context)
    if not ENTITY_START_RE.match(page):
        return ""
    marker = REPORT_MARKER_RE.search(page)
    if marker is None or marker.start() <= 0 or marker.start() > MAX_SOURCE_TITLE_CHARS:
        return ""
    entity = normalize_space(page[: marker.start()]).strip(" -–:;,")
    # Report mastheads can place the office address on the same OCR line as
    # the issuer.  Address text is not part of the entity alias and creates
    # brittle false negatives, so cut only at unambiguous address starters.
    address = ADDRESS_BREAK_RE.search(entity)
    if address is not None:
        entity = entity[: address.start()].strip(" -–:;,")
    return entity[:MAX_SOURCE_TITLE_CHARS] if ENTITY_START_RE.match(entity) else ""


def canonical_entity_name(value: object) -> str:
    """Canonicalise legal-form spelling only; preserve issuer name tokens.

    This is intentionally not fuzzy matching.  ``CTCP`` and ``Công ty Cổ
    phần`` are the same legal-form notation, so expanding/removing those
    tokens lets an exact issuer phrase match while retaining the distinctive
    name (for example ``cảng hàng không việt nam``).
    """
    text = str(value or "")
    for abbreviated, expanded in CORPORATE_ABBREVIATIONS.items():
        text = re.sub(rf"\b{re.escape(abbreviated)}\b", expanded, text, flags=re.IGNORECASE)
    tokens = [
        token
        for token in TOKEN_RE.findall(fold_text(text))
        if token not in LEGAL_FORM_TOKENS
    ]
    return " ".join(tokens)


def build_report_entity_aliases(
    tables: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Build one source-derived alias record per literal document title.

    The raw context digest links the alias to the precise source page from
    which it was observed.  Distinct title spellings are intentionally kept;
    the resolver later accepts a question only if all matching aliases imply
    exactly one ticker.
    """
    aliases: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for table in tables:
        ticker = str(table.get("ticker") or "")
        document_id = str(table.get("document_id") or "")
        raw_context = str(table.get("context_before") or "")
        entity = source_report_entity(raw_context)
        canonical = canonical_entity_name(entity)
        if not ticker or not document_id or len(canonical.split()) < 3:
            continue
        key = (ticker, document_id, canonical)
        if key in seen:
            continue
        seen.add(key)
        aliases.append(
            {
                "schema_version": REPORT_ENTITY_VERSION,
                "ticker": ticker,
                "document_id": document_id,
                "report_year": table.get("report_year"),
                "scope": table.get("scope"),
                "source_entity": entity,
                "canonical_entity": canonical,
                "source_context_sha256": hashlib.sha256(raw_context.encode("utf-8")).hexdigest(),
                "normalization_policy": REPORT_ENTITY_POLICY,
                "evidence_eligible": False,
                "training_eligible": False,
            }
        )
    return sorted(
        aliases,
        key=lambda row: (
            str(row["ticker"]),
            str(row["document_id"]),
            str(row["canonical_entity"]),
        ),
    )


def resolve_question_entity(
    question: object, aliases: Iterable[Mapping[str, Any]]
) -> dict[str, Any] | None:
    """Resolve one ticker only when a source-title issuer name is unique.

    The question and alias must share the exact legal-form-normalised issuer
    token sequence.  A match that maps to multiple tickers is deliberately
    rejected; scope is never inferred from the title because an issuer can
    publish both separate and consolidated statements.
    """
    normalized_question = canonical_entity_name(question)
    if not normalized_question:
        return None
    matches: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for alias in aliases:
        canonical = str(alias.get("canonical_entity") or "")
        if len(canonical.split()) < 3:
            continue
        if canonical in normalized_question:
            ticker = str(alias.get("ticker") or "")
            if ticker:
                matches[ticker].append(alias)
    if len(matches) != 1:
        return None
    ticker, rows = next(iter(matches.items()))
    canonical_names = sorted({str(row["canonical_entity"]) for row in rows})
    return {
        "policy": REPORT_ENTITY_RESOLUTION_POLICY,
        "ticker": ticker,
        "matched_canonical_entities": canonical_names,
        "matched_source_entities": sorted({str(row["source_entity"]) for row in rows}),
        "matched_document_ids": sorted({str(row["document_id"]) for row in rows}),
        "scope_inferred": False,
    }


def validate_report_entity_alias_sidecar(bundle: Path, sidecar: Path) -> dict[str, Any]:
    """Validate an entity sidecar as source-only navigation metadata."""
    bundle, sidecar = bundle.resolve(), sidecar.resolve()
    if sidecar.parent != bundle:
        raise ValueError("Report-entity sidecar must reside directly in bundle-dir")
    manifest_path = sidecar.with_suffix(".manifest.json")
    if not sidecar.is_file() or not manifest_path.is_file():
        raise FileNotFoundError("Report-entity sidecar or manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if int(manifest.get("schema_version") or 0) != REPORT_ENTITY_VERSION:
        raise ValueError("Unsupported report-entity sidecar schema")
    if str(manifest.get("bundle_tables_sha256") or "") != sha256_file(bundle / "tables.jsonl"):
        raise ValueError("Report-entity manifest does not match tables.jsonl")
    if str(manifest.get("sidecar_sha256") or "") != sha256_file(sidecar):
        raise ValueError("Report-entity sidecar checksum mismatch")
    if manifest.get("evidence_eligible") is not False or manifest.get("training_eligible") is not False:
        raise ValueError("Report-entity sidecar must remain non-evidence and non-training metadata")
    return manifest
