"""V2 Decimal executor with cell-token verification and sandbox telemetry."""
from __future__ import annotations

from collections import Counter
from decimal import Decimal, DivisionByZero, InvalidOperation
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import yaml

from .execution_sandbox import DecimalSandboxPolicy, SandboxViolation, execute_decimal_ast
from .numeric_cell_tokens import (
    NumericCellTokenError,
    load_numeric_cell_tokens,
    verify_operand_token,
)


PROTOCOL = "grounded_execution_replay_v2"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def contract() -> dict[str, bool]:
    return {
        "research_only": True,
        "evidence_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
        "may_select_value": False,
        "may_execute_formula_outside_protocol": False,
    }


def _require(path: Path, expected: object, label: str) -> None:
    if not isinstance(expected, str) or sha(path) != expected:
        raise ValueError(f"SHA-256 mismatch for {label}")


def convert_currency(
    raw: Decimal,
    multiplier: Decimal,
    divisor: Decimal,
) -> tuple[Decimal, Decimal]:
    policy = DecimalSandboxPolicy()
    normalized = execute_decimal_ast(
        {"op": "multiply", "args": ["raw", "multiplier"]},
        {"raw": raw, "multiplier": multiplier},
        policy=policy,
    ).value
    converted = execute_decimal_ast(
        {"op": "divide", "args": ["normalized", "divisor"]},
        {"normalized": normalized, "divisor": divisor},
        policy=policy,
    ).value
    return normalized, converted


def _write_telemetry(
    output: Path,
    records: list[Mapping[str, Any]],
    *,
    policy: DecimalSandboxPolicy,
    token_registry: Path | None,
) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for record in records
        ),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "protocol": "vifinqa_execution_telemetry_v1",
        "sandbox_policy": {
            **policy.to_dict(),
            "policy_sha256": policy.policy_sha256,
            "interpreter": "declarative_decimal_ast_only",
            "python_source_execution": False,
            "filesystem_primitives": False,
            "network_primitives": False,
            "process_primitives": False,
        },
        "inputs": {
            "numeric_cell_token_registry": (
                None
                if token_registry is None
                else {"path": str(token_registry), "sha256": sha(token_registry)}
            )
        },
        "outputs": {"telemetry": {"path": str(output), "sha256": sha(output)}},
        "counts": {
            "record_count": len(records),
            "status_counts": dict(Counter(str(record.get("status")) for record in records)),
        },
        "source_contract": contract(),
    }
    manifest_path = output.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return {**manifest, "manifest_path": str(manifest_path)}


