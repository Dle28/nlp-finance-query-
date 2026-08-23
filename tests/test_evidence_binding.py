from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from finance_query.evidence_binding import (
    CONFLICT,
    FAIL,
    NOT_APPLICABLE,
    PASS,
    UNRESOLVED,
    build_evidence_binding,
    resolve_revision_hierarchy,
    resolve_unit_precedence,
    validate_evidence_binding,
    validate_formula_compatibility,
)


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _binding(
    *,
    operand_id: str = "net_revenue",
    period_status: str = PASS,
    period_overrides: dict | None = None,
    revision_status: str = PASS,
    required_fields: list[str] | None = None,
    role_anchor: dict | None = None,
) -> dict:
    document_uid = f"VJC_2024_consolidated_{operand_id}"
    table_uid = f"table-vjc-2024-{operand_id}"
    raw_cell_digest = "a" * 64

    def cell(row_index: int, column_index: int) -> dict:
        return {
            "kind": "raw_cell",
            "document_uid": document_uid,
            "internal_table_uid": table_uid,
            "row_index": row_index,
            "column_index": column_index,
            "raw_text_sha256": raw_cell_digest,
        }

    document_anchor = {
        "kind": "document_metadata",
        "document_uid": document_uid,
        "raw_text_sha256": raw_cell_digest,
    }
    period = {
        "status": period_status,
        "raw_period_label": "Năm 2024",
        "period_years": [2024],
        "period_grain": "fiscal_year",
        "flow_or_stock": "flow",
        "start_date": "2024-01-01",
        "end_date": "2024-12-31",
        "as_of_date": None,
        "fiscal_year": 2024,
        "fiscal_quarter": None,
        "comparative_basis": "current",
        "source_anchors": [cell(0, 2)],
    }
    period.update(period_overrides or {})
    unit = {
        "status": PASS,
        "raw_unit_label": "Triệu đồng",
        "normalized_currency": "VND",
        "scale": "1000000",
        "semantic_unit": "monetary",
        "resolution_level": "column",
        "conversion_status": "NOT_REQUIRED",
        "source_anchors": [cell(0, 2)],
    }
    return build_evidence_binding(
        operand_id=operand_id,
        document_uid=document_uid,
        internal_table_uid=table_uid,
        source_integrity={"status": PASS, "value_cell": cell(3, 2)},
        variable_binding={
            "status": PASS,
            "variable_id": operand_id,
            "raw_row_label": "Doanh thu thuần",
            "recognition_method": "exact_source_alias",
            "source_anchors": [cell(3, 0)],
        },
        period_binding=period,
        unit_binding=unit,
        entity_scope_binding={
            "entity_status": PASS,
            "scope_status": PASS,
            "entity": "VJC",
            "scope": "consolidated",
            "source_anchors": [document_anchor],
        },
        entity_role_binding={
            "status": PASS,
            "role": "parent",
            "recognition_method": "human_verified_document_role_v1",
            "source_anchors": [role_anchor or document_anchor],
        },
        revision_binding={
            "status": revision_status,
            "revision_policy": "latest_valid",
            "audit_status": "audited",
            "revision_status": "original",
            "publication_date": "2025-03-30",
            "effective_date": "2025-03-30",
            "effective_version": "2025-audited-original",
            "source_document_uid": document_uid,
            "supersedes_document_uid": None,
            "source_anchors": [document_anchor],
        },
        binding_lineage={
            "document_sha256": "b" * 64,
            "table_sha256": "c" * 64,
            "raw_cell_sha256": raw_cell_digest,
            "header_evidence_sha256": _hash(
                {
                    "period_source_anchors": period["source_anchors"],
                    "unit_source_anchors": unit["source_anchors"],
                }
            ),
            "binding_schema_version": 2,
            "resolver_version": "evidence-binding-resolver-v1",
        },
        required_fields=required_fields,
    )


