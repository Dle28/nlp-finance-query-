"""Conservative typed graph candidates for the graph-review queue."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .operation_graphs import (
    GRAPH_PROTOCOL,
    canonical_sha256,
    sha256_file,
    source_contract,
    validate_completed_graph,
)


PROTOCOL = "vifinqa_operation_graph_candidates_v1"
_COUNT_RESULT_RE = re.compile(r"\b(?:có\s+bao\s+nhiêu|bao\s+nhiêu|số\s+lượng)\s+(?:công\s+ty|doanh\s+nghiệp|năm)\b", re.I)
_RATIO_RESULT_RE = re.compile(r"(?:tỷ\s+(?:lệ|trọng|số)|phần\s+trăm|%)", re.I)


def _stage_semantic_key(node: Mapping[str, Any]) -> str:
    stage_id = str(node.get("stage_id") or "")
    return re.sub(r"^stage_\d+_", "", stage_id)


def _semantic_sequence_blockers(
    *, operator: str, item: Mapping[str, Any], stage_nodes: list[dict[str, Any]]
) -> list[str]:
    """Reject one-node graphs when the question requires a typed sequence."""

    question = str(item.get("question") or "")
    required = {str(value) for value in item.get("required_operations") or []}
    if operator in {"filter_positive", "filter_negative"} and _COUNT_RESULT_RE.search(question):
        return ["FILTER_THEN_COUNT_REQUIRES_TYPED_SEQUENCE"]
    if operator in {"select_argmin", "select_argmax", "min", "max"} and _RATIO_RESULT_RE.search(question):
        return ["RATIO_THEN_RANKING_REQUIRES_TYPED_SEQUENCE"]
    if operator in {"mean", "median", "min", "max", "select_argmin", "select_argmax"} and (
        {"multi_company_population", "multi_year_range"} & required
    ):
        semantic_keys = {_stage_semantic_key(node) for node in stage_nodes}
        if len(semantic_keys) > 1:
            return ["POPULATION_STAGE_SEMANTICS_NOT_HOMOGENEOUS"]
    return []


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _typed_candidate(item: Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    operators = [str(value) for value in item.get("final_operator_candidates") or []]
    if not operators:
        return None, ["FINAL_OPERATOR_NOT_TYPED"]
    if len(operators) != 1:
        return None, ["OPERATOR_SEQUENCE_REQUIRES_HUMAN_ORDERING"]
    operator = operators[0]
    stage_nodes = [dict(value) for value in item.get("stage_nodes") or []]
    stage_ids = [str(value.get("node_id") or "") for value in stage_nodes]
    if not stage_ids or any(not value for value in stage_ids):
        return None, ["STAGE_REFERENCE_MISSING"]
    if operator in {"subtract", "divide", "yoy_growth_percent"} and len(stage_ids) != 2:
        return None, ["BINARY_OPERATOR_STAGE_ARITY_MISMATCH"]
    if operator in {"mean", "median", "min", "max", "select_argmin", "select_argmax"} and len(stage_ids) < 2:
        return None, ["POPULATION_OPERATOR_STAGE_ARITY_MISMATCH"]
    if operator in {"identity", "ratio_to_percent", "filter_positive", "filter_negative", "round"} and len(stage_ids) != 1:
        return None, ["UNARY_OPERATOR_STAGE_ARITY_MISMATCH"]
    sequence_blockers = _semantic_sequence_blockers(
        operator=operator, item=item, stage_nodes=stage_nodes
    )
    if sequence_blockers:
        return None, sequence_blockers
    required = {str(value) for value in item.get("required_operations") or []}
    graph = {
        "schema_version": 1,
        "protocol": GRAPH_PROTOCOL,
        "question_id": item.get("question_id"),
        "source_route_sha256": item.get("source_route_sha256"),
        "population_axes": [
            axis for axis, requirement in (
                ("entity", "multi_company_population"),
                ("year", "multi_year_range"),
            ) if requirement in required
        ],
        "nodes": [
            *stage_nodes,
            {"node_id": f"op:final:{operator}", "op": operator, "inputs": stage_ids},
        ],
        "final_node_id": f"op:final:{operator}",
        "source_contract": source_contract(),
    }
    try:
        validation = validate_completed_graph(graph, item.get("route_contract") or {})
    except ValueError as error:
        return graph, [str(error)]
    return {**graph, "validation": validation}, []


def build_operation_graph_candidates(
    *, review_queue: Path, review_manifest: Path, output_dir: Path,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite graph candidates: {output_dir}")
    manifest = json.loads(review_manifest.read_text(encoding="utf-8"))
    if ((manifest.get("outputs") or {}).get("queue") or {}).get("sha256") != sha256_file(review_queue):
        raise ValueError("operation graph review queue SHA-256 mismatch")
    rows: list[dict[str, Any]] = []
    statuses: Counter[str] = Counter()
    blockers: Counter[str] = Counter()
    for item in _rows(review_queue):
        if item.get("queue_status") != "review_required_operation_graph":
            continue
        candidate, reasons = _typed_candidate(item)
        status = "typed_candidate_validated_not_authorized" if candidate is not None and not reasons else "blocked_graph_candidate"
        statuses[status] += 1
        blockers.update(reasons)
        payload = {
            "question_id": item.get("question_id"),
            "candidate_status": status,
            "candidate_graph": candidate,
            "reason_codes": reasons,
            "source_queue_item_sha256": item.get("queue_item_sha256"),
            "source_route_sha256": item.get("source_route_sha256"),
        }
        rows.append({
            "schema_version": 1,
            "protocol": PROTOCOL,
            **payload,
            "candidate_item_sha256": canonical_sha256(payload),
            "source_contract": source_contract(),
        })
    output_dir.mkdir(parents=True, exist_ok=False)
    output = output_dir / "operation_graph_candidates_v1.jsonl"
    _write_jsonl(output, rows)
    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "status": "typed_candidates_not_authorized",
        "inputs": {
            "review_queue": {"path": str(review_queue), "sha256": sha256_file(review_queue)},
            "review_manifest": {"path": str(review_manifest), "sha256": sha256_file(review_manifest)},
        },
        "outputs": {"candidates": {"path": str(output), "sha256": sha256_file(output)}},
        "counts": {
            "candidate_item_count": len(rows),
            "candidate_status_counts": dict(sorted(statuses.items())),
            "blocker_reason_counts": dict(sorted(blockers.items())),
        },
        "source_contract": source_contract(),
    }
    manifest_path = output_dir / "operation_graph_candidates_v1.manifest.json"
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**result, "manifest_path": str(manifest_path)}
