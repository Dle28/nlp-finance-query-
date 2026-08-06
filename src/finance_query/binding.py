from __future__ import annotations

import json
import re
import unicodedata
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Iterable

from .execution import convert_unit, parse_decimal
from .retrieval import AssetStore
from .schemas import DirectBinding, QuestionPlan, RetrievedTable


TOKEN_RE = re.compile(r"[\w%]+", re.UNICODE)
NUMERIC_LIKE_RE = re.compile(r"^[\s()\-+\d.,%]+$")


def normalize(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(TOKEN_RE.findall(text))


def token_jaccard(left: str, right: str) -> float:
    a = set(TOKEN_RE.findall(normalize(left)))
    b = set(TOKEN_RE.findall(normalize(right)))
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def text_similarity(left: str, right: str) -> float:
    a = normalize(left)
    b = normalize(right)
    if not a or not b:
        return 0.0
    substring_bonus = 1.0 if a in b or b in a else 0.0
    sequence = SequenceMatcher(None, a, b).ratio()
    overlap = token_jaccard(a, b)
    return min(1.0, 0.45 * sequence + 0.45 * overlap + 0.10 * substring_bonus)


def row_label(row: list[str]) -> str:
    if not row:
        return ""
    cells = row
    if len(cells) > 1 and re.fullmatch(r"\d{1,3}", cells[0].strip()):
        cells = cells[1:]

    labels: list[str] = []
    for cell in cells:
        if NUMERIC_LIKE_RE.fullmatch(cell.strip()) and labels:
            break
        if not NUMERIC_LIKE_RE.fullmatch(cell.strip()):
            labels.append(cell)
        if len(labels) >= 3:
            break
    return " > ".join(labels) if labels else cells[0]


def column_descriptions(rows: list[list[str]], data_row_index: int) -> dict[int, str]:
    descriptions: dict[int, list[str]] = {}
    for row in rows[: min(data_row_index, 6)]:
        for column, cell in enumerate(row):
            if cell:
                descriptions.setdefault(column, []).append(cell)
    return {
        column: " > ".join(dict.fromkeys(values))
        for column, values in descriptions.items()
    }


def column_score(
    description: str,
    *,
    target_year: int | None,
    report_year: int | None,
    question: str,
) -> float:
    normalized = normalize(description)
    q = normalize(question)
    score = 0.0

    if target_year is not None:
        if str(target_year) in normalized:
            score += 1.0
        if report_year == target_year and "nam nay" in _strip_diacritics(normalized):
            score += 0.75
        if report_year is not None and target_year == report_year - 1:
            stripped = _strip_diacritics(normalized)
            if "nam truoc" in stripped or "ky truoc" in stripped:
                score += 0.75

    stripped_description = _strip_diacritics(normalized)
    stripped_question = _strip_diacritics(q)
    if "cuoi nam" in stripped_question or "31 12" in stripped_question:
        if "cuoi nam" in stripped_description or "31 12" in stripped_description:
            score += 0.35
    if "dau nam" in stripped_question or "01 01" in stripped_question:
        if "dau nam" in stripped_description or "01 01" in stripped_description:
            score += 0.35
    return score


def _strip_diacritics(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value)
    return "".join(character for character in decomposed if unicodedata.category(character) != "Mn").replace("đ", "d")


def candidate_bindings(
    store: AssetStore,
    plan: QuestionPlan,
    retrieved: Iterable[RetrievedTable],
    *,
    max_tables: int = 10,
) -> list[DirectBinding]:
    if plan.family != "direct_lookup" or not plan.operands:
        return []

    metric = plan.operands[0].metric
    target_year = plan.operands[0].period or (plan.years[0] if len(plan.years) == 1 else None)
    candidates = list(retrieved)[:max_tables]
    assets = store.get_assets(candidate.internal_table_uid for candidate in candidates)
    bindings: list[DirectBinding] = []

    for table_rank, candidate in enumerate(candidates, start=1):
        asset = assets.get(candidate.internal_table_uid)
        if asset is None:
            continue
        rows = json.loads(asset.get("rows_json") or "[]")
        if not rows:
            continue

        row_candidates: list[tuple[float, int, str]] = []
        for row_index, row in enumerate(rows):
            label = row_label(row)
            similarity = text_similarity(metric, label)
            if similarity > 0.15:
                row_candidates.append((similarity, row_index, label))

        for row_similarity, row_index, label in sorted(row_candidates, reverse=True)[:5]:
            row = rows[row_index]
            descriptions = column_descriptions(rows, row_index)
            value_candidates: list[tuple[float, int, str, object]] = []

            for column_index, raw_value in enumerate(row):
                parsed = parse_decimal(raw_value)
                if parsed.value is None:
                    continue
                header = descriptions.get(column_index, "")
                period_score = column_score(
                    header,
                    target_year=target_year,
                    report_year=asset.get("report_year"),
                    question=plan.original_question,
                )
                # Avoid choosing row-number or note columns unless no better value exists.
                structural_penalty = 0.25 if column_index <= 1 and period_score == 0 else 0.0
                value_candidates.append(
                    (period_score - structural_penalty, column_index, header, parsed)
                )

            if not value_candidates:
                continue

            period_score, column_index, header, parsed = max(value_candidates)
            retrieval_prior = 1.0 / table_rank
            binding_score = (
                0.58 * row_similarity
                + 0.27 * min(1.0, max(0.0, period_score))
                + 0.15 * retrieval_prior
            )
            if parsed.value is None:
                continue

            source_unit = asset.get("unit_hint")
            target_unit = plan.requested_unit
            warnings = list(parsed.warnings)
            value = Decimal(parsed.value)
            try:
                converted = convert_unit(value, source_unit, target_unit)
            except ValueError as exc:
                converted = value
                warnings.append(str(exc))

            bindings.append(
                DirectBinding(
                    internal_table_uid=candidate.internal_table_uid,
                    document_id=asset["document_id"],
                    row_index=row_index,
                    column_index=column_index,
                    row_text=label,
                    column_text=header,
                    raw_value=str(row[column_index]),
                    parsed_value=parsed.value,
                    source_unit=source_unit,
                    target_unit=target_unit,
                    converted_value=format(converted, "f"),
                    binding_score=binding_score,
                    warnings=warnings,
                )
            )

    return sorted(bindings, key=lambda binding: binding.binding_score, reverse=True)
