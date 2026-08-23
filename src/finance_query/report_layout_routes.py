"""Non-evidence section-navigation hints backed by the report layout graph."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from .financial_metrics import fold_text
from .report_layout_graph import LAYOUT_GRAPH_SOURCE_CONTRACT
from .table_structure import sha256_file


REPORT_LAYOUT_ROUTE_HINT_VERSION = 1
REPORT_LAYOUT_ROUTE_HINT_PROTOCOL = "report_layout_navigation_hints_v1"
LAYOUT_ROUTE_HINT_SOURCE_CONTRACT = {
    **LAYOUT_GRAPH_SOURCE_CONTRACT,
    "may_select_table_candidate": True,
    "may_select_value_cell": False,
    "may_compute_answer": False,
    "may_infer_scope": False,
    "may_select_layout_section": False,
}
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP_TOKENS = frozenset(
    {"bao", "cua", "cho", "cong", "ctcp", "duoc", "gia", "la", "nam", "nguoi", "phan", "tai", "theo", "trong", "tu", "va", "voi"}
)


def _sha256_json(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _tokens(value: object) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall(fold_text(str(value or "")))
        if len(token) >= 2 and token not in _STOP_TOKENS and not token.isdigit()
    }


def build_report_layout_navigation_hints(
    review_items: Iterable[Mapping[str, Any]],
    corporate_route_hints: Iterable[Mapping[str, Any]],
    layout_sections: Iterable[Mapping[str, Any]],
    *,
    maximum_candidates: int = 5,
) -> list[dict[str, Any]]:
    """Rank literal source-heading overlap only inside an already-known document.

    The question must already have exactly one source document from the
    corporate report route hint. A positive overlap proposes navigation
    sections but does not select one; source/table/row/cell verification still
    belongs to downstream evidence gates.
    """
    if maximum_candidates <= 0:
        raise ValueError("maximum_candidates must be positive")
    items: dict[int, dict[str, Any]] = {}
    for row in review_items:
        question_id = row.get("id")
        if type(question_id) is not int or question_id in items:
            raise ValueError("Review items require unique integer IDs")
        items[question_id] = dict(row)
    routes: dict[int, dict[str, Any]] = {}
    for row in corporate_route_hints:
        question_id = row.get("question_id")
        if type(question_id) is not int or question_id in routes:
            raise ValueError("Corporate route hints require unique integer IDs")
        routes[question_id] = dict(row)
    if set(items) != set(routes):
        raise ValueError("Review items and corporate route hints must cover the same IDs")
    sections_by_document: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw_section in layout_sections:
        section = dict(raw_section)
        document_id = str(section.get("document_id") or "")
        if not document_id or section.get("source_contract") != LAYOUT_GRAPH_SOURCE_CONTRACT:
            raise ValueError("Layout section violates its navigation-only contract")
        sections_by_document[document_id].append(section)
    for rows in sections_by_document.values():
        rows.sort(key=lambda row: (int(row.get("section_ordinal") or 0), str(row.get("layout_section_id") or "")))

    output: list[dict[str, Any]] = []
    for question_id in sorted(items):
        item, route = items[question_id], routes[question_id]
        base = {
            "schema_version": REPORT_LAYOUT_ROUTE_HINT_VERSION,
            "layout_route_hint_id": _sha256_json({"question_id": question_id}),
            "question_id": question_id,
            "source_document_id": None,
            "candidate_sections": [],
            "layout_section_selection_allowed": False,
            "source_contract": dict(LAYOUT_ROUTE_HINT_SOURCE_CONTRACT),
        }
        primary_documents = route.get("primary_document_ids")
        if (
            route.get("route_hint_status") != "exact_scope_source_document_found"
            or not isinstance(primary_documents, list)
            or len(primary_documents) != 1
        ):
            base["layout_route_hint_status"] = "question_context_incomplete_or_ambiguous"
            output.append(base)
            continue
        document_id = primary_documents[0]
        if not isinstance(document_id, str) or not document_id:
            raise ValueError(f"Q{question_id}: corporate route hint has invalid primary document")
        base["source_document_id"] = document_id
        sections = sections_by_document.get(document_id)
        if not sections:
            base["layout_route_hint_status"] = "source_document_layout_missing"
            output.append(base)
            continue
        query_tokens = _tokens(item.get("effective_metric") or item.get("question"))
        ranked: list[tuple[int, int, dict[str, Any], list[str]]] = []
        for section in sections:
            heading_tokens = _tokens(section.get("source_layout_label"))
            overlap = sorted(query_tokens.intersection(heading_tokens))
            if len(overlap) >= 2:
                ranked.append((len(overlap), int(section.get("section_ordinal") or 0), section, overlap))
        ranked.sort(key=lambda value: (-value[0], value[1], str(value[2].get("layout_section_id") or "")))
        for overlap_count, _, section, overlap in ranked[:maximum_candidates]:
            base["candidate_sections"].append(
                {
                    "layout_section_id": section.get("layout_section_id"),
                    "section_ordinal": section.get("section_ordinal"),
                    "source_layout_label": section.get("source_layout_label"),
                    "internal_table_uids": list(section.get("internal_table_uids") or []),
                    "literal_heading_overlap_tokens": overlap,
                    "literal_heading_overlap_count": overlap_count,
                }
            )
        base["layout_route_hint_status"] = (
            "layout_navigation_candidates_found" if base["candidate_sections"] else "no_literal_source_heading_overlap"
        )
        output.append(base)
    return output


def validate_report_layout_navigation_hints(output_dir: Path) -> dict[str, Any]:
    """Validate hashes and prohibit a hint artifact from selecting a section."""
    output_dir = output_dir.resolve()
    hints_path = output_dir / "report_layout_navigation_hints_v1.jsonl"
    manifest_path = output_dir / "report_layout_navigation_hints_v1.manifest.json"
    if not hints_path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError("Report layout navigation-hint output is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("schema_version") != REPORT_LAYOUT_ROUTE_HINT_VERSION
        or manifest.get("protocol") != REPORT_LAYOUT_ROUTE_HINT_PROTOCOL
        or manifest.get("source_contract") != LAYOUT_ROUTE_HINT_SOURCE_CONTRACT
        or manifest.get("hints_sha256") != sha256_file(hints_path)
    ):
        raise ValueError("Report layout navigation-hint manifest is malformed")
    rows = [json.loads(line) for line in hints_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) != manifest.get("question_count") or len({row.get("question_id") for row in rows}) != len(rows):
        raise ValueError("Report layout navigation hints lack exact question coverage")
    for row in rows:
        if row.get("source_contract") != LAYOUT_ROUTE_HINT_SOURCE_CONTRACT or row.get("layout_section_selection_allowed") is not False:
            raise ValueError("Report layout navigation hint attempted to select a section")
    return manifest
