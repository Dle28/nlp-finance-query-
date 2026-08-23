from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from finance_query.certified_canonical import (
    CertifiedCanonicalError,
    verify_phase5_source_profile_compatibility,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


class CertifiedCanonicalPhase5SemanticCompatibilityTests(unittest.TestCase):
    def _inputs(self, root: Path) -> dict[str, Path | str]:
        route_id = "route"
        assertions = root / "assertions.jsonl"
        _write_jsonl(
            assertions,
            [
                {
                    "assertion_id": "assertion-balance",
                    "phase3_request_id": "request-balance",
                    "phase3_proposal_sha256": "proposal-balance",
                    "internal_table_uid": "table-balance",
                    "route_id": route_id,
                    "field": "table_semantics",
                    "proposed_value": "balance_sheet",
                    "training_eligible": False,
                    "certification_allowed": False,
                },
                {
                    "assertion_id": "assertion-note",
                    "phase3_request_id": "request-note",
                    "phase3_proposal_sha256": "proposal-note",
                    "internal_table_uid": "table-note",
                    "route_id": route_id,
                    "field": "table_semantics",
                    "proposed_value": "financial_note_section",
                    "training_eligible": False,
                    "certification_allowed": False,
                },
                {
                    "assertion_id": "assertion-no-span",
                    "phase3_request_id": "request-no-span",
                    "phase3_proposal_sha256": "proposal-no-span",
                    "internal_table_uid": "table-no-span",
                    "route_id": route_id,
                    "field": "table_semantics",
                    "proposed_value": "income_statement",
                    "training_eligible": False,
                    "certification_allowed": False,
                },
            ],
        )
        phase45_manifest = root / "phase45.json"
        phase45_manifest.write_text(
            json.dumps(
                {
                    "protocol": "vifinqa_ccl_phase45_semantic_verifier_v1",
                    "run_status": "phase_4_5_deterministic_verification_complete_not_certified",
                    "training_eligible": False,
                    "certification_allowed": False,
                    "outputs": {assertions.name: {"sha256": _sha(assertions)}},
                }
            ),
            encoding="utf-8",
        )
        spans = root / "spans.jsonl"
        _write_jsonl(
            spans,
            [
                {
                    "heading_span_id": "span-balance",
                    "phase45_assertion_id": "assertion-balance",
                    "phase3_request_id": "request-balance",
                    "internal_table_uid": "table-balance",
                    "route_id": route_id,
                    "status": "SOURCE_HEADING_SPAN_MATERIALIZED",
                    "source_heading": "BẢNG CÂN ĐỐI KẾ TOÁN HỢP NHẤT",
                    "source_heading_sha256": "a" * 64,
                    "source_context_sha256": "b" * 64,
                    "training_eligible": False,
                    "certification_allowed": False,
                },
                {
                    "heading_span_id": "span-note",
                    "phase45_assertion_id": "assertion-note",
                    "phase3_request_id": "request-note",
                    "internal_table_uid": "table-note",
                    "route_id": route_id,
                    "status": "SOURCE_HEADING_SPAN_MATERIALIZED",
                    "source_heading": "12. Tài sản cố định hữu hình",
                    "source_heading_sha256": "c" * 64,
                    "source_context_sha256": "d" * 64,
                    "training_eligible": False,
                    "certification_allowed": False,
                },
                {
                    "heading_span_id": "span-no-span",
                    "phase45_assertion_id": "assertion-no-span",
                    "phase3_request_id": "request-no-span",
                    "internal_table_uid": "table-no-span",
                    "route_id": route_id,
                    "status": "UNRESOLVED",
                    "source_heading": None,
                    "source_heading_sha256": None,
                    "source_context_sha256": None,
                    "training_eligible": False,
                    "certification_allowed": False,
                },
            ],
        )
        phase4_manifest = root / "phase4.json"
        phase4_manifest.write_text(
            json.dumps(
                {
                    "protocol": "vifinqa_ccl_phase4_heading_span_materialization_v1",
                    "run_status": "phase_4_heading_span_materialization_complete_not_certified",
                    "training_eligible": False,
                    "certification_allowed": False,
                    "inputs": {
                        assertions.name: {"sha256": _sha(assertions)},
                        phase45_manifest.name: {"sha256": _sha(phase45_manifest)},
                    },
                    "outputs": {spans.name: {"sha256": _sha(spans)}},
                }
            ),
            encoding="utf-8",
        )
        profiles = root / "profiles.jsonl"
        _write_jsonl(
            profiles,
            [
                {
                    "table_topic_profile_id": "profile-balance",
                    "phase4_heading_span_id": "span-balance",
                    "phase45_assertion_id": "assertion-balance",
                    "phase3_request_id": "request-balance",
                    "internal_table_uid": "table-balance",
                    "route_id": route_id,
                    "status": "SOURCE_PROFILED_LAYOUT_CONTEXT",
                    "profile": {
                        "profile_kind": "balance_sheet",
                        "profile_family": "financial_statement",
                    },
                    "training_eligible": False,
                    "certification_allowed": False,
                },
                {
                    "table_topic_profile_id": "profile-note",
                    "phase4_heading_span_id": "span-note",
                    "phase45_assertion_id": "assertion-note",
                    "phase3_request_id": "request-note",
                    "internal_table_uid": "table-note",
                    "route_id": route_id,
                    "status": "SOURCE_PROFILED_LAYOUT_CONTEXT",
                    "profile": {
                        "profile_kind": "financial_note_section",
                        "profile_family": "financial_note",
                    },
                    "training_eligible": False,
                    "certification_allowed": False,
                },
                {
                    "table_topic_profile_id": "profile-no-span",
                    "phase4_heading_span_id": "span-no-span",
                    "phase45_assertion_id": "assertion-no-span",
                    "phase3_request_id": "request-no-span",
                    "internal_table_uid": "table-no-span",
                    "route_id": route_id,
                    "status": "UNRESOLVED",
                    "profile": None,
                    "training_eligible": False,
                    "certification_allowed": False,
                },
            ],
        )
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
        return {
            "phase45_assertions": assertions,
            "phase45_manifest": phase45_manifest,
            "phase4_heading_spans": spans,
            "phase4_manifest": phase4_manifest,
            "phase5_profiles": profiles,
            "phase5_manifest": phase5_manifest,
            "route_id": route_id,
        }

    def test_emits_context_only_for_a_controlled_statement_label(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = self._inputs(root)
            result = verify_phase5_source_profile_compatibility(**inputs, output_dir=root / "compatibility")
            self.assertEqual((result.compatibility_count, result.compatible_context_count, result.unresolved_count), (3, 1, 2))
            rows = [
                json.loads(line)
                for line in (root / "compatibility/phase5_source_profile_compatibility_v1.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            by_assertion = {row["phase45_assertion_id"]: row for row in rows}
            self.assertEqual(by_assertion["assertion-balance"]["status"], "SOURCE_PROFILE_COMPATIBLE_CONTEXT_ONLY")
            self.assertEqual(by_assertion["assertion-note"]["reason_codes"], ["source_profile_is_not_a_statement_type"])
            self.assertEqual(by_assertion["assertion-no-span"]["reason_codes"], ["no_materialized_source_heading_span"])
            self.assertTrue(all(not row["campaign_candidate_allowed"] for row in rows))
            self.assertTrue(all(not row["training_eligible"] and not row["certification_allowed"] for row in rows))
            report = json.loads(
                (root / "compatibility/phase5_source_profile_compatibility_report.json").read_text(encoding="utf-8")
            )
            comparison = report["statement_profile_comparison"]
            self.assertEqual(comparison["source_profiled_statement_count"], 1)
            self.assertEqual(comparison["model_enum_compatible_context_count"], 1)
            self.assertEqual(comparison["model_enum_compatible_context_rate"], 1.0)

    def test_rejects_a_tampered_hash_bound_profile_file_before_writing_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = self._inputs(root)
            Path(inputs["phase5_profiles"]).write_text("{}\n", encoding="utf-8")
            output_dir = root / "compatibility"
            with self.assertRaisesRegex(CertifiedCanonicalError, "SHA-256 mismatch: Phase 5 table profiles"):
                verify_phase5_source_profile_compatibility(**inputs, output_dir=output_dir)
            self.assertFalse(output_dir.exists())
