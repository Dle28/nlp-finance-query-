#!/usr/bin/env python3
"""Create blank, separate human-response templates for semantic review queues."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "verify_computational_semantic_human_reviews",
    ROOT / "scripts" / "verify_computational_semantic_human_reviews_v1.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_VERIFY = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_VERIFY)


PROTOCOL = "computational_semantic_human_review_response_template_v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Expected JSON object at {path}:{line_number}")
            rows.append(value)
    return rows


def _template(packet: Mapping[str, Any], review_kind: str, reviewer_id: str) -> dict[str, Any]:
    common = {
        "schema_version": 1,
        "protocol": _VERIFY.RESPONSE_PROTOCOL,
        "review_kind": review_kind,
        "question_id": packet["question_id"],
        "immutable_review_context_sha256": packet["immutable_review_context_sha256"],
        "decision": None,
        "decision_provenance": None,
        "reviewer_id": reviewer_id,
        "reviewed_at": None,
        "source_coordinates_checked": [],
        "notes": "",
        "is_blank_template": True,
        "materialization_allowed": False,
        "source_contract": packet["source_contract"],
    }
    if review_kind == "source_coordinate":
        return {**common, "period_unit_dimension_checked": None}
    if review_kind == "scope":
        return {
            **common,
            "proposed_scope": None,
            "scope_evidence_kind": None,
            "period_unit_dimension_checked": None,
        }
    return {**common, "proposed_dimension_contract": None, "dimension_evidence_kind": None}


def build(
    *,
    queue: Path,
    queue_manifest: Path,
    review_kind: str,
    reviewer_id: str,
    output: Path,
    manifest_output: Path,
) -> dict[str, Any]:
    """Build a blank response file without changing an immutable review queue."""
    if output.exists() or manifest_output.exists():
        raise FileExistsError(f"Refusing to overwrite review response template: {output}")
    if review_kind not in _VERIFY.QUEUE_PROTOCOLS or not reviewer_id.strip():
        raise ValueError("A supported review_kind and non-empty reviewer_id are required")
    manifest = load_json(queue_manifest)
    queue_sha = _VERIFY.require_hash(
        queue,
        ((manifest.get("outputs") or {}).get("queue") or {}).get("sha256"),
        "review queue",
    )
    queue_protocol = manifest.get("protocol")
    if (
        queue_protocol not in _VERIFY.QUEUE_PROTOCOLS[review_kind]
        or manifest.get("labels_prepopulated") is not False
        or manifest.get("materialization_allowed") is not False
        or not isinstance(manifest.get("source_contract"), Mapping)
    ):
        raise ValueError("Review queue manifest is not a blank non-materializable queue")
    packets = _VERIFY.index(load_jsonl(queue), "question_id", "review queue")
    if (
        not packets
        or manifest.get("question_count") != len(packets)
        or manifest.get("question_ids") != sorted(packets)
    ):
        raise ValueError("Review queue manifest does not cover queue question IDs exactly")
    templates: list[dict[str, Any]] = []
    for question_id in sorted(packets):
        packet = packets[question_id]
        if (
            packet.get("protocol") != queue_protocol
            or packet.get("materialization_allowed") is not False
            or packet.get("source_contract") != manifest.get("source_contract")
            or _VERIFY.canonical_sha256(packet.get("review_context")) != packet.get("immutable_review_context_sha256")
            or (packet.get("review_decision_contract") or {}).get("decision") is not None
        ):
            raise ValueError(f"Q{question_id}: immutable review packet is malformed")
        templates.append(_template(packet, review_kind, reviewer_id))

    output.parent.mkdir(parents=True, exist_ok=True)
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in templates),
        encoding="utf-8",
    )
    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "review_kind": review_kind,
        "reviewer_id": reviewer_id,
        "template_status": "blank_separate_response_template",
        "response_count": len(templates),
        "question_ids": [row["question_id"] for row in templates],
        "labels_prepopulated": False,
        "materialization_allowed": False,
        "inputs": {
            "queue": {"path": str(queue), "sha256": queue_sha},
            "queue_manifest": {"path": str(queue_manifest), "sha256": sha256_file(queue_manifest)},
        },
        "outputs": {"template": {"path": str(output), "sha256": sha256_file(output)}},
        "source_contract": manifest["source_contract"],
    }
    manifest_output.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--queue-manifest", type=Path, required=True)
    parser.add_argument("--review-kind", choices=sorted(_VERIFY.QUEUE_PROTOCOLS), required=True)
    parser.add_argument("--reviewer-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path, required=True)
    args = parser.parse_args()
    result = build(**{name: value.resolve() if isinstance(value, Path) else value for name, value in vars(args).items()})
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
