"""Typed whole-question operation graphs with a fail-closed review boundary."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


GRAPH_PROTOCOL = "vifinqa_whole_question_operation_graph_v1"
QUEUE_PROTOCOL = "vifinqa_operation_graph_review_queue_v1"
DECISION_PROTOCOL = "vifinqa_operation_graph_human_decision_v1"

_ARITY: dict[str, tuple[int, int | None]] = {
    "stage_ref": (0, 0),
    "identity": (1, 1),
    "add": (2, None),
    "subtract": (2, 2),
    "divide": (2, 2),
    "ratio_to_percent": (1, 1),
    "mean": (2, None),
    "median": (2, None),
    "min": (2, None),
    "max": (2, None),
    "select_argmin": (2, None),
    "select_argmax": (2, None),
    "filter_positive": (1, None),
    "filter_negative": (1, None),
    "yoy_growth_percent": (2, 2),
    "round": (1, 1),
}

_REQUIREMENT_COVERAGE = {
    "subtract": {"subtract_or_difference", "stage_output_dependency"},
    "mean": {"average_or_median", "stage_output_dependency"},
    "median": {"average_or_median", "stage_output_dependency"},
    "min": {"min_max_ranking", "stage_output_dependency"},
    "max": {"min_max_ranking", "stage_output_dependency"},
    "select_argmin": {"min_max_ranking", "stage_output_dependency"},
    "select_argmax": {"min_max_ranking", "stage_output_dependency"},
    "filter_positive": {"positive_negative_filter", "stage_output_dependency"},
    "filter_negative": {"positive_negative_filter", "stage_output_dependency"},
    "yoy_growth_percent": {
        "year_over_year_growth",
        "ratio_or_percent",
        "stage_output_dependency",
    },
    "ratio_to_percent": {"ratio_or_percent"},
    "round": {"requested_rounding"},
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def source_contract() -> dict[str, bool]:
    return {
        "candidate_only": True,
        "evidence_eligible": False,
        "may_execute_formula": False,
        "eligible_for_materialization": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
    }


def _operator_candidates(required: Sequence[object], question: str) -> list[str]:
    requirements = {str(value) for value in required}
    text = " ".join(question.casefold().split())
    result: list[str] = []
    if "subtract_or_difference" in requirements:
        result.append("subtract")
    if "average_or_median" in requirements:
        if "trung vị" in text:
            result.append("median")
        elif "trung bình" in text or "bình quân" in text:
            result.append("mean")
        else:
            result.extend(["mean", "median"])
    if "min_max_ranking" in requirements:
        if any(value in text for value in ("cao nhất", "lớn nhất", "tối đa")):
            result.append("select_argmax")
        elif any(value in text for value in ("thấp nhất", "nhỏ nhất", "tối thiểu")):
            result.append("select_argmin")
        else:
            result.extend(["select_argmin", "select_argmax"])
    if "positive_negative_filter" in requirements:
        if "âm" in text or "nhỏ hơn 0" in text:
            result.append("filter_negative")
        elif "dương" in text or "lớn hơn 0" in text:
            result.append("filter_positive")
        else:
            result.extend(["filter_positive", "filter_negative"])
    if "year_over_year_growth" in requirements:
        result.append("yoy_growth_percent")
    if "ratio_or_percent" in requirements:
        result.append("ratio_to_percent")
    if "requested_rounding" in requirements:
        result.append("round")
    return list(dict.fromkeys(result))


def validate_completed_graph(graph: Mapping[str, Any], route: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an authored graph; return coverage only when fully executable."""

    if graph.get("protocol") != GRAPH_PROTOCOL or graph.get("schema_version") != 1:
        raise ValueError("unexpected operation graph protocol")
    if graph.get("source_route_sha256") != canonical_sha256(route):
        raise ValueError("operation graph is stale for route")
    nodes = graph.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("operation graph requires nodes")
    population_axes = graph.get("population_axes") or []
    if (
        not isinstance(population_axes, list)
        or any(value not in {"entity", "year"} for value in population_axes)
        or len(set(population_axes)) != len(population_axes)
    ):
        raise ValueError("operation graph population axes are invalid")
    seen: set[str] = set()
    stage_refs: set[str] = set()
    covered = {"reported_value"}
    if "entity" in population_axes:
        covered.add("multi_company_population")
    if "year" in population_axes:
        covered.add("multi_year_range")
    for node in nodes:
        if not isinstance(node, Mapping):
            raise ValueError("operation graph node must be a mapping")
        node_id, op = node.get("node_id"), node.get("op")
        inputs = node.get("inputs")
        if not isinstance(node_id, str) or not node_id or node_id in seen:
            raise ValueError("operation graph node IDs must be unique")
        if op not in _ARITY or not isinstance(inputs, list) or any(value not in seen for value in inputs):
            raise ValueError("operation graph is not topologically valid")
        minimum, maximum = _ARITY[str(op)]
        if len(inputs) < minimum or (maximum is not None and len(inputs) > maximum):
            raise ValueError(f"operation graph arity is invalid for {op}")
        if op == "stage_ref":
            stage_id = node.get("stage_id")
            if not isinstance(stage_id, str) or not stage_id:
                raise ValueError("stage_ref requires stage_id")
            stage_refs.add(stage_id)
        else:
            covered.update(_REQUIREMENT_COVERAGE.get(str(op), set()))
        seen.add(node_id)
    final_node_id = graph.get("final_node_id")
    if final_node_id not in seen:
        raise ValueError("operation graph final node is missing")
    route_stages = {
        str(stage.get("stage_id") or "")
        for stage in route.get("stages") or []
        if isinstance(stage, Mapping) and str(stage.get("stage_id") or "")
    }
    if stage_refs != route_stages:
        raise ValueError("operation graph stage references do not match route")
    required = {str(value) for value in route.get("required_operations") or []}
    missing = sorted(required - covered)
    if missing:
        raise ValueError("operation graph does not cover route requirements: " + ", ".join(missing))
    return {
        "status": "validated_not_authorized",
        "covered_operations": sorted(covered),
        "node_count": len(nodes),
        "graph_sha256": canonical_sha256(graph),
        "source_contract": source_contract(),
    }


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"{path} must contain JSON objects")
    return rows


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def build_operation_graph_queue(
    *,
    route_overlay: Path,
    route_overlay_manifest: Path,
    route_packets: Path,
    route_packets_manifest: Path,
    output_dir: Path,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite operation graph queue: {output_dir}")
    overlay_manifest = json.loads(route_overlay_manifest.read_text(encoding="utf-8"))
    packet_manifest = json.loads(route_packets_manifest.read_text(encoding="utf-8"))
    if ((overlay_manifest.get("outputs") or {}).get("overlay") or {}).get("sha256") != sha256_file(route_overlay):
        raise ValueError("route overlay SHA-256 mismatch")
    if ((packet_manifest.get("outputs") or {}).get("packets") or {}).get("sha256") != sha256_file(route_packets):
        raise ValueError("route packets SHA-256 mismatch")
    overlays = {int(row["question_id"]): row for row in _load_rows(route_overlay)}
    packets = {int(row["question_id"]): row for row in _load_rows(route_packets)}
    if set(overlays) != set(packets):
        raise ValueError("route overlay and packets do not cover the same questions")
    queue: list[dict[str, Any]] = []
    templates: list[dict[str, Any]] = []
    statuses = Counter()
    for question_id in sorted(overlays):
        overlay, packet = overlays[question_id], packets[question_id]
        if overlay.get("route_status") != "composed_execution_required":
            continue
        stages = [stage for stage in packet.get("stages") or [] if isinstance(stage, Mapping)]
        missing_context = list((packet.get("feedback") or {}).get("missing_contract") or [])
        status = (
            "blocked_missing_context"
            if missing_context
            else "blocked_missing_route_stages"
            if not stages
            else "review_required_operation_graph"
        )
        statuses[status] += 1
        route_contract = {
            "question_id": question_id,
            "required_operations": list(overlay.get("required_operations") or []),
            "stages": [
                {"stage_id": str(stage.get("stage_id") or "")}
                for stage in stages
                if str(stage.get("stage_id") or "")
            ],
        }
        payload = {
            "question_id": question_id,
            "question": overlay.get("question"),
            "queue_status": status,
            "required_operations": list(overlay.get("required_operations") or []),
            "missing_operations": list(overlay.get("missing_operations") or []),
            "missing_context": missing_context,
            "route_packet_status": packet.get("packet_status"),
            "route_contract": route_contract,
            "stage_nodes": [
                {
                    "node_id": f"stage:{stage.get('stage_id')}",
                    "op": "stage_ref",
                    "stage_id": stage.get("stage_id"),
                    "inputs": [],
                }
                for stage in stages
            ],
            "final_operator_candidates": _operator_candidates(
                overlay.get("required_operations") or [], str(overlay.get("question") or "")
            ),
            "source_route_sha256": canonical_sha256(route_contract),
            "source_overlay_row_sha256": canonical_sha256(overlay),
            "source_packet_sha256": canonical_sha256(packet),
        }
        item = {
            "schema_version": 1,
            "protocol": QUEUE_PROTOCOL,
            **payload,
            "queue_item_sha256": canonical_sha256(payload),
            "source_contract": source_contract(),
        }
        queue.append(item)
        templates.append(
            {
                "schema_version": 1,
                "protocol": DECISION_PROTOCOL,
                "question_id": question_id,
                "queue_item_sha256": item["queue_item_sha256"],
                "decision": None,
                "completed_graph": None,
                "decision_provenance": None,
                "reviewer_id": None,
                "reviewed_at": None,
                "notes": "",
                "is_blank_template": True,
                "source_contract": source_contract(),
            }
        )
    output_dir.mkdir(parents=True, exist_ok=False)
    queue_path = output_dir / "operation_graph_review_queue_v1.jsonl"
    template_path = output_dir / "operation_graph_human_decision_template_v1.jsonl"
    _write_jsonl(queue_path, queue)
    _write_jsonl(template_path, templates)
    result = {
        "schema_version": 1,
        "protocol": QUEUE_PROTOCOL,
        "status": "operation_graph_queue_built_not_materialized",
        "inputs": {
            "route_overlay": {"path": str(route_overlay), "sha256": sha256_file(route_overlay)},
            "route_overlay_manifest": {"path": str(route_overlay_manifest), "sha256": sha256_file(route_overlay_manifest)},
            "route_packets": {"path": str(route_packets), "sha256": sha256_file(route_packets)},
            "route_packets_manifest": {"path": str(route_packets_manifest), "sha256": sha256_file(route_packets_manifest)},
        },
        "outputs": {
            "queue": {"path": str(queue_path), "sha256": sha256_file(queue_path)},
            "blank_decisions": {"path": str(template_path), "sha256": sha256_file(template_path)},
        },
        "counts": {
            "queue_item_count": len(queue),
            "human_decision_count": 0,
            "queue_status_counts": dict(sorted(statuses.items())),
        },
        "source_contract": source_contract(),
    }
    manifest_path = output_dir / "operation_graph_review_queue_v1.manifest.json"
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {**result, "manifest_path": str(manifest_path)}
