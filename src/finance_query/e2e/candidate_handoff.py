"""Shared immutable-input checks for typed candidate handoff queues.

Candidate handoffs are a narrow bridge from a typed question plan to a future
source-review workflow.  They may never mutate route, period, binding or
answer artifacts.  Keeping the common lineage checks here prevents each
operation-specific queue from reimplementing a slightly different boundary.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


BLOCKER_PROTOCOL = "vifinqa_replay_blocker_queue_v1"
PLAN_PROTOCOL = "typed_operand_decomposition_fail_closed_v1"
SOURCE_CONTRACT = {
    "candidate_only": True,
    "may_materialize_route": False,
    "may_materialize_period": False,
    "may_materialize_binding": False,
    "may_materialize_answer": False,
    "may_execute_formula": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def read_jsonl(path: Path, *, name: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{name}:{line_number} must be a JSON object")
        result.append(value)
    return result


def index_question_id(
    rows: list[dict[str, Any]], *, name: str
) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        value = row.get("question_id")
        if isinstance(value, bool) or not isinstance(value, int) or value in result:
            raise ValueError(f"{name} requires unique integer question_id")
        result[value] = row
    return result


def _require_output(manifest: Mapping[str, Any], output_name: str, path: Path) -> None:
    expected = ((manifest.get("outputs") or {}).get(output_name) or {}).get("sha256")
    if not isinstance(expected, str) or expected != sha256_file(path):
        raise ValueError(f"SHA-256 mismatch for blocker queue output {output_name}")


def _queue_path(manifest_path: Path, filename: str) -> Path:
    path = manifest_path.parent / filename
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def load_blocker_rows(blocker_manifest_path: Path) -> list[dict[str, Any]]:
    """Load only the two pre-binding queue strata from one verified manifest."""

    manifest = _json(blocker_manifest_path)
    if manifest.get("protocol") != BLOCKER_PROTOCOL:
        raise ValueError("unexpected replay blocker queue protocol")
    operation_path = _queue_path(blocker_manifest_path, "operation_graph_queue.jsonl")
    route_path = _queue_path(blocker_manifest_path, "route_definition_queue.jsonl")
    _require_output(manifest, "operation_graph", operation_path)
    _require_output(manifest, "route_definition", route_path)
    rows = read_jsonl(operation_path, name="operation graph queue") + read_jsonl(
        route_path, name="route definition queue"
    )
    index_question_id(rows, name="combined candidate blocker queues")
    return rows


def load_typed_plans(plan_path: Path, manifest_path: Path) -> dict[int, dict[str, Any]]:
    manifest = _json(manifest_path)
    if manifest.get("protocol") != PLAN_PROTOCOL:
        raise ValueError("unexpected typed operand plan protocol")
    if manifest.get("sidecar_sha256") != sha256_file(plan_path):
        raise ValueError("SHA-256 mismatch for typed operand plans")
    return index_question_id(read_jsonl(plan_path, name="typed operand plans"), name="typed operand plans")


def write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
