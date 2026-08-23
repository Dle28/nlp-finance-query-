import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from finance_query.route_packets import materialize_route_packets


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def source_contract() -> dict[str, bool]:
    return {
        "navigation_metadata_only": True,
        "evidence_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
    }


def prepare_valid_inputs(root: Path, *, duplicate_route_id: bool = False) -> dict[str, Path]:
    routes_path = root / "routes.jsonl"
    routes_manifest_path = root / "routes.manifest.json"
    candidates_path = root / "candidates.jsonl"
    candidates_manifest_path = root / "candidates.manifest.json"
    roles_path = root / "candidates.table_roles.jsonl"
    sectors_path = root / "candidates.sectors.jsonl"
    routing_path = root / "table_routing_catalog_v1.jsonl"
    routing_manifest_path = root / "table_routing_catalog_v1.manifest.json"
    documents_path = root / "document_metadata_v1.jsonl"
    tables_path = root / "tables_structured_v2.jsonl"
    structure_manifest_path = root / "table_structure_v2.manifest.json"

    route = {
        "question_id": 1,
        "question": "Hệ số thanh toán nhanh của HPG năm 2022 là bao nhiêu?",
        "route_status": "metric_candidate",
        "question_context": {"entities": ["HPG"], "years": [2022], "scope": "consolidated"},
        "stages": [
            {
                "stage_id": "stage_1_quick_ratio",
                "route_kind": "metric",
                "metric_id": "quick_ratio",
                "retrieval_filters": {"sectors": []},
                "required_operands": [
                    {
                        "role": "current_assets",
                        "concept_id": "current_assets",
                        "concept_path": ["assets", "current_assets"],
                        "period_type": "instant",
                        "statement_types": ["balance_sheet"],
                    }
                ],
            }
        ],
        "feedback": None,
        "source_contract": source_contract(),
    }
    routes = [route, dict(route)] if duplicate_route_id else [route]
    write_jsonl(routes_path, routes)
    routes_manifest_path.write_text(
        json.dumps(
            {
                "output": {"sha256": sha256(routes_path)},
                "question_count": len(routes),
                "question_id_count": len(routes),
                "inputs": {"taxonomy": {"sha256": "taxonomy-hash"}},
                "source_contract": source_contract(),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    row = ["Tài sản ngắn hạn", "100"]
    table = {"internal_table_uid": "t1", "document_id": "HPG_2022_consolidated", "rows": [["Chỉ tiêu", "Số tiền"], row]}
    routing = {
        "internal_table_uid": "t1",
        "document_id": "HPG_2022_consolidated",
        "company": "HPG",
        "report_year": 2022,
        "report_scope": "consolidated",
        "available_period_years": [2021, 2022],
        "routing_eligible": True,
        "table_type": "balance_sheet",
        "table_type_status": "source_structural",
    }
    document = {
        "document_id": "HPG_2022_consolidated",
        "company": "HPG",
        "report_year": 2022,
        "report_scope": "consolidated",
    }
    candidate = {
        "internal_table_uid": "t1",
        "document_id": "HPG_2022_consolidated",
        "row_index": 1,
        "raw_source_row": row,
        "table_type": "balance_sheet",
        "table_type_status": "source_structural",
        "routing_eligible": True,
        "navigation_gate_status": "ready",
        "navigation_reason_codes": [],
        "match_status": "exact_unique",
        "concept_candidates": [{"concept_id": "current_assets"}],
        "source_contract": source_contract(),
    }
    role = {
        "internal_table_uid": "t1",
        "document_id": "HPG_2022_consolidated",
        "existing_table_type": "balance_sheet",
        "existing_table_type_status": "source_structural",
        "proposed_table_type": "balance_sheet",
        "status": "source_structural",
        "reason_codes": [],
    }
    sector = {"document_id": "HPG_2022_consolidated", "sector": "unknown"}
    write_jsonl(candidates_path, [candidate])
    write_jsonl(roles_path, [role])
    write_jsonl(sectors_path, [sector])
    write_jsonl(routing_path, [routing])
    write_jsonl(documents_path, [document])
    write_jsonl(tables_path, [table])

    candidates_manifest_path.write_text(
        json.dumps(
            {
                "candidate_record_count": 1,
                "table_count": 1,
                "document_count": 1,
                "inputs": {
                    "taxonomy": {"sha256": "taxonomy-hash"},
                    "routing_catalog": {"sha256": sha256(routing_path)},
                    "structured_tables": {"sha256": sha256(tables_path)},
                },
                "outputs": {
                    "row_candidates": {"sha256": sha256(candidates_path)},
                    "table_role_candidates": {"sha256": sha256(roles_path)},
                    "sector_candidates": {"sha256": sha256(sectors_path)},
                },
                "source_contract": source_contract(),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    routing_manifest_path.write_text(
        json.dumps(
            {
                "table_count": 1,
                "document_count": 1,
                "table_catalog_sha256": sha256(routing_path),
                "document_metadata_sha256": sha256(documents_path),
                "input_structure_sha256": sha256(tables_path),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    structure_manifest_path.write_text(
        json.dumps({"table_count": 1, "sidecar_sha256": sha256(tables_path)}, sort_keys=True),
        encoding="utf-8",
    )
    return {
        "routes_path": routes_path,
        "routes_manifest_path": routes_manifest_path,
        "candidates_path": candidates_path,
        "candidates_manifest_path": candidates_manifest_path,
        "table_roles_path": roles_path,
        "sectors_path": sectors_path,
        "routing_catalog_path": routing_path,
        "routing_manifest_path": routing_manifest_path,
        "document_metadata_path": documents_path,
        "structured_tables_path": tables_path,
        "structure_manifest_path": structure_manifest_path,
    }


class BuildQuestionRoutePacketsTests(unittest.TestCase):
    def test_duplicate_question_ids_fail_before_packet_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = prepare_valid_inputs(root, duplicate_route_id=True)
            output = root / "packets.jsonl"
            with self.assertRaisesRegex(ValueError, "duplicate IDs"):
                materialize_route_packets(**paths, output=output)
            self.assertFalse(output.exists())

    def test_manifest_hash_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = prepare_valid_inputs(root)
            bad_manifest = json.loads(paths["routes_manifest_path"].read_text(encoding="utf-8"))
            bad_manifest["output"]["sha256"] = "0" * 64
            paths["routes_manifest_path"].write_text(
                json.dumps(bad_manifest, sort_keys=True), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch for question routes"):
                materialize_route_packets(**paths, output=root / "packets.jsonl")

    def test_hash_bound_output_has_no_final_selection_or_answer_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = prepare_valid_inputs(root)
            output = root / "packets.jsonl"
            manifest = materialize_route_packets(**paths, output=output, cap=20)
            manifest_path = output.with_suffix(".manifest.json")
            examples_path = output.with_name(output.stem + ".examples.jsonl")
            packet = json.loads(output.read_text(encoding="utf-8").strip())
            persisted_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest, persisted_manifest)
            self.assertEqual(manifest["outputs"]["packets"]["sha256"], sha256(output))
            self.assertEqual(manifest["outputs"]["examples"]["sha256"], sha256(examples_path))
            self.assertEqual(manifest["packet_status_counts"], {"bounded": 1})
            self.assertEqual(manifest["operand_candidate_recall_proxy"], 1.0)
            self.assertEqual(packet["question_id"], 1)
            self.assertEqual(packet["packet_status"], "bounded")
            candidate = packet["stages"][0]["required_operands"][0]["navigation_candidates"][0]
            self.assertEqual(candidate["row_index"], 1)
            self.assertEqual(candidate["raw_source_row"], ["Tài sản ngắn hạn", "100"])
            self.assertNotIn("selected_candidate", candidate)
            self.assertNotIn("selected_table", packet)
            self.assertNotIn("answer", packet)
            self.assertNotIn("value", candidate)
            for key, value in packet["source_contract"].items():
                if key != "navigation_metadata_only":
                    self.assertFalse(value)
            self.assertEqual(len(examples_path.read_text(encoding="utf-8").splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
