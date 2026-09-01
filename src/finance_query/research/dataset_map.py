"""Question-intrinsic ViFinQA dataset map and frozen-baseline census.

This module intentionally never imports the answer-capable E2E pipeline.  It
describes what a question asks from question text only, then joins the locked
baseline result in a separate output plane.  Question IDs are accepted only by
the artifact builder as tracking keys; classification functions never receive
them.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import tempfile
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


TAXONOMY_PROTOCOL = "vifinqa_question_intrinsic_dataset_map_v1"
BASELINE_PROTOCOL = "vifinqa_frozen_baseline_family_census_v1"
MANIFEST_PROTOCOL = "vifinqa_dataset_research_map_manifest_v1"
SCHEMA_VERSION = 1

SOURCE_CONTRACT = {
    "research_only": True,
    "evidence_eligible": False,
    "may_materialize_answer": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
}

YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
EXACT_DATE_RE = re.compile(
    r"\b(?:tại|đến|vào)\s+ngày\s+(\d{1,2})[/-](\d{1,2})[/-]((?:19|20)\d{2})\b",
    re.IGNORECASE,
)
TICKER_TOKEN_RE = re.compile(r"\b[A-Z]{2,6}\b")

OPERATION_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("counterfactual", (r"\bgiả sử\b", r"\btheo kịch bản\b", r"\bnếu .{0,120}(?:tăng|giảm)\b")),
    ("count", (
        r"\bcó bao nhiêu\b",
        r"\btrong bao nhiêu năm\b",
        r"\bbao nhiêu (?:công ty|doanh nghiệp|đơn vị|năm)\b",
        r"\bsố (?:công ty|doanh nghiệp|đơn vị|năm).{0,180}\blà bao nhiêu\b",
    )),
    ("rank_or_select_extreme", (r"\bcao nhất\b", r"\bthấp nhất\b", r"\blớn nhất\b", r"\bnhỏ nhất\b", r"\bxếp hạng\b", r"\bđứng thứ\b")),
    ("median", (r"\btrung vị\b",)),
    ("mean", (
        r"\btrung bình (?:của|trong|giữa|các|nhóm|toàn|qua)\b",
        r"\bbình quân (?:của|trong|giữa|các|nhóm)\b",
        r"^(?:hãy )?tính (?:giá trị |trị )?(?:trung bình|bình quân)\b",
        r"^(?:giá trị )?trung bình\b",
        r"\b(?:đạt|có) bình quân\b",
        r"\bbình quân (?:tỷ lệ|mức|giá trị|phần chênh lệch)\b",
    )),
    ("percentage_change", (r"\b(?:tăng|giảm|thay đổi|chênh lệch).{0,45}\bphần trăm\b", r"\btốc độ tăng\b", r"\btăng trưởng\b")),
    ("difference", (
        r"\bchênh lệch (?:giữa|của|so với|nhau)\b",
        r"\b(?:tăng|giảm|thay đổi) bao nhiêu\b",
        r"\b(?:bé hơn|hơn|nhiều hơn|ít hơn) .{0,110} (?:bao nhiêu|mấy)\b",
    )),
    ("net", (r"\blãi ròng\b", r"\bkết quả(?: hoạt động tài chính)? (?:thuần|ròng)\b")),
    ("ratio", (
        r"\btỷ lệ .{0,90}\btrên\b",
        r"\btỷ số\b",
        r"\bhệ số\b",
        r"\bbiên lợi nhuận\b",
        r"\bCFO margin\b",
        r"\bRO[AE]\b",
        r"\bgấp .{0,100} (?:bao nhiêu|mấy) lần\b",
        r"\btỷ trọng\b",
    )),
    ("threshold_filter", (
        r"\b(?:lớn hơn|nhỏ hơn|cao hơn|thấp hơn|vượt)\b",
        r"\b(?:dưới|trên)\s+(?!\d+(?:[.,]\d+)?\s*(?:năm|tháng|ngày)\b)(?:-?\d|mức|ngưỡng|trung vị)\b",
        r"\b(?:CFO|LNST|lợi nhuận|dòng tiền|lưu chuyển tiền|giá trị|vốn lưu động ròng).{0,50}\b(?:âm|dương)\b",
    )),
    ("comparison", (r"\bso với\b", r"\bgiữa .{0,100} và\b", r"\btừ (?:năm )?(?:19|20)\d{2} sang (?:năm )?(?:19|20)\d{2}\b")),
    ("filter", (
        r"\bchỉ xét\b",
        r"\bxét (?:các|nhóm)\b",
        r"\btrong (?:nhóm|số|các doanh nghiệp|các công ty)\b",
        r"\b(?:các|những) (?:công ty|doanh nghiệp|đồng tiền|năm).{0,100}\bcó\b",
        r"\bnăm .{0,120}\b(?:ghi nhận|duy trì|đạt)\b",
        r"\bđồng thời\b",
        r"\bvừa .{0,140}\bvừa\b",
    )),
    ("sum", (
        r"\btổng cộng (?:của|giữa|các)\b",
        r"\bcộng lại\b",
        r"^(?:hãy )?tính tổng\b",
        r"\btích lũy\b",
        r"\btổng (?:mức|giá trị|số dư|doanh thu|chi phí).{0,260}\b(?:của|với) (?:các|những)\b",
    )),
)

TEMPORAL_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("exact_date", (r"\b(?:tại|đến|vào) ngày \d{1,2}[/-]\d{1,2}[/-](?:19|20)\d{2}\b",)),
    ("closing_instant", (r"\bcuối (?:năm|kỳ)\b", r"\bđến cuối năm\b", r"\btại ngày 31 tháng 12\b", r"\btại ngày 31/12\b")),
    ("opening_instant", (r"\bđầu (?:năm|kỳ)\b", r"\bngày 1 tháng 1\b", r"\b1/1/(?:19|20)\d{2}\b")),
    ("period_flow", (r"\btrong năm\b", r"\bcho năm kết thúc\b", r"\bcả năm\b")),
    ("period_range", (r"\btrong giai đoạn\b", r"\bgiai đoạn (?:19|20)\d{2}\s*[-–]\s*(?:19|20)\d{2}\b")),
    ("period_transition", (r"\btừ (?:năm )?(?:19|20)\d{2} sang (?:năm )?(?:19|20)\d{2}\b", r"\bso với năm trước\b")),
    ("discrete_period_set", (r"\btrong (?:các|số các) năm\b",)),
)

ROLE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("parent", r"\bcông ty mẹ\b"),
    ("subsidiary", r"\bcông ty con\b"),
    ("associate", r"\bcông ty liên kết\b"),
    ("group", r"\btập đoàn\b"),
)

SCOPE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("separate", r"\b(?:riêng|công ty mẹ)\b"),
    ("consolidated", r"\bhợp nhất\b"),
)

UNIT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("percent", r"\b(?:phần trăm|%)\b"),
    ("multiple", r"\blần\b"),
    ("shares", r"\bcổ phiếu\b"),
    ("million_vnd", r"\btriệu đồng\b"),
    ("billion_vnd", r"\btỷ đồng\b"),
    ("thousand_vnd", r"\bnghìn đồng\b"),
    ("vnd", r"\b(?:đồng|VND)\b"),
)

METRIC_FAMILY_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cash_and_deposits", (r"\btiền và tương đương tiền\b", r"\btiền gửi\b", r"\btiền mặt\b")),
    ("revenue_and_income", (r"\bdoanh thu\b", r"\bthu nhập\b", r"\bcổ tức nhận\b")),
    ("expense", (r"\bchi phí\b", r"\bgiá vốn\b")),
    ("profit", (r"\blợi nhuận\b", r"\blãi ròng\b", r"\bkết quả thuần\b", r"\bLNST\b", r"\bLNTT\b")),
    ("assets", (r"\btài sản\b", r"\bkhấu hao\b")),
    ("liabilities_and_debt", (r"\bnợ\b", r"\bvay\b", r"\btrái phiếu\b")),
    ("equity_and_reserves", (r"\bvốn chủ sở hữu\b", r"\bvốn điều lệ\b", r"\bquỹ\b")),
    ("cash_flow", (r"\blưu chuyển tiền\b", r"\bdòng tiền\b", r"\bCFO\b")),
    ("receivables", (r"\bphải thu\b", r"\bcho vay khách hàng\b")),
    ("payables", (r"\bphải trả\b", r"\bphải nộp\b")),
    ("inventory", (r"\bhàng tồn kho\b", r"\btồn kho\b")),
    ("tax", (r"\bthuế\b", r"\bTNDN\b")),
    ("shares_and_ownership", (r"\bcổ phiếu\b", r"\btỷ lệ sở hữu\b", r"\bquyền biểu quyết\b", r"\blợi ích kinh tế\b")),
    ("financial_ratio", (r"\bRO[AE]\b", r"\bhệ số\b", r"\bbiên lợi nhuận\b", r"\btỷ số\b", r"\btỷ trọng\b")),
)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} must contain an object")
            rows.append(value)
    return rows


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def load_entity_aliases(path: Path | None) -> dict[str, str]:
    if path is None or not path.is_file():
        return {}
    aliases: dict[str, str] = {}
    with path.open(encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            ticker = normalize_text(str(row.get("Mã CK") or "")).upper()
            name = normalize_text(str(row.get("Tên công ty") or ""))
            if not re.fullmatch(r"[A-Z]{2,6}", ticker):
                continue
            aliases[ticker.casefold()] = ticker
            if name:
                aliases[name.casefold()] = ticker
    return aliases


def _matches(text: str, definitions: Sequence[tuple[str, Sequence[str]]]) -> tuple[list[str], dict[str, list[str]]]:
    labels: list[str] = []
    surfaces: dict[str, list[str]] = {}
    for label, patterns in definitions:
        found: list[str] = []
        for pattern in patterns:
            found.extend(match.group(0) for match in re.finditer(pattern, text, re.IGNORECASE))
        if found:
            labels.append(label)
            surfaces[label] = list(dict.fromkeys(normalize_text(item).casefold() for item in found))
    return labels, surfaces


def _entities(question: str, aliases: Mapping[str, str]) -> list[str]:
    found: list[tuple[int, str]] = []
    known = set(aliases.values())
    for match in TICKER_TOKEN_RE.finditer(question):
        ticker = match.group(0).upper()
        if ticker in known:
            found.append((match.start(), ticker))
    lowered = question.casefold()
    for alias, ticker in aliases.items():
        if alias == ticker.casefold() or len(alias) < 8:
            continue
        position = lowered.find(alias)
        if position >= 0:
            found.append((position, ticker))
    result: list[str] = []
    for _, ticker in sorted(found):
        if ticker not in result:
            result.append(ticker)
    return result


def _primary_question_type(operations: Sequence[str]) -> str:
    operation_set = set(operations)
    if "counterfactual" in operation_set:
        return "counterfactual_composition"
    if "count" in operation_set and "filter" in operation_set:
        return "filter_then_count"
    if "rank_or_select_extreme" in operation_set and "filter" in operation_set:
        return "filter_then_rank_or_select"
    if "filter" in operation_set and operation_set.intersection({"mean", "median", "sum"}):
        return "filter_then_aggregate"
    if "count" in operation_set:
        return "filter_then_count" if "filter" in operation_set else "count_matching_items"
    if "filter" in operation_set and operation_set.intersection({"ratio", "difference", "percentage_change", "comparison", "net", "threshold_filter"}):
        return "filter_then_compute"
    if "rank_or_select_extreme" in operation_set:
        return "rank_or_select_extreme"
    if "mean" in operation_set or "median" in operation_set or "sum" in operation_set:
        return "aggregate"
    if "percentage_change" in operation_set:
        return "percentage_change"
    if "difference" in operation_set:
        return "difference"
    if "net" in operation_set:
        return "net_value_composition"
    if "comparison" in operation_set or "threshold_filter" in operation_set:
        return "comparison"
    if "ratio" in operation_set:
        return "ratio_or_margin"
    if operation_set == {"lookup"}:
        return "reported_value_lookup"
    return "candidate_unresolved"


def _output_type(question: str, operations: Sequence[str]) -> str:
    operation_set = set(operations)
    if "count" in operation_set:
        return "count"
    if "rank_or_select_extreme" in operation_set and re.search(r"\b(?:công ty|doanh nghiệp|năm) nào\b", question, re.IGNORECASE):
        return "entity_or_period"
    if "percentage_change" in operation_set or re.search(r"\bbao nhiêu phần trăm\b", question, re.IGNORECASE):
        return "percentage"
    if re.search(r"\b(?:đúng|sai|có hay không)\b", question, re.IGNORECASE):
        return "boolean"
    return "numeric_value"


def _value_cardinality(operations: Sequence[str], entity_count: int, year_count: int) -> str:
    if any(op in operations for op in ("filter", "rank_or_select_extreme", "count", "mean", "median", "sum", "counterfactual")):
        return "value_set"
    if entity_count > 1 or year_count > 1 or any(op in operations for op in ("comparison", "difference", "percentage_change")):
        return "multiple_values"
    return "single_value"


def _expected_source_topology(entity_count: int, year_count: int, operations: Sequence[str]) -> str:
    composed = any(op in operations for op in ("filter", "rank_or_select_extreme", "count", "mean", "median", "sum", "counterfactual"))
    if entity_count > 1 and year_count > 1:
        return "multi_entity_multi_period_composed"
    if entity_count > 1:
        return "multi_entity_composed" if composed else "multi_entity_values"
    if year_count > 1:
        return "single_entity_multi_period_composed" if composed else "single_entity_multi_period_values"
    if composed:
        return "single_entity_value_set_composed"
    return "single_value_source_unresolved"


def _wording_template(question: str, aliases: Mapping[str, str]) -> str:
    value = normalize_text(question).casefold()
    for alias in sorted(aliases, key=len, reverse=True):
        if len(alias) >= 8:
            value = value.replace(alias, " <entity> ")
    value = re.sub(r"\b[a-z]{2,6}\b", lambda m: " <entity> " if m.group(0).upper() in set(aliases.values()) else m.group(0), value)
    value = re.sub(r"\b(?:19|20)\d{2}\b", " <year> ", value)
    value = re.sub(r"\b\d+(?:[.,]\d+)?\b", " <number> ", value)
    return re.sub(r"\s+", " ", value).strip()


def classify_question(question: str, aliases: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Classify question semantics without accepting or consulting an ID."""
    aliases = aliases or {}
    normalized = normalize_text(question)
    years = sorted({int(value) for value in YEAR_RE.findall(normalized)})
    entities = _entities(normalized, aliases)
    operations, operation_surfaces = _matches(normalized, OPERATION_PATTERNS)
    if (
        re.match(r"^tổng (?:giá trị|số dư|số phải trả|chi phí|doanh thu)", normalized, re.IGNORECASE)
        and (len(entities) > 1 or len(years) > 1 or re.search(r"\btrong số\b", normalized, re.IGNORECASE))
        and "sum" not in operations
    ):
        operations.append("sum")
        operation_surfaces["sum"] = ["multi-value total question form"]
    if not operations:
        if re.search(
            r"\b(?:là|bằng|ở mức) (?:bao nhiêu|mấy)\b|"
            r"\b(?:bao nhiêu|mấy) (?:triệu|tỷ|nghìn|trăm|đồng|cổ phiếu)\b|"
            r"^(?:hãy )?tính .+\btheo đơn vị\b",
            normalized,
            re.IGNORECASE,
        ):
            operations = ["lookup"]
            operation_surfaces = {"lookup": ["reported-value question form"]}
        else:
            operations = ["unresolved"]
            operation_surfaces = {"unresolved": []}
    temporal, temporal_surfaces = _matches(normalized, TEMPORAL_PATTERNS)
    if not temporal:
        temporal = ["single_fiscal_year"] if len(years) == 1 else (["multiple_years_unspecified_relation"] if len(years) > 1 else ["time_unspecified"])
    roles = [label for label, pattern in ROLE_PATTERNS if re.search(pattern, normalized, re.IGNORECASE)]
    scopes = [label for label, pattern in SCOPE_PATTERNS if re.search(pattern, normalized, re.IGNORECASE)]
    units = [label for label, pattern in UNIT_PATTERNS if re.search(pattern, normalized, re.IGNORECASE)]
    metric_families, metric_surfaces = _matches(normalized, METRIC_FAMILY_PATTERNS)
    if not metric_families:
        metric_families = ["long_tail_unresolved_metric"]
        metric_surfaces = {"long_tail_unresolved_metric": []}
    entity_set_cue = bool(re.search(r"\b(?:nhóm|các doanh nghiệp|các công ty|trong số)\b", normalized, re.IGNORECASE))
    if len(entities) > 1:
        entity_family = "explicit_multi_entity_set"
    elif entity_set_cue:
        entity_family = "entity_set_partially_resolved"
    elif len(entities) == 1:
        entity_family = "single_entity"
    else:
        entity_family = "entity_unresolved_from_closed_registry"
    value_cardinality = _value_cardinality(operations, len(entities), len(years))
    output_type = _output_type(normalized, operations)
    primary = _primary_question_type(operations)
    expected_topology = _expected_source_topology(len(entities), len(years), operations)
    signature_fields = {
        "question_type": primary,
        "operations": sorted(operations),
        "temporal": sorted(temporal),
        "entity_family": entity_family,
        "entity_roles": sorted(roles),
        "reporting_scopes": sorted(scopes),
        "value_cardinality": value_cardinality,
        "output_type": output_type,
        "expected_source_topology": expected_topology,
        "metric_families": sorted(metric_families),
    }
    return {
        "taxonomy_basis": "question_text_only",
        "question_type": primary,
        "operation_families": operations,
        "operation_surface_forms": operation_surfaces,
        "temporal_families": temporal,
        "temporal_surface_forms": temporal_surfaces,
        "years_mentioned": years,
        "exact_dates_mentioned": ["-".join((year, month.zfill(2), day.zfill(2))) for day, month, year in EXACT_DATE_RE.findall(normalized)],
        "entity_family": entity_family,
        "entities_resolved": entities,
        "entity_roles": roles,
        "reporting_scopes": scopes,
        "value_cardinality": value_cardinality,
        "expected_source_topology": expected_topology,
        "actual_source_topology": "UNRESOLVED_WITHOUT_GOLD_BINDING",
        "output_type": output_type,
        "requested_units": units or ["unspecified"],
        "metric_families": metric_families,
        "metric_surface_forms": metric_surfaces,
        "multi_step_candidate": len(set(operations) - {"lookup", "unresolved"}) >= 2,
        "semantic_signature": canonical_json_sha256(signature_fields),
        "wording_template": _wording_template(normalized, aliases),
        "candidate_category": primary == "candidate_unresolved" or "unresolved" in operations,
    }


