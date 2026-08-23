from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from finance_query.certified_canonical import (
    CertifiedCanonicalError,
    build_phase5_source_first_semantic_routes,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


class CertifiedCanonicalPhase5SemanticRoutingTests(unittest.TestCase):
    def _inputs(self, root: Path) -> dict[str, Path | str]:
        route_id = "route"
        spans = root / "spans.jsonl"
        span_rows = []
        profile_rows = []
        cases = (
            ("statement", "SOURCE_HEADING_SPAN_MATERIALIZED", {"profile_kind": "balance_sheet", "profile_family": "financial_statement"}),
            ("note", "SOURCE_HEADING_SPAN_MATERIALIZED", {"profile_kind": "financial_note_section", "profile_family": "financial_note"}),
            ("unprofiled", "SOURCE_HEADING_SPAN_MATERIALIZED", None),
            ("no-span", "UNRESOLVED", None),
        )
        for name, span_status, profile in cases:
            assertion_id = f"assertion-{name}"
            span_id = f"span-{name}"
            span_rows.append(
                {
                    "heading_span_id": span_id,
                    "phase45_assertion_id": assertion_id,
                    "phase3_request_id": f"request-{name}",
                    "internal_table_uid": f"table-{name}",
                    "route_id": route_id,
                    "status": span_status,
                    "source_heading": f"heading-{name}" if span_status != "UNRESOLVED" else None,
                    "source_heading_sha256": "a" * 64 if span_status != "UNRESOLVED" else None,
                    "source_context_sha256": "b" * 64 if span_status != "UNRESOLVED" else None,
                    "training_eligible": False,
                    "certification_allowed": False,
                }
            )
            profile_rows.append(
                {
                    "table_topic_profile_id": f"profile-{name}",
                    "phase4_heading_span_id": span_id,
                    "phase45_assertion_id": assertion_id,
                    "phase3_request_id": f"request-{name}",
                    "internal_table_uid": f"table-{name}",
                    "route_id": route_id,
                    "status": "SOURCE_PROFILED_LAYOUT_CONTEXT" if profile is not None else "UNRESOLVED",
                    "profile": profile,
                    "training_eligible": False,
                    "certification_allowed": False,
                }
            )
        _write_jsonl(spans, span_rows)
        phase4_manifest = root / "phase4.json"
        phase4_manifest.write_text(
            json.dumps(
                {
                    "protocol": "vifinqa_ccl_phase4_heading_span_materialization_v1",
                    "run_status": "phase_4_heading_span_materialization_complete_not_certified",
                    "training_eligible": False,
                    "certification_allowed": False,
                    "outputs": {spans.name: {"sha256": _sha(spans)}},
                }
            ),
            encoding="utf-8",
        )
        profiles = root / "profiles.jsonl"
        _write_jsonl(profiles, profile_rows)
        phase5_manifest = root / "phase5.json"
        phase5_manifest.write_text(
            json.dumps(
                {
                    "protocol": "vifinqa_ccl_phase5_table_topic_profile_v1",
                    "run_status": "phase_5_table_topic_profile_complete_not_certified",
                    "training_eligible": False,
                    "certification_allowed": False,
                    "inputs": {
                        spans.name: {"sha256": _sha(spans)},
                        phase4_manifest.name: {"sha256": _sha(phase4_manifest)},
                    },
                    "outputs": {profiles.name: {"sha256": _sha(profiles)}},
                }
            ),
            encoding="utf-8",
        )
        row_label_contexts = root / "row-label-contexts.jsonl"
        row_label_contexts.write_text(
            json.dumps(
                {
                    "row_label_context_id": "row-context-no-span",
                    "phase45_assertion_id": "assertion-no-span",
                    "phase3_request_id": "request-no-span",
                    "internal_table_uid": "table-no-span",
                    "route_id": route_id,
                    "status": "SOURCE_ROW_LABEL_RELATION_CONTEXT_ONLY",
                    "source_cell_role": "row_label",
                    "source_cell_text": "Row context",
                    "source_cell_text_sha256": "c" * 64,
                    "training_eligible": False,
                    "certification_allowed": False,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        row_label_manifest = root / "row-label-context.json"
        row_label_manifest.write_text(
            json.dumps(
                {
                    "protocol": "vifinqa_ccl_phase4_row_label_context_v1",
                    "run_status": "phase_4_row_label_context_materialization_complete_not_certified",
                    "training_eligible": False,
                    "certification_allowed": False,
                    "outputs": {row_label_contexts.name: {"sha256": _sha(row_label_contexts)}},
                }
            ),
            encoding="utf-8",
        )
        return {
            "phase4_heading_spans": spans,
            "phase4_manifest": phase4_manifest,
            "phase5_profiles": profiles,
            "phase5_manifest": phase5_manifest,
            "phase4_row_label_contexts": row_label_contexts,
            "phase4_row_label_context_manifest": row_label_manifest,
            "route_id": route_id,
        }

    def test_routes_only_literal_statement_profiles_to_the_deterministic_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = build_phase5_source_first_semantic_routes(**self._inputs(root), output_dir=root / "routes")
            self.assertEqual(
                (
                    result.route_count,
                    result.deterministic_source_profile_count,
                    result.deterministic_note_context_component_count,
                    result.deterministic_row_label_context_count,
                    result.unresolved_source_context_count,
                ),
                (4, 1, 1, 1, 0),
            )
            rows = [
                json.loads(line)
                for line in (root / "routes/phase5_source_first_semantic_routes_v1.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            by_assertion = {row["phase45_assertion_id"]: row for row in rows}
            statement = by_assertion["assertion-statement"]
            self.assertEqual(statement["status"], "DETERMINISTIC_SOURCE_PROFILE_CONTEXT_ONLY")
            self.assertEqual(statement["source_profile_semantic_label"], "balance_sheet")
            self.assertFalse(statement["llm_dispatch_allowed"])
            self.assertEqual(by_assertion["assertion-note"]["status"], "DETERMINISTIC_NOTE_CONTEXT_COMPONENTS_REQUIRED")
            self.assertEqual(by_assertion["assertion-unprofiled"]["status"], "UNRESOLVED_NARROW_SOURCE_RULE_REQUIRED")
            self.assertEqual(by_assertion["assertion-no-span"]["status"], "DETERMINISTIC_ROW_LABEL_RELATION_CONTEXT_ONLY")
            self.assertEqual(by_assertion["assertion-no-span"]["source_cell_text"], "Row context")
            self.assertTrue(all(not row["training_eligible"] and not row["certification_allowed"] for row in rows))

    def test_rejects_tampered_hash_bound_profiles_before_writing_routes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = self._inputs(root)
            Path(inputs["phase5_profiles"]).write_text("{}\n", encoding="utf-8")
            output_dir = root / "routes"
            with self.assertRaisesRegex(CertifiedCanonicalError, "SHA-256 mismatch: Phase 5 table profiles"):
                build_phase5_source_first_semantic_routes(**inputs, output_dir=output_dir)
            self.assertFalse(output_dir.exists())
