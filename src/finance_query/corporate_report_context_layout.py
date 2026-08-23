"""Layout-heading supplements for immutable corporate-context review packets."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from .financial_metrics import fold_text
from .report_layout_graph import LAYOUT_GRAPH_SOURCE_CONTRACT
from .table_structure import sha256_file


CORPORATE_REPORT_CONTEXT_LAYOUT_SUPPLEMENT_VERSION = 1
CORPORATE_REPORT_CONTEXT_LAYOUT_SUPPLEMENT_PROTOCOL = "corporate_report_context_layout_supplement_v1"
CONTEXT_LAYOUT_SUPPLEMENT_SOURCE_CONTRACT = {
    "metadata_only": True,
    "navigation_only": True,
    "review_only": True,
    "evidence_eligible": False,
    "training_eligible": False,
    "submission_eligible": False,
    "materialization_allowed": False,
    "may_select_table_candidate": False,
    "may_select_value_cell": False,
    "may_compute_answer": False,
    "may_infer_scope": False,
    "may_promote_provenance": False,
}
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP_TOKENS = frozenset(
    {"bao", "cua", "cho", "cong", "ctcp", "duoc", "gia", "la", "nam", "nguoi", "phan", "tai", "theo", "trong", "tu", "va", "voi"}
)


def _sha256_json(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _tokens(value: object) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall(fold_text(str(value or "")))
        if len(token) >= 2 and token not in _STOP_TOKENS and not token.isdigit()
    }


def build_corporate_report_context_layout_supplement(
    packets: Iterable[Mapping[str, Any]], layout_sections: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Expose at most three source-heading navigation aids per review packet."""
    sections_by_document: dict[str, list[dict[str, Any]]] = {}
    for row in layout_sections:
        section = dict(row)
        document_id = str(section.get("document_id") or "")
        if not document_id or section.get("source_contract") != LAYOUT_GRAPH_SOURCE_CONTRACT:
            raise ValueError("Layout section is not a validated navigation-only record")
        sections_by_document.setdefault(document_id, []).append(section)
    for sections in sections_by_document.values():
        sections.sort(key=lambda row: (int(row.get("section_ordinal") or 0), str(row.get("layout_section_id") or "")))

    output: list[dict[str, Any]] = []
    seen_question_ids: set[int] = set()
    for packet in packets:
        question_id = packet.get("question_id")
        review_context = packet.get("review_context") or {}
        proposed = ((review_context.get("retrieval_metadata_consensus") or {}).get("proposed_question_context") or {})
        document_id = proposed.get("source_document_id")
        question = review_context.get("question")
        immutable_hash = packet.get("immutable_review_context_sha256")
        if (
            type(question_id) is not int
            or question_id in seen_question_ids
            or not isinstance(document_id, str)
            or not document_id
            or not isinstance(question, str)
            or not isinstance(immutable_hash, str)
        ):
            raise ValueError("Context-review packet lacks an immutable proposed source document")
        seen_question_ids.add(question_id)
        ranked: list[tuple[int, int, Mapping[str, Any], list[str]]] = []
        query_tokens = _tokens(question)
        for section in sections_by_document.get(document_id, []):
            overlap = sorted(query_tokens.intersection(_tokens(section.get("source_layout_label"))))
            if len(overlap) >= 2:
                ranked.append((len(overlap), int(section.get("section_ordinal") or 0), section, overlap))
        ranked.sort(key=lambda value: (-value[0], value[1], str(value[2].get("layout_section_id") or "")))
        candidates = [
            {
                "layout_section_id": section.get("layout_section_id"),
                "section_ordinal": section.get("section_ordinal"),
                "source_layout_label": section.get("source_layout_label"),
                "literal_heading_overlap_tokens": overlap,
                "literal_heading_overlap_count": overlap_count,
            }
            for overlap_count, _, section, overlap in ranked[:3]
        ]
        output.append(
            {
                "schema_version": CORPORATE_REPORT_CONTEXT_LAYOUT_SUPPLEMENT_VERSION,
                "protocol": CORPORATE_REPORT_CONTEXT_LAYOUT_SUPPLEMENT_PROTOCOL,
                "question_id": question_id,
                "immutable_review_context_sha256": immutable_hash,
                "source_document_id": document_id,
                "layout_supplement_status": "literal_source_heading_candidates_found" if candidates else "no_literal_source_heading_overlap",
                "source_heading_candidates": candidates,
                "materialization_allowed": False,
                "source_contract": dict(CONTEXT_LAYOUT_SUPPLEMENT_SOURCE_CONTRACT),
            }
        )
    return sorted(output, key=lambda row: row["question_id"])


def validate_corporate_report_context_layout_supplement(output_dir: Path) -> dict[str, Any]:
    """Check immutable queue bindings and non-materializable supplement output."""
    output_dir = output_dir.resolve()
    supplement_path = output_dir / "corporate_report_context_layout_supplement_v1.jsonl"
    manifest_path = output_dir / "corporate_report_context_layout_supplement_v1.manifest.json"
    if not supplement_path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError("Corporate report context layout supplement is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("schema_version") != CORPORATE_REPORT_CONTEXT_LAYOUT_SUPPLEMENT_VERSION
        or manifest.get("protocol") != CORPORATE_REPORT_CONTEXT_LAYOUT_SUPPLEMENT_PROTOCOL
        or manifest.get("source_contract") != CONTEXT_LAYOUT_SUPPLEMENT_SOURCE_CONTRACT
        or manifest.get("materialization_allowed") is not False
        or manifest.get("supplement_sha256") != sha256_file(supplement_path)
    ):
        raise ValueError("Context layout supplement manifest is malformed")
    rows = [json.loads(line) for line in supplement_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) != manifest.get("question_count") or len({row.get("question_id") for row in rows}) != len(rows):
        raise ValueError("Context layout supplement lacks exact question coverage")
    for row in rows:
        if row.get("source_contract") != CONTEXT_LAYOUT_SUPPLEMENT_SOURCE_CONTRACT or row.get("materialization_allowed") is not False:
            raise ValueError("Context layout supplement attempted materialization")
    return manifest
