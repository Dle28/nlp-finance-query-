import json
import tempfile
import unittest
from pathlib import Path

from finance_query.report_entities import (
    build_report_entity_aliases,
    canonical_entity_name,
    resolve_question_entity,
    source_report_entity,
    validate_report_entity_alias_sidecar,
)
from finance_query.table_structure import sha256_file


class ReportEntityTests(unittest.TestCase):
    def test_entity_must_be_at_source_page_start_before_report_marker(self):
        acv = (
            "Tổng Công ty Cảng Hàng không Việt Nam - CTCP THUYẾT MINH "
            "BÁO CÁO TÀI CHÍNH HỢP NHẤT"
        )
        body_mention = (
            "93.936.585.717 Tổng Công ty Cảng Hàng không Việt Nam "
            "- khoản phải thu khách hàng"
        )
        self.assertEqual(
            source_report_entity(acv), "Tổng Công ty Cảng Hàng không Việt Nam - CTCP"
        )
        self.assertEqual(source_report_entity(body_mention), "")

    def test_generic_company_sentence_and_address_are_not_entity_aliases(self):
        self.assertEqual(
            source_report_entity(
                "Công ty hoặc các cá nhân được coi là liên quan. BÁO CÁO TÀI CHÍNH"
            ),
            "",
        )
        self.assertEqual(
            source_report_entity(
                "Công ty Cổ phần Tập đoàn PC1 Tầng 1, số 583 Nguyễn Trãi MẪU SỐ B01"
            ),
            "Công ty Cổ phần Tập đoàn PC1",
        )

    def test_resolver_uses_unique_source_title_alias_and_legal_form_only(self):
        aliases = build_report_entity_aliases(
            [
                {
                    "ticker": "ACV",
                    "document_id": "ACV_2022",
                    "report_year": 2022,
                    "scope": "consolidated",
                    "context_before": (
                        "Tổng Công ty Cảng Hàng không Việt Nam - CTCP THUYẾT MINH "
                        "BÁO CÁO TÀI CHÍNH HỢP NHẤT"
                    ),
                },
                {
                    "ticker": "VJC",
                    "document_id": "VJC_2017",
                    "report_year": 2017,
                    "scope": "separate",
                    "context_before": (
                        "93.936.585.717 Tổng Công ty Cảng Hàng không Việt Nam "
                        "- khoản phải thu khách hàng"
                    ),
                },
            ]
        )
        resolution = resolve_question_entity(
            "Tốc độ tăng trưởng tiền của Tổng Công ty Cảng Hàng không Việt Nam từ 2021 đến 2022",
            aliases,
        )
        self.assertEqual(resolution["ticker"], "ACV")
        self.assertFalse(resolution["scope_inferred"])
        self.assertEqual(canonical_entity_name("CTCP Tập đoàn PC1"), "pc1")

    def test_resolver_rejects_same_source_title_for_multiple_tickers(self):
        aliases = [
            {"ticker": "AAA", "canonical_entity": "doanh nghiep thu nghiem", "source_entity": "Công ty Doanh nghiệp thử nghiệm", "document_id": "a"},
            {"ticker": "BBB", "canonical_entity": "doanh nghiep thu nghiem", "source_entity": "Công ty Doanh nghiệp thử nghiệm", "document_id": "b"},
        ]
        self.assertIsNone(resolve_question_entity("Doanh nghiệp thử nghiệm năm 2023", aliases))

    def test_sidecar_manifest_rejects_training_eligibility(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary)
            tables = bundle / "tables.jsonl"
            tables.write_text("{}\n", encoding="utf-8")
            sidecar = bundle / "report_entity_aliases_v1.jsonl"
            sidecar.write_text("{}\n", encoding="utf-8")
            manifest = {
                "schema_version": 1,
                "bundle_tables_sha256": sha256_file(tables),
                "evidence_eligible": False,
                "training_eligible": False,
                "sidecar_sha256": sha256_file(sidecar),
            }
            sidecar.with_suffix(".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(validate_report_entity_alias_sidecar(bundle, sidecar)["schema_version"], 1)
            manifest["training_eligible"] = True
            sidecar.with_suffix(".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "non-evidence"):
                validate_report_entity_alias_sidecar(bundle, sidecar)
