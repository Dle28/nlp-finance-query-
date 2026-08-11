import hashlib
import json
import unittest

from finance_query.llm_staged_audit import (
    direct_replay_selected_stage,
    execution_matches,
    independently_execute_packet_stage,
)


def with_hash(packet: dict) -> dict:
    canonical = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {**packet, "packet_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}


def source(uid: str, value: str) -> tuple[dict, dict]:
    return (
        {
            "internal_table_uid": uid,
            "rows": [["Metric", value]],
        },
        {
            "canonical_headers": {
                "columns": [
                    {"column_index": 0, "source_label": "Metric", "role": "row_label"},
                    {
                        "column_index": 1,
                        "source_label": "31/12/2022 VND",
                        "role": "value_or_text",
                    },
                ]
            },
            "row_profiles": [{"row_index": 0, "role": "data", "numeric_columns": [1]}],
        },
    )


def packet() -> tuple[dict, dict, dict]:
    values = {
        "HPG": {"current_assets": "100", "inventory": "20", "current_liabilities": "40"},
        "HSG": {"current_assets": "80", "inventory": "20", "current_liabilities": "40"},
    }
    candidates, tables, contexts = [], {}, {}
    for company, variables in values.items():
        for variable, value in variables.items():
            uid = f"{company}-{variable}"
            table, context = source(uid, value)
            tables[uid], contexts[uid] = table, context
            candidates.append(
                {
                    "candidate_id": f"{company}|{variable}|{uid}|0",
                    "company": company,
                    "variable_id": variable,
                    "internal_table_uid": uid,
                    "document_id": f"{company}-2022-consolidated",
                    "report_scope": "consolidated",
                    "row_index": 0,
                    "raw_row_label": variable,
                    "available_value_cells": [
                        {
                            "column_index": 1,
                            "canonical_header": "31/12/2022 VND",
                            "raw_value": value,
                            "unit_labels": ["VND"],
                        }
                    ],
                }
            )
    value = {
        "schema_version": 1,
        "protocol": "qwen_evidence_bounded_table_review_v1",
        "question_id": 1,
        "stage": {"stage_id": "quick_ratio_screen", "metric_id": "quick_ratio"},
        "required_bindings": [
            {"company": company, "variable_id": variable}
            for company in values
            for variable in values[company]
        ],
        "scope_contract": {"status": "resolved", "viable_report_scopes": ["consolidated"]},
        "document_contract": {
            "status": "resolved",
            "selected_document_ids": {
                "HPG": "HPG-2022-consolidated",
                "HSG": "HSG-2022-consolidated",
            },
        },
        "candidates": candidates,
    }
    return with_hash(value), tables, contexts


def review(source_packet: dict) -> dict:
    return {
        "id": 1,
        "annotation_status": "machine_provisional",
        "packet_sha256": source_packet["packet_sha256"],
        "selected_bindings": [
            {
                "company": candidate["company"],
                "variable_id": candidate["variable_id"],
                "internal_table_uid": candidate["internal_table_uid"],
                "row_index": candidate["row_index"],
                **candidate["available_value_cells"][0],
            }
            for candidate in source_packet["candidates"]
        ],
    }


class LlmStagedAuditTests(unittest.TestCase):
    def test_direct_replay_reopens_every_selected_source_cell(self):
        source_packet, tables, contexts = packet()
        result = direct_replay_selected_stage(source_packet, review(source_packet), tables, contexts)
        self.assertEqual(result["status"], "direct_replay_ready")
        self.assertEqual(result["replayed_binding_count"], 6)

    def test_direct_replay_blocks_a_tampered_v2_value(self):
        source_packet, tables, contexts = packet()
        tables["HPG-current_assets"]["rows"][0][1] = "999"
        result = direct_replay_selected_stage(source_packet, review(source_packet), tables, contexts)
        self.assertEqual(result["status"], "direct_replay_blocked")
        self.assertIn("stored_value_differs_from_v2", result["reason_codes"])

    def test_independent_stage_execution_never_uses_llm_selection(self):
        source_packet, tables, contexts = packet()
        stage = {
            "stage_id": "quick_ratio_screen",
            "metric_id": "quick_ratio",
            "entities": ["HPG", "HSG"],
            "required_variables": ["current_assets", "inventory", "current_liabilities"],
        }
        independent = independently_execute_packet_stage(source_packet, stage, tables, contexts)
        self.assertEqual(independent["status"], "independent_critic_ready")
        execution = independent["deterministic_stage_execution"]
        self.assertEqual(execution["eligible_entities"], ["HSG"])
        self.assertTrue(execution_matches(execution, dict(execution)))

    def test_execution_match_requires_the_same_selected_winner(self):
        left = {
            "status": "stage_complete",
            "stage_id": "gross_profit_margin_change_rank",
            "metric_id": "gross_profit_margin_change",
            "metric_values": {"HSG": "1", "MSR": "0"},
            "aggregate": "argmax_unique_signed_change",
            "aggregate_value": "1",
            "winning_entity": "HSG",
        }
        self.assertFalse(execution_matches(left, {**left, "winning_entity": "MSR"}))

    def test_independent_critic_blocks_multiple_source_cells_for_one_binding(self):
        source_packet, tables, contexts = packet()
        alternate = dict(source_packet["candidates"][0])
        alternate["candidate_id"] = "HPG|current_assets|HPG-current-assets-alt|0"
        alternate["internal_table_uid"] = "HPG-current-assets-alt"
        table, context = source("HPG-current-assets-alt", "100")
        tables["HPG-current-assets-alt"], contexts["HPG-current-assets-alt"] = table, context
        untrusted = dict(source_packet)
        untrusted["candidates"] = [*source_packet["candidates"], alternate]
        untrusted.pop("packet_sha256")
        untrusted = with_hash(untrusted)
        stage = {
            "stage_id": "quick_ratio_screen",
            "metric_id": "quick_ratio",
            "entities": ["HPG", "HSG"],
            "required_variables": ["current_assets", "inventory", "current_liabilities"],
        }
        result = independently_execute_packet_stage(untrusted, stage, tables, contexts)
        self.assertEqual(result["status"], "independent_critic_blocked")
        self.assertIn("multiple_independently_replayed_source_cells", result["reason_codes"])


if __name__ == "__main__":
    unittest.main()
