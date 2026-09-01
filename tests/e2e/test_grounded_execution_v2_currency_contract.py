from __future__ import annotations

import hashlib
import json
from pathlib import Path

from finance_query.e2e.core.grounded_authorization import _composed_binding_plan
from finance_query.e2e.core.grounded_execution_v2 import run


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _binding(question_id: int) -> dict[str, object]:
    request = {
        "kind": "currency",
        "unit": "ty_dong",
        "vnd_to_output_divisor": "1000000000",
    }
    return {
        "question_id": question_id,
        "route_status": "route_complete",
        "binding_packet_status": "binding_ready",
        "question_context": {"years": [2024]},
        "requested_output_unit": request,
        "stages": [
            {
                "stage_id": "stage_1",
                "route_kind": "reported_concept",
                "metric_id": None,
                "required_operands": [
                    {
                        "binding_status": "binding_ready",
                        "formula_output_kind": "currency",
                        "requested_output_unit": request,
                        "raw_decimal_candidate": "2500",
                        "source_unit": "trieu_dong",
                        "source_to_vnd_multiplier": "1000000",
                        "document_id": f"doc-{question_id}",
                        "internal_table_uid": f"table-{question_id}",
                        "row_index": 1,
                        "column_index": 1,
                        "cell_provenance": {"source": "test"},
                        "role": "value",
                    }
                ],
            }
        ],
    }


def _mean_binding(question_id: int, *, shared_table: bool = False) -> dict[str, object]:
    request = {
        "kind": "currency",
        "unit": "ty_dong",
        "vnd_to_output_divisor": "1000000000",
    }
    stage_ids = ["stage_1", "stage_2", "stage_3"]
    stages = []
    for index, (stage_id, raw) in enumerate(zip(stage_ids, ("1000", "2000", "3000"), strict=True), start=1):
        stages.append(
            {
                "stage_id": stage_id,
                "route_kind": "reported_concept",
                "metric_id": None,
                "required_operands": [
                    {
                        "binding_status": "binding_ready",
                        "formula_output_kind": "currency",
                        "requested_output_unit": request,
                        "raw_decimal_candidate": raw,
                        "source_unit": "trieu_dong",
                        "source_to_vnd_multiplier": "1000000",
                        "document_id": f"doc-{question_id}-{stage_id}",
                        "internal_table_uid": (
                            f"table-{question_id}"
                            if shared_table
                            else f"table-{question_id}-{stage_id}"
                        ),
                        "row_index": index,
                        "column_index": index,
                        "cell_provenance": {"source": "test"},
                        "role": "value",
                    }
                ],
            }
        )
    binding_args = [f"q{question_id}:stage:{stage_id}:role:value" for stage_id in stage_ids]
    return {
        "question_id": question_id,
        "route_status": "route_complete",
        "binding_packet_status": "binding_ready",
        "question_context": {"entities": ["AAA", "BBB", "CCC"], "years": [2024]},
        "requested_output_unit": request,
        "stages": stages,
        "controlled_operation_graph": {
            "protocol": "vifinqa_controlled_composition_graph_v1",
            "stage_order": stage_ids,
            "operation_ast": {"op": "mean", "args": stage_ids},
            "binding_operation_ast": {"op": "mean", "args": binding_args},
            "final_node_id": "mean_1",
        },
    }


def test_public_grounded_executor_checks_the_currency_contract_before_converting(tmp_path: Path) -> None:
    bindings = tmp_path / "bindings.jsonl"
    bindings.write_text(
        "".join(json.dumps(_binding(question_id)) + "\n" for question_id in range(1, 1013)),
        encoding="utf-8",
    )
    bindings_manifest = tmp_path / "bindings.manifest.json"
    bindings_manifest.write_text(
        json.dumps({"outputs": {"bindings": {"sha256": _sha(bindings)}}}), encoding="utf-8"
    )
    registry = tmp_path / "metrics.yaml"
    registry.write_text("metrics: []\n", encoding="utf-8")
    output = tmp_path / "execution.jsonl"

    summary = run(
        bindings=bindings,
        bindings_manifest=bindings_manifest,
        metric_registry=registry,
        output=output,
    )

    first = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    trace = first["stage_traces"][0]
    assert summary["counts"]["execution_status_counts"] == {"execution_replay_ready": 1012}
    assert trace["converted_output_decimal"] == "2.5"
    assert trace["requested_output_unit"]["unit"] == "ty_dong"


