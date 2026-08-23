from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from finance_query.certified_canonical import CertifiedCanonicalError, materialize_phase5_numbered_note_context


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CertifiedCanonicalPhase5NoteContextTests(unittest.TestCase):
    def _inputs(self, root: Path) -> dict[str, Path | str]:
        route_id = "route"
        cases = (
            ("first", "12. Tài sản cố định hữu hình", "financial_note", "financial_note_section"),
            ("nested", "7.7) Lãi suất áp dụng", "financial_note", "financial_note_section"),
            ("enumerated", "c) Cổ phiếu", "financial_note", "financial_note_section"),
            ("statement", "BẢNG CÂN ĐỐI KẾ TOÁN", "financial_statement", "balance_sheet"),
        )
        spans = root / "spans.jsonl"
        profiles = root / "profiles.jsonl"
        span_rows = []
        profile_rows = []
        for name, heading, family, kind in cases:
            assertion = f"assertion-{name}"
            span = f"span-{name}"
            span_rows.append(
                {
                    "heading_span_id": span,
                    "phase45_assertion_id": assertion,
                    "phase3_request_id": f"request-{name}",
                    "internal_table_uid": f"table-{name}",
                    "route_id": route_id,
                    "status": "SOURCE_HEADING_SPAN_MATERIALIZED",
                    "source_heading": heading,
                    "source_heading_sha256": "a" * 64,
                    "source_context_sha256": "b" * 64,
                    "training_eligible": False,
                    "certification_allowed": False,
                }
            )
            profile_rows.append(
                {
                    "table_topic_profile_id": f"profile-{name}",
                    "phase4_heading_span_id": span,
                    "phase45_assertion_id": assertion,
                    "phase3_request_id": f"request-{name}",
                    "internal_table_uid": f"table-{name}",
                    "route_id": route_id,
                    "status": "SOURCE_PROFILED_LAYOUT_CONTEXT",
                    "profile": {"profile_family": family, "profile_kind": kind},
                    "training_eligible": False,
                    "certification_allowed": False,
                }
            )
        spans.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in span_rows), encoding="utf-8")
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
        profiles.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in profile_rows), encoding="utf-8")
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
            "phase4_heading_spans": spans,
            "phase4_manifest": phase4_manifest,
            "phase5_profiles": profiles,
            "phase5_manifest": phase5_manifest,
            "route_id": route_id,
        }

    def test_splits_only_literal_numbered_note_heading_components(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = materialize_phase5_numbered_note_context(**self._inputs(root), output_dir=root / "contexts")
            self.assertEqual((result.context_count, result.materialized_count, result.unresolved_count), (3, 3, 0))
            rows = [
                json.loads(line)
                for line in (root / "contexts/phase5_numbered_note_context_v1.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            by_assertion = {row["phase45_assertion_id"]: row for row in rows}
            self.assertEqual(by_assertion["assertion-first"]["note_locator_literal"], "12.")
            self.assertEqual(by_assertion["assertion-first"]["note_topic_literal"], "Tài sản cố định hữu hình")
            self.assertEqual(by_assertion["assertion-nested"]["note_locator_literal"], "7.7)")
            self.assertEqual(by_assertion["assertion-nested"]["note_topic_literal"], "Lãi suất áp dụng")
            self.assertEqual(by_assertion["assertion-enumerated"]["note_locator_literal"], "c)")
            self.assertEqual(by_assertion["assertion-enumerated"]["note_topic_literal"], "Cổ phiếu")
            self.assertTrue(all(not row["training_eligible"] and not row["certification_allowed"] for row in rows))

    def test_rejects_tampered_phase4_input_before_writing_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = self._inputs(root)
            Path(inputs["phase4_heading_spans"]).write_text("{}\n", encoding="utf-8")
            output_dir = root / "contexts"
            with self.assertRaisesRegex(CertifiedCanonicalError, "SHA-256 mismatch: Phase 4 heading spans"):
                materialize_phase5_numbered_note_context(**inputs, output_dir=output_dir)
            self.assertFalse(output_dir.exists())