def _baseline_status(row: Mapping[str, Any]) -> str:
    certificate = row.get("answer_certificate") or {}
    status = str(certificate.get("status") or row.get("authorization_status") or "UNKNOWN").upper()
    if status == "ABSTAIN":
        return "ABSTAIN"
    if "COMPLETE" in status or status in {"PASS", "ANSWER"}:
        return "ANSWERED_UNSCORED"
    return "UNKNOWN"


GENERAL_BLOCKER_FAMILIES: tuple[str, ...] = (
    "ANSWER_DECIMAL_INVALID",
    "BINDING_FIELD_NOT_PASS",
    "BINDING_LINEAGE_DOCUMENT_SHA256_INVALID",
    "BINDING_LINEAGE_RAW_CELL_SHA256_INVALID",
    "BINDING_LINEAGE_TABLE_SHA256_INVALID",
    "BINDING_NOT_FULLY_ELIGIBLE",
    "COUNTERFACTUAL_DIMENSION_INVALID",
    "COUNTERFACTUAL_NOT_REJECTED",
    "COUNTERFACTUAL_UNCHECKED",
    "EXECUTION_NOT_PASSED",
    "FORMULA_COMPATIBILITY_CONSTRAINT_VIOLATION",
    "FORMULA_COMPATIBILITY_RULE",
    "MULTI_STAGE_EXECUTION_GRAPH_NOT_MATERIALIZED",
    "NO_EXECUTABLE_STAGE",
    "PERIOD_YEAR_MISMATCH",
    "UNIT_CONVERSION_UNVERIFIED",
    "VARIABLE_MISMATCH",
)


