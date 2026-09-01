#!/usr/bin/env python3
"""Run a controlled multi-entity metric/plan ablation.

The primary competition builder currently receives the legacy review plan.  In
that plan, complex multi-entity questions and simple aggregation questions can
both have an empty ``operands`` list, and the operation can disagree with the
typed-plan artifact.  This adapter supplies the frozen typed-plan contract to
only the simple aggregation lane and then delegates all table hydration,
Decimal arithmetic, proposal verification, evidence writing, and submission
validation to the canonical builder.

This is an authorized best-effort submission candidate.  Typed plans and row
similarity are routing/selection signals; cells still come from the current
structured table asset through ``choose_semantic_cell``.  Complex or abstained
plans are deliberately not forced through this lane.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Mapping


VARIANT_PROTOCOL = "vifinqa_multi_entity_metric_plan_v1"
BUILDER_NAME = "_vifinqa_canonical_submission_builder"


def _load_builder() -> Any:
    builder_path = Path(__file__).resolve().parents[1] / "e2e" / "build_competition_submission_v1.py"
    spec = importlib.util.spec_from_file_location(BUILDER_NAME, builder_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load canonical builder: {builder_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILDER = _load_builder()
_ORIGINAL_MULTI_ENTITY = BUILDER.multi_entity_program_answer
_ORIGINAL_METRIC_HINT = BUILDER.multi_entity_metric_hint
_ORIGINAL_OPERATION = BUILDER.multi_entity_operation
_TYPED_PLANS: dict[int, dict[str, Any]] = {}
_TRACE: list[dict[str, Any]] = []
_STATS: Counter[str] = Counter()

# These are high-signal row concepts that a token-overlap score can otherwise
# miss.  Each rule is ``(metric triggers, acceptable row anchors)``.  The
# rules are deliberately small and domain-shaped: they do not lower or raise
# the canonical resolver threshold globally, and they only gate the new
# multi-entity single-cell arithmetic lane.
_ROW_ANCHOR_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("luu chuyen",), ("luu chuyen",)),
    (("xay dung co ban",), ("co ban do dang",)),
    (("dang luu hanh",), ("luu hanh",)),
    (("cam ket l c",), ("l c",)),
    (("thue va cac khoan phai nop",), ("thue va cac khoan phai nop",)),
    (("phai tra ngan han",), ("phai tra ngan han",)),
    (("phai thu ngan han",), ("phai thu ngan han",)),
    (("ben lien quan",), ("ben lien quan",)),
    (("cho vay khach hang",), ("cho vay khach hang",)),
    (("hao mon luy ke",), ("hao mon luy ke",)),
    (("quy khen thuong phuc loi",), ("quy khen thuong phuc loi",)),
    (("lai thuan",), ("lai thuan",)),
    (("chi phi thue thu nhap",), ("chi phi thue thu nhap",)),
    (("hien hanh",), ("hien hanh",)),
    (("trich lap du phong",), ("trich lap du phong", "du phong cho vay khach hang", "du phong rui ro tin dung")),
    (("du phong giam gia",), ("du phong giam gia",)),
    (("san sang de ban",), ("san sang de ban",)),
    (("doanh thu hoat dong tai chinh",), ("doanh thu hoat dong tai chinh",)),
    (("chi phi tai chinh",), ("chi phi tai chinh",)),
    (("chi phi lai vay",), ("chi phi lai vay",)),
    (("phai tra nguoi ban ngan han",), ("phai tra nguoi ban ngan han",)),
    (("trai phieu chinh phu",), ("trai phieu chinh phu",)),
    (("dau tu vao cong ty lien ket",), ("dau tu vao cong ty lien ket",)),
    (("tien va cac khoan tuong duong tien",), ("tien va cac khoan tuong duong tien",)),
    (("vay dai han",), ("vay dai han",)),
)

# Words that can make an unrelated accounting row look good merely because
# they occur in the surrounding question shell.  They are used only for a
# secondary informative-hit check after the explicit anchors above.
_GENERIC_ROW_TOKENS = {
    "bao", "cao", "chi", "cong", "cua", "cuoi", "dau", "dong", "gia", "giam",
    "hoat", "hon", "kinh", "ky", "lai", "nam", "nhat", "phi", "so", "tang",
    "thay", "thuan", "tinh", "tri", "trong", "tru", "ty", "vao", "du",
}


def _row_label(row: Any) -> str:
    if not isinstance(row, list):
        return ""
    parts: list[str] = []
    for cell in row[:3]:
        if BUILDER.parse_decimal(cell) is None and str(cell).strip():
            parts.append(str(cell))
    return " ".join(parts)


_FULL_TABLE_INDEX_KEY: int | None = None
_FULL_TABLE_INDEX: dict[tuple[str, int, str], list[dict[str, Any]]] = {}


def _full_table_index(tables_by_uid: Mapping[str, Mapping[str, Any]]) -> dict[tuple[str, int, str], list[dict[str, Any]]]:
    """Index the already-loaded immutable corpus for typed-plan navigation."""

    global _FULL_TABLE_INDEX_KEY, _FULL_TABLE_INDEX
    key = id(tables_by_uid)
    if _FULL_TABLE_INDEX_KEY == key:
        return _FULL_TABLE_INDEX
    index: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
    for raw_table in tables_by_uid.values():
        table = dict(raw_table)
        ticker = BUILDER.table_ticker(table)
        if not ticker:
            continue
        try:
            year = int(table.get("report_year"))
        except (TypeError, ValueError):
            year = BUILDER.document_year(table.get("document_id"))
        if year is None:
            continue
        scope = str(table.get("scope") or "").strip().lower() or "unknown"
        index.setdefault((ticker.upper(), int(year), scope), []).append(table)
    _FULL_TABLE_INDEX_KEY = key
    _FULL_TABLE_INDEX = index
    return index


def _typed_scope(item: Mapping[str, Any], typed: Mapping[str, Any]) -> str:
    for value in (typed.get("scope"), (item.get("question_plan") or {}).get("scope")):
        scope = str(value or "").strip().lower()
        if scope in {"separate", "consolidated"}:
            return scope
    return ""


def _typed_year(typed: Mapping[str, Any], item: Mapping[str, Any]) -> int | None:
    values = typed.get("years") or (item.get("question_plan") or {}).get("years") or []
    for value in reversed(values):
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    years = BUILDER.YEAR_RE.findall(str(item.get("question") or ""))
    return int(years[-1]) if years else None


def _question_id(item: Mapping[str, Any]) -> int | None:
    for key in ("id", "question_id"):
        value = item.get(key)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    plan = item.get("question_plan")
    if isinstance(plan, Mapping):
        try:
            return int(plan.get("question_id"))
        except (TypeError, ValueError):
            pass
    return None


def _typed_plan(item: Mapping[str, Any]) -> dict[str, Any] | None:
    question_id = _question_id(item)
    return _TYPED_PLANS.get(question_id) if question_id is not None else None


def _typed_tickers(typed: Mapping[str, Any]) -> list[str]:
    values = typed.get("tickers") or typed.get("entities") or []
    return [str(value).strip().upper() for value in values if str(value).strip()]


def _compact(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" ,.;:?")


def _clean_metric(value: Any, *, tickers: set[str] | None = None) -> str:
    """Remove only aggregation/entity/time shells from a typed hint.

    The typed compiler already provides a bounded metric hint.  The cleaner
    keeps accounting qualifiers such as ``của khách hàng`` and ``của nhân
    viên`` while removing the entity list and presentation unit.  Several
    variants are tried; the candidate with the strongest per-entity row match
    wins later in ``_patched_multi_entity_program_answer``.
    """

    text = BUILDER.normalize(value)
    text = _compact(text)
    prefixes = (
        "tinh gia tri trung binh cua chi tieu ",
        "tinh gia tri trung binh cua ",
        "gia tri trung binh cua ",
        "tinh trung binh cua ",
        "tinh trung binh ",
        "trung binh cua ",
        "tinh tong ",
        "tinh gia tri ",
    )
    for prefix in prefixes:
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break

    # A few typed operand hints are emitted as ``của <metric>`` rather than
    # ``<metric> của <entities>``.  Keep the unmodified hint as another
    # variant, but make the leading preposition removable for row matching.
    if text.startswith("cua "):
        text = text[4:]

    # ``ty trong trung binh cua X`` is an aggregation shell, while ``ty
    # trong cua khach hang`` is part of a metric and is retained.
    text = re.sub(r"^(ty trong|ty le|ty so)\s+trung binh\s+cua\s+", r"\1 ", text)

    # Entity introductions delimit the requested row from the issuer list.
    # These patterns intentionally do not match ``cua khach hang`` or ``cua
    # nhan vien``.
    text = re.split(
        r"\s+cua\s+(?:cac\s+)?(?:cong ty me|cong ty|ctcp|ngan hang|tap doan|tong cong ty)\b",
        text,
        maxsplit=1,
    )[0]
    text = re.split(
        r"\s+cua\s+(?:[a-z0-9-]+\s+){0,12}cong ty me\b",
        text,
        maxsplit=1,
    )[0]
    if tickers:
        # Some compiler hints use a bare ticker list (``của DLG, VJC``)
        # instead of the longer company introduction.  Only cut at ``cua``
        # when the tail contains one of the exact planned tickers.
        for match in list(re.finditer(r"\s+cua\s+", text)):
            tail_tokens = set(re.findall(r"[a-z0-9]+", text[match.end() :]))
            if tail_tokens & {str(ticker).lower() for ticker in tickers}:
                text = text[: match.start()]
                break
    text = re.split(r"\s+o\s+muc\s+(?:cong ty me|cong ty)\b", text, maxsplit=1)[0]

    # Remove years and presentation clauses only when they are clearly time
    # or output-unit shells.  Internal ``nam`` in ``thu nhap binh quan nam``
    # remains intact.
    text = re.sub(
        r"\s+(?:cuoi nam|dau nam|trong nam|vao nam|tai nam|nam)\s+(?:19|20)\d{2}\b",
        " ",
        text,
    )
    text = re.sub(r"\s+(?:19|20)\d{2}\b", " ", text)
    text = re.sub(r"\s+nam\s+(?=cua\s+(?:cac\s+)?cong ty)\b", " ", text)
    text = re.sub(
        r"\s+(?:ra don vi|don vi|tinh bang|theo don vi)\s+(?:trieu|ty|nghin|ngan|tram)?\s*(?:dong|vnd)?\b.*$",
        " ",
        text,
    )
    text = re.sub(r"\s+nam\s*$", " ", text)
    text = re.sub(r"\s+(?:la|bao nhieu)\s*$", " ", text)
    return _compact(text)


def _metric_variants(item: Mapping[str, Any], typed: Mapping[str, Any]) -> list[str]:
    operands = typed.get("operands") or []
    raw_values: list[str] = []
    if isinstance(operands, list) and operands:
        first = operands[0]
        if isinstance(first, Mapping):
            hints = first.get("metric_hints") or []
            if isinstance(hints, list):
                raw_values.extend(str(value) for value in hints if str(value).strip())
    raw_values.append(str(item.get("question") or ""))

    variants: list[str] = []
    tickers = {value.lower() for value in _typed_tickers(typed)}
    for raw in raw_values:
        # The cleaned operand hint is the primary navigation query.  Retain
        # the raw hint as a fallback because some OCR-specific qualifiers are
        # useful to the canonical resolver.
        for value in (_clean_metric(raw, tickers=tickers), raw):
            value = _compact(value)
            if len(BUILDER.content_tokens(value)) < 2 or value in variants:
                continue
            variants.append(value)
    return variants


def _full_corpus_candidate_item(
    item: Mapping[str, Any],
    typed: Mapping[str, Any],
    metric: str,
    tables_by_uid: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, Any] | None:
    """Build a bounded candidate view from the current full table asset.

    The legacy review shortlist can omit the exact row for a typed multi-
    entity question.  This helper searches only the requested ticker/year
    pairs, keeps the preferred scope, and emits at most twelve best table-row
    candidates per entity.  It never supplies a value: the canonical
    ``choose_semantic_cell`` path still hydrates the row and Decimal-replays
    the selected cell.
    """

    if not tables_by_uid:
        return None
    year = _typed_year(typed, item)
    if year is None:
        return None
    scope = _typed_scope(item, typed)
    index = _full_table_index(tables_by_uid)
    candidates: list[dict[str, Any]] = []
    for ticker in _typed_tickers(typed):
        tables: list[dict[str, Any]] = []
        if scope:
            tables.extend(index.get((ticker, year, scope), []))
        if not tables:
            for (indexed_ticker, indexed_year, indexed_scope), values in index.items():
                if indexed_ticker == ticker and indexed_year == year and indexed_scope in {scope, "unknown"}:
                    tables.extend(values)
        table_rows: list[tuple[float, int, bool, dict[str, Any], list[Any]]] = []
        for table in tables:
            rows = table.get("rows") or []
            best_row: tuple[float, int, bool, list[Any]] | None = None
            for row_index, row in enumerate(rows):
                label = _row_label(row)
                if not label or not any(BUILDER.parse_decimal(cell) is not None for cell in row[1:]):
                    continue
                score = BUILDER.semantic_row_score(metric, str(item.get("question") or ""), label)
                anchor_ok = _row_anchor_satisfied(metric, label)
                hits = len(BUILDER.content_tokens(metric) & BUILDER.content_tokens(label))
                if score <= 0.0 or hits < 1:
                    continue
                row_key = (score + (1.5 if anchor_ok else 0.0), hits, anchor_ok, -row_index)
                if best_row is None or row_key > (
                    best_row[0] + (1.5 if best_row[2] else 0.0),
                    len(BUILDER.content_tokens(metric) & BUILDER.content_tokens(_row_label(best_row[3]))),
                    best_row[2],
                    -best_row[1],
                ):
                    best_row = (score, row_index, anchor_ok, row)
            if best_row is not None:
                score, row_index, anchor_ok, row = best_row
                table_rows.append((score, row_index, anchor_ok, table, row))
        if not table_rows:
            continue
        anchor_rows = [entry for entry in table_rows if entry[2]]
        ranked_rows = anchor_rows or table_rows
        ranked_rows.sort(
            key=lambda entry: (
                -float(entry[0]),
                not entry[2],
                str(entry[3].get("document_id") or ""),
                int(entry[1]),
            )
        )
        for rank, (score, row_index, anchor_ok, table, row) in enumerate(ranked_rows[:12], start=1):
            evidence_indices = set(range(min(8, len(table.get("rows") or []))))
            evidence_indices.update(range(max(0, row_index - 2), min(len(table.get("rows") or []), row_index + 3)))
            evidence = [
                {"index": index_value, "row": (table.get("rows") or [])[index_value]}
                for index_value in sorted(evidence_indices)
            ]
            candidates.append(
                {
                    "rank": rank,
                    "review_score": max(0.0, min(1.0, float(score) / 10.0)),
                    "candidate_source": "typed_full_corpus_navigation",
                    "research_candidate_only": True,
                    "ticker": ticker,
                    "document_id": table.get("document_id"),
                    "report_year": year,
                    "scope": table.get("scope") or scope,
                    "internal_table_uid": table.get("internal_table_uid"),
                    "ticker_match": True,
                    "year_match": True,
                    "scope_match": True,
                    "evidence_window": evidence,
                }
            )
    if not candidates:
        return None
    augmented = dict(item)
    augmented["candidates"] = candidates
    return augmented


def _selection_quality(selection: Mapping[str, Any], metric: str) -> tuple[float, int, int]:
    label = BUILDER.normalize(selection.get("row_label") or "")
    metric_norm = BUILDER.normalize(metric)
    metric_tokens = BUILDER.content_tokens(metric_norm)
    label_tokens = BUILDER.content_tokens(label)
    hits = len(metric_tokens & label_tokens)

    # Require a contiguous two-token phrase when the metric has enough text.
    # This rejects rows that only share generic words such as ``số`` or
    # ``nhân viên`` with a different financial line.
    metric_words = metric_norm.split()
    phrase_length = 0
    for width in range(min(5, len(metric_words)), 1, -1):
        if any(
            " ".join(metric_words[index : index + width]) in label
            for index in range(0, len(metric_words) - width + 1)
        ):
            phrase_length = width
            break
    # ``thu nhập bình quân`` is materially different from a generic income
    # or employee-count row.  Require that discriminating phrase whenever it
    # is part of the typed metric; this is a narrow guard for the current
    # failure class, not a global score threshold change.
    if "thu nhap binh quan" in metric_norm:
        if "binh quan" not in label:
            phrase_length = 0
        # Do not silently answer an annual request with a monthly row.  The
        # table may contain both; a row-level period mismatch is semantic, not
        # something a larger lexical score can repair.
        if "binh quan nam" in metric_norm and "thang" in label and "nam" not in label:
            phrase_length = 0
    if not _row_anchor_satisfied(metric_norm, label):
        phrase_length = 0
    informative_metric_tokens = metric_tokens - _GENERIC_ROW_TOKENS
    informative_hits = len(informative_metric_tokens & label_tokens)
    if len(informative_metric_tokens) >= 2 and informative_hits < 2:
        phrase_length = 0
    score = float(selection.get("score") or 0.0)
    return score + 0.28 * phrase_length + 0.08 * hits, phrase_length, hits


def _row_anchor_satisfied(metric: str, label: str) -> bool:
    metric_norm = BUILDER.normalize(metric)
    label_norm = BUILDER.normalize(label)
    for triggers, anchors in _ROW_ANCHOR_RULES:
        if any(trigger in metric_norm for trigger in triggers) and not any(
            anchor in label_norm for anchor in anchors
        ):
            return False
    return True


def _is_derived_metric(metric: str) -> bool:
    """Return whether a single typed operand cannot represent this metric.

    The simple aggregation route can average one directly reported cell per
    entity.  A ratio/tỷ-trọng question needs a numerator and denominator
    contract, so accepting a raw reserve row here would be a silent semantic
    substitution.  Direct reported ``tỷ lệ`` rows remain eligible unless the
    wording signals a ratio/derived or otherwise ambiguous construction.
    """

    normalized = f" {BUILDER.normalize(metric)} "
    if any(
        marker in normalized
        for marker in (
            " ty trong ",
            " ty le ",
            " ty so ",
            " he so ",
            " so voi ",
            " tren ",
            " cagr ",
            " bien loi nhuan ",
            " lai lo ",
        )
    ):
        return True
    return False


def _is_contract_candidate(typed: Mapping[str, Any]) -> bool:
    tickers = _typed_tickers(typed)
    return bool(
        typed.get("decomposition_status") == "complete"
        and typed.get("route") == "simple_aggregation"
        and len(tickers) >= 2
        and len(typed.get("operands") or []) == len(tickers)
        and (typed.get("operation_ast") or {}).get("op") in {"mean", "sum", "min", "max", "count"}
    )


def _patched_metric_hint(item: dict[str, Any]) -> str:
    override = (item.get("question_plan") or {}).get("_multi_entity_metric_override_v1")
    if override:
        return str(override)
    return _ORIGINAL_METRIC_HINT(item)


def _patched_operation(item: dict[str, Any]) -> str | None:
    plan = item.get("question_plan") or {}
    override = plan.get("_multi_entity_operation_override_v1")
    if override:
        return str(override)
    return _ORIGINAL_OPERATION(item)


def _patched_multi_entity_program_answer(
    item: dict[str, Any],
    *,
    tables_by_uid: dict[str, dict[str, Any]] | None = None,
) -> tuple[Any, list[dict[str, Any]], str, str] | None:
    typed = _typed_plan(item)
    question_id = _question_id(item)
    if typed is None:
        _STATS["typed_plan_missing"] += 1
        return None
    if not _is_contract_candidate(typed):
        _STATS["complex_or_non_simple_skipped"] += 1
        if typed.get("decomposition_status") == "abstain":
            _STATS["abstain_plan_skipped"] += 1
        return None

    variants = _metric_variants(item, typed)
    if not variants:
        _STATS["metric_hint_missing"] += 1
        return None
    if any(_is_derived_metric(metric) for metric in variants):
        _STATS["derived_metric_skipped"] += 1
        _TRACE.append(
            {
                "question_id": question_id,
                "status": "SKIPPED_DERIVED_METRIC_NEEDS_FORMULA_CONTRACT",
                "typed_route": typed.get("route"),
                "typed_operation": (typed.get("operation_ast") or {}).get("op"),
                "metric_variants": variants,
            }
        )
        return None

    best: tuple[tuple[float, int, int, int, int], tuple[Any, list[dict[str, Any]], str, str], str, str] | None = None
    for variant_index, metric in enumerate(variants):
        plan = dict(item.get("question_plan") or {})
        plan["_multi_entity_metric_override_v1"] = metric
        plan["_multi_entity_operation_override_v1"] = (typed.get("operation_ast") or {}).get("op")
        candidate_item = dict(item)
        candidate_item["question_plan"] = plan
        candidate_views: list[tuple[str, dict[str, Any]]] = []
        full_candidate_item = _full_corpus_candidate_item(
            candidate_item,
            typed,
            metric,
            tables_by_uid,
        )
        if full_candidate_item is not None:
            candidate_views.append(("full_corpus", full_candidate_item))
            _STATS["full_corpus_candidate_views"] += 1
        candidate_views.append(("review_bundle", candidate_item))
        for view_name, candidate_view in candidate_views:
            result = _ORIGINAL_MULTI_ENTITY(candidate_view, tables_by_uid=tables_by_uid)
            if result is None:
                continue
            selections = result[1]
            quality_rows = [_selection_quality(selection, metric) for selection in selections]
            if not quality_rows:
                continue
            min_score = min(row[0] for row in quality_rows)
            min_phrase = min(row[1] for row in quality_rows)
            min_hits = min(row[2] for row in quality_rows)
            if min_phrase < 2 or min_hits < 2:
                continue
            quality = (min_score, min_phrase, min_hits, int(view_name == "full_corpus"), -variant_index)
            if best is None or quality > best[0]:
                best = (quality, result, metric, view_name)

    if best is None:
        _STATS["contract_candidate_rejected"] += 1
        _TRACE.append(
            {
                "question_id": question_id,
                "status": "REJECTED_NO_STRONG_PER_ENTITY_ROW",
                "typed_route": typed.get("route"),
                "typed_operation": (typed.get("operation_ast") or {}).get("op"),
            }
        )
        return None

    quality, result, metric, view_name = best
    _STATS["contract_candidate_accepted"] += 1
    _TRACE.append(
        {
            "question_id": question_id,
            "status": "ACCEPTED",
            "metric": metric,
            "operation": (typed.get("operation_ast") or {}).get("op"),
            "min_quality": quality[0],
            "min_phrase_length": quality[1],
            "min_token_hits": quality[2],
            "selection_count": len(result[1]),
            "candidate_view": view_name,
            "sources": [
                {
                    "ticker_role": row.get("role"),
                    "document_id": row.get("document_id"),
                    "internal_table_uid": row.get("internal_table_uid"),
                    "row_index": row.get("row_index"),
                    "column_index": row.get("column_index"),
                    "row_label": row.get("row_label"),
                    "score": row.get("score"),
                }
                for row in result[1]
            ],
        }
    )
    return result


def _load_typed_plans(path: Path) -> dict[int, dict[str, Any]]:
    plans: dict[int, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            question_id = int(record["question_id"])
            plans[question_id] = record
    if len(plans) != 1012:
        raise ValueError(f"typed-plan count={len(plans)} expected 1012")
    return plans


def _write_variant_metadata(output_dir: Path, *, typed_plans_path: Path) -> None:
    report_path = output_dir / "build_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["multi_entity_metric_variant"] = {
        "protocol": VARIANT_PROTOCOL,
        "typed_plans_path": str(typed_plans_path),
        "typed_plan_count": len(_TYPED_PLANS),
        "contract": {
            "route": "simple_aggregation",
            "decomposition_status": "complete",
            "operations": ["count", "max", "mean", "min", "sum"],
            "min_entities": 2,
            "one_metric_per_operand": True,
        },
        "stats": dict(sorted(_STATS.items())),
        "trace_path": str(output_dir / "multi_entity_metric_trace_v1.jsonl"),
        "answer_authority": "current_structured_table_decimal_replay",
        "complex_plans_forced": False,
        "research_candidate_only": True,
        "promotion_allowed": False,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    trace_path = output_dir / "multi_entity_metric_trace_v1.jsonl"
    trace_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in _TRACE),
        encoding="utf-8",
    )


def main() -> None:
    parser = BUILDER.configure_parser(argparse.ArgumentParser())
    parser.add_argument(
        "--typed-plans",
        type=Path,
        required=True,
        help="Frozen typed_operand_plans_v1.jsonl used only for the simple aggregation gate",
    )
    args = parser.parse_args()

    global _TYPED_PLANS
    _TYPED_PLANS = _load_typed_plans(args.typed_plans)
    BUILDER.multi_entity_metric_hint = _patched_metric_hint
    BUILDER.multi_entity_operation = _patched_operation
    BUILDER.multi_entity_program_answer = _patched_multi_entity_program_answer

    BUILDER.build(args)
    _write_variant_metadata(args.output, typed_plans_path=args.typed_plans)
    print(
        json.dumps(
            {
                "protocol": VARIANT_PROTOCOL,
                "output": str(args.output),
                "stats": dict(sorted(_STATS.items())),
                "trace_count": len(_TRACE),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
