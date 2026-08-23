from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from finance_query.artifact_registry import sha256_file
from finance_query.typed_planner import TYPED_OPERAND_PLAN_PROTOCOL
from scripts.build_fingerprint_canaries import build_canaries


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def write_manifest(path: Path, payload: dict) -> None:
    path.with_suffix(".manifest.json").write_text(json.dumps(payload), encoding="utf-8")


class FingerprintCanaryTests(unittest.TestCase):
    def _bundle(self, root: Path) -> tuple[Path, Path, Path]:
        bundle = root / "bundle"
        bundle.mkdir()
        items = [
            {
                "id": 1,
                "question": "Giá trị chỉ tiêu Metric của AAA năm 2023?",
                "question_plan": {
                    "family": "direct_lookup",
                    "tickers": ["AAA"],
                    "years": [2023],
                    "scope": "separate",
                    "requested_unit": "million_vnd",
                },
            },
            {
                "id": 2,
                "question": "Giá trị chỉ tiêu Metric khác của AAA năm 2023?",
                "question_plan": {
                    "family": "direct_lookup",
                    "tickers": ["AAA"],
                    "years": [2023],
                    "scope": "separate",
                    "requested_unit": "million_vnd",
                },
            },
        ]
        write_jsonl(bundle / "review_items.jsonl", items)
        write_jsonl(bundle / "tables.jsonl", [{"internal_table_uid": "t1"}])
        structured = bundle / "tables_structured_v2.jsonl"
        write_jsonl(
            structured,
            [{
                "internal_table_uid": "t1", "ticker": "AAA", "report_year": 2023,
                "scope": "separate", "unit_hint": "million_vnd", "rows": [["Metric", "10"]],
                "cell_provenance": [[{"r": 0, "c": 0}, {"r": 0, "c": 1}]],
                "structure_quality": {"status": "reconstructed_from_raw_html"},
            }],
        )
        (bundle / "table_structure_v2.manifest.json").write_text(json.dumps({
            "structure_version": 2, "error_count": 0, "repaired_table_count": 1, "table_count": 1,
            "input_bundle_tables_sha256": sha256_file(bundle / "tables.jsonl"),
            "sidecar_sha256": sha256_file(structured),
        }), encoding="utf-8")
        context = bundle / "tables_evidence_context_v3.jsonl"
        write_jsonl(
            context,
            [{
                "internal_table_uid": "t1",
                "canonical_headers": {"columns": [{"column_index": 1, "source_label": "2023"}]},
                "row_profiles": [{"row_index": 0, "role": "data", "numeric_columns": [1]}],
            }],
        )
        (bundle / "table_evidence_context_v3.manifest.json").write_text(json.dumps({
            "evidence_context_version": 3, "error_count": 0,
            "numeric_binding_policy": "one_reliable_raw_v2_number_per_cell",
            "input_structure_sha256": sha256_file(structured),
            "input_bundle_tables_sha256": sha256_file(bundle / "tables.jsonl"),
            "sidecar_sha256": sha256_file(context),
        }), encoding="utf-8")
        census = root / "census.jsonl"
        write_jsonl(census, [
            {"question_id": 1, "fingerprint": "fp-direct", "family": "direct_lookup", "route": "operator_contract_candidate"},
            {"question_id": 2, "fingerprint": "fp-direct", "family": "direct_lookup", "route": "operator_contract_candidate"},
        ])
        write_manifest(census, {
            "protocol": "deterministic_question_plan_fingerprint_v1",
            "bundle_review_items_sha256": sha256_file(bundle / "review_items.jsonl"),
            "sidecar_sha256": sha256_file(census),
        })
        typed = root / "typed.jsonl"
        write_jsonl(typed, [
            {"question_id": qid, "plan_fingerprint": f"plan-{qid}", "decomposition_status": "complete", "reason_codes": []}
            for qid in (1, 2)
        ])
        write_manifest(typed, {
            "protocol": TYPED_OPERAND_PLAN_PROTOCOL,
            "review_items_sha256": sha256_file(bundle / "review_items.jsonl"),
            "sidecar_sha256": sha256_file(typed),
        })
        return bundle, census, typed

    def test_canary_is_explicitly_blocked_without_exact_evidence(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle, census, typed = self._bundle(root)
            rows, manifest = build_canaries(bundle, census, typed, root / "canaries.jsonl", min_question_count=2)
            self.assertEqual(manifest["verdict_counts"], {"explicit_block": 1})
            self.assertEqual(rows[0]["question_id"], 1)
            self.assertEqual(rows[0]["reason_codes"], ["no_exact_evidence_sidecar_for_representative"])
            self.assertFalse(rows[0]["submission_eligible"])

    def test_direct_canary_replays_exact_v2_v3_binding(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle, census, typed = self._bundle(root)
            direct = root / "direct.jsonl"
            write_jsonl(direct, [{
                "id": 1,
                "family": "direct_lookup",
                "candidates": [{
                    "internal_table_uid": "t1",
                    "source_discovery": {
                        "policy": "exact_raw_v2_metric_token_sequence_v1",
                        "raw_row_label": "Metric",
                        "row_index": 0,
                        "metric_match": {"matched_metric": "Metric"},
                        "value_binding": {
                            "status": "cell_bound", "row_index": 0, "column_index": 1,
                            "value": "10", "column_label": "2023", "source_cell": {"r": 0, "c": 1},
                        },
                    },
                }],
            }])
            write_manifest(direct, {
                "bundle_review_items_sha256": sha256_file(bundle / "review_items.jsonl"),
                # Legacy spelling remains accepted only as an exact alias of
                # the direct-replay protocol's raw_tables_sha256 field.
                "bundle_tables_sha256": sha256_file(bundle / "tables.jsonl"),
                "structured_tables_sha256": sha256_file(bundle / "tables_structured_v2.jsonl"),
                "sidecar_sha256": sha256_file(direct),
            })
            rows, manifest = build_canaries(
                bundle, census, typed, root / "canaries.jsonl", direct_path=direct, min_question_count=2
            )
            self.assertEqual(manifest["verdict_counts"], {"exact_bound": 1})
            self.assertEqual(rows[0]["verdict"], "exact_bound")
            self.assertEqual(rows[0]["exact_binding_proof"][0]["canonical_header"], "2023")
            self.assertEqual(rows[0]["exact_binding_proof"][0]["raw_value"], "10")


if __name__ == "__main__":
    unittest.main()
