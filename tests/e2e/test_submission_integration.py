from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import yaml

from finance_query import cli


def _builder_module():
    path = Path(__file__).resolve().parents[2] / "scripts/e2e/build_competition_submission_v1.py"
    spec = importlib.util.spec_from_file_location("competition_submission_builder_integration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_research_coordinates_are_hydrated_without_copying_raw_values(tmp_path: Path) -> None:
    builder = _builder_module()
    research_path = tmp_path / "period_candidates.jsonl"
    research_path.write_text(
        json.dumps(
            {
                "question_id": 7,
                "packet_status": "unique_period_column_candidate",
                "protocol": "period_column_candidate_packets_v1",
                "stages": [
                    {
                        "stage_id": "stage_metric",
                        "required_operands": [
                            {
                                "period_column_candidates": [
                                    {
                                        "internal_table_uid": "uid-1",
                                        "row_index": 1,
                                        "column_index": 2,
                                        "raw_value": "999999999",
                                    }
                                ]
                            }
                        ],
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    hints, stats = builder.load_research_candidate_hints(
        [research_path],
        tables_by_uid={
            "uid-1": {
                "document_id": "DOC_2022",
                "rows": [["Header", "2022", "VND"], ["Metric", "x", "123"]],
                "column_labels": ["Nhãn dòng", "2022", "VND"],
            }
        },
    )
    assert stats["coordinates_accepted"] == 1
    assert hints[7][0]["internal_table_uid"] == "uid-1"
    assert "raw_value" not in hints[7][0]


def test_reranker_policy_requires_explicit_model_opt_in() -> None:
    builder = _builder_module()

    with pytest.raises(ValueError, match="baseline_no_reranker"):
        builder.resolve_reranker_policy(
            SimpleNamespace(
                reranker_policy="baseline_no_reranker",
                finetuned_model_root=Path("/tmp/model"),
            )
        )
    assert builder.resolve_reranker_policy(
        SimpleNamespace(
            reranker_policy="use_finetuned_reranker",
            finetuned_model_root=Path("/tmp/model"),
        )
    ) == "use_finetuned_reranker"


def test_period_neighbor_dense_expansion_is_hydrated_and_tagged(monkeypatch) -> None:
    builder = _builder_module()
    observed_requests: list[dict[str, object]] = []

    def fake_search_dense_batch(**kwargs):
        observed_requests.extend(kwargs["requests"])
        return [
            [
                {
                    "internal_table_uid": "uid-exact",
                    "document_id": "AAA_financial_statements_2020_separate",
                    "rank": 1,
                    "score": 0.91,
                }
            ],
            [
                {
                    "internal_table_uid": "uid-neighbor",
                    "document_id": "AAA_financial_statements_2021_separate",
                    "rank": 2,
                    "score": 0.83,
                }
            ],
        ]

    monkeypatch.setattr(
        "finance_query.e2e.core.dense_retrieval.search_dense_batch",
        fake_search_dense_batch,
    )
    items = {
        7: {
            "question": "Doanh thu năm 2020 là bao nhiêu?",
            "question_plan": {
                "tickers": ["AAA"],
                "years": [2020],
                "scope": "separate",
            },
            "candidates": [],
        }
    }
    tables = {
        "uid-exact": {
            "internal_table_uid": "uid-exact",
            "document_id": "AAA_financial_statements_2020_separate",
            "rows": [["", "2020"], ["Doanh thu", "10"]],
        },
        "uid-neighbor": {
            "internal_table_uid": "uid-neighbor",
            "document_id": "AAA_financial_statements_2021_separate",
            "rows": [["", "2020", "2021"], ["Doanh thu", "10", "11"]],
        },
    }

    expanded, stats = builder.expand_review_items_with_dense(
        items=items,
        tables_by_uid=tables,
        index_dir=Path("unused-index"),
        limit=10,
        period_neighbor_offset=1,
    )

    assert [request["report_year"] for request in observed_requests] == [2020, 2021]
    assert stats["requests"] == 2
    assert stats["period_neighbor_requests"] == 1
    assert stats["period_neighbor_hits"] == 1
    assert stats["period_neighbor_hydrated_hits"] == 1
    candidates = expanded[7]["candidates"]
    assert candidates[0]["period_neighbor_offset"] == 0
    assert candidates[1]["period_neighbor_offset"] == 1
    assert candidates[1]["period_request_year"] == 2021


def test_period_neighbor_exact_year_wins_when_years_overlap(monkeypatch) -> None:
    builder = _builder_module()
    observed_requests: list[dict[str, object]] = []

    def fake_search_dense_batch(**kwargs):
        observed_requests.extend(kwargs["requests"])
        results = []
        for request in kwargs["requests"]:
            year = int(request["report_year"])
            if year == 2022:
                prefix = "neighbor-2022"
            else:
                prefix = f"exact-{year}"
            results.append(
                [
                    {
                        "internal_table_uid": f"{prefix}-{rank}",
                        "document_id": f"AAA_financial_statements_{year}_separate",
                        "rank": rank,
                        "score": 1.0 / rank,
                    }
                    for rank in range(1, 42)
                ]
            )
        return results

    # The imported function resolves search_dense_batch from its module at
    # call time, so patch the actual retrieval module as well.
    monkeypatch.setattr(
        "finance_query.e2e.core.dense_retrieval.search_dense_batch",
        fake_search_dense_batch,
    )
    tables = {
        f"{prefix}-{rank}": {
            "internal_table_uid": f"{prefix}-{rank}",
            "document_id": f"AAA_financial_statements_{year}_separate",
            "rows": [["", "2020", "2021"], ["Doanh thu", "10", "11"]],
        }
        for prefix, year in (("exact-2020", 2020), ("exact-2021", 2021), ("neighbor-2022", 2022))
        for rank in range(1, 42)
    }
    items = {
        7: {
            "id": 7,
            "question": "Doanh thu AAA năm 2020 và 2021 là bao nhiêu?",
            "question_plan": {"tickers": ["AAA"], "years": [2020, 2021], "scope": "separate"},
            "candidates": [],
        }
    }

    expanded, stats = builder.expand_review_items_with_dense(
        items=items,
        tables_by_uid=tables,
        index_dir=Path("unused-index"),
        limit=10,
        period_neighbor_offset=1,
    )

    assert [request["report_year"] for request in observed_requests] == [2020, 2021, 2022]
    candidates = expanded[7]["candidates"]
    assert stats["requests"] == 3
    assert [candidate["period_request_year"] for candidate in candidates if candidate["internal_table_uid"] == "exact-2021-1"] == [2021]
    assert [candidate["period_neighbor_offset"] for candidate in candidates if candidate["internal_table_uid"] == "exact-2021-1"] == [0]
    assert any(candidate["period_neighbor_offset"] == 1 for candidate in candidates)


def test_period_aware_emission_reserves_a_bounded_neighbor_prefix() -> None:
    builder = _builder_module()
    candidates = [
        {"internal_table_uid": "standard-1", "rank": 1},
        {"internal_table_uid": "standard-2", "rank": 2},
        {"internal_table_uid": "standard-3", "rank": 3},
        {"internal_table_uid": "neighbor-1", "rank": 4, "period_neighbor_offset": 1},
        {"internal_table_uid": "standard-4", "rank": 5},
        {"internal_table_uid": "neighbor-2", "rank": 6, "period_neighbor_offset": 1},
    ]
    selected = builder.period_aware_emission_candidates(
        candidates,
        limit=6,
        neighbor_slots=2,
    )
    assert [row["internal_table_uid"] for row in selected] == [
        "standard-1",
        "standard-2",
        "standard-3",
        "neighbor-1",
        "neighbor-2",
        "standard-4",
    ]


def test_period_neighbor_navigation_only_preserves_pre_dense_answer_pool() -> None:
    builder = _builder_module()
    pre_dense = {
        7: {
            "question": "question",
            "candidates": [
                {"internal_table_uid": "review-uid", "period_neighbor_offset": 0},
            ],
        }
    }
    expanded = {
        7: {
            "question": "question",
            "candidates": [
                {"internal_table_uid": "review-uid", "period_neighbor_offset": 0},
                {"internal_table_uid": "exact-dense-uid", "period_neighbor_offset": 0},
                {"internal_table_uid": "neighbor-uid", "period_neighbor_offset": 1},
            ],
        }
    }

    selected = builder._answer_review_items_for_period_neighbor(
        expanded,
        period_neighbor_offset=1,
        navigation_only=True,
        pre_dense_items=pre_dense,
    )

    assert [candidate["internal_table_uid"] for candidate in selected[7]["candidates"]] == [
        "review-uid"
    ]
    assert [candidate["internal_table_uid"] for candidate in expanded[7]["candidates"]] == [
        "review-uid",
        "exact-dense-uid",
        "neighbor-uid",
    ]


def test_neighbor_arm_does_not_add_context_to_precise_direct_lookup() -> None:
    builder = _builder_module()
    assert builder.should_emit_candidate_context(
        family="direct_lookup",
        tier="direct_source_replay_v1",
        period_neighbor_offset=1,
    ) is False
    assert builder.should_emit_candidate_context(
        family="direct_lookup",
        tier="source_first_exact_row_v1",
        period_neighbor_offset=1,
    ) is False
    assert builder.should_emit_candidate_context(
        family="direct_lookup",
        tier="semantic_cell_heuristic",
        period_neighbor_offset=1,
    ) is True
    assert builder.should_emit_candidate_context(
        family="multi_operand",
        tier="program_multi_entity_plan",
        period_neighbor_offset=None,
    ) is True


def test_model_answer_requires_current_table_and_final_critic_replay() -> None:
    builder = _builder_module()
    record = {
        "id": 7,
        "execution_status": "grounded",
        "grounding_status": "staged_exact_cells_replayed",
        "result_value": "7.5",
        "result_unit": "percent",
        "direct_replay_gate": {
            "stage": {
                "status": "direct_replay_ready",
                "replayed_bindings": [
                    {
                        "internal_table_uid": "uid-1",
                        "document_id": "DOC_2022",
                        "row_index": 1,
                        "column_index": 2,
                        "raw_value": "123",
                        "variable_id": "metric",
                    }
                ],
            }
        },
        "independent_critic_gate": {
            "stage": {
                "status": "independent_critic_ready",
                "deterministic_stage_execution": {
                    "aggregate_value": "7.5",
                    "final_stage": True,
                },
            }
        },
    }
    tables = {
        "uid-1": {
            "document_id": "DOC_2022",
            "rows": [["Header", "2022", "VND"], ["Metric", "x", "123"]],
        }
    }
    item = {"question_plan": {"requested_unit": "percent"}}
    replay = builder.validate_model_answer_candidate(
        record,
        question_item=item,
        tables_by_uid=tables,
    )
    assert replay is not None
    assert replay[0] == builder.Decimal("7.5")

    record["direct_replay_gate"]["stage"]["replayed_bindings"][0]["raw_value"] = "999"
    assert (
        builder.validate_model_answer_candidate(
            record,
            question_item=item,
            tables_by_uid=tables,
        )
        is None
    )


def test_cli_exposes_primary_submission_command(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "finance-query",
            "build-submission",
            "--disable-research-fusion",
            "--require-source-line-map",
        ],
    )
    args = cli.parse_args()
    assert args.command == "build-submission"
    assert args.disable_research_fusion is True
    assert args.require_source_line_map is True


def test_cli_exposes_period_neighbor_experiment_controls(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "finance-query",
            "build-submission",
            "--period-neighbor-offset",
            "1",
            "--period-neighbor-table-slots",
            "3",
            "--period-neighbor-navigation-only",
        ],
    )
    args = cli.parse_args()
    assert args.period_neighbor_offset == 1
    assert args.period_neighbor_table_slots == 3
    assert args.period_neighbor_navigation_only is True


def test_cli_exposes_embedded_verifier_controls(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "finance-query",
            "build-submission",
            "--verification-config",
            "configs/e2e/deterministic_replay_v1_locked.yaml",
            "--verification-candidate-limit",
            "3",
        ],
    )
    args = cli.parse_args()
    assert args.verification_config.name == "deterministic_replay_v1_locked.yaml"
    assert args.verification_candidate_limit == 3


def test_embedded_verifier_replaces_only_the_candidate_ledger_and_drops_history_fixtures(
    tmp_path: Path, monkeypatch
) -> None:
    builder = _builder_module()
    config = tmp_path / "locked.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "protocol": "vifinqa_grounded_e2e_v1",
                "run_name": "historical-run",
                "paths": {
                    "period_packets": "period.jsonl",
                    "best_candidate_predictions": "historical_candidates.jsonl",
                    "expected_bindings": "historical_bindings.jsonl",
                },
            }
        ),
        encoding="utf-8",
    )
    ledger = tmp_path / "current_candidates.jsonl"
    ledger.write_text("", encoding="utf-8")
    output = tmp_path / "build"
    output.mkdir()
    observed: dict[str, object] = {}

    def fake_load(path: Path):
        observed["config"] = yaml.safe_load(path.read_text(encoding="utf-8"))
        return object()

    def fake_run(_inputs: object, *, output_dir: Path):
        output_dir.mkdir()
        (output_dir / "answer_certificates_v1.jsonl").write_text("", encoding="utf-8")

    monkeypatch.setattr(builder, "load_deterministic_replay_inputs", fake_load)
    monkeypatch.setattr(builder, "run_deterministic_replay", fake_run)
    certificates = builder._run_submission_verifier(
        base_config=config,
        candidate_ledger_path=ledger,
        output_dir=output,
    )
    generated = observed["config"]
    assert generated["run_name"] == "submission-proposal-verifier"
    assert generated["paths"]["best_candidate_predictions"] == str(ledger.resolve())
    assert "expected_bindings" not in generated["paths"]
    assert generated["paths"]["period_packets"] == str((tmp_path / "period.jsonl").resolve())
    assert certificates.name == "answer_certificates_v1.jsonl"
