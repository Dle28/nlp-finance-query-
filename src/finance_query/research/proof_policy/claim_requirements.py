"""Fail-closed V13 claim-requirement and semantic-coverage contracts.

V13 is a research-only shadow overlay. It never mutates V12 artifacts and it
cannot authorize an answer, release, promotion, training, or submission.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


REQUIREMENT_PROTOCOL = "vifinqa_claim_requirement_set_v1"
COVERAGE_PROTOCOL = "vifinqa_semantic_coverage_certificate_v2"
COMPOSED_TAXONOMY_PROTOCOL = "vifinqa_composed_blocker_taxonomy_v1"
ROUTE_TAXONOMY_PROTOCOL = "vifinqa_route_blocker_taxonomy_v2"
TEMPORAL_PROTOCOL = "vifinqa_temporal_semantics_v1"
CLAIM_REQUIREMENT_GENERATOR_VERSION = "v4"
TEMPORAL_RULES_VERSION = "v3"
PROOF_STATUSES = {"PASS", "FAIL", "UNRESOLVED", "NOT_APPLICABLE", "NOT_CHECKED"}
ISSUER_TICKERS_V1 = frozenset({
    "AAA", "ABB", "ACB", "ACV", "ASM", "BAB", "BAF", "BID", "BSR", "BVH",
    "CEO", "CRE", "CTG", "DBC", "DCM", "DIG", "DLG", "DNH", "DPM", "DTK",
    "DXG", "DXS", "EIB", "EVF", "FIT", "FOX", "FPT", "FTS", "GAS", "GEE",
    "GEG", "GEX", "GVR", "HAG", "HBC", "HDB", "HDG", "HHS", "HHV", "HND",
    "HNG", "HPG", "HPX", "HSG", "HUT", "IJC", "KBC", "KHG", "KLB", "MBB",
    "MBS", "MCH", "MML", "MPC", "MSB", "MSN", "MSR", "MWG", "NAB", "NKG",
    "NLG", "NVB", "NVL", "OCB", "OGC", "PDR", "PLX", "PNJ", "POW", "PRT",
    "PVT", "QNS", "SAB", "SAM", "SCR", "SGB", "SHB", "SJG", "SNZ", "SSB",
    "SSH", "SSI", "STB", "TTF", "VAB", "VCB", "VGC", "VGT", "VIB", "VIC",
    "VIF", "VJC", "VNM", "VPB", "VPI", "VRE", "VSC", "VSF",
})


def canonical_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} must contain a JSON object")
        rows.append(value)
    return rows


def index_by_question(rows: Iterable[Mapping[str, Any]], label: str) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for raw in rows:
        question_id = raw.get("question_id")
        if not isinstance(question_id, int) or isinstance(question_id, bool):
            raise ValueError(f"{label} requires integer question_id")
        if question_id in result:
            raise ValueError(f"{label} has duplicate question_id {question_id}")
        result[question_id] = dict(raw)
    return result


def source_contract() -> dict[str, bool]:
    return {
        "research_only": True,
        "evidence_eligible": False,
        "may_materialize_answer": False,
        "may_execute_formula": False,
        "promotion_allowed": False,
        "training_eligible": False,
        "submission_eligible": False,
        "release_authorized": False,
    }


def _normalized(text: str) -> str:
    value = unicodedata.normalize("NFKD", text)
    value = "".join(character for character in value if not unicodedata.combining(character))
    value = value.replace("đ", "d").replace("Đ", "D")
    return " ".join(value.casefold().split())


def _explicit_entity_role(question: str) -> str | None:
    text = _normalized(question)
    matches = [role for role, phrase in (("parent", "cong ty me"), ("subsidiary", "cong ty con")) if phrase in text]
    if set(matches) == {"parent", "subsidiary"}:
        # In claims such as "đầu tư vào công ty con của công ty mẹ DLG", the
        # subsidiary phrase names the metric/counterparty axis while the
        # reporting entity is explicitly the parent. Do not let the object
        # phrase erase the subject-role proposition.
        if "cua cong ty me" in text or "(cong ty me)" in text:
            return "parent"
    return matches[0] if len(matches) == 1 else None


def _explicit_entities(question: str) -> list[str]:
    """Extract only literal tickers in the closed V1 issuer registry."""
    parenthesized = re.findall(r"\(([A-Z][A-Z0-9]{1,5})\)", question)
    tokens = re.findall(r"(?<![A-Z0-9])([A-Z][A-Z0-9]{1,5})(?![A-Z0-9])", question)
    result: list[str] = []
    for value in [*parenthesized, *tokens]:
        if value in ISSUER_TICKERS_V1 and value not in result:
            result.append(value)
    return result


def _explicit_years(question: str) -> list[int]:
    return sorted({int(value) for value in re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", question)})


def _explicit_reporting_scope(question: str) -> str | None:
    text = _normalized(question)
    matches = [scope for scope, phrase in (("separate", "rieng le"), ("consolidated", "hop nhat")) if phrase in text]
    return matches[0] if len(matches) == 1 else None


def _explicit_statement_role(question: str) -> str | None:
    text = _normalized(question)
    rules = (
        ("balance_sheet", ("bang can doi ke toan", "bang can doi")),
        ("income_statement", ("bao cao ket qua kinh doanh", "ket qua hoat dong kinh doanh")),
        ("cash_flow_statement", ("bao cao luu chuyen tien te", "luu chuyen tien te")),
    )
    matches = [role for role, phrases in rules if any(phrase in text for phrase in phrases)]
    return matches[0] if len(matches) == 1 else None


def _explicit_metric_qualifiers(question: str) -> dict[str, str]:
    text = _normalized(question)
    result: dict[str, str] = {}
    tax = [value for value, phrase in (("before_tax", "truoc thue"), ("after_tax", "sau thue")) if phrase in text]
    gross_net = [
        value
        for value, phrases in (
            ("gross", ("loi nhuan gop", "gia tri gop")),
            ("net", ("loi nhuan thuan", "thu nhap thuan", "gia tri thuan")),
        )
        if any(phrase in text for phrase in phrases)
    ]
    if len(tax) == 1:
        result["metric.tax_treatment"] = tax[0]
    if len(gross_net) == 1:
        result["metric.gross_net_basis"] = gross_net[0]
    return result


def claim_temporal_object(question: str, years: Sequence[int]) -> dict[str, Any]:
    """Return claim-derived temporal semantics only; never inspect source packets."""
    text = _normalized(question)
    exact = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-]((?:19|20)\d{2})\b", question)
    if exact is None:
        exact = re.search(r"\bngay\s+(\d{1,2})\s+thang\s+(\d{1,2})\s+nam\s+((?:19|20)\d{2})\b", text)
    if exact:
        day, month, year = (int(exact.group(1)), int(exact.group(2)), int(exact.group(3)))
        try:
            exact_date = date(year, month, day).isoformat()
        except ValueError:
            return {
                "kind": "UNRESOLVED",
                "start": None,
                "end": None,
                "role": "UNRESOLVED",
                "requested_years": list(years),
                "derivation": "invalid_exact_date_literal",
            }
        return {
            "kind": "instant",
            "start": None,
            "end": exact_date,
            "role": "explicit_instant",
            "requested_years": list(years),
            "derivation": "exact_date_literal",
        }
    if any(phrase in text for phrase in ("dau nam", "dau ky", "so du dau")):
        kind, role = "instant", "opening"
    elif any(phrase in text for phrase in ("cuoi nam", "cuoi ky", "tai ngay", "so du cuoi")):
        kind, role = "instant", "closing"
    elif re.search(r"(?<!\w)nam(?!\w)", text) or re.search(r"(?<!\w)ky(?!\w)", text):
        kind, role = "duration", "current_duration"
    else:
        kind, role = "UNRESOLVED", "UNRESOLVED"
    return {
        "kind": kind,
        "start": None,
        "end": None,
        "role": role,
        "requested_years": list(years),
        "derivation": f"claim_literal_temporal_rules_{TEMPORAL_RULES_VERSION}",
    }


def _requirement(
    question_id: int,
    definition_id: str,
    dimension: str,
    applicability: str,
    *,
    expected: object,
    basis: str,
    depends_on: Sequence[str] = (),
) -> dict[str, Any]:
    if applicability not in {"REQUIRED", "NOT_APPLICABLE", "NOT_CHECKED"}:
        raise ValueError(f"invalid applicability {applicability}")
    payload = {
        "dimension": dimension,
        "applicability": applicability,
        "expected": expected,
        "requirement_basis": basis,
        "depends_on": list(depends_on),
    }
    return {
        "obligation_id": canonical_sha256({"question_id": question_id, "definition_id": definition_id, **payload}),
        **payload,
    }


def validate_dependency_graph(requirements: Sequence[Mapping[str, Any]]) -> None:
    dimensions = [str(item.get("dimension") or "") for item in requirements]
    if len(dimensions) != len(set(dimensions)) or not all(dimensions):
        raise ValueError("requirement dimensions must be unique and non-empty")
    graph = {str(item["dimension"]): [str(value) for value in item.get("depends_on") or []] for item in requirements}
    for node, dependencies in graph.items():
        missing = [dependency for dependency in dependencies if dependency not in graph]
        if missing:
            raise ValueError(f"requirement {node} has missing dependencies {missing}")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise ValueError(f"requirement dependency cycle at {node}")
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph[node]:
            visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node)


def build_claim_requirement_set(route: Mapping[str, Any], *, definition_id: str = "UNPINNED_TEST_DEFINITION") -> dict[str, Any]:
    """Generate auditable obligations while refusing to claim exhaustiveness."""
    question_id = int(route["question_id"])
    question = str(route.get("question") or "")
    context = route.get("question_context") if isinstance(route.get("question_context"), Mapping) else {}
    context_entities = [str(value) for value in context.get("entities") or [] if str(value)]
    context_years = [int(value) for value in context.get("years") or [] if isinstance(value, int) and not isinstance(value, bool)]
    entities = _explicit_entities(question)
    years = _explicit_years(question)
    requested_unit = route.get("requested_output_unit") if isinstance(route.get("requested_output_unit"), Mapping) else {}
    role = _explicit_entity_role(question)
    scope = _explicit_reporting_scope(question)
    statement_role = _explicit_statement_role(question)
    qualifiers = _explicit_metric_qualifiers(question)
    temporal = claim_temporal_object(question, years)
    required_operations = [str(value) for value in route.get("required_operations") or []]
    entity_parse_disagrees = bool(entities and context_entities and sorted(context_entities) != sorted(entities))
    entity_applicability = "REQUIRED" if entities and not entity_parse_disagrees else "NOT_CHECKED"
    entity_basis = (
        "raw_claim_ticker_literal"
        if entity_applicability == "REQUIRED"
        else "raw_claim_entity_ambiguous_context_disagreement"
        if entity_parse_disagrees
        else "raw_claim_entity_not_detected"
    )
    requirements = [
        _requirement(question_id, definition_id, "source.integrity", "REQUIRED", expected=None, basis="immutable_exact_source_lineage_required"),
        _requirement(question_id, definition_id, "source.truth_tier", "NOT_CHECKED", expected=["ocr_derived", "visual_source_verified", "source_native"], basis="v12_does_not_classify_source_truth_tier", depends_on=("source.integrity",)),
        _requirement(question_id, definition_id, "entity.identity", entity_applicability, expected=entities if entity_applicability == "REQUIRED" else None, basis=entity_basis, depends_on=("source.integrity",)),
        _requirement(question_id, definition_id, "entity.role", "REQUIRED" if role else "NOT_CHECKED", expected=role, basis="raw_claim_literal" if role else "raw_claim_role_not_detected", depends_on=("entity.identity", "source.integrity")),
        _requirement(question_id, definition_id, "reporting.scope", "REQUIRED" if scope else "NOT_CHECKED", expected=scope, basis="raw_claim_literal" if scope else "raw_claim_scope_not_detected", depends_on=("entity.identity", "source.integrity")),
        _requirement(question_id, definition_id, "statement.role", "REQUIRED" if statement_role else "NOT_CHECKED", expected=statement_role, basis="raw_claim_literal" if statement_role else "raw_claim_statement_role_not_detected", depends_on=("source.integrity",)),
        _requirement(question_id, definition_id, "variable.metric", "REQUIRED", expected={"claim_expression": question, "canonical_metric_id": None}, basis="canonical_metric_requirement_not_independently_parsed", depends_on=("source.integrity",)),
        _requirement(question_id, definition_id, "temporal.period", "REQUIRED" if years else "NOT_CHECKED", expected=temporal, basis=f"raw_claim_temporal_rules_{TEMPORAL_RULES_VERSION}", depends_on=("source.integrity",)),
        _requirement(question_id, definition_id, "unit.scale", "REQUIRED", expected={"unit": requested_unit.get("unit") or "source_unit", "kind": requested_unit.get("kind") or "source_unit"}, basis=str(requested_unit.get("source") or "source_unit_must_be_bound"), depends_on=("source.integrity",)),
        _requirement(question_id, definition_id, "metric.tax_treatment", "REQUIRED" if "metric.tax_treatment" in qualifiers else "NOT_CHECKED", expected=qualifiers.get("metric.tax_treatment"), basis="raw_claim_metric_qualifier" if "metric.tax_treatment" in qualifiers else "raw_claim_tax_treatment_not_detected", depends_on=("variable.metric", "source.integrity")),
        _requirement(question_id, definition_id, "metric.gross_net_basis", "REQUIRED" if "metric.gross_net_basis" in qualifiers else "NOT_CHECKED", expected=qualifiers.get("metric.gross_net_basis"), basis="raw_claim_metric_qualifier" if "metric.gross_net_basis" in qualifiers else "raw_claim_gross_net_basis_not_detected", depends_on=("variable.metric", "source.integrity")),
        _requirement(question_id, definition_id, "accounting.measurement_basis", "NOT_CHECKED", expected=None, basis="dimension_not_covered_by_current_claim_rules", depends_on=("variable.metric", "source.integrity")),
        _requirement(question_id, definition_id, "formula.definition", "REQUIRED", expected={"required_operations": required_operations, "typed_slots": None, "arity": None, "rounding": None}, basis="route_operation_requirements_diagnostic_only", depends_on=("variable.metric",)),
        _requirement(question_id, definition_id, "operand.set", "REQUIRED", expected={"operand_obligations": None, "cardinality": None}, basis="typed_operand_obligations_not_materialized", depends_on=("formula.definition", "temporal.period")),
        _requirement(question_id, definition_id, "operand.compatibility", "REQUIRED", expected=None, basis="separate_compatibility_matrix_required", depends_on=("formula.definition", "operand.set")),
    ]
    validate_dependency_graph(requirements)
    payload = {
        "schema_version": 1,
        "protocol": REQUIREMENT_PROTOCOL,
        "question_id": question_id,
        "claim": question,
        "generator": {
            "name": "deterministic_raw_claim_rules",
            "version": CLAIM_REQUIREMENT_GENERATOR_VERSION,
            "temporal_rules_version": TEMPORAL_RULES_VERSION,
            "definition_id": definition_id,
            "independent_completeness_basis": False,
        },
        "requirements": requirements,
        "dependency_graph": [
            {"obligation_id": item["obligation_id"], "dimension": item["dimension"], "depends_on": item["depends_on"]}
            for item in requirements
            if item["depends_on"]
        ],
        "requirement_set_completeness": "CLAIM_COMPLETENESS_UNESTABLISHED",
        "reason_codes": [
            "NO_INDEPENDENT_REQUIREMENT_UNIVERSE",
            *(["AMBIGUOUS_RAW_ENTITY_PARSE_CONTEXT_DISAGREEMENT"] if entity_parse_disagrees else []),
            *(["CONTEXT_TEMPORAL_DISAGREES_WITH_RAW_CLAIM"] if context_years and sorted(context_years) != sorted(years) else []),
        ],
        "source_contract": source_contract(),
    }
    return {**payload, "claim_requirement_set_id": canonical_sha256(payload)}


def _answer(certificate: Mapping[str, Any]) -> Mapping[str, Any]:
    value = certificate.get("answer_certificate")
    return value if isinstance(value, Mapping) else {}


def _receipts(certificate: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [value for value in _answer(certificate).get("binding_receipts") or [] if isinstance(value, Mapping)]


def _status_values(receipts: Sequence[Mapping[str, Any]], field: str) -> set[str]:
    return {str((receipt.get("field_statuses") or {}).get(field) or "UNRESOLVED") for receipt in receipts}


def _all_pass(receipts: Sequence[Mapping[str, Any]], field: str) -> bool:
    return bool(receipts) and _status_values(receipts, field) == {"PASS"}


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _typed_anchor_hashes(section: Mapping[str, Any]) -> list[str]:
    hashes: list[str] = []
    anchors = section.get("source_anchors") or []
    if not isinstance(anchors, list) or not anchors:
        return []
    for anchor in anchors:
        if not isinstance(anchor, Mapping) or not anchor.get("kind") or not anchor.get("document_uid"):
            return []
        candidates = [
            anchor.get("raw_text_sha256"),
            anchor.get("source_file_sha256"),
            anchor.get("document_source_sha256"),
        ]
        valid = [str(value) for value in candidates if _is_sha256(value)]
        if not valid:
            return []
        hashes.extend(valid)
    return sorted(set(hashes))


def _receipt_refs(receipts: Sequence[Mapping[str, Any]], section_name: str | None = None) -> list[str]:
    refs: set[str] = set()
    for receipt in receipts:
        if _is_sha256(receipt.get("binding_id")):
            refs.add(f"binding:{receipt['binding_id']}")
        section = receipt.get(section_name) if section_name else receipt.get("source_value_cell")
        if isinstance(section, Mapping):
            if section_name:
                refs.update(f"anchor:{value}" for value in _typed_anchor_hashes(section))
            elif section.get("kind") and section.get("document_uid") and _is_sha256(section.get("raw_text_sha256")):
                refs.add(f"anchor:{section['raw_text_sha256']}")
    return sorted(refs)


def _receipts_have_typed_section(receipts: Sequence[Mapping[str, Any]], section_name: str, *, require_approval: bool = False) -> bool:
    if not receipts:
        return False
    for receipt in receipts:
        if not _is_sha256(receipt.get("binding_id")):
            return False
        section = receipt.get(section_name)
        if not isinstance(section, Mapping) or not _typed_anchor_hashes(section):
            return False
        source_cell = receipt.get("source_value_cell")
        if not isinstance(source_cell, Mapping) or not source_cell.get("document_uid"):
            return False
        anchors = section.get("source_anchors") or []
        if any(anchor.get("document_uid") != source_cell.get("document_uid") for anchor in anchors if isinstance(anchor, Mapping)):
            return False
        if require_approval and not _is_sha256(section.get("approval_id")):
            return False
    return True


def _valid_iso_date(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _check_requirement(requirement: Mapping[str, Any], receipts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    dimension = str(requirement["dimension"])
    applicability = str(requirement["applicability"])
    expected = requirement.get("expected")
    if applicability == "NOT_APPLICABLE":
        return {"status": "NOT_APPLICABLE", "evidence_refs": [], "reason_codes": []}
    if applicability == "NOT_CHECKED":
        return {"status": "NOT_CHECKED", "evidence_refs": [], "reason_codes": ["DIMENSION_NOT_CHECKED"]}
    if dimension == "source.integrity":
        evidence = _receipt_refs(receipts)
        source_anchors_valid = all(
            isinstance(cell := receipt.get("source_value_cell"), Mapping)
            and bool(cell.get("kind"))
            and bool(cell.get("document_uid"))
            and _is_sha256(cell.get("raw_text_sha256"))
            for receipt in receipts
        )
        passed = _all_pass(receipts, "source_integrity_status") and bool(receipts) and source_anchors_valid and all(_is_sha256(receipt.get("binding_id")) for receipt in receipts)
        return {"status": "PASS" if passed else "UNRESOLVED", "evidence_refs": evidence, "reason_codes": [] if passed else ["SOURCE_INTEGRITY_OR_TYPED_ANCHOR_NOT_PASS"]}
    if dimension == "entity.identity":
        actual = sorted({str((receipt.get("entity_scope") or {}).get("entity")) for receipt in receipts if (receipt.get("entity_scope") or {}).get("entity")})
        evidence = _receipt_refs(receipts, "entity_scope")
        nested_pass = all((receipt.get("entity_scope") or {}).get("entity_status") == "PASS" for receipt in receipts)
        authoritative = _all_pass(receipts, "entity_status") and nested_pass and _receipts_have_typed_section(receipts, "entity_scope", require_approval=True)
        matched = actual == sorted(expected or [])
        status = "PASS" if authoritative and matched else "FAIL" if authoritative else "UNRESOLVED"
        reasons = [] if status == "PASS" else ["ENTITY_PROPOSITION_CONTRADICTS_CLAIM"] if status == "FAIL" else ["ENTITY_TYPED_ANCHOR_UNPROVEN"]
        return {"status": status, "evidence_refs": evidence, "actual": actual, "reason_codes": reasons}
    if dimension == "entity.role":
        actual = sorted({str((receipt.get("entity_role") or {}).get("role")) for receipt in receipts if (receipt.get("entity_role") or {}).get("role")})
        evidence = _receipt_refs(receipts, "entity_role")
        nested_pass = all((receipt.get("entity_role") or {}).get("status") == "PASS" for receipt in receipts)
        authoritative = _all_pass(receipts, "entity_role_status") and nested_pass and _receipts_have_typed_section(receipts, "entity_role", require_approval=True)
        matched = actual == [str(expected)]
        status = "PASS" if authoritative and matched else "FAIL" if authoritative else "UNRESOLVED"
        reasons = [] if status == "PASS" else ["ENTITY_ROLE_PROPOSITION_CONTRADICTS_CLAIM"] if status == "FAIL" else ["ENTITY_ROLE_TYPED_ANCHOR_UNPROVEN"]
        return {"status": status, "evidence_refs": evidence, "actual": actual, "reason_codes": reasons}
    if dimension == "reporting.scope":
        actual = sorted({str((receipt.get("entity_scope") or {}).get("scope")) for receipt in receipts if (receipt.get("entity_scope") or {}).get("scope")})
        evidence = _receipt_refs(receipts, "entity_scope")
        nested_pass = all((receipt.get("entity_scope") or {}).get("scope_status") == "PASS" for receipt in receipts)
        authoritative = _all_pass(receipts, "scope_status") and nested_pass and _receipts_have_typed_section(receipts, "entity_scope", require_approval=True)
        matched = actual == [str(expected)]
        status = "PASS" if authoritative and matched else "FAIL" if authoritative else "UNRESOLVED"
        reasons = [] if status == "PASS" else ["REPORTING_SCOPE_PROPOSITION_CONTRADICTS_CLAIM"] if status == "FAIL" else ["REPORTING_SCOPE_TYPED_ANCHOR_UNPROVEN"]
        return {"status": status, "evidence_refs": evidence, "actual": actual, "reason_codes": reasons}
    if dimension == "temporal.period":
        period_sections = [receipt.get("period") for receipt in receipts]
        actual_years = sorted({int(year) for receipt in receipts for year in (receipt.get("period") or {}).get("period_years") or [] if isinstance(year, int)})
        actual_kinds = sorted({"instant" if (receipt.get("period") or {}).get("period_grain") == "instant" else "duration" if (receipt.get("period") or {}).get("period_grain") == "fiscal_year" else "UNRESOLVED" for receipt in receipts})
        actual_starts = sorted({str(value) for receipt in receipts if _valid_iso_date(value := (receipt.get("period") or {}).get("start_date"))})
        actual_ends = sorted({str(value) for receipt in receipts if _valid_iso_date(value := (receipt.get("period") or {}).get("end_date"))})
        wanted = expected if isinstance(expected, Mapping) else {}
        role = str(wanted.get("role") or "UNRESOLVED")
        role_match = True
        if role == "opening":
            role_match = bool(actual_ends) and all(value.endswith("-01-01") for value in actual_ends)
        elif role == "closing":
            role_match = bool(actual_ends) and all(value.endswith("-12-31") for value in actual_ends)
        elif role == "current_duration":
            role_match = actual_kinds == ["duration"]
        if wanted.get("start") is not None:
            role_match = role_match and actual_starts == [str(wanted["start"])]
        if wanted.get("end") is not None:
            role_match = role_match and actual_ends == [str(wanted["end"])]
        required_end = role in {"opening", "closing", "explicit_instant"} or wanted.get("end") is not None
        required_start = wanted.get("start") is not None
        comparable_fields_present = bool(period_sections) and all(
            isinstance(period, Mapping)
            and bool(period.get("period_years"))
            and period.get("period_grain") in {"instant", "fiscal_year"}
            and (not required_end or _valid_iso_date(period.get("end_date")))
            and (not required_start or _valid_iso_date(period.get("start_date")))
            for period in period_sections
        )
        calendar_consistent = comparable_fields_present and all(
            all(
                not _valid_iso_date(period.get(field))
                or date.fromisoformat(str(period[field])).year in set(period.get("period_years") or [])
                for field in ("start_date", "end_date")
            )
            for period in period_sections
            if isinstance(period, Mapping)
        )
        nested_pass = all((receipt.get("period") or {}).get("status") == "PASS" for receipt in receipts)
        evidence = _receipt_refs(receipts, "period")
        authoritative = (
            _all_pass(receipts, "period_status")
            and nested_pass
            and _receipts_have_typed_section(receipts, "period")
            and comparable_fields_present
            and calendar_consistent
        )
        matched = (
            actual_years == list(wanted.get("requested_years") or [])
            and actual_kinds == [str(wanted.get("kind"))]
            and role_match
        )
        status = "PASS" if authoritative and matched else "FAIL" if authoritative else "UNRESOLVED"
        reasons = [] if status == "PASS" else ["TEMPORAL_PROPOSITION_CONTRADICTS_CLAIM"] if status == "FAIL" else ["TEMPORAL_TYPED_ANCHOR_UNPROVEN"]
        return {"status": status, "evidence_refs": evidence, "actual": {"years": actual_years, "kinds": actual_kinds, "start_dates": actual_starts, "end_dates": actual_ends}, "reason_codes": reasons}
    return {"status": "UNRESOLVED", "evidence_refs": [], "reason_codes": [f"TYPED_{dimension.upper().replace('.', '_')}_RECEIPT_MISSING"]}


def build_semantic_coverage_certificate(requirement_set: Mapping[str, Any], certificate: Mapping[str, Any]) -> dict[str, Any]:
    answer = _answer(certificate)
    receipts = _receipts(certificate)
    requirements = [item for item in requirement_set.get("requirements") or [] if isinstance(item, Mapping)]
    validate_dependency_graph(requirements)
    checks = [
        {
            "obligation_id": requirement["obligation_id"],
            "dimension": requirement["dimension"],
            "applicability": requirement["applicability"],
            **_check_requirement(requirement, receipts),
        }
        for requirement in requirements
    ]
    by_dimension = {str(item["dimension"]): item for item in checks}
    requirement_by_dimension = {str(item["dimension"]): item for item in requirements}
    changed = True
    while changed:
        changed = False
        for dimension, check in by_dimension.items():
            if check["status"] != "PASS":
                continue
            dependencies = [str(value) for value in requirement_by_dimension[dimension].get("depends_on") or []]
            blockers = [dependency for dependency in dependencies if by_dimension[dependency]["status"] != "PASS"]
            if blockers:
                check["status"] = "UNRESOLVED"
                check["reason_codes"] = sorted(set(check.get("reason_codes") or []).union({"DEPENDENCY_NOT_PASS"}))
                check["blocked_by"] = blockers
                changed = True
    unchecked = sorted(item["dimension"] for item in checks if item["status"] == "NOT_CHECKED")
    unresolved = sorted(item["dimension"] for item in checks if item["applicability"] == "REQUIRED" and item["status"] != "PASS")
    internally_complete = not unchecked and not unresolved
    payload = {
        "schema_version": 2,
        "protocol": COVERAGE_PROTOCOL,
        "question_id": int(requirement_set["question_id"]),
        "claim_requirement_set_id": requirement_set["claim_requirement_set_id"],
        "v12_answer_certificate_id": answer.get("answer_certificate_id"),
        "v12_certificate_status": answer.get("status") or "ABSTAIN",
        "proof_obligations": checks,
        "internal_coverage_status": "INTERNALLY_COMPLETE" if internally_complete else "INTERNAL_COVERAGE_INCOMPLETE",
        "unchecked_dimensions": unchecked,
        "unchecked_dimension_count": len(unchecked),
        "required_unresolved_dimensions": unresolved,
        "claim_completeness_status": "CLAIM_COMPLETENESS_UNESTABLISHED",
        "claim_complete": False,
        "reason_codes": [
            *[str(code) for code in requirement_set.get("reason_codes") or []],
            *(["UNCHECKED_DIMENSIONS_PRESENT"] if unchecked else []),
            *(["REQUIRED_PROOF_OBLIGATION_UNRESOLVED"] if unresolved else []),
        ],
        "source_truth_tier": {"tier": "EXTRACTED_SOURCE_UNCLASSIFIED", "status": "UNRESOLVED", "reason_codes": ["V12_SOURCE_TRUTH_TIER_NOT_DECLARED"]},
        "release_effect": "NONE_V13_SHADOW_ONLY",
        "source_contract": source_contract(),
    }
    return {**payload, "semantic_coverage_certificate_id": canonical_sha256(payload)}


FORMULA_DEFINITION_OPERATIONS = {"subtract_or_difference", "ratio_or_percent", "average_or_median", "min_max_ranking", "year_over_year_growth", "positive_negative_filter", "requested_rounding"}
OPERAND_SET_OPERATIONS = {"reported_value", "multi_company_population", "multi_year_range", "stage_output_dependency"}


def classify_composed_blocker(route: Mapping[str, Any], binding: Mapping[str, Any], certificate: Mapping[str, Any]) -> dict[str, Any] | None:
    if route.get("route_status") != "composed_execution_required" or certificate.get("authorization_status") == "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY":
        return None
    missing = {str(value) for value in route.get("missing_operations") or []}
    planned_operand_count = sum(len(stage.get("required_operands") or []) for stage in binding.get("stages") or [] if isinstance(stage, Mapping))
    if missing.intersection(FORMULA_DEFINITION_OPERATIONS):
        primary = "FORMULA_DEFINITION_INCOMPLETE"
    elif missing.intersection(OPERAND_SET_OPERATIONS):
        primary = "OPERAND_SET_INCOMPLETE"
    else:
        primary = "COMPOSED_CAUSE_UNESTABLISHED"
    secondary = []
    if primary != "FORMULA_DEFINITION_INCOMPLETE" and missing.intersection(FORMULA_DEFINITION_OPERATIONS):
        secondary.append("FORMULA_DEFINITION_INCOMPLETE")
    if primary != "OPERAND_SET_INCOMPLETE" and missing.intersection(OPERAND_SET_OPERATIONS):
        secondary.append("OPERAND_SET_INCOMPLETE")
    return {
        "schema_version": 1,
        "protocol": COMPOSED_TAXONOMY_PROTOCOL,
        "question_id": int(route["question_id"]),
        "primary_blocker": primary,
        "secondary_blockers": sorted(set(secondary)),
        "missing_operations": sorted(missing),
        "planned_operand_count": planned_operand_count,
        "compatibility_evaluable": planned_operand_count > 1,
        "compatibility_evaluated": False,
        "compatibility_receipt_id": None,
        "source_contract": source_contract(),
    }


def classify_route_blocker(route: Mapping[str, Any]) -> dict[str, Any] | None:
    if route.get("route_status") != "route_incomplete":
        return None
    missing = {str(value) for value in route.get("missing_operations") or []}
    covered = {str(value) for value in route.get("covered_operations") or []}
    reasons = {str(value) for value in route.get("reason_codes") or []}
    if "ratio_or_percent" in missing and "reported_value" in covered:
        primary, basis = "FORMULA_OR_OPERATOR_DEFINITION_UNRESOLVED", "reported_value_covered_ratio_operator_missing"
    elif "reported_value" in missing:
        primary, basis = "TABLE_OR_METRIC_BINDING_UNRESOLVED", "reported_value_operation_uncovered"
    elif any(code.startswith("MISSING_TABLE_ROLE") for code in reasons):
        primary, basis = "TABLE_ROLE_UNRESOLVED", "explicit_missing_table_role_reason"
    elif "stage_output_dependency" in missing:
        primary, basis = "DECOMPOSITION_OMITTED_REQUIRED_SOURCE", "stage_output_dependency_uncovered"
    else:
        primary, basis = "ROUTE_CAUSE_UNESTABLISHED", "available_route_metadata_cannot_establish_cause"
    return {
        "schema_version": 2,
        "protocol": ROUTE_TAXONOMY_PROTOCOL,
        "question_id": int(route["question_id"]),
        "primary_blocker": primary,
        "classification_basis": basis,
        "route_reason_codes": sorted(reasons),
        "missing_operations": sorted(missing),
        "covered_operations": sorted(covered),
        "causal_attribution_verified": False,
        "reason_codes": ["CAUSE_REQUIRES_SOURCE_SEARCH_AUDIT"],
        "source_contract": source_contract(),
    }


def build_temporal_semantics(route: Mapping[str, Any], period_packet: Mapping[str, Any], certificate: Mapping[str, Any]) -> dict[str, Any] | None:
    if route.get("route_status") != "route_complete" or certificate.get("authorization_status") == "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY":
        return None
    question = str(route.get("question") or "")
    years = _explicit_years(question)
    required = claim_temporal_object(question, years)
    operands = [operand for stage in period_packet.get("stages") or [] if isinstance(stage, Mapping) for operand in stage.get("required_operands") or [] if isinstance(operand, Mapping)]
    observed_types = sorted({str(item.get("period_type") or "UNRESOLVED") for item in operands})
    candidates = [candidate for operand in operands for candidate in operand.get("period_column_candidates") or [] if isinstance(candidate, Mapping)]
    source_expressions = sorted({str(item.get("source_label")) for item in candidates if item.get("source_label")})
    return {
        "schema_version": 1,
        "protocol": TEMPORAL_PROTOCOL,
        "question_id": int(route["question_id"]),
        "claim_requirement": required,
        "source_observation": {"period_types": observed_types, "source_expressions": source_expressions or None},
        "kind_match": len(observed_types) == 1 and observed_types[0] == required["kind"],
        "proof_status": "UNRESOLVED",
        "period_packet_status": period_packet.get("packet_status"),
        "reason_codes": ["SOURCE_PERIOD_EXPRESSION_NOT_FOUND" if not candidates else "SOURCE_PERIOD_CANDIDATE_NOT_AUTHORIZED"],
        "source_contract": source_contract(),
    }


def validate_v13_partition(*, question_ids: set[int], composed_rows: Sequence[Mapping[str, Any]], route_rows: Sequence[Mapping[str, Any]], temporal_rows: Sequence[Mapping[str, Any]], coverage_rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    partitions = {
        "composed": {int(row["question_id"]) for row in composed_rows},
        "route": {int(row["question_id"]) for row in route_rows},
        "temporal": {int(row["question_id"]) for row in temporal_rows},
        "v12_complete_shadowed": {int(row["question_id"]) for row in coverage_rows if row.get("v12_certificate_status") == "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY"},
    }
    combined: set[int] = set()
    for name, values in partitions.items():
        overlap = combined.intersection(values)
        if overlap:
            raise ValueError(f"V13 first-blocker partitions overlap at {name}: {sorted(overlap)[:5]}")
        combined.update(values)
    if combined != question_ids:
        raise ValueError(f"V13 partition mismatch missing={sorted(question_ids-combined)[:5]} extra={sorted(combined-question_ids)[:5]}")
    return {name: len(values) for name, values in partitions.items()}


def status_counts(rows: Sequence[Mapping[str, Any]], field: str) -> dict[str, int]:
    return dict(sorted(Counter(str(row.get(field) or "UNKNOWN") for row in rows).items()))
