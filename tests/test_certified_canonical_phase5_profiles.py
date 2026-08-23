from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from finance_query.certified_canonical import build_phase5_table_topic_profiles


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CertifiedCanonicalPhase5ProfileTests(unittest.TestCase):
    def test_profiles_only_literal_statement_and_numbered_note_headings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spans = root / "spans.jsonl"
            rows = [
                {
                    "heading_span_id": "span-balance",
                    "phase45_assertion_id": "assertion-balance",
                    "phase3_request_id": "request-balance",
                    "internal_table_uid": "table-balance",
                    "route_id": "route",
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
                    "route_id": "route",
                    "status": "SOURCE_HEADING_SPAN_MATERIALIZED",
                    "source_heading": "12. Tài sản cố định hữu hình",
                    "source_heading_sha256": "c" * 64,
                    "source_context_sha256": "d" * 64,
                    "training_eligible": False,
                    "certification_allowed": False,
                },
                {
                    "heading_span_id": "span-enumerated-note",
                    "phase45_assertion_id": "assertion-enumerated-note",
                    "phase3_request_id": "request-enumerated-note",
                    "internal_table_uid": "table-enumerated-note",
                    "route_id": "route",
                    "status": "SOURCE_HEADING_SPAN_MATERIALIZED",
                    "source_heading": "ii) Số cổ phiếu phổ thông bình quân gia quyền",
                    "source_heading_sha256": "e" * 64,
                    "source_context_sha256": "f" * 64,
                    "training_eligible": False,
                    "certification_allowed": False,
                },
                {
                    "heading_span_id": "span-unresolved",
                    "phase45_assertion_id": "assertion-unresolved",
                    "phase3_request_id": "request-unresolved",
                    "internal_table_uid": "table-unresolved",
                    "route_id": "route",
                    "status": "UNRESOLVED",
                    "source_heading": None,
                    "source_heading_sha256": None,
                    "source_context_sha256": None,
                    "training_eligible": False,
                    "certification_allowed": False,
                },
            ]
            spans.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
            manifest = root / "phase4.json"
            manifest.write_text(
                json.dumps(
                    {
                        "protocol": "vifinqa_ccl_phase4_heading_span_materialization_v1",
                        "run_status": "phase_4_heading_span_materialization_complete_not_certified",
                        "training_eligible": False,
                        "certification_allowed": False,
                        "outputs": {"spans.jsonl": {"sha256": _sha(spans)}},
                    }
                ),
                encoding="utf-8",
            )
            result = build_phase5_table_topic_profiles(
                phase4_heading_spans=spans,
                phase4_manifest=manifest,
                output_dir=root / "phase5",
                route_id="route",
            )
            self.assertEqual((result.profile_count, result.source_profiled_count, result.unresolved_count), (4, 3, 1))
            profiles = [
                json.loads(line)
                for line in (root / "phase5/phase5_table_topic_profiles_v1.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            by_span = {row["phase4_heading_span_id"]: row for row in profiles}
            self.assertEqual(by_span["span-balance"]["profile"]["profile_kind"], "balance_sheet")
            self.assertEqual(by_span["span-note"]["profile"]["profile_kind"], "financial_note_section")
            self.assertEqual(
                by_span["span-enumerated-note"]["derivation_rule"], "literal_enumerated_financial_note_heading_v1"
            )
            self.assertEqual(by_span["span-unresolved"]["reason_codes"], ["heading_span_unresolved"])
            self.assertTrue(all(not row["training_eligible"] and not row["certification_allowed"] for row in profiles))
