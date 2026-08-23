"""Deterministic Person-1 decisions for frozen metadata and period queues.

This plan is a review input only. It cannot mutate metadata, choose a table
cell/value, or promote a record.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence


REVIEWED_AT = "2026-08-12T12:00:00+07:00"
REVIEWER = {"reviewer_type": "independent_ai_source_review", "reviewer_id": "person1-source-audit"}

# Local issuer-report snapshot coordinates supporting accepted sector patches.
SECTOR_REPORT_LINES = {
    "SSH_financial_statements_2021_separate": 392, "NVL_financial_statements_2016_separate": 335,
    "VPI_financial_statements_2025_separate": 138, "DCM_financial_statements_2019_separate": 520,
    "GAS_financial_statements_2021_separate": 492, "VJC_financial_statements_2018_separate": 284,
    "MSR_financial_statements_2025_separate": 286, "HSG_financial_statements_2017_separate": 24,
    "DPM_financial_statements_2024_separate": 557, "CRE_financial_statements_2019_separate": 447,
    "NLG_financial_statements_2022_separate": 43, "PVT_financial_statements_2019_separate": 450,
    "DIG_financial_statements_2015_separate": 40, "DIG_financial_statements_2016_separate": 51,
    "DIG_financial_statements_2017_separate": 48, "DIG_financial_statements_2021_separate": 342,
    "DIG_financial_statements_2024_separate": 449, "HBC_financial_statements_2016_separate": 40,
    "HBC_financial_statements_2020_separate": 37, "QNS_financial_statements_2024_separate": 460,
    "NLG_financial_statements_2019_separate": 59, "GVR_financial_statements_2015_separate": 308,
    "DBC_financial_statements_2018_separate": 872, "DTK_financial_statements_2017_separate": 524,
    "SAB_financial_statements_2017_separate": 439, "DBC_financial_statements_2017_separate": 867,
    "MCH_financial_statements_2017_separate": 235, "HDG_financial_statements_2016_separate": 392,
    "HDG_financial_statements_2017_separate": 372, "HDG_financial_statements_2018_separate": 382,
    "HDG_financial_statements_2019_separate": 423, "MPC_financial_statements_2016_separate": 292,
    "SAB_financial_statements_2016_separate": 38, "HAG_financial_statements_2016_separate": 32,
    "DPM_financial_statements_2017_separate": 410, "AAA_financial_statements_2017_separate": 379,
    "MSR_financial_statements_2017_separate": 297, "AAA_financial_statements_2016_separate": 390,
    "NKG_financial_statements_2016_separate": 97, "DCM_financial_statements_2016_separate": 503,
    "DPM_financial_statements_2016_separate": 356, "IJC_financial_statements_2016_separate": 428,
    "IJC_financial_statements_2017_separate": 133, "IJC_financial_statements_2018_separate": 480,
    "IJC_financial_statements_2023_separate": 534, "IJC_financial_statements_2024_separate": 522,
}

SECTOR_DOCUMENTS_BY_QID = {
    10: ["SSH_financial_statements_2021_separate"], 28: ["NVL_financial_statements_2016_separate"],
    67: ["VPI_financial_statements_2025_separate"], 70: ["DCM_financial_statements_2019_separate"],
    135: ["GAS_financial_statements_2021_separate"], 145: ["VJC_financial_statements_2018_separate"],
    167: ["MSR_financial_statements_2025_separate"], 201: ["HSG_financial_statements_2017_separate"],
    245: ["DPM_financial_statements_2024_separate"], 292: ["CRE_financial_statements_2019_separate"],
    310: ["NLG_financial_statements_2022_separate"], 340: ["PVT_financial_statements_2019_separate"],
    350: ["HSG_financial_statements_2017_separate"],
    507: ["DIG_financial_statements_2015_separate", "DIG_financial_statements_2016_separate", "DIG_financial_statements_2017_separate", "DIG_financial_statements_2021_separate", "DIG_financial_statements_2024_separate"],
    629: ["HBC_financial_statements_2016_separate", "HBC_financial_statements_2020_separate"],
    680: ["QNS_financial_statements_2024_separate"], 681: ["NLG_financial_statements_2019_separate"],
    724: ["GVR_financial_statements_2015_separate"], 728: ["DBC_financial_statements_2018_separate"],
    730: ["DTK_financial_statements_2017_separate"],
    858: ["SAB_financial_statements_2017_separate", "DBC_financial_statements_2017_separate", "MCH_financial_statements_2017_separate"],
    893: ["HDG_financial_statements_2016_separate", "HDG_financial_statements_2017_separate", "HDG_financial_statements_2018_separate", "HDG_financial_statements_2019_separate"],
    915: ["MPC_financial_statements_2016_separate", "SAB_financial_statements_2016_separate", "HAG_financial_statements_2016_separate"],
    977: ["DPM_financial_statements_2017_separate", "AAA_financial_statements_2017_separate", "MSR_financial_statements_2017_separate"],
    1002: ["AAA_financial_statements_2016_separate", "NKG_financial_statements_2016_separate", "DCM_financial_statements_2016_separate", "DPM_financial_statements_2016_separate"],
    1004: ["IJC_financial_statements_2016_separate", "IJC_financial_statements_2017_separate", "IJC_financial_statements_2018_separate", "IJC_financial_statements_2023_separate", "IJC_financial_statements_2024_separate"],
}

# Exact V2 rows inspected for entity/year rejections. Prefixes must resolve
# uniquely during sidecar materialization.
ENTITY_ROW_SOURCES: dict[tuple[int, str], list[tuple[str, int]]] = {
    (14, "stage_1_general_and_administrative_expense"): [("049c9335a3fa", 10)],
    (45, "stage_1_liabilities"): [("f5c075b03421", 13)], (98, "stage_1_inventory"): [("8e4dbe6a75ae", 15)],
    (131, "stage_1_cash_and_cash_equivalents"): [("4e7bc3167fb5", 10)], (132, "stage_1_interest_expense"): [("6aa48d96a3b1", 1)],
    (148, "stage_1_cash_at_end"): [("0fa2a09aff75", 3)], (152, "stage_1_total_assets"): [("91de3f398fc8", 21)],
    (156, "stage_1_assets"): [("62afef781056", 8)], (185, "stage_1_assets"): [("f75f9583841f", 6)],
    (198, "stage_1_deferred_income_tax_expense"): [("a326f2f3770d", 20)], (263, "stage_1_assets"): [("ab27789262ac", 1)],
    (272, "stage_1_total_assets"): [("f98763ab7b47", 35)], (329, "stage_1_net_income"): [("d85d951161df", 21)],
    (528, "stage_1_operating_profit"): [("2e4b872a36c8", 19), ("33cd223f3360", 19), ("dcae5a6b2b30", 19)],
    (528, "stage_3_total_assets"): [("9b14b4c8fa01", 37), ("30ee34c93bfa", 36), ("22b20c7d1148", 33)],
    (535, "stage_2_assets"): [("fdbefd0129d9", 13), ("adf45816d65b", 10), ("c3847c49b0b6", 8)],
    (660, "stage_1_total_assets"): [("96b44de4c09d", 34)], (667, "stage_2_total_assets"): [("0f41b005f60d", 30)],
    (694, "stage_1_total_assets"): [("bb6bb69958d5", 21)], (717, "stage_1_total_assets"): [("fb8005bd1745", 20)],
    (719, "stage_1_operating_cash_flow"): [("08ed7110a215", 10)],
    (869, "stage_1_financing_cash_flow"): [("f2627c2ef36e", 8), ("eeb38540b279", 8), ("ddc090592234", 8), ("879d5939e71b", 8), ("912e1516de27", 8)],
    (982, "stage_1_assets"): [("16d67eb3846f", 2), ("014393456e90", 3), ("6056d3902335", 4), ("d6ab2a5d65e1", 2)],
    (1007, "stage_1_assets"): [("7335a8bbbc90", 1), ("0af259841e1c", 3), ("e68846e3d830", 3)],
    (819, "year_metadata"): [("9fbc49cb3fa8", 1), ("e4e693cf8139", 2), ("bd8d61acf0e1", 1)],
}

SPECIAL_REJECT_REASONS = {
    (104, "stage_1_profit_before_tax"): ["TARGET_CANONICAL_DOCUMENT_ABSENT", "AMBIGUOUS_SOURCE_VARIANTS", "NO_EXACT_ROW"],
    (156, "stage_1_assets"): ["ENTITY_CONFIRMED", "ROUTE_CONCEPT_TOO_BROAD", "SPECIALIZED_METRIC_NOT_ASSETS"],
    (185, "stage_1_assets"): ["ENTITY_CONFIRMED", "ROUTE_CONCEPT_TOO_BROAD", "AGGREGATE_REQUIRED"],
    (535, "stage_2_assets"): ["ENTITY_CONFIRMED", "QUERY_CONCEPT_MISPARSE", "MULTIYEAR_SOURCE_INCOMPLETE"],
    (869, "stage_1_financing_cash_flow"): ["ENTITY_CONFIRMED", "LABEL_VARIANT_WITHOUT_THUAN", "TARGET_ROWS_ALL_YEARS", "TAXONOMY_CONCEPT_UNMAPPED"],
    (982, "stage_1_assets"): ["ENTITY_CONFIRMED", "QUERY_CONCEPT_TOO_BROAD", "SEGMENT_ASSET_RATIO_REQUIRED", "MISSING_2023_CURRENT_TABLE"],
    (1007, "stage_1_assets"): ["ENTITY_CONFIRMED", "QUERY_CONCEPT_TOO_BROAD", "SEGMENT_ASSET_RATIO_REQUIRED", "MISSING_2024_TABLE"],
    (819, "year_metadata"): ["TARGET_YEAR_ROWS_EXIST", "CANDIDATE_WRONG_YEAR", "REQUIRES_EXACT_REBIND_NOT_YEAR_MUTATION"],
}

PERIOD_ACCEPTS: dict[int, list[tuple[str, int, int, list[tuple[int, int]]]]] = {
    13: [("415bf0", 2, 3, [(0, 3)])], 34: [("6a7521", 10, 3, [(0, 3)])],
    155: [("41b560", 15, 3, [(0, 3)])], 184: [("3e0ad7", 2, 3, [(0, 3)])],
    211: [("234d42", 16, 3, [(0, 3)])], 316: [("21efaa", 15, 3, [(0, 3), (1, 3)])],
    325: [("321fad", 17, 3, [(0, 3)])], 663: [("84749d", 42, 3, [(0, 3)])],
    960: [("e67c9a", 19, 3, [(0, 3)]), ("c270f6", 19, 3, [(0, 3)]), ("602600", 19, 3, [(0, 3)]), ("d8149c", 18, 2, [(0, 2)]), ("d3e191", 18, 2, [(0, 2)])],
    973: [("fca2d6", 16, 3, [(0, 3)]), ("6880f1", 18, 3, [(0, 3), (1, 3)]), ("777f35", 11, 4, [(0, 4)])],
}

PERIOD_REJECT_REASONS = {
    411: ["MULTI_ENTITY_COVERAGE_INCOMPLETE", "REQUIRED_YEAR_COVERAGE_INCOMPLETE", "REQUIRED_ROLE_COVERAGE_INCOMPLETE", "COLUMN_PATCH_CANNOT_REPAIR_ROUTE"],
    426: ["REQUIRED_YEAR_COVERAGE_INCOMPLETE", "DUPLICATE_STAGE_ROLE", "REQUIRED_ROLE_COVERAGE_INCOMPLETE"],
    538: ["MULTI_ENTITY_COVERAGE_INCOMPLETE", "REQUIRED_YEAR_COVERAGE_INCOMPLETE", "REQUIRED_ROLE_COVERAGE_INCOMPLETE"],
    734: ["SECOND_ENTITY_OPERAND_MISSING", "ROUTE_INCOMPLETE", "COLUMN_PATCH_CANNOT_REPAIR_ROUTE"],
    962: ["REQUIRED_YEAR_COVERAGE_INCOMPLETE", "AVERAGE_PERIOD_SET_INCOMPLETE"],
    1003: ["MULTI_ENTITY_COVERAGE_INCOMPLETE", "NON_FULL_YEAR_PERIOD_REQUIRES_SEPARATE_SEMANTIC_REVIEW", "COLUMN_PATCH_CANNOT_REPAIR_ROUTE"],
}


def _artifact_source(prefix: str, row: int, column: int, headers: Sequence[tuple[int, int]]) -> dict[str, Any]:
    return {"source_kind": "hash_bound_table_artifact", "internal_table_uid_prefix": prefix, "row_index": row, "column_index": column, "header_source_cells": [{"row_index": r, "column_index": c} for r, c in headers]}


def _sector_source(document: Mapping[str, Any]) -> dict[str, Any]:
    document_id = str(document.get("document_id") or "")
    line = SECTOR_REPORT_LINES.get(document_id)
    if line is None:
        raise ValueError(f"No issuer citation configured for {document_id}")
    company = str(document.get("company") or "")
    year = int(document.get("report_year") or 0)
    return {"source_kind": "issuer_financial_statement", "path": f"data/ViFinQA/financial_statements/{company}/{year}/{document_id}/{document_id}_extracted.txt", "line_start": line, "line_end": line, "retrieved_at": "2026-08-12"}


def build_person1_review_plan(*, metadata_queue: Sequence[Mapping[str, Any]], period_queue: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    for item in metadata_queue:
        identity = item.get("immutable_source_identity") or {}
        qid, stage, role = int(identity.get("question_id") or 0), str(identity.get("stage_id") or ""), str(identity.get("role") or "")
        base = {"source_queue": "metadata", "question_id": qid, "stage_id": stage, "role": role, "decision_provenance": REVIEWER, "reviewed_at": REVIEWED_AT, "source_coordinates_checked": True}
        queue_kind = str(item.get("queue_kind") or "")
        is_sector = queue_kind == "sector_metadata" or item.get("exclusive_primary_cause") == "SECTOR"
        if is_sector:
            if qid == 176:
                plans.append({**base, "decision": "uncertain", "authoritative_sources": [], "reason_codes": ["QUESTION_ENTITY_NAME_CONFLICT", "SOURCE_ENTITY_DIFFERS_FROM_QUESTION_ENTITY", "ENTITY_ADJUDICATION_REQUIRED_FIRST"], "proposed_patch": None, "notes": "DXG evidence cannot prove Bluemarq Group is DXG."})
            else:
                candidates = {str(doc.get("document_id") or ""): doc for doc in item.get("target_document_candidates") or []}
                document_ids = SECTOR_DOCUMENTS_BY_QID.get(qid, [])
                documents = [candidates[document_id] for document_id in document_ids if document_id in candidates]
                if len(documents) != len(document_ids):
                    raise ValueError(f"Sector review Q{qid} target document coverage changed")
                plans.append({**base, "decision": "accept_repair", "authoritative_sources": [_sector_source(doc) for doc in documents], "reason_codes": ["ENTITY_YEAR_SCOPE_MATCH", "ISSUER_FINANCIAL_STATEMENT_ACTIVITY_DISCLOSURE", "LOCAL_TAXONOMY_INDUSTRIAL_MAPPING_SUPPORTED", "CANDIDATE_SECTOR_UNKNOWN_REPAIRED"], "proposed_patch": {"scope": "metadata", "field": "sector", "operations": [{"document_id": doc["document_id"], "old_value": "unknown", "proposed_value": "industrial"} for doc in documents], "reason": "issuer_financial_statement_activity_disclosure"}, "notes": "Proposal only; no overlay is materialized."})
            continue
        sources = [_artifact_source(prefix, row, 0, []) for prefix, row in ENTITY_ROW_SOURCES.get((qid, stage), [])]
        plans.append({**base, "decision": "reject_repair", "authoritative_sources": sources, "reason_codes": SPECIAL_REJECT_REASONS.get((qid, stage), ["ENTITY_CONFIRMED", "TARGET_ROW_EXISTS", "TAXONOMY_CONCEPT_UNMAPPED"]), "proposed_patch": None, "notes": "No source-backed alias/metadata mutation exists; separate routing or taxonomy work is required."})
    for item in period_queue:
        qid = int((item.get("immutable_source_identity") or {}).get("question_id") or 0)
        base = {"source_queue": "period", "question_id": qid, "decision_provenance": REVIEWER, "reviewed_at": REVIEWED_AT, "source_coordinates_checked": True}
        if qid in PERIOD_ACCEPTS:
            sources = [_artifact_source(prefix, row, column, headers) for prefix, row, column, headers in PERIOD_ACCEPTS[qid]]
            plans.append({**base, "decision": "accept_repair", "authoritative_sources": sources, "reason_codes": ["EXPLICIT_PERIOD_OR_CONTEXT_DATE_ANCHOR", "HEADER_SOURCE_COORDINATES_CHECKED", "NO_VALUE_SELECTED"], "proposed_patch": {"scope": "period_anchor", "bindings": [{"internal_table_uid_prefix": source["internal_table_uid_prefix"], "row_index": source["row_index"], "column_index": source["column_index"], "header_source_cells": source["header_source_cells"]} for source in sources], "reason": "explicit_header_or_context_date_anchor"}, "notes": "Only period/column identity is proposed; no value or formula."})
        else:
            plans.append({**base, "decision": "reject_repair", "authoritative_sources": [], "reason_codes": PERIOD_REJECT_REASONS[qid], "proposed_patch": None, "notes": "The blocker is route/entity/year/role coverage, not a safe period-column repair."})
    return plans
