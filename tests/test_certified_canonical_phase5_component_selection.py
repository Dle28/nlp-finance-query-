from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from finance_query.certified_canonical import (
    CertifiedCanonicalError,
    build_phase5_component_selection_packets,
    render_phase5_component_selection_prompt,
    validate_phase5_component_selection_responses,
)
from finance_query.report_navigation_overlay import build_report_navigation_overlay


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _text_sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class CertifiedCanonicalPhase5ComponentSelectionTests(unittest.TestCase):
    def _inputs(self, root: Path, *, contents_table_uids: set[str] | None = None) -> dict[str, Path | str]:
        route_id = "route"
        contents_table_uids = contents_table_uids or set()

        def context(*, suffix: str, route_status: str, route_context: dict[str, object]) -> dict[str, object]:
            return {
                "table_structure_context_id": f"structure-{suffix}",
                "source_first_semantic_route_id": f"source-route-{suffix}",
                "phase45_assertion_id": f"assertion-{suffix}",
                "phase3_request_id": f"request-{suffix}",
                "internal_table_uid": f"table-{suffix}",
                "route_id": route_id,
                "source_first_route_status": route_status,
                "status": "SOURCE_STRUCTURE_CONTEXT_ONLY",
                "route_context": route_context,
                "table_structure": {
                    "source_quality_status": "needs_review",
                    "source_quality_reason_codes": ["generic_table_semantics"],
                    "canonical_columns": [
                        {
                            "column_index": 0,
                            "role": "row_label",
                            "source_header_cells": [],
                        },
                        {
                            "column_index": 1,
                            "role": "value_or_text",
                            "source_header_cells": [
                                {
                                    "source_table_uid": f"table-{suffix}",
                                    "raw_row_index": 0,
                                    "raw_column_index": 1,
                                    "literal": "2024",
                                    "literal_sha256": _text_sha("2024"),
                                }
                            ],
                        },
                    ],
                    "source_row_labels": [
                        {
                            "raw_row_index": 1,
                            "raw_column_index": 0,
                            "literal": "Doanh thu",
                            "literal_sha256": _text_sha("Doanh thu"),
                        }
                    ],
                },
                "reason_codes": [],
                "llm_dispatch_allowed": False,
                "campaign_candidate_allowed": False,
                "training_eligible": False,
                "certification_allowed": False,
            }

        contexts = [
            context(
                suffix="note",
                route_status="DETERMINISTIC_NOTE_CONTEXT_COMPONENTS_REQUIRED",
                route_context={
                    "kind": "literal_numbered_note_heading",
                    "note_topic_literal": "Lãi suất",
                    "note_topic_sha256": _text_sha("Lãi suất"),
                    "source_heading_sha256": "a" * 64,
                },
            ),
            context(
                suffix="row",
                route_status="DETERMINISTIC_ROW_LABEL_RELATION_CONTEXT_ONLY",
                route_context={
                    "kind": "selected_source_cell_relation",
                    "raw_row_index": 1,
                    "raw_column_index": 0,
                    "literal": "Trái phiếu",
                    "literal_sha256": _text_sha("Trái phiếu"),
                    "row_label_relation_id": "relation-row",
                },
            ),
            context(
                suffix="statement",
                route_status="DETERMINISTIC_SOURCE_PROFILE_CONTEXT_ONLY",
                route_context={
                    "kind": "literal_financial_statement_heading",
                    "literal": "BẢNG CÂN ĐỐI KẾ TOÁN",
                    "literal_sha256": _text_sha("BẢNG CÂN ĐỐI KẾ TOÁN"),
                    "deterministic_profile_kind": "balance_sheet",
                },
            ),
        ]
        contexts_path = root / "contexts.jsonl"
        contexts_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in contexts), encoding="utf-8"
        )
        manifest_path = root / "contexts-manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "protocol": "vifinqa_ccl_phase5_table_structure_context_v1",
                    "run_status": "phase_5_table_structure_context_complete_not_certified",
                    "training_eligible": False,
                    "certification_allowed": False,
                    "outputs": {contexts_path.name: {"sha256": _sha(contexts_path)}},
                }
            ),
            encoding="utf-8",
        )
        raw_tables = root / "tables.jsonl"
        raw_rows = []
        for suffix in ("note", "row", "statement"):
            table_uid = f"table-{suffix}"
            raw_rows.append(
                {
                    "internal_table_uid": table_uid,
                    "document_id": "test-report",
                    "local_ordinal": len(raw_rows),
                    "table_sha256": suffix[0] * 64,
                    "rows": (
                        [["NỘI DUNG", "TRANG"], ["Báo cáo quản trị", "1 - 2"], ["Thuyết minh", "3 - 4"]]
                        if table_uid in contents_table_uids
                        else [["Chỉ tiêu", "2024"], ["Doanh thu", "100"]]
                    ),
                }
            )
        raw_tables.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in raw_rows), encoding="utf-8")
        navigation_overlay_dir = root / "navigation-overlay"
        build_report_navigation_overlay(raw_tables=raw_tables, output_dir=navigation_overlay_dir)
        return {
            "table_structure_contexts": contexts_path,
            "table_structure_context_manifest": manifest_path,
            "navigation_overlay_dir": navigation_overlay_dir,
            "route_id": route_id,
        }

    def test_builds_complete_source_component_menus_and_bypasses_statements(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = build_phase5_component_selection_packets(**self._inputs(root), output_dir=root / "packets")
            self.assertEqual((result.packet_count, result.deterministic_bypass_count), (2, 1))
            self.assertEqual(result.navigation_blocked_count, 0)
            rows = [json.loads(line) for line in (root / "packets/phase5_component_selection_packets_v1.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual({row["source_first_route_status"] for row in rows}, {
                "DETERMINISTIC_NOTE_CONTEXT_COMPONENTS_REQUIRED",
                "DETERMINISTIC_ROW_LABEL_RELATION_CONTEXT_ONLY",
            })
            self.assertTrue(all(row["llm_dispatch_allowed"] is False for row in rows))
            manifest = json.loads((root / "packets/phase5_component_selection_manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["navigation_overlay_required"])
            self.assertTrue(all(len(row["components"]) == 3 for row in rows))
            self.assertTrue(all(row["task"]["response_contract"]["maximum_supporting_component_ids"] == 4 for row in rows))
            prompt = render_phase5_component_selection_prompt(rows[0])
            self.assertIn("Do not infer or write a semantic table label", prompt)
            self.assertIn("at most 4 listed", prompt)
            self.assertIn(rows[0]["components"][0]["component_id"], prompt)

    def test_blocks_a_contents_page_before_packet_construction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = build_phase5_component_selection_packets(
                **self._inputs(root, contents_table_uids={"table-row"}), output_dir=root / "packets"
            )
            self.assertEqual(
                (result.packet_count, result.deterministic_bypass_count, result.navigation_blocked_count), (1, 1, 1)
            )
            rows = [
                json.loads(line)
                for line in (root / "packets/phase5_component_selection_packets_v1.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual({row["internal_table_uid"] for row in rows}, {"table-note"})
            report = json.loads((root / "packets/phase5_component_selection_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["navigation_blocked_table_uids"], ["table-row"])

    def test_validator_accepts_closed_world_selection_and_rejects_invented_component(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = self._inputs(root)
            build_phase5_component_selection_packets(**inputs, output_dir=root / "packets")
            packets = [json.loads(line) for line in (root / "packets/phase5_component_selection_packets_v1.jsonl").read_text(encoding="utf-8").splitlines()]
            raw = root / "responses.jsonl"
            rows = []
            for index, packet in enumerate(packets):
                menu = packet["components"]
                primary = next(item["component_id"] for item in menu if item["role"] == "report_scope_or_selected_relation_context")
                support = next(item["component_id"] for item in menu if item["role"] == "column_header")
                response = {
                    "primary_component_id": primary if index == 0 else "invented",
                    "supporting_component_ids": [support],
                    "unresolved_conditions": [],
                }
                rows.append({"component_selection_packet_id": packet["component_selection_packet_id"], "response": json.dumps(response)})
            raw.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            result = validate_phase5_component_selection_responses(
                packets=root / "packets/phase5_component_selection_packets_v1.jsonl",
                packet_manifest=root / "packets/phase5_component_selection_manifest.json",
                raw_responses=raw,
                output_dir=root / "validation",
                route_id="route",
            )
            self.assertEqual((result.valid_selection_count, result.abstention_count, result.invalid_count), (1, 0, 1))
            output = [json.loads(line) for line in (root / "validation/phase5_component_selection_results_v1.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual({row["status"] for row in output}, {"VALID_COMPONENT_SELECTION_ONLY", "INVALID_UNRESOLVED"})
            self.assertTrue(all(row["training_eligible"] is False and row["certification_allowed"] is False for row in output))

    def test_excludes_numeric_data_values_but_keeps_year_headers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = self._inputs(root)
            contexts = [json.loads(line) for line in Path(inputs["table_structure_contexts"]).read_text(encoding="utf-8").splitlines()]
            for context in contexts:
                columns = context["table_structure"]["canonical_columns"]
                columns[1]["source_header_cells"].append(
                    {
                        "source_table_uid": context["internal_table_uid"],
                        "raw_row_index": 1,
                        "raw_column_index": 1,
                        "literal": "171.646.117.933",
                        "literal_sha256": _text_sha("171.646.117.933"),
                    }
                )
            contexts_path = Path(inputs["table_structure_contexts"])
            contexts_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in contexts), encoding="utf-8")
            manifest_path = Path(inputs["table_structure_context_manifest"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["outputs"][contexts_path.name]["sha256"] = _sha(contexts_path)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            build_phase5_component_selection_packets(**inputs, output_dir=root / "packets")
            packets = [json.loads(line) for line in (root / "packets/phase5_component_selection_packets_v1.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertTrue(all("2024" in {component["literal"] for component in packet["components"]} for packet in packets))
            self.assertTrue(all("171.646.117.933" not in {component["literal"] for component in packet["components"]} for packet in packets))
            self.assertTrue(all(packet["component_menu_stats"]["numeric_value_header_exclusion_count"] == 1 for packet in packets))

    def test_rejects_tampered_contexts_before_writing_packets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = self._inputs(root)
            Path(inputs["table_structure_contexts"]).write_text("{}\n", encoding="utf-8")
            output_dir = root / "packets"
            with self.assertRaisesRegex(CertifiedCanonicalError, "SHA-256 mismatch: table-structure contexts"):
                build_phase5_component_selection_packets(**inputs, output_dir=output_dir)
            self.assertFalse(output_dir.exists())
