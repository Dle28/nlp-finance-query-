"""Literal-only repair candidates for blocked route context.

These records are a human-review aid.  They are never merged into question
plans, routes, bindings, training data, or submissions.  A candidate proves
only that an exact phrase exists in the immutable question text.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


PROTOCOL = "vifinqa_route_context_repair_candidates_v1"
DECISION_PROTOCOL = "vifinqa_route_context_repair_human_decision_v1"
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_TICKER_RE = re.compile(r"\(([A-Z][A-Z0-9]{1,5})\)")
_OPERATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("subtract_or_difference", re.compile(r"\b(?:trừ\s+đi|chênh\s+lệch|biến\s+động|vượt)\b", re.I)),
    ("average_or_median", re.compile(r"\b(?:trung\s+bình|bình\s+quân|trung\s+vị)\b", re.I)),
    ("min_max_ranking", re.compile(r"\b(?:cao\s+nhất|lớn\s+nhất|thấp\s+nhất|nhỏ\s+nhất)\b", re.I)),
    ("positive_negative_filter", re.compile(r"\b(?:dương|âm|lớn\s+hơn\s+0|nhỏ\s+hơn\s+0)\b", re.I)),
    # "so với năm" is only a comparison relation.  It is handled below with
    # output-semantics guards and must not independently imply growth.
    ("year_over_year_growth", re.compile(r"\b(?:tăng\s+trưởng|tỷ\s+lệ\s+tăng)\b", re.I)),
    ("ratio_or_percent", re.compile(r"(?:tỷ\s+(?:lệ|số|trọng)|hệ\s+số|phần\s+trăm|%)", re.I)),
    ("requested_rounding", re.compile(r"\blàm\s+tròn\b", re.I)),
    # Bare "trong số" does not identify the population axis.  It is handled
    # below only when the literal context proves a company population.
    ("multi_company_population", re.compile(r"\b(?:các\s+(?:công\s+ty|doanh\s+nghiệp)|nhóm\s+cổ\s+phiếu)\b", re.I)),
    ("multi_year_range", re.compile(r"\b(?:giai\s+đoạn|qua\s+các\s+năm|trong\s+các\s+năm|giữa\s+(?:cuối\s+)?năm)\b", re.I)),
)
_COMPARISON_TO_YEAR_RE = re.compile(r"\bso\s+với\s+năm\b", re.I)
_BARE_AMONG_RE = re.compile(r"\btrong\s+số\b", re.I)
_ABSOLUTE_DIFFERENCE_RE = re.compile(
    r"\b(?:trừ\s+đi|chênh\s+lệch|hiệu\s+số|biến\s+động)\b", re.I
)
_RELATIVE_CHANGE_RE = re.compile(
    r"(?:%|phần\s+trăm|tăng\s+bao\s+nhiêu|giảm\s+bao\s+nhiêu|"
    r"tăng\s+trưởng|tỷ\s+lệ\s+(?:tăng|giảm))",
    re.I,
)
_COMPANY_POPULATION_AFTER_AMONG_RE = re.compile(
    r"^\s*(?:các\s+)?(?:công\s+ty|doanh\s+nghiệp|mã\s+cổ\s+phiếu|cổ\s+phiếu)\b",
    re.I,
)
_YEAR_POPULATION_AFTER_AMONG_RE = re.compile(r"^\s*(?:các\s+)?năm\b", re.I)
_TICKER_TOKEN_RE = re.compile(r"(?<!\w)[A-Z][A-Z0-9]{1,5}(?!\w)")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def source_contract() -> dict[str, Any]:
    return {
        "candidate_only": True,
        "review_state": "machine_provisional",
        "needs_human": True,
        "evidence_eligible": False,
        "eligible_for_materialization": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
        "may_change_question_plan": False,
        "may_change_route": False,
        "may_select_value": False,
        "may_execute_formula": False,
    }


def _rows(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"{path} must contain JSON objects")
    return rows


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _literal_span(question: str, match: re.Match[str], value: object) -> dict[str, Any]:
    return {
        "value": value,
        "literal_text": match.group(0),
        "char_start": match.start(),
        "char_end": match.end(),
        "question_sha256": hashlib.sha256(question.encode("utf-8")).hexdigest(),
    }


def _operation_literal_candidates(question: str) -> list[dict[str, Any]]:
    candidates = [
        _literal_span(question, match, operation)
        for operation, pattern in _OPERATION_PATTERNS
        for match in pattern.finditer(question)
    ]

    # A year comparison is compatible with absolute subtraction and relative
    # growth.  Only the latter may mint a growth candidate.
    if not _ABSOLUTE_DIFFERENCE_RE.search(question) or _RELATIVE_CHANGE_RE.search(question):
        candidates.extend(
            _literal_span(question, match, "year_over_year_growth")
            for match in _COMPARISON_TO_YEAR_RE.finditer(question)
        )

    # "Trong số" is axis-neutral.  Accept it as multi-company only when the
    # following literal context names companies/securities or contains at
    # least two ticker-like identifiers.  A year population stays unlabelled.
    for match in _BARE_AMONG_RE.finditer(question):
        suffix = question[match.end():]
        if _YEAR_POPULATION_AFTER_AMONG_RE.search(suffix):
            continue
        if _COMPANY_POPULATION_AFTER_AMONG_RE.search(suffix):
            candidates.append(_literal_span(question, match, "multi_company_population"))
            continue
        population_window = suffix[:120]
        if len(_TICKER_TOKEN_RE.findall(population_window)) >= 2:
            candidates.append(_literal_span(question, match, "multi_company_population"))
    return candidates


def literal_repair_candidates(question: str, missing_field: str) -> list[dict[str, Any]]:
    if missing_field == "scope":
        matches: list[tuple[str, re.Match[str]]] = []
        for value, pattern in (
            # Entity role and reporting perimeter are independent.  A parent
            # company may publish either separate or consolidated statements.
            ("separate", re.compile(r"\bbáo\s+cáo(?:\s+tài\s+chính)?\s+riêng(?:\s+lẻ)?\b", re.I)),
            ("consolidated", re.compile(r"\b(?:hợp\s+nhất|báo\s+cáo\s+hợp\s+nhất)\b", re.I)),
        ):
            matches.extend((value, match) for match in pattern.finditer(question))
        values = {value for value, _match in matches}
        if len(values) != 1:
            return []
        return [_literal_span(question, match, value) for value, match in matches]
    if missing_field == "entity":
        return [_literal_span(question, match, match.group(1)) for match in _TICKER_RE.finditer(question)]
    if missing_field == "year":
        return [_literal_span(question, match, int(match.group(1))) for match in _YEAR_RE.finditer(question)]
    if missing_field == "controlled_operation_contract":
        return _operation_literal_candidates(question)
    # Literal concepts require taxonomy/registry review; matching prose to a
    # financial variable is semantic authorization, not a typography repair.
    return []


def build_route_context_repair_queue(
    *, operation_queue: Path, operation_manifest: Path,
    route_packets: Path, route_packets_manifest: Path, output_dir: Path,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite context repair queue: {output_dir}")
    op_manifest = json.loads(operation_manifest.read_text(encoding="utf-8"))
    packet_manifest = json.loads(route_packets_manifest.read_text(encoding="utf-8"))
    if ((op_manifest.get("outputs") or {}).get("queue") or {}).get("sha256") != sha256_file(operation_queue):
        raise ValueError("operation queue SHA-256 mismatch")
    if ((packet_manifest.get("outputs") or {}).get("packets") or {}).get("sha256") != sha256_file(route_packets):
        raise ValueError("route packets SHA-256 mismatch")
    packets = {int(row["question_id"]): row for row in _rows(route_packets)}
    queue: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    statuses: Counter[str] = Counter()
    field_counts: Counter[str] = Counter()
    proposal_counts: Counter[str] = Counter()
    for operation_item in _rows(operation_queue):
        if operation_item.get("queue_status") != "blocked_missing_context":
            continue
        question_id = int(operation_item["question_id"])
        packet = packets.get(question_id)
        if packet is None:
            raise ValueError(f"Missing route packet Q{question_id}")
        if canonical_sha256(packet) != operation_item.get("source_packet_sha256"):
            raise ValueError(f"Operation queue is stale for route packet Q{question_id}")
        question = str(packet.get("question") or "")
        missing_fields = [str(value) for value in operation_item.get("missing_context") or []]
        proposals = {
            field: literal_repair_candidates(question, field)
            for field in missing_fields
        }
        for field in missing_fields:
            field_counts[field] += 1
            if proposals[field]:
                proposal_counts[field] += 1
        status = (
            "machine_provisional_requires_human"
            if missing_fields and all(proposals[field] for field in missing_fields)
            else "blocked_no_literal_repair"
        )
        statuses[status] += 1
        payload = {
            "question_id": question_id,
            "question": question,
            "repair_status": status,
            "missing_context": missing_fields,
            "literal_repair_candidates": proposals,
            "unresolved_fields": [field for field in missing_fields if not proposals[field]],
            "source_packet_sha256": operation_item.get("source_packet_sha256"),
            "source_operation_queue_item_sha256": operation_item.get("queue_item_sha256"),
        }
        item = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            **payload,
            "repair_item_sha256": canonical_sha256(payload),
            "source_contract": source_contract(),
        }
        queue.append(item)
        decisions.append({
            "schema_version": 1,
            "protocol": DECISION_PROTOCOL,
            "question_id": question_id,
            "repair_item_sha256": item["repair_item_sha256"],
            "decision": None,
            "approved_context_repairs": None,
            "decision_provenance": None,
            "reviewer_id": None,
            "reviewed_at": None,
            "notes": "",
            "is_blank_template": True,
            "source_contract": source_contract(),
        })
    output_dir.mkdir(parents=True, exist_ok=False)
    queue_path = output_dir / "route_context_repair_candidates_v1.jsonl"
    decisions_path = output_dir / "route_context_repair_human_decisions_v1.jsonl"
    _write_jsonl(queue_path, queue)
    _write_jsonl(decisions_path, decisions)
    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "status": "machine_provisional_not_materialized",
        "inputs": {
            "operation_queue": {"path": str(operation_queue), "sha256": sha256_file(operation_queue)},
            "operation_manifest": {"path": str(operation_manifest), "sha256": sha256_file(operation_manifest)},
            "route_packets": {"path": str(route_packets), "sha256": sha256_file(route_packets)},
            "route_packets_manifest": {"path": str(route_packets_manifest), "sha256": sha256_file(route_packets_manifest)},
        },
        "outputs": {
            "queue": {"path": str(queue_path), "sha256": sha256_file(queue_path)},
            "blank_decisions": {"path": str(decisions_path), "sha256": sha256_file(decisions_path)},
        },
        "counts": {
            "queue_item_count": len(queue),
            "repair_status_counts": dict(sorted(statuses.items())),
            "missing_field_counts": dict(sorted(field_counts.items())),
            "fields_with_literal_proposal_counts": dict(sorted(proposal_counts.items())),
            "human_decision_count": 0,
        },
        "source_contract": source_contract(),
    }
    manifest_path = output_dir / "route_context_repair_candidates_v1.manifest.json"
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**result, "manifest_path": str(manifest_path)}