def _general_blocker_family(reason_code: str) -> str:
    """Remove question/operand identifiers from a diagnostic blocker code."""
    value = str(reason_code).strip().upper()
    for family in GENERAL_BLOCKER_FAMILIES:
        if family in value:
            return family
    first = value.split(":", 1)[0]
    if re.fullmatch(r"Q\d+", first):
        return "QUESTION_SCOPED_UNCLASSIFIED_BLOCKER"
    return first or "UNKNOWN_BLOCKER"


def _scoring_observation(counts: Mapping[str, int], total: int) -> dict[str, Any]:
    prediction_count = int(counts.get("ANSWERED_UNSCORED", 0))
    abstain_count = int(counts.get("ABSTAIN", 0))
    return {
        "model_outcome_counts": {
            "PASS": None,
            "FAIL": None,
            "ABSTAIN": abstain_count,
        },
        "pass_count_status": "NOT_MEASURABLE_WITHOUT_GOLD_ANSWERS",
        "fail_count_status": "NOT_MEASURABLE_WITHOUT_GOLD_ANSWERS",
        "prediction_count": prediction_count,
        "coverage_rate": prediction_count / total if total else None,
        "abstention_rate": abstain_count / total if total else None,
        "false_confident_prediction_count": 0 if prediction_count == 0 else None,
        "false_confidence_rate": (
            "NOT_ESTIMABLE_NO_PREDICTIONS"
            if prediction_count == 0
            else "NOT_MEASURABLE_WITHOUT_GOLD_ANSWERS"
        ),
        "calibration_status": (
            "NOT_ESTIMABLE_NO_PREDICTIONS"
            if prediction_count == 0
            else "NOT_MEASURABLE_WITHOUT_SCORED_PREDICTIONS"
        ),
        "confidence_observation_status": (
            "NO_PREDICTION_CONFIDENCE_EMITTED"
            if prediction_count == 0
            else "PREDICTIONS_UNSCORED"
        ),
    }


