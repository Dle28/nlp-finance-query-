import json
import tempfile
import unittest
from pathlib import Path

from finance_query.corporate_report_context_review import (
    CONTEXT_REVIEW_SOURCE_CONTRACT,
    CORPORATE_REPORT_CONTEXT_REVIEW_PROTOCOL,
    CORPORATE_REPORT_CONTEXT_REVIEW_VERSION,
    build_corporate_report_context_review_packets,
    canonical_sha256,
    response_template,
    sha256_file,
    validate_corporate_report_context_review_queue,
    verify_corporate_report_context_human_reviews,
)


def item(question_id=7, *, plan=None, ticker="VJC", year=2022, scope="separate"):
    return {
        "id": question_id,
        "question": "Chỉ tiêu năm 2022 của Vietjet là gì?",
        "question_plan": plan if plan is not None else {"tickers": [ticker], "years": [year], "scope": "unknown"},
        "candidates": [
            {
                "rank": rank,
                "internal_table_uid": f"uid-{rank}",
                "document_id": "VJC_2022_separate",
                "ticker": ticker,
                "report_year": year,
                "scope": scope,
            }
            for rank in range(1, 6)
        ],
    }


def family():
    return {
        "issuer_ticker": "VJC",
        "report_year": 2022,
        "report_type": "financial_statements",
        "report_family_id": "family-1",
        "family_status": "source_derived_unique_scope_members",
        "members": [
            {"document_id": "VJC_2022_separate", "report_scope": "separate"},
            {"document_id": "VJC_2022_consolidated", "report_scope": "consolidated"},
        ],
    }


def blocked_hint(question_id=7):
    return {"question_id": question_id, "route_hint_status": "question_context_incomplete_or_ambiguous"}


class CorporateReportContextReviewTests(unittest.TestCase):
    def test_packet_requires_top_five_consensus_and_source_family_member(self):
        packets, exclusions = build_corporate_report_context_review_packets([item()], [family()], [blocked_hint()])
        self.assertEqual(exclusions, {})
        self.assertEqual(len(packets), 1)
        context = packets[0]["review_context"]
        self.assertEqual(
            context["retrieval_metadata_consensus"]["proposed_question_context"],
            {"ticker": "VJC", "report_year": 2022, "report_scope": "separate", "source_document_id": "VJC_2022_separate"},
        )
        self.assertEqual(context["source_report_family"]["parallel_scope_document_ids"], ["VJC_2022_consolidated"])
        self.assertFalse(packets[0]["source_contract"]["materialization_allowed"])

    def test_plan_conflict_is_annotated_but_top_five_scope_conflict_is_excluded(self):
        conflicting = item(plan={"tickers": ["ACB"], "years": [2022], "scope": "unknown"})
        scope_conflict = item(question_id=8)
        scope_conflict["candidates"][4]["scope"] = "consolidated"
        packets, exclusions = build_corporate_report_context_review_packets(
            [conflicting, scope_conflict], [family()], [blocked_hint(), blocked_hint(8)]
        )
        self.assertEqual(len(packets), 1)
        self.assertEqual(
            packets[0]["review_context"]["machine_question_plan_metadata_relation"],
            "conflicting_or_ambiguous_machine_question_plan",
        )
        self.assertEqual(exclusions["top_five_metadata_not_single_source_context"], 1)

    def _write_queue(self, directory: Path):
        packets, _ = build_corporate_report_context_review_packets([item()], [family()], [blocked_hint()])
        queue = directory / "corporate_report_context_review_queue_v1.jsonl"
        queue.write_text(json.dumps(packets[0], sort_keys=True) + "\n", encoding="utf-8")
        manifest = {
            "schema_version": CORPORATE_REPORT_CONTEXT_REVIEW_VERSION,
            "protocol": CORPORATE_REPORT_CONTEXT_REVIEW_PROTOCOL,
            "queue_status": "blank_human_question_context_review",
            "question_count": 1,
            "question_ids": [7],
            "labels_prepopulated": False,
            "materialization_allowed": False,
            "outputs": {"queue": {"sha256": sha256_file(queue)}},
            "source_contract": CONTEXT_REVIEW_SOURCE_CONTRACT,
        }
        queue_manifest = directory / "corporate_report_context_review_queue_v1.manifest.json"
        queue_manifest.write_text(json.dumps(manifest), encoding="utf-8")
        return packets[0], queue, queue_manifest

    def test_queue_validation_rejects_packet_context_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            packet, queue, _ = self._write_queue(directory)
            self.assertEqual(validate_corporate_report_context_review_queue(directory)["question_count"], 1)
            packet["review_context"]["question"] = "tampered"
            queue.write_text(json.dumps(packet) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                validate_corporate_report_context_review_queue(directory)

    def test_verified_approval_stays_non_materializable_and_rejects_substitution(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            packet, queue, queue_manifest = self._write_queue(directory)
            response = response_template(packet, reviewer_id="reviewer-1")
            response.update(
                {
                    "decision": "approve_question_context",
                    "decision_provenance": "human_verified",
                    "reviewed_at": "2026-08-15T00:00:00Z",
                    "reviewed_context": packet["review_context"]["retrieval_metadata_consensus"]["proposed_question_context"],
                    "source_documents_checked": ["VJC_2022_separate"],
                    "is_blank_template": False,
                }
            )
            responses = directory / "responses.jsonl"
            responses.write_text(json.dumps(response) + "\n", encoding="utf-8")
            receipt = verify_corporate_report_context_human_reviews(
                queue=queue,
                queue_manifest=queue_manifest,
                completed_responses=responses,
                reviewer_id="reviewer-1",
                output=directory / "receipt.json",
            )
            self.assertFalse(receipt["context_amendment_application_allowed"])
            self.assertFalse(receipt["materialization_allowed"])
            response["reviewed_context"] = {"ticker": "ACB"}
            responses.write_text(json.dumps(response) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "approval must verify exactly"):
                verify_corporate_report_context_human_reviews(
                    queue=queue,
                    queue_manifest=queue_manifest,
                    completed_responses=responses,
                    reviewer_id="reviewer-1",
                    output=directory / "receipt-2.json",
                )


if __name__ == "__main__":
    unittest.main()
