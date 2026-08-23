#!/usr/bin/env python3
"""Validate blind final-review responses and measure model agreement by stratum.

This scorer never promotes a row.  It only records whether the two closed-world
model selections match an independently completed final-review response.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 1
CALIBRATION_PROTOCOL = "vifinqa_ccl_phase5_component_selection_final_review_calibration_v1"
RESPONSE_PROTOCOL = "vifinqa_ccl_phase5_component_selection_final_review_response_v1"
SCORE_PROTOCOL = "vifinqa_ccl_phase5_component_selection_final_review_score_v1"
ASSIGNMENT_NAME = "component_selection_final_review_assignment_v1.jsonl"
RESPONSE_TEMPLATE_NAME = "component_selection_final_review_response_template_v1.jsonl"
LEDGER_NAME = "component_selection_model_comparison_ledger_v1.jsonl"
REPORT_NAME = "component_selection_final_review_calibration_report.json"
MANIFEST_NAME = "component_selection_final_review_calibration_manifest.json"
COMPARISON_NAME = "component_selection_final_review_comparison_v1.jsonl"
SCORE_NAME = "component_selection_final_review_calibration_score_v1.json"
SCORE_MANIFEST_NAME = "component_selection_final_review_score_manifest.json"
RESPONSE_KEYS = {
    "schema_version",
    "protocol",
    "calibration_item_id",
    "immutable_assignment_sha256",
    "reviewer_id",
    "reviewed_at_utc",
    "review_decision",
    "primary_component_id",
    "supporting_component_ids",
    "unresolved_conditions",
    "source_coordinates_checked",
    "training_eligible",
    "certification_allowed",
}
ALLOWED_UNRESOLVED = {"ambiguous_source_components", "insufficient_source_components", "source_text_damage"}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON input: {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSONL input: {path}:{line_number}") from error
            if not isinstance(value, dict):
                raise ValueError(f"expected JSON object: {path}:{line_number}")
            rows.append(value)
    return rows


def _write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    with path.open("x", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _require_hash(path: Path, expected: object, *, label: str) -> None:
    if not isinstance(expected, str) or expected != sha256_file(path):
        raise ValueError(f"SHA-256 mismatch for {label}")


def _id_map(rows: list[dict[str, Any]], key: str, *, label: str) -> dict[str, dict[str, Any]]:
    output = {str(row.get(key) or ""): row for row in rows}
    if not output or len(output) != len(rows) or "" in output:
        raise ValueError(f"{label} identities are malformed")
    return output


def _require_non_promotable(row: Mapping[str, Any], *, label: str) -> None:
    if row.get("training_eligible") is not False or row.get("certification_allowed") is not False:
        raise ValueError(f"{label} is unexpectedly promotable")


def _review_menu(assignment: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    packet = assignment.get("review_packet") or {}
    components = packet.get("components") if isinstance(packet, Mapping) else None
    if not isinstance(components, list):
        raise ValueError("assignment review packet component menu is malformed")
    menu = {str(component.get("component_id") or ""): component for component in components if isinstance(component, Mapping)}
    if not menu or len(menu) != len(components):
        raise ValueError("assignment review packet component identities are malformed")
    return menu


def _valid_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _validate_response(response: Mapping[str, Any], assignment: Mapping[str, Any]) -> None:
    if set(response) != RESPONSE_KEYS:
        raise ValueError("final-review response keys are malformed")
    if (
        response.get("schema_version") != SCHEMA_VERSION
        or response.get("protocol") != RESPONSE_PROTOCOL
        or response.get("calibration_item_id") != assignment.get("calibration_item_id")
        or response.get("immutable_assignment_sha256") != _canonical_sha(assignment)
    ):
        raise ValueError("final-review response is not bound to its immutable assignment")
    _require_non_promotable(response, label="final-review response")
    if (
        not isinstance(response.get("reviewer_id"), str)
        or not response["reviewer_id"].strip()
        or not _valid_timestamp(response.get("reviewed_at_utc"))
        or response.get("source_coordinates_checked") is not True
    ):
        raise ValueError("final-review response lacks reviewer identity, UTC timestamp, or source-coordinate check")
    decision = response.get("review_decision")
    primary = response.get("primary_component_id")
    supporting = response.get("supporting_component_ids")
    unresolved = response.get("unresolved_conditions")
    if (
        not isinstance(supporting, list)
        or len(supporting) > 4
        or len(set(supporting)) != len(supporting)
        or not all(isinstance(component_id, str) for component_id in supporting)
        or not isinstance(unresolved, list)
        or not all(isinstance(condition, str) and condition in ALLOWED_UNRESOLVED for condition in unresolved)
    ):
        raise ValueError("final-review component selection is malformed")
    menu = _review_menu(assignment)
    if decision == "SELECT_COMPONENTS":
        if not isinstance(primary, str) or menu.get(primary, {}).get("role") != "report_scope_or_selected_relation_context":
            raise ValueError("final-review primary component is outside the permitted context menu")
        if any(menu.get(component_id, {}).get("role") not in {"column_header", "row_label"} for component_id in supporting):
            raise ValueError("final-review supporting component is outside the permitted menu")
    elif decision == "ABSTAIN_UNRESOLVED":
        if primary is not None or supporting or not unresolved:
            raise ValueError("final-review abstention must contain no components and at least one unresolved condition")
    else:
        raise ValueError("final-review decision is unsupported")


def _model_comparison(*, response: Mapping[str, Any], model: Mapping[str, Any]) -> dict[str, Any]:
    human_primary = response.get("primary_component_id")
    human_support = set(response.get("supporting_component_ids") or [])
    model_primary = model.get("primary_component_id")
    model_support = set(model.get("supporting_component_ids") or [])
    intersection = human_support & model_support
    union = human_support | model_support
    return {
        "primary_match": human_primary == model_primary,
        "support_exact_match": human_support == model_support,
        "support_intersection_count": len(intersection),
        "support_union_count": len(union),
        "support_jaccard": 1.0 if not union else len(intersection) / len(union),
    }


def _metrics(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    completed = len(rows)
    models = ("qwen", "mistral")
    output: dict[str, Any] = {
        "reviewed_item_count": completed,
        "human_abstention_count": sum(row["human_review"]["review_decision"] == "ABSTAIN_UNRESOLVED" for row in rows),
    }
    for model in models:
        comparisons = [row[f"{model}_comparison"] for row in rows]
        output[f"{model}_primary_match_count"] = sum(item["primary_match"] for item in comparisons)
        output[f"{model}_support_exact_match_count"] = sum(item["support_exact_match"] for item in comparisons)
        output[f"{model}_mean_support_jaccard"] = (
            sum(item["support_jaccard"] for item in comparisons) / completed if completed else None
        )
    return output


def score_final_review_calibration(
    *,
    assignment: Path,
    response_template: Path,
    ledger: Path,
    calibration_manifest: Path,
    responses: Path,
    output_dir: Path,
) -> dict[str, Any]:
    inputs = {
        "assignment": assignment.resolve(),
        "response_template": response_template.resolve(),
        "ledger": ledger.resolve(),
        "calibration_manifest": calibration_manifest.resolve(),
        "responses": responses.resolve(),
    }
    if any(not path.is_file() for path in inputs.values()):
        missing = [str(path) for path in inputs.values() if not path.is_file()]
        raise FileNotFoundError(f"missing final-review scoring input: {missing}")
    if output_dir.exists() or output_dir.resolve() in {path.resolve() for path in inputs.values()}:
        raise FileExistsError("final-review score output-dir must be new and distinct from its inputs")
    before_hashes = {name: sha256_file(path) for name, path in inputs.items()}
    manifest = _json(inputs["calibration_manifest"])
    if (
        manifest.get("protocol") != CALIBRATION_PROTOCOL
        or manifest.get("run_status") != "final_review_calibration_assignment_ready_not_scored"
        or manifest.get("model_decisions_blinded") is not True
        or manifest.get("human_review_required") is not True
    ):
        raise ValueError("calibration manifest is not a blind, unscored final-review package")
    _require_non_promotable(manifest, label="calibration manifest")
    outputs = manifest.get("outputs") or {}
    for path, name, label in (
        (inputs["assignment"], ASSIGNMENT_NAME, "final-review assignment"),
        (inputs["response_template"], RESPONSE_TEMPLATE_NAME, "final-review response template"),
        (inputs["ledger"], LEDGER_NAME, "model comparison ledger"),
    ):
        _require_hash(path, (outputs.get(name) or {}).get("sha256"), label=label)

    assignment_by_id = _id_map(_jsonl(inputs["assignment"]), "calibration_item_id", label="final-review assignment")
    template_by_id = _id_map(_jsonl(inputs["response_template"]), "calibration_item_id", label="final-review response template")
    ledger_by_id = _id_map(_jsonl(inputs["ledger"]), "calibration_item_id", label="model comparison ledger")
    if set(assignment_by_id) != set(template_by_id) or set(assignment_by_id) != set(ledger_by_id):
        raise ValueError("calibration package does not cover one consistent item set")
    for item_id, item in assignment_by_id.items():
        _require_non_promotable(item, label=f"assignment {item_id}")
        if item.get("protocol") != CALIBRATION_PROTOCOL or item.get("model_decisions_blinded") is not True:
            raise ValueError("review assignment weakens the blind calibration contract")
        template = template_by_id[item_id]
        if (
            template.get("protocol") != RESPONSE_PROTOCOL
            or template.get("immutable_assignment_sha256") != _canonical_sha(item)
            or any(template.get(key) is not None for key in ("reviewer_id", "reviewed_at_utc", "review_decision", "primary_component_id"))
            or template.get("supporting_component_ids") != []
            or template.get("unresolved_conditions") != []
            or template.get("source_coordinates_checked") is not None
        ):
            raise ValueError("response template is not blank or is not bound to its assignment")
        model_ledger = ledger_by_id[item_id]
        if (
            model_ledger.get("protocol") != "ccl_phase5_component_selection_agreement_v1"
            or model_ledger.get("review_stratum") != item.get("review_stratum")
        ):
            raise ValueError("model comparison ledger does not bind the review stratum")
        _require_non_promotable(model_ledger, label=f"model ledger {item_id}")

    response_by_id = _id_map(_jsonl(inputs["responses"]), "calibration_item_id", label="final-review response")
    if set(response_by_id) != set(assignment_by_id):
        raise ValueError("final-review responses must cover exactly the immutable assignment")
    comparison_rows: list[dict[str, Any]] = []
    for item_id in sorted(assignment_by_id):
        assignment_row = assignment_by_id[item_id]
        response = response_by_id[item_id]
        _validate_response(response, assignment_row)
        model_ledger = ledger_by_id[item_id]
        qwen = model_ledger.get("primary_model") or {}
        mistral = model_ledger.get("challenger_model") or {}
        if not isinstance(qwen, Mapping) or not isinstance(mistral, Mapping):
            raise ValueError("model comparison ledger selections are malformed")
        qwen_comparison = _model_comparison(response=response, model=qwen)
        mistral_comparison = _model_comparison(response=response, model=mistral)
        comparison_rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "protocol": SCORE_PROTOCOL,
                "calibration_item_id": item_id,
                "component_selection_packet_id": assignment_row["review_packet"]["component_selection_packet_id"],
                "review_stratum": assignment_row["review_stratum"],
                "human_review": {
                    "review_decision": response["review_decision"],
                    "primary_component_id": response["primary_component_id"],
                    "supporting_component_ids": response["supporting_component_ids"],
                    "unresolved_conditions": response["unresolved_conditions"],
                },
                "qwen_comparison": qwen_comparison,
                "mistral_comparison": mistral_comparison,
                "training_eligible": False,
                "certification_allowed": False,
            }
        )
    after_hashes = {name: sha256_file(path) for name, path in inputs.items()}
    if after_hashes != before_hashes:
        raise ValueError("final-review scoring changed a hash-bound input")

    output_dir.mkdir(parents=True)
    comparison_path = output_dir / COMPARISON_NAME
    _write_jsonl(comparison_path, comparison_rows)
    by_stratum: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in comparison_rows:
        by_stratum[str(row["review_stratum"])].append(row)
    score = {
        "schema_version": SCHEMA_VERSION,
        "protocol": SCORE_PROTOCOL,
        "run_status": "final_review_calibration_scored_not_promoted",
        "global_metrics": _metrics(comparison_rows),
        "stratum_metrics": {stratum: _metrics(rows) for stratum, rows in sorted(by_stratum.items())},
        "input_hashes_unchanged": True,
        "training_eligible_output_count": 0,
        "certification_allowed": False,
        "next_gate": "explicit_policy_review_of_calibration_error_and_sample_size_before_any_bounded_pilot",
    }
    score_path = output_dir / SCORE_NAME
    score_path.write_text(json.dumps(score, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    score_manifest = {
        "schema_version": SCHEMA_VERSION,
        "protocol": SCORE_PROTOCOL,
        "run_status": "final_review_calibration_scored_not_promoted",
        "inputs": {name: {"path": str(path), "sha256": before_hashes[name]} for name, path in inputs.items()},
        "outputs": {
            COMPARISON_NAME: {"sha256": sha256_file(comparison_path)},
            SCORE_NAME: {"sha256": sha256_file(score_path)},
        },
        "training_eligible": False,
        "certification_allowed": False,
    }
    (output_dir / SCORE_MANIFEST_NAME).write_text(
        json.dumps(score_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return score


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assignment", type=Path, required=True)
    parser.add_argument("--response-template", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--calibration-manifest", type=Path, required=True)
    parser.add_argument("--responses", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(score_final_review_calibration(**vars(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