def test_public_grounded_executor_executes_a_three_entity_mean_in_vnd_then_converts(tmp_path: Path) -> None:
    bindings = tmp_path / "bindings.jsonl"
    bindings.write_text(
        "".join(json.dumps(_mean_binding(question_id)) + "\n" for question_id in range(1, 1013)),
        encoding="utf-8",
    )
    bindings_manifest = tmp_path / "bindings.manifest.json"
    bindings_manifest.write_text(
        json.dumps({"outputs": {"bindings": {"sha256": _sha(bindings)}}}), encoding="utf-8"
    )
    registry = tmp_path / "metrics.yaml"
    registry.write_text("metrics: []\n", encoding="utf-8")
    output = tmp_path / "execution.jsonl"

    summary = run(
        bindings=bindings,
        bindings_manifest=bindings_manifest,
        metric_registry=registry,
        output=output,
    )

    first = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    composition = first["composition_trace"]
    assert summary["counts"]["execution_status_counts"] == {"execution_replay_ready": 1012}
    assert composition["status"] == "execution_replay_ready"
    assert composition["execution_kind"] == "controlled_cross_stage_composition"
    assert composition["converted_output_decimal"] == "2"


def test_public_grounded_executor_allows_many_exact_cells_from_one_table(tmp_path: Path) -> None:
    bindings = tmp_path / "bindings.jsonl"
    bindings.write_text(
        "".join(
            json.dumps(_mean_binding(question_id, shared_table=True)) + "\n"
            for question_id in range(1, 1013)
        ),
        encoding="utf-8",
    )
    bindings_manifest = tmp_path / "bindings.manifest.json"
    bindings_manifest.write_text(
        json.dumps({"outputs": {"bindings": {"sha256": _sha(bindings)}}}), encoding="utf-8"
    )
    registry = tmp_path / "metrics.yaml"
    registry.write_text("metrics: []\n", encoding="utf-8")
    output = tmp_path / "execution.jsonl"

    summary = run(
        bindings=bindings,
        bindings_manifest=bindings_manifest,
        metric_registry=registry,
        output=output,
    )

    first = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    sources = [trace["operand_sources"][0] for trace in first["stage_traces"]]
    assert summary["counts"]["execution_status_counts"] == {"execution_replay_ready": 1012}
    assert {source["internal_table_uid"] for source in sources} == {"table-1"}
    assert {(source["row_index"], source["column_index"]) for source in sources} == {
        (1, 1),
        (2, 2),
        (3, 3),
    }
    assert first["composition_trace"]["converted_output_decimal"] == "2"


def test_authorization_builds_a_typed_plan_for_a_three_entity_mean() -> None:
    request = {
        "kind": "currency",
        "unit": "ty_dong",
        "vnd_to_output_divisor": "1000000000",
    }
    stage_ids = ["stage_1", "stage_2", "stage_3"]
    binding_ids = [f"q21:stage:{stage_id}:role:value" for stage_id in stage_ids]
    question = {
        "question_context": {"years": [2024], "scope": "consolidated"},
        "requested_output_unit": request,
        "controlled_operation_graph": {
            "protocol": "vifinqa_controlled_composition_graph_v1",
            "stage_order": stage_ids,
            "operation_ast": {"op": "mean", "args": stage_ids},
            "binding_operation_ast": {"op": "mean", "args": binding_ids},
        },
    }
    materialized_stages = []
    for stage_id, entity, binding_id in zip(stage_ids, ("AAA", "BBB", "CCC"), binding_ids, strict=True):
        stage = {
            "stage_id": stage_id,
            "required_operands": [{"role": "value", "concept_id": "loan_loss_provision"}],
        }
        binding = {
            "operand_id": binding_id,
            "entity_scope_binding": {"entity": entity},
            "entity_role_binding": {"role": "consolidated"},
        }
        materialized_stages.append((stage, stage["required_operands"], [binding]))

    plan = _composed_binding_plan(question=question, materialized_stages=materialized_stages)

    assert plan["operation_ast"] == {"op": "mean", "args": binding_ids}
    assert plan["formula_compatibility"]["formula_id"] == "controlled_cross_entity_mean"
    assert plan["formula_compatibility"]["different_entity_required"] is True


def test_authorization_builds_a_temporal_same_entity_subtract_plan() -> None:
    request = {
        "kind": "currency",
        "unit": "ty_dong",
        "vnd_to_output_divisor": "1000000000",
    }
    stage_ids = ["stage_new", "stage_old"]
    binding_ids = [f"q31:stage:{stage_id}:role:{role}" for stage_id, role in zip(stage_ids, ("x1", "x0"), strict=True)]
    question = {
        "question_context": {"entities": ["AAA"], "years": [2023, 2024], "scope": "separate"},
        "requested_output_unit": request,
        "controlled_operation_graph": {
            "protocol": "vifinqa_controlled_composition_graph_v1",
            "composition_mode": "temporal_same_entity_two_period_subtract_v1",
            "stage_order": stage_ids,
            "operation_ast": {"op": "subtract", "args": stage_ids},
            "binding_operation_ast": {"op": "subtract", "args": binding_ids},
        },
    }
    materialized_stages = []
    for stage_id, role, year, binding_id in zip(stage_ids, ("x1", "x0"), (2024, 2023), binding_ids, strict=True):
        stage = {
            "stage_id": stage_id,
            "required_operands": [{"role": role, "concept_id": "temporal_value", "period_labels": [str(year)]}],
        }
        binding = {
            "operand_id": binding_id,
            "entity_scope_binding": {"entity": "AAA"},
            "entity_role_binding": {"role": None},
        }
        materialized_stages.append((stage, stage["required_operands"], [binding]))

    plan = _composed_binding_plan(question=question, materialized_stages=materialized_stages)

    assert plan["operation_ast"] == {"op": "subtract", "args": binding_ids}
    assert plan["formula_compatibility"]["formula_id"] == "controlled_temporal_same_entity_subtract"
    assert plan["formula_compatibility"]["same_entity_required"] is True
    assert all("period_years" not in rule["field"] for rule in plan["formula_compatibility"]["cross_operand_rules"])