def _group_baseline(rows: Sequence[Mapping[str, Any]], dimension: str, categories: callable) -> list[dict[str, Any]]:
    groups: defaultdict[str, Counter[str]] = defaultdict(Counter)
    reasons: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        for category in categories(row):
            groups[str(category)][str(row["baseline_status"])] += 1
            reasons[str(category)].update(
                _general_blocker_family(str(code))
                for code in row.get("abstain_reason_codes") or []
            )
    result: list[dict[str, Any]] = []
    for category, counts in sorted(groups.items()):
        total = sum(counts.values())
        result.append({
            "dimension": dimension,
            "category": category,
            "question_count": total,
            "pass_count": None,
            "fail_count": None,
            "abstain_count": counts["ABSTAIN"],
            "answered_unscored_count": counts["ANSWERED_UNSCORED"],
            "unknown_status_count": counts["UNKNOWN"],
            "common_abstain_reason_counts": dict(reasons[category].most_common()),
            "scoring_status": "NOT_MEASURABLE_WITHOUT_GOLD_ANSWERS",
            **_scoring_observation(counts, total),
        })
    return result


def _summary(taxonomy_rows: Sequence[Mapping[str, Any]], baseline_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    def counts(field: str) -> dict[str, int]:
        return dict(sorted(Counter(str(row["taxonomy"][field]) for row in taxonomy_rows).items()))

    operation_counts: Counter[str] = Counter()
    temporal_counts: Counter[str] = Counter()
    metric_counts: Counter[str] = Counter()
    operation_forms: defaultdict[str, Counter[str]] = defaultdict(Counter)
    temporal_forms: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for row in taxonomy_rows:
        taxonomy = row["taxonomy"]
        operation_counts.update(taxonomy["operation_families"])
        temporal_counts.update(taxonomy["temporal_families"])
        metric_counts.update(taxonomy["metric_families"])
        for concept, forms in taxonomy["operation_surface_forms"].items():
            operation_forms[concept].update(forms)
        for concept, forms in taxonomy["temporal_surface_forms"].items():
            temporal_forms[concept].update(forms)
    matrices: list[dict[str, Any]] = []
    matrices.extend(_group_baseline(baseline_rows, "question_family", lambda row: [row["taxonomy"]["question_type"]]))
    matrices.extend(_group_baseline(baseline_rows, "operation_family", lambda row: row["taxonomy"]["operation_families"]))
    matrices.extend(_group_baseline(baseline_rows, "temporal_family", lambda row: row["taxonomy"]["temporal_families"]))
    matrices.extend(_group_baseline(baseline_rows, "metric_family", lambda row: row["taxonomy"]["metric_families"]))
    matrices.extend(_group_baseline(baseline_rows, "source_topology", lambda row: [row["taxonomy"]["expected_source_topology"]]))
    matrices.extend(_group_baseline(baseline_rows, "entity_group", lambda row: [row["taxonomy"]["entity_family"]]))
    baseline_counts = Counter(str(row["baseline_status"]) for row in baseline_rows)
    return {
        "protocol": BASELINE_PROTOCOL,
        "schema_version": SCHEMA_VERSION,
        "source_contract": SOURCE_CONTRACT,
        "separation_contract": {
            "dataset_taxonomy": "question intrinsic; no baseline result used",
            "current_model_result": "joined only after taxonomy is frozen",
            "question_id_role": "tracking_only",
        },
        "question_count": len(taxonomy_rows),
        "question_family_counts": counts("question_type"),
        "entity_family_counts": counts("entity_family"),
        "value_cardinality_counts": counts("value_cardinality"),
        "expected_source_topology_counts": counts("expected_source_topology"),
        "operation_family_counts": dict(sorted(operation_counts.items())),
        "temporal_family_counts": dict(sorted(temporal_counts.items())),
        "metric_family_counts": dict(sorted(metric_counts.items())),
        "candidate_category_count": sum(bool(row["taxonomy"]["candidate_category"]) for row in taxonomy_rows),
        "linguistic_diversity": {
            "unique_questions": len({row["question_sha256"] for row in taxonomy_rows}),
            "unique_wording_templates": len({row["taxonomy"]["wording_template"] for row in taxonomy_rows}),
            "unique_semantic_signatures": len({row["taxonomy"]["semantic_signature"] for row in taxonomy_rows}),
            "operation_surface_forms": {key: dict(sorted(value.items())) for key, value in sorted(operation_forms.items())},
            "temporal_surface_forms": {key: dict(sorted(value.items())) for key, value in sorted(temporal_forms.items())},
        },
        "baseline_overall": {
            "question_count": len(baseline_rows),
            "status_counts": dict(sorted(baseline_counts.items())),
            "pass_count": None,
            "fail_count": None,
            "accuracy": "NOT_MEASURABLE_WITHOUT_GOLD_ANSWERS",
            **_scoring_observation(baseline_counts, len(baseline_rows)),
        },
        "baseline_by_dimension": matrices,
    }


def _render_research_report(summary: Mapping[str, Any]) -> str:
    families = summary["question_family_counts"]
    operations = summary["operation_family_counts"]
    temporal = summary["temporal_family_counts"]
    baseline = summary["baseline_overall"]
    family_lines = "\n".join(f"- `{name}`: {count}" for name, count in sorted(families.items(), key=lambda item: (-item[1], item[0])))
    operation_lines = "\n".join(f"- `{name}`: {count}" for name, count in sorted(operations.items(), key=lambda item: (-item[1], item[0])))
    temporal_lines = "\n".join(f"- `{name}`: {count}" for name, count in sorted(temporal.items(), key=lambda item: (-item[1], item[0])))
    return f"""# Dataset map and frozen-baseline research report v1

## RESEARCH QUESTION

Dataset 1.012 câu chứa những family/phenomenon nào, và baseline khóa hiện tại
xử lý từng family ra sao trước khi có bất kỳ thay đổi mô hình nào?

## OBSERVATION

Taxonomy question-intrinsic phủ {summary['question_count']} câu. Có
{summary['linguistic_diversity']['unique_wording_templates']} wording template
và {summary['linguistic_diversity']['unique_semantic_signatures']} semantic
signature. Số candidate category chưa giải quyết: {summary['candidate_category_count']}.

Question families:

{family_lines}

Operation families là multi-label nên tổng count có thể lớn hơn 1.012:

{operation_lines}

Temporal families:

{temporal_lines}

## HYPOTHESIS

Không có model-change hypothesis trong vòng này. Đây là census bắt buộc trước
experiment; không được dùng kết quả full-population này để tune rồi gọi lại là
unseen evaluation.

## BASELINE

Baseline duy nhất là deterministic E2E locked replay. Trạng thái:
`{json.dumps(baseline['status_counts'], ensure_ascii=False, sort_keys=True)}`.
Pass/fail accuracy, false-confidence và calibration là
không thể chấm theo gold vì public corpus không chứa gold answer, gold table,
gold evidence hay official split. Baseline phát
{baseline['prediction_count']} prediction nên false-confident prediction count
là `{baseline['false_confident_prediction_count']}`, còn false-confidence rate
và calibration là `{baseline['calibration_status']}` do tập prediction rỗng.

## SUBSET DESIGN

Discovery: không áp dụng cho dataset census.  
Development: không áp dụng; không thay đổi mô hình.  
Unseen evaluation: chưa được tạo; cần evaluator/gold độc lập và split freeze.

## CHANGE TESTED

Không thay đổi prediction, routing, parsing, retrieval, reasoning, threshold
hoặc model. Chỉ thêm research-only taxonomy/census sidecar.

## EXPECTED RESULT

Trước khi chạy: đủ đúng 1.012 ID, taxonomy không đọc baseline status, baseline
replay byte-match, và mọi actual source topology không có gold phải giữ
`UNRESOLVED_WITHOUT_GOLD_BINDING`.

## RESULT — DEVELOPMENT

Không áp dụng.

## RESULT — UNSEEN

Chưa đo; không có gold/evaluator độc lập.

## RESULT — FULL DATASET

Taxonomy phủ đủ 1.012 câu; baseline trả `ABSTAIN` cho
{baseline['status_counts'].get('ABSTAIN', 0)} câu. Kết quả theo từng question,
operation, temporal, source-topology và entity family nằm trong
`baseline_by_dimension` của JSON summary.

## IMPROVEMENTS

Không tuyên bố model improvement. Artifact mới làm đo lường family-level và
linguistic diversity có thể tái lập.

## REGRESSIONS

Không có model change nên không có regression prediction. Baseline output phải
byte-match reference; mismatch làm replay fail.

## GENERALIZATION

Company holdout: chưa chạy.  
Wording holdout: chưa chạy.  
Metric holdout: chưa chạy.  
Composition holdout: chưa chạy.

## OVERFITTING RISK

`LOW` cho census vì classifier không nhận Question ID và không thay đổi model.
Rủi ro của model hypothesis vẫn `UNASSESSED` cho tới khi có frozen holdouts.

## STRENGTHS

- Tách `WHAT THE DATASET CONTAINS` khỏi `HOW THE CURRENT MODEL HANDLES IT`.
- Hash-bound input/output và từ chối overwrite artifact.
- Không biến retrieval candidate hoặc blocker thành gold evidence.

## WEAKNESSES

- Không có gold nên chưa đo được PASS, FAIL, answer accuracy, false-confidence
  hoặc calibration.
- Actual one-cell/row/table/report topology chưa thể xác nhận.
- Taxonomy rule-based là candidate research map, không phải semantic gold.

## WHAT THIS EXPERIMENT PROVES

Chứng minh population, baseline replay và multidimensional census có thể được
tạo lại mà không dùng Question ID để phân loại và không sửa mô hình.

## WHAT THIS EXPERIMENT DOES NOT PROVE

Không chứng minh baseline đúng/sai ở family nào; không chứng minh bất kỳ rule
mới nào generalize; không cấp answer, training, promotion hoặc submission.

## VERDICT

`INVESTIGATE FURTHER` — cần một evaluator/gold độc lập, hash-bound và frozen
split trước model-change hypothesis đầu tiên.
"""


def build_dataset_research_map(
    *,
    questions: Path,
    baseline_certificates: Path,
    output_dir: Path,
    entity_aliases: Path | None = None,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite artifact directory: {output_dir}")
    question_rows = load_jsonl(questions)
    certificate_rows = load_jsonl(baseline_certificates)
    if len(question_rows) != expected_question_count:
        raise ValueError(f"expected {expected_question_count} questions, found {len(question_rows)}")
    aliases = load_entity_aliases(entity_aliases)
    taxonomy_rows: list[dict[str, Any]] = []
    question_by_id: dict[int, dict[str, Any]] = {}
    for source_row in question_rows:
        question_id = int(source_row["id"])
        question = normalize_text(str(source_row["question"]))
        if question_id in question_by_id:
            raise ValueError(f"duplicate question ID {question_id}")
        taxonomy = classify_question(question, aliases)
        row = {
            "protocol": TAXONOMY_PROTOCOL,
            "schema_version": SCHEMA_VERSION,
            "question_id": question_id,
            "question_id_role": "tracking_only",
            "question": question,
            "question_sha256": hashlib.sha256(question.encode("utf-8")).hexdigest(),
            "taxonomy": taxonomy,
            "source_contract": SOURCE_CONTRACT,
        }
        taxonomy_rows.append(row)
        question_by_id[question_id] = row
    certificate_by_id: dict[int, dict[str, Any]] = {}
    for row in certificate_rows:
        question_id = int(row["question_id"])
        if question_id in certificate_by_id:
            raise ValueError(f"duplicate baseline certificate question ID {question_id}")
        certificate_by_id[question_id] = row
    if set(question_by_id) != set(certificate_by_id):
        raise ValueError("questions and baseline certificates do not cover identical IDs")
    baseline_rows: list[dict[str, Any]] = []
    for question_id in sorted(question_by_id):
        taxonomy_row = question_by_id[question_id]
        certificate = certificate_by_id[question_id]
        cert = certificate.get("answer_certificate") or {}
        baseline_rows.append({
            "protocol": BASELINE_PROTOCOL,
            "schema_version": SCHEMA_VERSION,
            "question_id": question_id,
            "question_sha256": taxonomy_row["question_sha256"],
            "baseline_status": _baseline_status(certificate),
            "baseline_raw_status": str(cert.get("status") or certificate.get("authorization_status") or "UNKNOWN"),
            "abstain_reason_codes": sorted(str(value) for value in (cert.get("abstain_reason_codes") or [])),
            "taxonomy": taxonomy_row["taxonomy"],
            "source_contract": SOURCE_CONTRACT,
        })
    taxonomy_rows.sort(key=lambda row: int(row["question_id"]))
    summary = _summary(taxonomy_rows, baseline_rows)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        taxonomy_path = temp_dir / "dataset_taxonomy_v1.jsonl"
        baseline_path = temp_dir / "baseline_by_question_v1.jsonl"
        summary_path = temp_dir / "dataset_research_summary_v1.json"
        report_path = temp_dir / "dataset_map_research_report_v1.md"
        _write_jsonl(taxonomy_path, taxonomy_rows)
        _write_jsonl(baseline_path, baseline_rows)
        _write_json(summary_path, summary)
        report_path.write_text(_render_research_report(summary), encoding="utf-8")
        inputs = {
            "questions": {"path": str(questions), "sha256": sha256_file(questions)},
            "baseline_certificates": {"path": str(baseline_certificates), "sha256": sha256_file(baseline_certificates)},
        }
        if entity_aliases is not None:
            inputs["entity_aliases"] = {"path": str(entity_aliases), "sha256": sha256_file(entity_aliases)}
        manifest = {
            "protocol": MANIFEST_PROTOCOL,
            "schema_version": SCHEMA_VERSION,
            "question_count": len(taxonomy_rows),
            "question_id_role": "tracking_only",
            "taxonomy_basis": "question_text_only",
            "baseline_join_stage": "after_taxonomy_classification",
            "source_contract": SOURCE_CONTRACT,
            "inputs": inputs,
            "outputs": {
                taxonomy_path.name: {"sha256": sha256_file(taxonomy_path)},
                baseline_path.name: {"sha256": sha256_file(baseline_path)},
                summary_path.name: {"sha256": sha256_file(summary_path)},
                report_path.name: {"sha256": sha256_file(report_path)},
            },
        }
        _write_json(temp_dir / "manifest.json", manifest)
        temp_dir.rename(output_dir)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    return summary


def validate_dataset_research_map(output_dir: Path, *, expected_question_count: int = 1012) -> dict[str, Any]:
    manifest_path = output_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("protocol") != MANIFEST_PROTOCOL:
        raise ValueError("unexpected manifest protocol")
    if manifest.get("question_id_role") != "tracking_only" or manifest.get("taxonomy_basis") != "question_text_only":
        raise ValueError("taxonomy independence contract is missing")
    for name, descriptor in (manifest.get("outputs") or {}).items():
        if sha256_file(output_dir / name) != descriptor.get("sha256"):
            raise ValueError(f"output hash mismatch: {name}")
    taxonomy_rows = load_jsonl(output_dir / "dataset_taxonomy_v1.jsonl")
    baseline_rows = load_jsonl(output_dir / "baseline_by_question_v1.jsonl")
    if len(taxonomy_rows) != expected_question_count or len(baseline_rows) != expected_question_count:
        raise ValueError("dataset map does not cover the expected population")
    taxonomy_ids = {int(row["question_id"]) for row in taxonomy_rows}
    baseline_ids = {int(row["question_id"]) for row in baseline_rows}
    if len(taxonomy_ids) != expected_question_count or taxonomy_ids != baseline_ids:
        raise ValueError("question ID coverage is not one-to-one")
    taxonomy_hashes = {int(row["question_id"]): row["question_sha256"] for row in taxonomy_rows}
    for row in taxonomy_rows:
        expected = hashlib.sha256(normalize_text(str(row["question"])).encode("utf-8")).hexdigest()
        if row.get("question_sha256") != expected:
            raise ValueError(f"question text hash mismatch: {row.get('question_id')}")
        if row.get("question_id_role") != "tracking_only" or row.get("taxonomy", {}).get("taxonomy_basis") != "question_text_only":
            raise ValueError("question ID or taxonomy basis contract violated")
    for row in baseline_rows:
        if row.get("question_sha256") != taxonomy_hashes[int(row["question_id"])]:
            raise ValueError("baseline join is not bound to taxonomy question text")
    summary = json.loads((output_dir / "dataset_research_summary_v1.json").read_text(encoding="utf-8"))
    if summary.get("question_count") != expected_question_count:
        raise ValueError("summary question count mismatch")
    return {
        "status": "VALIDATION_PASSED",
        "question_count": expected_question_count,
        "candidate_category_count": summary.get("candidate_category_count"),
        "baseline_status_counts": summary.get("baseline_overall", {}).get("status_counts"),
    }