def run(
    *,
    bindings: Path,
    bindings_manifest: Path,
    metric_registry: Path,
    output: Path,
    cell_tokens: Path | None = None,
    cell_tokens_manifest: Path | None = None,
    telemetry_output: Path | None = None,
) -> dict[str, Any]:
    binding_manifest = json.loads(bindings_manifest.read_text())
    _require(bindings, binding_manifest["outputs"]["bindings"]["sha256"], "binding V2")
    if bool(cell_tokens) != bool(cell_tokens_manifest):
        raise ValueError("numeric cell tokens and manifest must be supplied together")
    token_index = (
        load_numeric_cell_tokens(
            cell_tokens,
            cell_tokens_manifest,
            bindings_path=bindings,
            bindings_manifest_path=bindings_manifest,
        )
        if cell_tokens is not None and cell_tokens_manifest is not None
        else None
    )
    registry = {
        item["metric_id"]: item
        for item in yaml.safe_load(metric_registry.read_text())["metrics"]
    }
    records = rows(bindings)
    if len(records) != 1012 or {record["question_id"] for record in records} != set(
        range(1, 1013)
    ):
        raise ValueError("binding V2 coverage mismatch")

    sandbox_policy = DecimalSandboxPolicy()
    telemetry: list[dict[str, Any]] = []
    output_rows: list[dict[str, Any]] = []
    for record in sorted(records, key=lambda value: value["question_id"]):
        traces: list[dict[str, Any]] = []
        stage_status: list[str] = []
        stage_base_values: dict[str, Decimal] = {}
        composition_trace: dict[str, Any] | None = None
        if record["route_status"] != "route_complete":
            overall = "route_incomplete"
        elif record["binding_packet_status"] != "binding_ready":
            overall = "binding_conflict"
        else:
            overall = "execution_replay_ready"
            for stage in record["stages"]:
                metric = registry.get(stage.get("metric_id"))
                values: dict[str, Decimal] = {}
                sources: list[dict[str, Any]] = []
                bad: list[str] = []
                token_ids: list[str] = []
                normalization_sandbox: list[dict[str, Any]] = []
                kind = None
                request = None
                direct_reported_lookup = (
                    metric is None
                    and stage.get("route_kind") == "reported_concept"
                    and len(stage.get("required_operands") or []) == 1
                )
                if not metric and not direct_reported_lookup:
                    stage_status.append("formula_unsupported")
                    traces.append(
                        {
                            "stage_id": stage.get("stage_id"),
                            "status": "formula_unsupported",
                            "reason_codes": ["MISSING_REGISTRY_FORMULA"],
                        }
                    )
                    continue
                for operand in stage["required_operands"]:
                    if operand.get("binding_status") != "binding_ready":
                        bad.append("BINDING_NOT_READY")
                        continue
                    kind = operand.get("formula_output_kind")
                    request = operand.get("requested_output_unit")
                    decimal_literal = str(operand.get("raw_decimal_candidate") or "")
                    if token_index is not None:
                        key = (
                            int(record["question_id"]),
                            str(stage.get("stage_id") or ""),
                            str(operand.get("role") or ""),
                        )
                        token = token_index.get(key)
                        if token is None:
                            bad.append("NUMERIC_CELL_TOKEN_MISSING")
                            continue
                        try:
                            decimal_literal = verify_operand_token(
                                token,
                                question_id=record["question_id"],
                                stage_id=stage.get("stage_id"),
                                operand=operand,
                            )
                        except NumericCellTokenError:
                            bad.append("NUMERIC_CELL_TOKEN_MISMATCH")
                            continue
                        token_ids.append(str(token.get("token_id") or ""))
                    try:
                        raw = Decimal(decimal_literal)
                        multiplier = Decimal(str(operand["source_to_vnd_multiplier"]))
                    except (InvalidOperation, KeyError):
                        bad.append("NUMERIC_OR_UNIT_INVALID")
                        continue
                    if kind != "currency":
                        bad.append("NON_CURRENCY_FORMULA_INPUT_UNSUPPORTED")
                        continue
                    try:
                        normalized = execute_decimal_ast(
                            {"op": "multiply", "args": ["raw", "multiplier"]},
                            {"raw": raw, "multiplier": multiplier},
                            policy=sandbox_policy,
                        )
                    except SandboxViolation:
                        bad.append("NUMERIC_OR_UNIT_SANDBOX_REJECTED")
                        continue
                    values[operand["role"]] = normalized.value
                    normalization_sandbox.append(normalized.telemetry())
                    sources.append(
                        {
                            "role": operand["role"],
                            "raw_value_decimal": format(raw, "f"),
                            "base_vnd_value_decimal": format(normalized.value, "f"),
                            "source_to_vnd_multiplier": format(multiplier, "f"),
                            "document_id": operand["document_id"],
                            "internal_table_uid": operand["internal_table_uid"],
                            "row_index": operand["row_index"],
                            "column_index": operand["column_index"],
                            "cell_provenance": operand["cell_provenance"],
                        }
                    )
                if bad:
                    stage_status.append("binding_conflict")
                    traces.append(
                        {
                            "stage_id": stage.get("stage_id"),
                            "status": "binding_conflict",
                            "reason_codes": sorted(set(bad)),
                            "operand_sources": sources,
                        }
                    )
                    if token_index is not None:
                        telemetry.append(
                            {
                                "question_id": record["question_id"],
                                "stage_id": stage.get("stage_id"),
                                "status": "binding_conflict",
                                "reason_codes": sorted(set(bad)),
                                "numeric_cell_token_ids": sorted(token_ids),
                            }
                        )
                    continue
                try:
                    ast = (
                        {"op": "lookup", "args": [next(iter(values))]}
                        if direct_reported_lookup and len(values) == 1
                        else metric["formula_ast"]
                    )
                    sandbox_result = execute_decimal_ast(
                        ast,
                        values,
                        policy=sandbox_policy,
                    )
                    base = sandbox_result.value
                except DivisionByZero:
                    stage_status.append("execution_abstained")
                    traces.append(
                        {
                            "stage_id": stage.get("stage_id"),
                            "status": "execution_abstained",
                            "reason_codes": ["DIVISION_BY_ZERO"],
                            "operand_sources": sources,
                        }
                    )
                    continue
                except (KeyError, SandboxViolation, TypeError, ValueError):
                    stage_status.append("formula_unsupported")
                    traces.append(
                        {
                            "stage_id": stage.get("stage_id"),
                            "status": "formula_unsupported",
                            "reason_codes": ["FORMULA_UNSUPPORTED"],
                            "operand_sources": sources,
                        }
                    )
                    continue
                if request.get("kind") != "currency":
                    stage_status.append("binding_conflict")
                    traces.append(
                        {
                            "stage_id": stage.get("stage_id"),
                            "status": "binding_conflict",
                            "reason_codes": ["OUTPUT_KIND_CONFLICT"],
                            "operand_sources": sources,
                        }
                    )
                    continue
                try:
                    divisor = Decimal(str(request["vnd_to_output_divisor"]))
                    conversion_result = execute_decimal_ast(
                        {"op": "divide", "args": ["base", "divisor"]},
                        {"base": base, "divisor": divisor},
                        policy=sandbox_policy,
                    )
                    converted = conversion_result.value
                except (DivisionByZero, InvalidOperation, SandboxViolation):
                    stage_status.append("execution_abstained")
                    traces.append(
                        {
                            "stage_id": stage.get("stage_id"),
                            "status": "execution_abstained",
                            "reason_codes": ["OUTPUT_CONVERSION_REJECTED"],
                            "operand_sources": sources,
                        }
                    )
                    continue
                stage_status.append("execution_replay_ready")
                stage_identifier = str(stage.get("stage_id") or "")
                if not stage_identifier or stage_identifier in stage_base_values:
                    stage_status[-1] = "formula_unsupported"
                    traces.append(
                        {
                            "stage_id": stage.get("stage_id"),
                            "status": "formula_unsupported",
                            "reason_codes": ["COMPOSITION_STAGE_ID_INVALID"],
                            "operand_sources": sources,
                        }
                    )
                    continue
                stage_base_values[stage_identifier] = base
                traces.append(
                    {
                        "stage_id": stage.get("stage_id"),
                        "status": "execution_replay_ready",
                        "execution_kind": (
                            "reported_value_lookup"
                            if direct_reported_lookup
                            else "registry_formula"
                        ),
                        "raw_formula_base_vnd_decimal": format(base, "f"),
                        "converted_output_decimal": format(converted, "f"),
                        "requested_output_unit": request,
                        "operand_sources": sources,
                    }
                )
                telemetry.append(
                    {
                        "question_id": record["question_id"],
                        "stage_id": stage.get("stage_id"),
                        "status": "execution_replay_ready",
                        "numeric_cell_token_ids": sorted(token_ids),
                        "sandbox": {
                            "operand_normalization": normalization_sandbox,
                            "formula": sandbox_result.telemetry(),
                            "output_conversion": conversion_result.telemetry(),
                        },
                    }
                )
            precedence = {
                "route_incomplete": 5,
                "dependency_blocked": 4,
                "binding_conflict": 3,
                "formula_unsupported": 2,
                "execution_abstained": 1,
                "execution_replay_ready": 0,
            }
            overall = max(
                stage_status or ["dependency_blocked"],
                key=lambda value: precedence[value],
            )
            controlled_graph = record.get("controlled_operation_graph")
            if isinstance(controlled_graph, Mapping):
                operation_ast = controlled_graph.get("operation_ast")
                binding_operation_ast = controlled_graph.get("binding_operation_ast")
                stage_order = controlled_graph.get("stage_order")
                graph_protocol = str(controlled_graph.get("protocol") or "")
                expected_binding_args: list[str] = []
                if isinstance(stage_order, list):
                    for raw_stage_id in stage_order:
                        stage_id = str(raw_stage_id)
                        matching_stages = [
                            value for value in record.get("stages") or []
                            if str(value.get("stage_id") or "") == stage_id
                        ]
                        operands = matching_stages[0].get("required_operands") or [] if len(matching_stages) == 1 else []
                        if len(operands) == 1:
                            expected_binding_args.append(
                                f"q{record['question_id']}:stage:{stage_id}:role:{operands[0].get('role')}"
                            )
                graph_valid = (
                    graph_protocol == "vifinqa_controlled_composition_graph_v1"
                    and isinstance(stage_order, list)
                    and len(stage_order) == 2
                    and len(set(map(str, stage_order))) == 2
                    and operation_ast == {"op": "subtract", "args": stage_order}
                    and set(map(str, stage_order)) == set(stage_base_values)
                    and isinstance(binding_operation_ast, Mapping)
                    and binding_operation_ast.get("op") == "subtract"
                    and isinstance(binding_operation_ast.get("args"), list)
                    and binding_operation_ast.get("args") == expected_binding_args
                )
                if overall != "execution_replay_ready":
                    composition_trace = {
                        "stage_id": controlled_graph.get("final_node_id"),
                        "status": "dependency_blocked",
                        "reason_codes": ["COMPOSITION_STAGE_DEPENDENCY_NOT_READY"],
                    }
                    overall = "dependency_blocked" if overall == "execution_replay_ready" else overall
                elif not graph_valid:
                    composition_trace = {
                        "stage_id": controlled_graph.get("final_node_id"),
                        "status": "formula_unsupported",
                        "reason_codes": ["CONTROLLED_COMPOSITION_GRAPH_INVALID"],
                    }
                    overall = "formula_unsupported"
                else:
                    request = record.get("requested_output_unit")
                    try:
                        if not isinstance(request, Mapping) or request.get("kind") != "currency":
                            raise ValueError("composition output kind is not currency")
                        composition_result = execute_decimal_ast(
                            operation_ast,
                            stage_base_values,
                            policy=sandbox_policy,
                        )
                        composition_conversion = execute_decimal_ast(
                            {"op": "divide", "args": ["base", "divisor"]},
                            {
                                "base": composition_result.value,
                                "divisor": Decimal(str(request["vnd_to_output_divisor"])),
                            },
                            policy=sandbox_policy,
                        )
                    except (DivisionByZero, InvalidOperation, KeyError, SandboxViolation, TypeError, ValueError):
                        composition_trace = {
                            "stage_id": controlled_graph.get("final_node_id"),
                            "status": "execution_abstained",
                            "reason_codes": ["CONTROLLED_COMPOSITION_EXECUTION_REJECTED"],
                        }
                        overall = "execution_abstained"
                    else:
                        composition_trace = {
                            "stage_id": controlled_graph.get("final_node_id"),
                            "status": "execution_replay_ready",
                            "execution_kind": "controlled_cross_stage_composition",
                            "operation_ast": binding_operation_ast,
                            "operation_ast_sha256": canonical_sha(binding_operation_ast),
                            "raw_formula_base_vnd_decimal": format(composition_result.value, "f"),
                            "converted_output_decimal": format(composition_conversion.value, "f"),
                            "requested_output_unit": request,
                            "stage_order": stage_order,
                            "promotion_decision_sha256": controlled_graph.get("promotion_decision_sha256"),
                        }
                        telemetry.append(
                            {
                                "question_id": record["question_id"],
                                "stage_id": controlled_graph.get("final_node_id"),
                                "status": "execution_replay_ready",
                                "sandbox": {
                                    "composition": composition_result.telemetry(),
                                    "output_conversion": composition_conversion.telemetry(),
                                },
                            }
                        )
                        overall = "execution_replay_ready"
        output_row = {
                "schema_version": 2,
                "protocol": PROTOCOL,
                "question_id": record["question_id"],
                "execution_status": overall,
                "question_context": record["question_context"],
                "stage_traces": traces,
                "source_contract": contract(),
            }
        if composition_trace is not None:
            output_row["composition_trace"] = composition_trace
        output_rows.append(output_row)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for value in output_rows
        )
    )
    inputs: dict[str, Any] = {
        "bindings": {"path": str(bindings), "sha256": sha(bindings)},
        "metric_registry": {"path": str(metric_registry), "sha256": sha(metric_registry)},
        "bindings_manifest": {
            "path": str(bindings_manifest),
            "sha256": sha(bindings_manifest),
        },
    }
    if cell_tokens is not None and cell_tokens_manifest is not None:
        inputs["numeric_cell_tokens"] = {
            "path": str(cell_tokens),
            "sha256": sha(cell_tokens),
            "manifest_path": str(cell_tokens_manifest),
            "manifest_sha256": sha(cell_tokens_manifest),
        }
    outputs: dict[str, Any] = {
        "execution": {"path": str(output), "sha256": sha(output)}
    }
    telemetry_result = None
    if telemetry_output is not None:
        telemetry_result = _write_telemetry(
            telemetry_output,
            telemetry,
            policy=sandbox_policy,
            token_registry=cell_tokens,
        )
        outputs["telemetry"] = telemetry_result["outputs"]["telemetry"]
    result = {
        "schema_version": 2,
        "protocol": PROTOCOL,
        "inputs": inputs,
        "outputs": outputs,
        "counts": {
            "question_count": len(output_rows),
            "execution_status_counts": dict(
                Counter(value["execution_status"] for value in output_rows)
            ),
            "sandbox_execution_count": sum(
                1 for value in telemetry if value.get("sandbox") is not None
            ),
        },
        "sandbox_policy": {
            **sandbox_policy.to_dict(),
            "policy_sha256": sandbox_policy.policy_sha256,
        },
        "source_contract": contract(),
    }
    if telemetry_result is not None:
        result["telemetry_manifest_path"] = telemetry_result["manifest_path"]
    manifest_path = output.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    return {**result, "manifest_path": str(manifest_path)}