def test_authorization_builds_a_single_period_same_entity_subtract_plan() -> None:
    request = {
        "kind": "currency",
        "unit": "ty_dong",
        "vnd_to_output_divisor": "1000000000",
    }
    stage_ids = ["stage_income", "stage_expense"]
    binding_ids = [
        f"q32:stage:{stage_id}:role:{role}"
        for stage_id, role in zip(stage_ids, ("income", "expense"), strict=True)
    ]
    question = {
        "question_context": {"entities": ["AAA"], "years": [2024], "scope": "separate"},
        "requested_output_unit": request,
        "controlled_operation_graph": {
            "protocol": "vifinqa_controlled_composition_graph_v1",
            "composition_mode": "single_period_same_entity_subtract_v1",
            "stage_order": stage_ids,
            "operation_ast": {"op": "subtract", "args": stage_ids},
            "binding_operation_ast": {"op": "subtract", "args": binding_ids},
        },
    }
    materialized_stages = []
    for stage_id, role, binding_id in zip(stage_ids, ("income", "expense"), binding_ids, strict=True):
        stage = {
            "stage_id": stage_id,
            "required_operands": [{"role": role, "concept_id": role, "period_labels": ["2024"]}],
        }
        binding = {
            "operand_id": binding_id,
            "entity_scope_binding": {"entity": "AAA"},
            "entity_role_binding": {"role": None},
        }
        materialized_stages.append((stage, stage["required_operands"], [binding]))

    plan = _composed_binding_plan(question=question, materialized_stages=materialized_stages)

    assert plan["operation_ast"] == {"op": "subtract", "args": binding_ids}
    assert plan["formula_compatibility"]["formula_id"] == "controlled_single_period_same_entity_subtract"
    assert plan["formula_compatibility"]["same_entity_required"] is True
    assert any(rule["field"] == "period_years" for rule in plan["formula_compatibility"]["cross_operand_rules"])


def test_authorization_builds_a_same_entity_multi_period_argmax_plan() -> None:
    stage_ids = ["y2020", "y2021", "y2023"]
    binding_ids = [f"q33:stage:{stage_id}:role:value" for stage_id in stage_ids]
    stage_periods = {"y2020": 2020, "y2021": 2021, "y2023": 2023}
    question = {
        "question_context": {"entities": ["AAA"], "years": [2020, 2021, 2023], "scope": "separate"},
        "requested_output_unit": {"kind": "period", "unit": "year", "vnd_to_output_divisor": None},
        "controlled_operation_graph": {
            "protocol": "vifinqa_controlled_composition_graph_v1",
            "composition_mode": "same_entity_multi_period_argmax_v1",
            "stage_order": stage_ids,
            "stage_periods": stage_periods,
            "operation_ast": {"op": "arg_extreme_period", "direction": "max", "args": stage_ids},
            "binding_operation_ast": {
                "op": "arg_extreme_period",
                "direction": "max",
                "args": binding_ids,
            },
        },
    }
    materialized_stages = []
    for stage_id, binding_id in zip(stage_ids, binding_ids, strict=True):
        stage = {
            "stage_id": stage_id,
            "required_operands": [{"role": "value", "concept_id": "operating_cash_flow", "period_labels": [str(stage_periods[stage_id])]}],
        }
        binding = {
            "operand_id": binding_id,
            "entity_scope_binding": {"entity": "AAA"},
            "entity_role_binding": {"role": None},
        }
        materialized_stages.append((stage, stage["required_operands"], [binding]))

    plan = _composed_binding_plan(question=question, materialized_stages=materialized_stages)

    assert plan["operation_ast"] == {"op": "arg_extreme_period", "direction": "max", "args": binding_ids}
    assert plan["formula_compatibility"]["formula_id"] == "controlled_same_entity_multi_period_argmax"
    assert plan["formula_compatibility"]["same_entity_required"] is True
