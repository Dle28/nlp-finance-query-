from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from finance_query.certified_canonical import (
    CertifiedCanonicalError,
    materialize_phase5_table_structure_context,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_sha(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _text_sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class CertifiedCanonicalPhase5TableStructureContextTests(unittest.TestCase):
    def _inputs(self, root: Path) -> dict[str, Path | str]:
        route_id = "route"
        raw_rows: list[dict[str, object]] = []
        normalized_rows: list[dict[str, object]] = []
        routes: list[dict[str, object]] = []
        for suffix, status in (
            ("statement", "DETERMINISTIC_SOURCE_PROFILE_CONTEXT_ONLY"),
            ("note", "DETERMINISTIC_NOTE_CONTEXT_COMPONENTS_REQUIRED"),
            ("row", "DETERMINISTIC_ROW_LABEL_RELATION_CONTEXT_ONLY"),
        ):
            uid = f"table-{suffix}"
            raw = {
                "internal_table_uid": uid,
                "rows": [["", "2024"], [f"Nhãn {suffix}", "100"]],
                "header_row_indices": [0],
            }
            raw_rows.append(raw)
            normalized_rows.append(
                {
                    "internal_table_uid": uid,
                    "source_record_sha256": _json_sha(raw),
                    "canonical_grid": {
                        "rows": raw["rows"],
                        "row_count": 2,
                        "width": 2,
                        "columns": [
                            {"column_index": 0, "role": "row_label", "header_source_cells": []},
                            {
                                "column_index": 1,
                                "role": "value_or_text",
                                "header_source_cells": [{"row_index": 0, "column_index": 1}],
                            },
                        ],
                    },
                    "document": {
                        "document_id": "report",
                        "ticker": "ABC",
                        "report_year": 2024,
                        "scope": "consolidated",
                        "page_no": 1,
                        "local_ordinal": 1,
                    },
                    "quality": {"status": "needs_review", "reason_codes": ["generic_table_semantics"]},
                }
            )
            route: dict[str, object] = {
                "source_first_semantic_route_id": f"route-{suffix}",
                "phase45_assertion_id": f"assertion-{suffix}",
                "phase3_request_id": f"request-{suffix}",
                "internal_table_uid": uid,
                "route_id": route_id,
                "status": status,
                "reason_codes": [],
                "training_eligible": False,
                "certification_allowed": False,
            }
            if suffix == "statement":
                heading = "BẢNG CÂN ĐỐI KẾ TOÁN"
                route.update(source_heading=heading, source_heading_sha256=_text_sha(heading), source_profile_semantic_label="balance_sheet")
            elif suffix == "note":
                heading = "7. Lãi suất"
                route.update(source_heading=heading, source_heading_sha256=_text_sha(heading))
            routes.append(route)
        raw_path = root / "raw.jsonl"
        normalized_path = root / "normalized.jsonl"
        routes_path = root / "routes.jsonl"
        raw_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in raw_rows), encoding="utf-8")
        normalized_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in normalized_rows), encoding="utf-8")
        routes_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in routes), encoding="utf-8")
        notes_path = root / "notes.jsonl"
        notes_path.write_text(
            json.dumps(
                {
                    "phase45_assertion_id": "assertion-note",
                    "phase3_request_id": "request-note",
                    "internal_table_uid": "table-note",
                    "route_id": route_id,
                    "status": "SOURCE_NUMBERED_NOTE_CONTEXT_COMPONENTS_ONLY",
                    "source_heading_sha256": _text_sha("7. Lãi suất"),
                    "note_locator_literal": "7.",
                    "note_topic_literal": "Lãi suất",
                    "note_topic_sha256": _text_sha("Lãi suất"),
                    "training_eligible": False,
                    "certification_allowed": False,
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        row_contexts_path = root / "row-contexts.jsonl"
        row_literal = "Nhãn row"
        row_contexts_path.write_text(
            json.dumps(
                {
                    "phase45_assertion_id": "assertion-row",
                    "phase3_request_id": "request-row",
                    "internal_table_uid": "table-row",
                    "route_id": route_id,
                    "status": "SOURCE_ROW_LABEL_RELATION_CONTEXT_ONLY",
                    "raw_row_index": 1,
                    "raw_column_index": 0,
                    "source_cell_role": "row_label",
                    "source_cell_text": row_literal,
                    "source_cell_text_sha256": _text_sha(row_literal),
                    "row_label_relation_id": "relation-row",
                    "training_eligible": False,
                    "certification_allowed": False,
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        def manifest(path: Path, protocol: str, status: str, output: Path) -> None:
            path.write_text(
                json.dumps(
                    {
                        "protocol": protocol,
                        "run_status": status,
                        "training_eligible": False,
                        "certification_allowed": False,
                        "outputs": {output.name: {"sha256": _sha(output)}},
                    }
                ),
                encoding="utf-8",
            )

        route_manifest = root / "routes-manifest.json"
        note_manifest = root / "notes-manifest.json"
        row_manifest = root / "rows-manifest.json"
        manifest(route_manifest, "vifinqa_ccl_phase5_source_first_semantic_routing_v1", "phase_5_source_first_semantic_routing_complete_not_certified", routes_path)
        manifest(note_manifest, "vifinqa_ccl_phase5_numbered_note_context_v1", "phase_5_numbered_note_context_complete_not_certified", notes_path)
        manifest(row_manifest, "vifinqa_ccl_phase4_row_label_context_v1", "phase_4_row_label_context_materialization_complete_not_certified", row_contexts_path)
        inventory = root / "inventory.json"
        inventory.write_text(
            json.dumps(
                {
                    "protocol": "vifinqa_certified_canonical_v1",
                    "stage": "phase_0_freeze_and_baseline",
                    "inputs": {
                        "raw_tables": {"sha256": _sha(raw_path)},
                        "preprocessing_normalized": {"sha256": _sha(normalized_path)},
                    },
                }
            ),
            encoding="utf-8",
        )
        return {
            "source_first_routes": routes_path,
            "source_first_manifest": route_manifest,
            "phase5_note_contexts": notes_path,
            "phase5_note_context_manifest": note_manifest,
            "phase4_row_label_contexts": row_contexts_path,
            "phase4_row_label_context_manifest": row_manifest,
            "ccl_input_inventory": inventory,
            "raw_tables": raw_path,
            "normalized_tables": normalized_path,
            "route_id": route_id,
        }

    def test_joins_all_source_route_types_without_semantic_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = materialize_phase5_table_structure_context(**self._inputs(root), output_dir=root / "output")
            self.assertEqual((result.context_count, result.materialized_count), (3, 3))
            rows = [json.loads(line) for line in (root / "output/phase5_table_structure_context_v1.jsonl").read_text(encoding="utf-8").splitlines()]
            by_status = {row["source_first_route_status"]: row for row in rows}
            self.assertEqual(by_status["DETERMINISTIC_NOTE_CONTEXT_COMPONENTS_REQUIRED"]["route_context"]["note_topic_literal"], "Lãi suất")
            self.assertEqual(by_status["DETERMINISTIC_ROW_LABEL_RELATION_CONTEXT_ONLY"]["route_context"]["literal"], "Nhãn row")
            statement = by_status["DETERMINISTIC_SOURCE_PROFILE_CONTEXT_ONLY"]
            self.assertEqual(statement["route_context"]["deterministic_profile_kind"], "balance_sheet")
            self.assertEqual(statement["table_structure"]["canonical_columns"][1]["source_header_cells"][0]["literal"], "2024")
            self.assertTrue(all(row["llm_dispatch_allowed"] is False for row in rows))
            self.assertTrue(all(row["training_eligible"] is False and row["certification_allowed"] is False for row in rows))

    def test_rejects_tampered_source_first_routes_before_writing_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = self._inputs(root)
            Path(inputs["source_first_routes"]).write_text("{}\n", encoding="utf-8")
            output = root / "output"
            with self.assertRaisesRegex(CertifiedCanonicalError, "SHA-256 mismatch: source-first routes"):
                materialize_phase5_table_structure_context(**inputs, output_dir=output)
            self.assertFalse(output.exists())
