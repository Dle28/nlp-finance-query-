from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from finance_query.certified_canonical import CertifiedCanonicalError, run_certified_canonical


def _sha_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CertifiedCanonicalTests(unittest.TestCase):
    def _write_fixture(self, root: Path, *, mutate_canonical_number: bool = False) -> tuple[Path, dict[str, Path]]:
        paths = {
            "raw": root / "data/raw.jsonl",
            "v2": root / "data/v2.jsonl",
            "v3": root / "data/v3.jsonl",
            "normalized": root / "preprocessing/normalized_tables_v2.jsonl",
            "preprocessing_manifest": root / "preprocessing/preprocessing_manifest_v2.json",
        }
        for path in paths.values():
            path.parent.mkdir(parents=True, exist_ok=True)
        uid = "a" * 64
        raw = {
            "internal_table_uid": uid,
            "document_id": "AAA_2022_separate",
            "ticker": "AAA",
            "report_year": 2022,
            "scope": "separate",
            "page_no": 4,
            "local_ordinal": 1,
            "context_before": "Bảng cân đối kế toán. Đơn vị: VND.",
            "rows": [["Chỉ tiêu", "2022 VND"], ["Tiền", "1.000"]],
        }
        provenance = {
            "source_path": "/immutable/AAA.txt",
            "source_sha256": "1" * 64,
            "table_sha256": "2" * 64,
            "char_start": 100,
        }
        cell_provenance = [
            [
                {"source_row": 0, "source_cell": 0, "anchor_row": 0, "anchor_column": 0, "covered_by_span": False},
                {"source_row": 0, "source_cell": 1, "anchor_row": 0, "anchor_column": 1, "covered_by_span": False},
            ],
            [
                {"source_row": 1, "source_cell": 0, "anchor_row": 1, "anchor_column": 0, "covered_by_span": False},
                {"source_row": 1, "source_cell": 1, "anchor_row": 1, "anchor_column": 1, "covered_by_span": False},
            ],
        ]
        structured = {
            "internal_table_uid": uid,
            "document_id": raw["document_id"],
            "local_ordinal": 1,
            "structure_version": 2,
            "source_provenance": provenance,
            "rows": raw["rows"],
            "cell_provenance": cell_provenance,
        }
        context = {
            "internal_table_uid": uid,
            "document_id": raw["document_id"],
            "local_ordinal": 1,
            "source_provenance": provenance,
        }
        canonical_rows = [["Chỉ tiêu", "2022 VND"], ["Tiền", "1.000"]]
        if mutate_canonical_number:
            canonical_rows[1][1] = "10.000"
        normalized = {
            "internal_table_uid": uid,
            "document": {
                key: raw[key]
                for key in ("document_id", "ticker", "report_year", "scope", "page_no", "local_ordinal")
            },
            "source_provenance": provenance,
            "source_record_sha256": _sha_json(raw),
            "canonical_grid": {
                "structure_source": "tables_structured_v2",
                "width": 2,
                "rows": canonical_rows,
                "cell_provenance": cell_provenance,
                "columns": [
                    {"column_index": 0, "canonical_label": "Chỉ tiêu", "period_labels": [], "unit_labels": [], "header_source_cells": []},
                    {"column_index": 1, "canonical_label": "2022 VND", "period_labels": ["2022"], "unit_labels": ["VND"], "header_source_cells": [{"row_index": 0, "column_index": 1}]},
                ],
            },
            "outside_table_context": {"source_heading": "Bảng cân đối kế toán", "reader_heading": "Bảng cân đối kế toán"},
            "quality": {"status": "review_ready", "reason_codes": []},
        }
        for name, value in (("raw", raw), ("v2", structured), ("v3", context), ("normalized", normalized)):
            paths[name].write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")
        preprocessing_manifest = {
            "protocol": "vifinqa_preprocessing_v2",
            "schema_version": 2,
            "training_eligible": False,
            "inputs": {
                str(paths["raw"]): {"sha256": _sha_file(paths["raw"])},
                str(paths["v2"]): {"sha256": _sha_file(paths["v2"])},
                str(paths["v3"]): {"sha256": _sha_file(paths["v3"])},
            },
            "outputs": {"normalized_tables_v2.jsonl": {"sha256": _sha_file(paths["normalized"])}}
        }
        paths["preprocessing_manifest"].write_text(
            json.dumps(preprocessing_manifest), encoding="utf-8"
        )
        config = root / "configs/certified.yaml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            yaml.safe_dump(
                {
                    "protocol": "vifinqa_certified_canonical_v1",
                    "schema_version": 1,
                    "inputs": {
                        "raw_tables": "data/raw.jsonl",
                        "structured_v2": "data/v2.jsonl",
                        "evidence_context_v3": "data/v3.jsonl",
                        "preprocessing_normalized": "preprocessing/normalized_tables_v2.jsonl",
                        "preprocessing_manifest": "preprocessing/preprocessing_manifest_v2.json",
                    },
                    "validate_sidecar_manifests": False,
                    "benchmark": {"packets_per_bucket": 1, "require_target_range": False},
                }
            ),
            encoding="utf-8",
        )
        return config, paths

    def test_builds_hash_bound_phase_0_to_2_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, paths = self._write_fixture(root)
            before = _sha_file(paths["raw"])
            result = run_certified_canonical(config, root / "run")

            self.assertEqual(result.table_count, 1)
            self.assertEqual(result.lifecycle_counts, {"UNRESOLVED": 1})
            self.assertEqual(result.benchmark_packet_count, 1)
            self.assertEqual(before, _sha_file(paths["raw"]))
            validation = json.loads((root / "run/validation_report.json").read_text(encoding="utf-8"))
            self.assertEqual(validation["run_status"], "complete_phase_0_to_2_research_only")
            self.assertEqual(validation["training_eligible_output_count"], 0)
            self.assertTrue(validation["mutation_suite_passed"])
            assertions = [
                json.loads(line)
                for line in (root / "run/semantic_assertions.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual({item["field"] for item in assertions}, {
                "source_identity", "cell_lineage", "heading_context", "table_semantics", "period_context", "unit_context"
            })
            packet = json.loads((root / "run/benchmark_packets_v1.jsonl").read_text(encoding="utf-8"))
            graph = packet["evidence_graph"]
            self.assertEqual(graph["graph_scope"], "phase_3_packet_visible_anchors")
            self.assertFalse(graph["training_eligible"])
            self.assertIn(
                "heading_scopes_table",
                {relation["relation_type"] for relation in graph["relations"]},
            )
            self.assertIn(
                "period_applies_to_column",
                {relation["relation_type"] for relation in graph["relations"]},
            )
            self.assertTrue((root / "run/release_manifest.json").is_file())

    def test_numeric_difference_is_quarantined_not_promoted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, _ = self._write_fixture(root, mutate_canonical_number=True)
            result = run_certified_canonical(config, root / "run")
            self.assertEqual(result.lifecycle_counts, {"QUARANTINED": 1})
            record = json.loads((root / "run/cell_lineage_certificates.jsonl").read_text(encoding="utf-8"))
            self.assertIn("numeric_lexeme_changed", record["failure_codes"])
            certified = json.loads((root / "run/certification_results.jsonl").read_text(encoding="utf-8"))
            self.assertFalse(certified["training_eligible"])

    def test_refuses_reuse_of_a_completed_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, _ = self._write_fixture(root)
            output = root / "run"
            run_certified_canonical(config, output)
            with self.assertRaises(FileExistsError):
                run_certified_canonical(config, output)

    def test_refuses_preprocessing_manifest_from_another_normalized_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, paths = self._write_fixture(root)
            paths["normalized"].write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(CertifiedCanonicalError, "normalized artifact hash"):
                run_certified_canonical(config, root / "run")