def test_complete_binding_is_eligible_and_content_hashed() -> None:
    binding = _binding()
    assert binding["binding_status"] == "BOUND"
    assert binding["operand_eligible"] is True
    assert set(binding["field_statuses"].values()) == {PASS}
    assert validate_evidence_binding(binding) == []

    tampered = deepcopy(binding)
    tampered["period_binding"]["end_date"] = "2024-11-30"
    assert "BINDING_ID_DOES_NOT_MATCH_CONTENT" in validate_evidence_binding(tampered)


def test_document_text_line_is_valid_entity_role_anchor() -> None:
    binding = _binding(
        role_anchor={
            "kind": "document_text_line",
            "document_uid": "VJC_2024_consolidated_net_revenue",
            "line_number": 42,
            "source_file_sha256": "e" * 64,
            "raw_text_sha256": "f" * 64,
        }
    )
    assert validate_evidence_binding(binding) == []


def test_unresolved_period_is_visible_but_cannot_authorize_an_operand() -> None:
    binding = _binding(period_status=UNRESOLVED)
    assert binding["field_statuses"]["period_status"] == UNRESOLVED
    assert binding["binding_status"] == "BLOCKED"
    assert binding["operand_eligible"] is False
    assert validate_evidence_binding(binding) == []


def test_incomplete_instant_period_fails_not_inferred_from_a_date_label() -> None:
    binding = _binding(
        period_overrides={
            "raw_period_label": "31/12/2024",
            "period_grain": "instant",
            "flow_or_stock": "stock",
            "start_date": None,
            "end_date": None,
            "as_of_date": None,
            "fiscal_year": None,
        }
    )
    assert binding["field_statuses"]["period_status"] == FAIL
    assert "PERIOD_AS_OF_DATE_REQUIRED" in binding["reason_codes"]
    assert binding["operand_eligible"] is False


def test_not_applicable_requires_an_explicit_non_required_contract_field() -> None:
    allowed = _binding(
        revision_status=NOT_APPLICABLE,
        required_fields=["variable", "period", "unit", "entity", "entity_role", "scope", "source_integrity"],
    )
    assert allowed["binding_status"] == "BOUND"
    assert allowed["field_statuses"]["revision_status"] == NOT_APPLICABLE
    assert validate_evidence_binding(allowed) == []

    required = _binding(revision_status=NOT_APPLICABLE)
    assert required["binding_status"] == "BLOCKED"
    assert "BINDING_NOT_APPLICABLE_FIELD_REQUIRED:revision" in required["reason_codes"]


def test_lineage_hashes_bind_value_and_header_evidence() -> None:
    binding = _binding()
    tampered = deepcopy(binding)
    tampered["binding_lineage"]["raw_cell_sha256"] = "d" * 64
    errors = validate_evidence_binding(tampered)
    assert "BINDING_LINEAGE_RAW_CELL_HASH_MISMATCH" in errors
    assert "BINDING_ID_DOES_NOT_MATCH_CONTENT" in errors


def test_unit_precedence_uses_only_explicit_declarations() -> None:
    table = {
        "explicit": True,
        "resolution_level": "table",
        "raw_unit_label": "Triệu đồng",
        "normalized_currency": "VND",
        "scale": "1000000",
        "semantic_unit": "monetary",
        "source_anchors": [{"anchor": "table"}],
    }
    inferred_cell = {
        "explicit": False,
        "resolution_level": "cell",
        "raw_unit_label": "",
        "normalized_currency": "VND",
        "scale": "1",
        "semantic_unit": "monetary",
        "source_anchors": [],
    }
    assert resolve_unit_precedence([table, inferred_cell])["selected"] == table

    explicit_cell = {**table, "resolution_level": "cell", "raw_unit_label": "Đồng", "scale": "1"}
    assert resolve_unit_precedence([table, explicit_cell])["selected"] == explicit_cell

    conflict = {**explicit_cell, "raw_unit_label": "Nghìn đồng", "scale": "1000"}
    assert resolve_unit_precedence([explicit_cell, conflict])["status"] == CONFLICT


