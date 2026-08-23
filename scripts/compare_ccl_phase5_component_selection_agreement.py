#!/usr/bin/env python3
"""Compare two audited Phase 5 component-selection runs without promotion.

Agreement is a closed-world reproducibility signal only.  It never assigns a
table type or semantic label: primary-context disagreement is quarantined and
even exact agreement remains ineligible for training and certification.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


PROTOCOL = "ccl_phase5_component_selection_agreement_v1"
AUDIT_PROTOCOL = "ccl_phase5_component_selection_smoke_audit_v1"
PILOT_AUDIT_PROTOCOL = "ccl_phase5_component_selection_pilot_audit_v1"
RESULTS_NAME = "component_selection_agreement_v1.jsonl"
QUARANTINE_NAME = "component_selection_disagreement_quarantine_v1.jsonl"
REPORT_NAME = "component_selection_agreement_report.json"
MANIFEST_NAME = "component_selection_agreement_manifest.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _jsonl(path: Path) -> list[dict[str, Any]]:
    values = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not all(isinstance(value, dict) for value in values):
        raise ValueError(f"expected JSONL objects: {path}")
    return values


def _require_audit(audit: Mapping[str, Any], results: Path, *, label: str) -> None:
    if (
        audit.get("protocol") not in {AUDIT_PROTOCOL, PILOT_AUDIT_PROTOCOL}
        or audit.get("audit_passed") is not True
        or audit.get("training_eligible") is not False
        or audit.get("certification_allowed") is not False
    ):
        raise ValueError(f"{label} is not a passed non-promotable smoke audit")
    expected = ((audit.get("artifact_hashes") or {}).get(results.name))
    if not isinstance(expected, str) or expected != sha256_file(results):
        raise ValueError(f"{label} does not bind its validated results")


def _results_by_id(path: Path, *, label: str) -> dict[str, dict[str, Any]]:
    rows = _jsonl(path)
    result = {str(row.get("component_selection_packet_id") or ""): row for row in rows}
    if not result or len(result) != len(rows):
        raise ValueError(f"{label} result identities are invalid")
    if any(row.get("training_eligible") is not False or row.get("certification_allowed") is not False for row in rows):
        raise ValueError(f"{label} contains a promotable selection")
    return result


def _selection(row: Mapping[str, Any]) -> tuple[str, str | None, frozenset[str], tuple[str, ...]]:
    status = str(row.get("status") or "")
    selection = row.get("selection")
    if status == "INVALID_UNRESOLVED":
        if selection is not None:
            raise ValueError("invalid selection row unexpectedly contains a selection")
        return status, None, frozenset(), ()
    if not isinstance(selection, Mapping):
        raise ValueError("valid selection row has no selection payload")
    primary = selection.get("primary_component_id")
    supporting = selection.get("supporting_component_ids")
    unresolved = selection.get("unresolved_conditions")
    if primary is not None and not isinstance(primary, str):
        raise ValueError("selection primary ID is malformed")
    if not isinstance(supporting, list) or not all(isinstance(value, str) for value in supporting):
        raise ValueError("selection supporting IDs are malformed")
    if not isinstance(unresolved, list) or not all(isinstance(value, str) for value in unresolved):
        raise ValueError("selection unresolved conditions are malformed")
    return status, primary, frozenset(supporting), tuple(unresolved)


def _agreement_status(primary: tuple[str, str | None, frozenset[str], tuple[str, ...]], challenger: tuple[str, str | None, frozenset[str], tuple[str, ...]]) -> str:
    primary_status, primary_id, primary_supporting, primary_unresolved = primary
    challenger_status, challenger_id, challenger_supporting, challenger_unresolved = challenger
    if primary_status != challenger_status or primary_id != challenger_id:
        return "DISAGREEMENT_QUARANTINED"
    if primary_status == "INVALID_UNRESOLVED":
        return "SHARED_INVALID_UNRESOLVED"
    if primary_status == "VALID_ABSTENTION_ONLY":
        return "SHARED_ABSTENTION_ONLY" if primary_unresolved == challenger_unresolved else "DISAGREEMENT_QUARANTINED"
    if primary_supporting == challenger_supporting:
        return "EXACT_CLOSED_WORLD_AGREEMENT"
    return "PRIMARY_CONTEXT_AGREEMENT_SUPPORT_DIFFERENCE"


def compare_component_selection_agreement(
    *,
    primary_audit: Path,
    primary_results: Path,
    challenger_audit: Path,
    challenger_results: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Compare two independent, audit-passed closed-world component selections."""
    inputs = {
        "primary_audit": primary_audit.resolve(),
        "primary_results": primary_results.resolve(),
        "challenger_audit": challenger_audit.resolve(),
        "challenger_results": challenger_results.resolve(),
    }
    if any(not path.is_file() for path in inputs.values()):
        missing = [str(path) for path in inputs.values() if not path.is_file()]
        raise FileNotFoundError(f"missing agreement input: {missing}")
    if output_dir.exists() or output_dir.resolve() in {path.resolve() for path in inputs.values()}:
        raise FileExistsError("agreement output-dir must be new and distinct from its inputs")
    before_hashes = {role: sha256_file(path) for role, path in inputs.items()}
    primary_audit_json = _json(inputs["primary_audit"])
    challenger_audit_json = _json(inputs["challenger_audit"])
    _require_audit(primary_audit_json, inputs["primary_results"], label="primary audit")
    _require_audit(challenger_audit_json, inputs["challenger_results"], label="challenger audit")
    primary_route = primary_audit_json.get("route") or {}
    challenger_route = challenger_audit_json.get("route") or {}
    if (
        primary_route.get("route_id") != challenger_route.get("route_id")
        or primary_route.get("model_id") == challenger_route.get("model_id")
    ):
        raise ValueError("agreement inputs do not use one packet route and two distinct models")
    source_packet_hash_key = "phase5_component_selection_packets_v1.jsonl"
    if (
        (primary_audit_json.get("input_hashes") or {}).get(source_packet_hash_key)
        != (challenger_audit_json.get("input_hashes") or {}).get(source_packet_hash_key)
    ):
        raise ValueError("agreement inputs do not bind the same source packet set")
    primary_by_id = _results_by_id(inputs["primary_results"], label="primary")
    challenger_by_id = _results_by_id(inputs["challenger_results"], label="challenger")
    if set(primary_by_id) != set(challenger_by_id):
        raise ValueError("agreement inputs do not cover the same packet identities")

    rows: list[dict[str, Any]] = []
    for packet_id in sorted(primary_by_id):
        primary = primary_by_id[packet_id]
        challenger = challenger_by_id[packet_id]
        primary_selection = _selection(primary)
        challenger_selection = _selection(challenger)
        status = _agreement_status(primary_selection, challenger_selection)
        intersection = primary_selection[2] & challenger_selection[2]
        union = primary_selection[2] | challenger_selection[2]
        rows.append(
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "component_selection_packet_id": packet_id,
                "internal_table_uid": primary.get("internal_table_uid"),
                "agreement_status": status,
                "primary_model": {
                    "model_id": primary_route.get("model_id"),
                    "result_status": primary_selection[0],
                    "primary_component_id": primary_selection[1],
                    "supporting_component_ids": sorted(primary_selection[2]),
                    "unresolved_conditions": list(primary_selection[3]),
                },
                "challenger_model": {
                    "model_id": challenger_route.get("model_id"),
                    "result_status": challenger_selection[0],
                    "primary_component_id": challenger_selection[1],
                    "supporting_component_ids": sorted(challenger_selection[2]),
                    "unresolved_conditions": list(challenger_selection[3]),
                },
                "supporting_overlap": {"intersection_count": len(intersection), "union_count": len(union)},
                "training_eligible": False,
                "certification_allowed": False,
            }
        )
    after_hashes = {role: sha256_file(path) for role, path in inputs.items()}
    if after_hashes != before_hashes:
        raise ValueError("agreement comparison changed a hash-bound input")
    output_dir.mkdir(parents=True)
    results_path = output_dir / RESULTS_NAME
    with results_path.open("x", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    quarantine = [row for row in rows if row["agreement_status"] == "DISAGREEMENT_QUARANTINED"]
    quarantine_path = output_dir / QUARANTINE_NAME
    with quarantine_path.open("x", encoding="utf-8") as file:
        for row in quarantine:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    counts = dict(sorted(Counter(str(row["agreement_status"]) for row in rows).items()))
    report = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "run_status": "component_selection_agreement_complete_not_calibrated",
        "route_id": primary_route.get("route_id"),
        "models": [primary_route.get("model_id"), challenger_route.get("model_id")],
        "input_hashes_unchanged": True,
        "status_counts": counts,
        "quarantine_count": len(quarantine),
        "training_eligible_output_count": 0,
        "certification_allowed": False,
        "next_gate": "calibrate_against_final_review_samples_before_any_semantic_or_training_use",
    }
    report_path = output_dir / REPORT_NAME
    report_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "run_status": "component_selection_agreement_complete_not_calibrated",
        # Role-based keys prevent same-basename artifacts from overwriting one another.
        "inputs": {
            role: {"path": str(path), "sha256": before_hashes[role]}
            for role, path in inputs.items()
        },
        "outputs": {
            RESULTS_NAME: {"sha256": sha256_file(results_path)},
            QUARANTINE_NAME: {"sha256": sha256_file(quarantine_path)},
            REPORT_NAME: {"sha256": sha256_file(report_path)},
        },
        "input_hashes_unchanged": True,
        "training_eligible": False,
        "certification_allowed": False,
    }
    (output_dir / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary-audit", type=Path, required=True)
    parser.add_argument("--primary-results", type=Path, required=True)
    parser.add_argument("--challenger-audit", type=Path, required=True)
    parser.add_argument("--challenger-results", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(compare_component_selection_agreement(**vars(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