def test_revision_policy_is_query_specific_and_honours_point_in_time() -> None:
    def candidate(document_uid: str, publication_date: str, revision_status: str, *, supersedes: str | None = None) -> dict:
        return {
            "entity": "VJC",
            "scope": "consolidated",
            "period_key": "FY2024",
            "statement_type": "income_statement",
            "revision_binding": {
                "status": PASS,
                "source_document_uid": document_uid,
                "publication_date": publication_date,
                "effective_date": publication_date,
                "audit_status": "audited",
                "revision_status": revision_status,
                "supersedes_document_uid": supersedes,
            },
        }

    original = candidate("original", "2025-03-30", "original")
    restated = candidate("restated", "2025-06-30", "restated", supersedes="original")
    assert resolve_revision_hierarchy([original, restated], revision_policy="latest_valid")["selected_document_uid"] == "restated"
    assert resolve_revision_hierarchy([original, restated], revision_policy="point_in_time", as_of_date="2025-04-01")["selected_document_uid"] == "original"
    assert resolve_revision_hierarchy([original, restated], revision_policy="originally_reported")["selected_document_uid"] == "original"
    assert resolve_revision_hierarchy([original, restated], revision_policy="restated")["selected_document_uid"] == "restated"

    tied = resolve_revision_hierarchy(
        [candidate("document-a", "2025-04-01", "original"), candidate("document-b", "2025-04-01", "original")],
        revision_policy="originally_reported",
    )
    assert tied["status"] == CONFLICT


def test_formula_compatibility_allows_flow_with_opening_and_closing_stock() -> None:
    net_income = _binding(operand_id="net_income")
    assets_open = _binding(
        operand_id="assets_open",
        period_overrides={
            "raw_period_label": "01/01/2024",
            "period_grain": "instant",
            "flow_or_stock": "stock",
            "start_date": None,
            "end_date": None,
            "as_of_date": "2024-01-01",
            "fiscal_year": None,
            "comparative_basis": "opening_balance",
        },
    )
    assets_close = _binding(
        operand_id="assets_close",
        period_overrides={
            "raw_period_label": "31/12/2024",
            "period_grain": "instant",
            "flow_or_stock": "stock",
            "start_date": None,
            "end_date": None,
            "as_of_date": "2024-12-31",
            "fiscal_year": None,
            "comparative_basis": "closing_balance",
        },
    )
    operands = {binding["operand_id"]: binding for binding in (net_income, assets_open, assets_close)}
    contract = {
        "formula_id": "roa_average_assets_v1",
        "operand_constraints": {
            "net_income": {
                "period_grains": ["fiscal_year"], "flow_or_stock": ["flow"], "comparative_bases": ["current"],
                "semantic_units": ["monetary"], "revision_policies": ["latest_valid"],
            },
            "assets_open": {
                "period_grains": ["instant"], "flow_or_stock": ["stock"], "comparative_bases": ["opening_balance"],
                "semantic_units": ["monetary"], "revision_policies": ["latest_valid"],
            },
            "assets_close": {
                "period_grains": ["instant"], "flow_or_stock": ["stock"], "comparative_bases": ["closing_balance"],
                "semantic_units": ["monetary"], "revision_policies": ["latest_valid"],
            },
        },
        "cross_operand_rules": [
            {"kind": "same", "field": "entity", "operands": ["net_income", "assets_open", "assets_close"]},
            {"kind": "same", "field": "scope", "operands": ["net_income", "assets_open", "assets_close"]},
            {"kind": "same", "field": "normalized_currency", "operands": ["net_income", "assets_open", "assets_close"]},
        ],
    }
    assert validate_formula_compatibility(operands, contract) == []

    wrong = deepcopy(assets_close)
    wrong["entity_scope_binding"]["scope"] = "separate"
    assert any("SAME_FIELD_VIOLATION:scope" in error for error in validate_formula_compatibility({**operands, "assets_close": wrong}, contract))
