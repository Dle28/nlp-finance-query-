"""Deterministic machine-supervised curriculum for financial-table retrieval.

This module deliberately creates questions from source-bound cells and a small
allow-list of arithmetic operations.  It is *not* a way to promote retrieved
tables to evidence: every emitted example is only training supervision and
keeps the complete table/cell lineage needed to replay its answer.

The curriculum does not read benchmark questions.  That separation is
important: a report corpus may be used to create synthetic training examples
only when the caller has established that doing so is permitted by the target
evaluation's rules.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping

from .execution import execute_ast, parse_decimal, validate_operation_ast
from .table_structure import normalize_space


SYNTHETIC_CURRICULUM_PROTOCOL = "synthetic_finance_curriculum_v1"
SYNTHETIC_CURRICULUM_SCHEMA_VERSION = 1
SYNTHETIC_PROVENANCE = "synthetic_execution_verified"

_TOKEN_RE = re.compile(r"[\wÀ-ỹ]+", re.UNICODE)
_STOP_TOKENS = {
    "và",
    "của",
    "các",
    "khoản",
    "tổng",
    "số",
    "theo",
    "cho",
    "đến",
    "trong",
    "năm",
    "vnd",
    "đồng",
}
_SCOPE_PHRASES = {
    "separate": "báo cáo tài chính riêng",
    "consolidated": "báo cáo tài chính hợp nhất",
    "aggregated": "báo cáo tài chính tổng hợp",
}


@dataclass(frozen=True, slots=True)
class NumericCell:
    """One source cell that can be replayed with :func:`parse_decimal`."""

    table_uid: str
    row_index: int
    column_index: int
    row_label: str
    column_label: str
    raw_value: str
    parsed_value: str


@dataclass(frozen=True, slots=True)
class CandidateIndex:
    """Metadata and lexical indexes used only to construct hard negatives."""

    assets_by_uid: dict[str, dict[str, Any]]
    by_document: dict[str, tuple[str, ...]]
    by_ticker_year: dict[tuple[str, int], tuple[str, ...]]
    by_ticker_scope: dict[tuple[str, str], tuple[str, ...]]
    by_year_scope: dict[tuple[int, str], tuple[str, ...]]
    token_to_uids: dict[str, tuple[str, ...]]
    tokens_by_uid: dict[str, frozenset[str]]
    all_uids: tuple[str, ...]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"Expected object at {path}:{line_number}")
            rows.append(row)
    return rows


def load_table_assets(path: Path) -> list[dict[str, Any]]:
    """Load only structurally usable source tables.

    The synthetic generator fails closed around report identity.  It does not
    infer a ticker, year or accounting scope from the question, because it
    intentionally never reads questions at all.
    """

    assets: list[dict[str, Any]] = []
    seen_uids: set[str] = set()
    for line_number, asset in enumerate(load_jsonl(path), start=1):
        uid = str(asset.get("internal_table_uid") or "")
        ticker = str(asset.get("ticker") or "").strip()
        scope = str(asset.get("scope") or "").casefold().strip()
        year = asset.get("report_year")
        if not uid or uid in seen_uids:
            continue
        if not ticker or scope not in _SCOPE_PHRASES:
            continue
        if isinstance(year, bool) or not isinstance(year, int):
            continue
        rows = asset.get("rows")
        headers = asset.get("headers") or asset.get("column_labels")
        if not isinstance(rows, list) or not isinstance(headers, list):
            continue
        if not any(isinstance(row, list) for row in rows):
            continue
        copied = dict(asset)
        copied["internal_table_uid"] = uid
        copied["ticker"] = ticker
        copied["scope"] = scope
        copied["report_year"] = year
        copied["headers"] = [str(value or "") for value in headers]
        copied["rows"] = [
            [str(value or "") for value in row] if isinstance(row, list) else []
            for row in rows
        ]
        assets.append(copied)
        seen_uids.add(uid)
    return sorted(assets, key=lambda row: str(row["internal_table_uid"]))


def _tokens(value: str) -> frozenset[str]:
    return frozenset(
        token
        for token in (item.casefold() for item in _TOKEN_RE.findall(value))
        if len(token) > 1 and token not in _STOP_TOKENS and not token.isdigit()
    )


def _clean_text(value: Any) -> str:
    return normalize_space(str(value or ""))


def _row_label(row: list[str]) -> str | None:
    """Pick the first non-numeric label before allowing a data cell."""

    for raw in row[:3]:
        label = _clean_text(raw)
        if len(label) < 2:
            continue
        parsed = parse_decimal(label)
        if parsed.value is None:
            return label
    return None


def _column_label(asset: Mapping[str, Any], column_index: int) -> str | None:
    headers = asset.get("headers") or asset.get("column_labels") or []
    if not isinstance(headers, list) or column_index >= len(headers):
        return None
    label = _clean_text(headers[column_index])
    if not label or label.casefold() in {"nhãn dòng", "row label", "label"}:
        return None
    return label


def extract_numeric_cells(asset: Mapping[str, Any], *, max_cells: int) -> list[NumericCell]:
    """Return high-confidence source values only.

    A value with any parser warning (for example an OCR-ambiguous decimal) is
    not suitable for synthetic supervision.  The raw value remains in every
    accepted binding so a later consumer can replay the parser decision.
    """

    uid = str(asset["internal_table_uid"])
    header_rows = {
        int(index)
        for index in asset.get("header_row_indices") or []
        if isinstance(index, int) and not isinstance(index, bool)
    }
    cells: list[NumericCell] = []
    for row_index, row in enumerate(asset.get("rows") or []):
        if row_index in header_rows or not isinstance(row, list):
            continue
        label = _row_label(row)
        if label is None:
            continue
        for column_index, raw_value in enumerate(row[1:], start=1):
            if len(cells) >= max_cells:
                return cells
            column_label = _column_label(asset, column_index)
            if column_label is None:
                continue
            raw = _clean_text(raw_value)
            parsed = parse_decimal(raw)
            if (
                parsed.value is None
                or parsed.confidence != 1.0
                or parsed.warnings
            ):
                continue
            cells.append(
                NumericCell(
                    table_uid=uid,
                    row_index=row_index,
                    column_index=column_index,
                    row_label=label,
                    column_label=column_label,
                    raw_value=raw,
                    parsed_value=parsed.value,
                )
            )
    return cells


def table_passage(asset: Mapping[str, Any]) -> str:
    """Render the same source table consistently for positive and negatives."""

    context_trace = asset.get("context_trace") or {}
    source_title = ""
    if isinstance(context_trace, Mapping):
        source_title = _clean_text(context_trace.get("source_title"))
    context = source_title or _clean_text(asset.get("context_before"))
    headers = " | ".join(
        _clean_text(value) for value in (asset.get("headers") or []) if _clean_text(value)
    )
    table_rows = []
    for row in (asset.get("rows") or [])[:40]:
        if isinstance(row, list):
            rendered = " | ".join(_clean_text(value) for value in row if _clean_text(value))
            if rendered:
                table_rows.append(rendered)
    payload = "\n".join(
        part
        for part in (
            f"ISSUER: {asset.get('ticker')}",
            f"REPORT_YEAR: {asset.get('report_year')}",
            f"REPORT_SCOPE: {asset.get('scope')}",
            f"DOCUMENT: {asset.get('document_id')}",
            f"SECTION: {context[:700]}",
            f"COLUMNS: {headers}",
            "TABLE:\n" + "\n".join(table_rows),
        )
        if part
    )
    return payload[:6000]


def build_candidate_index(assets: Iterable[Mapping[str, Any]]) -> CandidateIndex:
    assets_by_uid: dict[str, dict[str, Any]] = {}
    by_document: defaultdict[str, list[str]] = defaultdict(list)
    by_ticker_year: defaultdict[tuple[str, int], list[str]] = defaultdict(list)
    by_ticker_scope: defaultdict[tuple[str, str], list[str]] = defaultdict(list)
    by_year_scope: defaultdict[tuple[int, str], list[str]] = defaultdict(list)
    token_to_uids: defaultdict[str, set[str]] = defaultdict(set)
    tokens_by_uid: dict[str, frozenset[str]] = {}

    for raw_asset in assets:
        asset = dict(raw_asset)
        uid = str(asset["internal_table_uid"])
        ticker = str(asset["ticker"])
        year = int(asset["report_year"])
        scope = str(asset["scope"])
        document_id = str(asset.get("document_id") or "")
        assets_by_uid[uid] = asset
        by_document[document_id].append(uid)
        by_ticker_year[(ticker, year)].append(uid)
        by_ticker_scope[(ticker, scope)].append(uid)
        by_year_scope[(year, scope)].append(uid)

        labels = []
        for row in asset.get("rows") or []:
            if isinstance(row, list):
                label = _row_label(row)
                if label:
                    labels.append(label)
        token_set = frozenset().union(*(_tokens(label) for label in labels)) if labels else frozenset()
        tokens_by_uid[uid] = token_set
        for token in token_set:
            token_to_uids[token].add(uid)

    def freeze(mapping: Mapping[Any, Iterable[str]]) -> dict[Any, tuple[str, ...]]:
        return {key: tuple(sorted(value)) for key, value in mapping.items()}

    return CandidateIndex(
        assets_by_uid=assets_by_uid,
        by_document=freeze(by_document),
        by_ticker_year=freeze(by_ticker_year),
        by_ticker_scope=freeze(by_ticker_scope),
        by_year_scope=freeze(by_year_scope),
        token_to_uids=freeze(token_to_uids),
        tokens_by_uid=tokens_by_uid,
        all_uids=tuple(sorted(assets_by_uid)),
    )


def _rank_negative_pool(
    uids: Iterable[str],
    *,
    positive_uid: str,
    selected: set[str],
    reference_tokens: frozenset[str],
    index: CandidateIndex,
) -> list[str]:
    ranked: list[tuple[int, int, str]] = []
    for uid in set(uids):
        if uid == positive_uid or uid in selected or uid not in index.assets_by_uid:
            continue
        candidate_tokens = index.tokens_by_uid.get(uid, frozenset())
        overlap = len(reference_tokens.intersection(candidate_tokens))
        union = len(reference_tokens.union(candidate_tokens))
        jaccard_scaled = int((1000 * overlap / union)) if union else 0
        ranked.append((-overlap, -jaccard_scaled, uid))
    return [uid for _, _, uid in sorted(ranked)]


def _bounded_pool(values: Iterable[str], *, seed: str, limit: int = 96) -> tuple[str, ...]:
    """Take a deterministic, bounded sample without global per-example sorts.

    The full report corpus has more than one hundred thousand tables.  Ranking
    every background candidate for every synthetic question would make data
    generation quadratic.  Index buckets are already sorted once at build time;
    this stride sampler preserves deterministic coverage while bounding each
    per-example negative-ranking operation.
    """

    ordered = tuple(values)
    if len(ordered) <= limit:
        return ordered
    start = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16) % len(ordered)
    stride = max(1, len(ordered) // limit)
    return tuple(ordered[(start + index * stride) % len(ordered)] for index in range(limit))


def build_hard_negatives(
    asset: Mapping[str, Any],
    *,
    row_label: str,
    index: CandidateIndex,
    limit: int,
) -> list[dict[str, Any]]:
    """Build a diversified, metadata-aware negative set for one source table."""

    uid = str(asset["internal_table_uid"])
    ticker = str(asset["ticker"])
    year = int(asset["report_year"])
    scope = str(asset["scope"])
    document_id = str(asset.get("document_id") or "")
    reference_tokens = _tokens(row_label)
    selected: set[str] = set()
    result: list[dict[str, Any]] = []

    token_matches: list[str] = []
    token_limit = max(8, 64 // max(1, len(reference_tokens)))
    for token in sorted(reference_tokens):
        token_matches.extend(
            _bounded_pool(
                index.token_to_uids.get(token, ()),
                seed=f"{uid}:metric:{token}",
                limit=token_limit,
            )
        )

    pools: list[tuple[str, Iterable[str]]] = [
        (
            "wrong_scope",
            (
                candidate_uid
                for candidate_uid in index.by_ticker_year.get((ticker, year), ())
                if str(index.assets_by_uid[candidate_uid].get("scope")) != scope
            ),
        ),
        (
            "wrong_year",
            (
                candidate_uid
                for candidate_uid in index.by_ticker_scope.get((ticker, scope), ())
                if int(index.assets_by_uid[candidate_uid].get("report_year") or -1) != year
            ),
        ),
        ("same_document", index.by_document.get(document_id, ())),
        ("same_metric_different_context", tuple(dict.fromkeys(token_matches))),
        (
            "wrong_entity",
            (
                candidate_uid
                for candidate_uid in index.by_year_scope.get((year, scope), ())
                if str(index.assets_by_uid[candidate_uid].get("ticker")) != ticker
            ),
        ),
        (
            "background",
            _bounded_pool(index.all_uids, seed=f"{uid}:background", limit=96),
        ),
    ]

    def add_candidate(candidate_uid: str, negative_type: str) -> None:
        candidate = index.assets_by_uid[candidate_uid]
        selected.add(candidate_uid)
        result.append(
            {
                "internal_table_uid": candidate_uid,
                "negative_type": negative_type,
                "document_id": str(candidate.get("document_id") or ""),
                "ticker": str(candidate.get("ticker") or ""),
                "report_year": candidate.get("report_year"),
                "scope": str(candidate.get("scope") or ""),
            }
        )

    # First reserve one candidate for every available failure mode. Without
    # this pass a populous wrong-scope pool can consume the full quota and the
    # reranker never learns wrong-year/entity/document distinctions.
    bounded_pools = [
        (
            negative_type,
            _bounded_pool(pool, seed=f"{uid}:{negative_type}", limit=96),
        )
        for negative_type, pool in pools
    ]
    for negative_type, pool in bounded_pools:
        ranked = _rank_negative_pool(
            pool,
            positive_uid=uid,
            selected=selected,
            reference_tokens=reference_tokens,
            index=index,
        )
        if ranked:
            add_candidate(ranked[0], negative_type)
        if len(result) >= limit:
            return result

    # Then fill the remaining quota deterministically, retaining the same
    # priority order for the difficult source-near cases.
    for negative_type, pool in bounded_pools:
        for candidate_uid in _rank_negative_pool(
            pool,
            positive_uid=uid,
            selected=selected,
            reference_tokens=reference_tokens,
            index=index,
        ):
            add_candidate(candidate_uid, negative_type)
            if len(result) >= limit:
                return result
    return result


def _scope_phrase(scope: str) -> str:
    return _SCOPE_PHRASES[scope]


def _stable_id(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _binding(cell: NumericCell, operand_id: str) -> dict[str, Any]:
    return {
        "operand_id": operand_id,
        "internal_table_uid": cell.table_uid,
        "row_index": cell.row_index,
        "column_index": cell.column_index,
        "row_label": cell.row_label,
        "column_label": cell.column_label,
        "raw_value": cell.raw_value,
        "parsed_value": cell.parsed_value,
    }


def _execution_verification(
    ast: Mapping[str, Any], bindings: list[dict[str, Any]]
) -> dict[str, Any] | None:
    if validate_operation_ast(ast):
        return None
    values: dict[str, Decimal] = {}
    for binding in bindings:
        parsed = parse_decimal(binding["raw_value"])
        if parsed.value is None or parsed.value != binding["parsed_value"]:
            return None
        values[str(binding["operand_id"])] = Decimal(parsed.value)
    try:
        result = execute_ast(ast, values)
        replayed = execute_ast(ast, values)
    except (ArithmeticError, TypeError, ValueError, ZeroDivisionError):
        return None
    if not isinstance(result, Decimal) or not result.is_finite() or result != replayed:
        return None
    return {
        "status": "passed",
        "inputs": {key: format(value, "f") for key, value in sorted(values.items())},
        "answer_decimal": format(result, "f"),
        "replayed_answer_decimal": format(replayed, "f"),
        "errors": [],
    }


def _split_for_ticker(ticker: str, *, validation_percent: int, test_percent: int) -> str:
    bucket = int(hashlib.sha256(ticker.encode("utf-8")).hexdigest()[:8], 16) % 100
    if bucket < test_percent:
        return "test"
    if bucket < test_percent + validation_percent:
        return "validation"
    return "train"


def _build_example(
    *,
    asset: Mapping[str, Any],
    question: str,
    question_template: str,
    ast: Mapping[str, Any],
    bindings: list[dict[str, Any]],
    source_tables_sha256: str,
    hard_negatives: list[dict[str, Any]],
    validation_percent: int,
    test_percent: int,
) -> dict[str, Any] | None:
    verification = _execution_verification(ast, bindings)
    if verification is None:
        return None
    uid = str(asset["internal_table_uid"])
    ticker = str(asset["ticker"])
    source_identity = {
        "table_uid": uid,
        "row_indices": [binding["row_index"] for binding in bindings],
        "column_indices": [binding["column_index"] for binding in bindings],
        "question_template": question_template,
        "operation_ast": ast,
    }
    curriculum_id = _stable_id(source_identity)
    planner_target = {
        "ticker": ticker,
        "report_year": int(asset["report_year"]),
        "scope": str(asset["scope"]),
        "document_id": str(asset.get("document_id") or ""),
        "operation_ast": ast,
        "operands": [
            {
                "operand_id": binding["operand_id"],
                "metric": binding["row_label"],
                "column": binding["column_label"],
            }
            for binding in bindings
        ],
    }
    return {
        "schema_version": SYNTHETIC_CURRICULUM_SCHEMA_VERSION,
        "protocol": SYNTHETIC_CURRICULUM_PROTOCOL,
        "curriculum_id": curriculum_id,
        "annotation_status": SYNTHETIC_PROVENANCE,
        "provenance": SYNTHETIC_PROVENANCE,
        "label_source": "deterministic_synthetic_generator",
        "training_eligible": True,
        "split": _split_for_ticker(
            ticker,
            validation_percent=validation_percent,
            test_percent=test_percent,
        ),
        "question": question,
        "question_template": question_template,
        "positive_table_uids": [uid],
        "hard_negative_table_uids": [
            str(item["internal_table_uid"]) for item in hard_negatives
        ],
        "hard_negatives": hard_negatives,
        "positive_passage": table_passage(asset),
        "source_bindings": bindings,
        "planner_target": planner_target,
        "execution_verification": verification,
        "source_lineage": {
            "tables_sha256": source_tables_sha256,
            "internal_table_uid": uid,
            "document_id": str(asset.get("document_id") or ""),
            "ticker": ticker,
            "report_year": int(asset["report_year"]),
            "scope": str(asset["scope"]),
        },
        "source_contract": {
            "derived_from_question_text": False,
            "human_label_required": False,
            "may_promote_provenance": False,
            "submission_eligible": False,
            "training_eligible": True,
        },
    }


def build_synthetic_curriculum(
    assets: Iterable[Mapping[str, Any]],
    *,
    source_tables_sha256: str,
    max_examples: int,
    max_cells_per_table: int,
    hard_negatives_per_example: int,
    min_hard_negatives: int,
    validation_percent: int = 10,
    test_percent: int = 10,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Materialize deterministic lookup/difference/ratio training examples."""

    if max_examples < 1:
        raise ValueError("max_examples must be positive")
    if max_cells_per_table < 1:
        raise ValueError("max_cells_per_table must be positive")
    if not 0 <= validation_percent <= 100 or not 0 <= test_percent <= 100:
        raise ValueError("split percentages must be within 0..100")
    if validation_percent + test_percent >= 100:
        raise ValueError("validation_percent + test_percent must be below 100")
    if min_hard_negatives < 1 or hard_negatives_per_example < min_hard_negatives:
        raise ValueError("Hard-negative limits are inconsistent")

    canonical_assets = sorted(
        (dict(asset) for asset in assets), key=lambda row: str(row["internal_table_uid"])
    )
    index = build_candidate_index(canonical_assets)
    examples: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    template_counts: Counter[str] = Counter()
    negative_counts: Counter[str] = Counter()
    skipped_insufficient_negatives = 0

    def add_example(
        asset: Mapping[str, Any],
        *,
        question: str,
        question_template: str,
        ast: Mapping[str, Any],
        bindings: list[dict[str, Any]],
        row_label: str,
    ) -> bool:
        nonlocal skipped_insufficient_negatives
        hard_negatives = build_hard_negatives(
            asset,
            row_label=row_label,
            index=index,
            limit=hard_negatives_per_example,
        )
        if len(hard_negatives) < min_hard_negatives:
            skipped_insufficient_negatives += 1
            return False
        example = _build_example(
            asset=asset,
            question=question,
            question_template=question_template,
            ast=ast,
            bindings=bindings,
            source_tables_sha256=source_tables_sha256,
            hard_negatives=hard_negatives,
            validation_percent=validation_percent,
            test_percent=test_percent,
        )
        if example is None or example["curriculum_id"] in seen_ids:
            return False
        seen_ids.add(str(example["curriculum_id"]))
        examples.append(example)
        template_counts[question_template] += 1
        negative_counts.update(item["negative_type"] for item in hard_negatives)
        return True

    for asset in canonical_assets:
        if len(examples) >= max_examples:
            break
        cells = extract_numeric_cells(asset, max_cells=max_cells_per_table)
        if not cells:
            continue
        scope_phrase = _scope_phrase(str(asset["scope"]))
        ticker = str(asset["ticker"])
        year = int(asset["report_year"])

        cells_by_row: defaultdict[int, list[NumericCell]] = defaultdict(list)
        for cell in cells:
            cells_by_row[cell.row_index].append(cell)
            question = (
                f"Trong {scope_phrase} năm {year} của {ticker}, giá trị "
                f"'{cell.row_label}' tại cột '{cell.column_label}' là bao nhiêu?"
            )
            add_example(
                asset,
                question=question,
                question_template="direct_lookup_v1",
                ast={"op": "lookup", "args": ["x0"]},
                bindings=[_binding(cell, "x0")],
                row_label=cell.row_label,
            )
            if len(examples) >= max_examples:
                break
        if len(examples) >= max_examples:
            break

        for row_cells in cells_by_row.values():
            if len(row_cells) < 2 or len(examples) >= max_examples:
                continue
            old, new = row_cells[0], row_cells[1]
            difference_question = (
                f"Trong {scope_phrase} năm {year} của {ticker}, chênh lệch của "
                f"'{new.row_label}' tại cột '{new.column_label}' so với cột "
                f"'{old.column_label}' là bao nhiêu?"
            )
            add_example(
                asset,
                question=difference_question,
                question_template="same_row_difference_v1",
                ast={"op": "subtract", "args": ["x1", "x0"]},
                bindings=[_binding(old, "x0"), _binding(new, "x1")],
                row_label=new.row_label,
            )
            if (
                Decimal(old.parsed_value) == 0
                or Decimal(old.parsed_value) == Decimal(new.parsed_value)
                or len(examples) >= max_examples
            ):
                continue
            ratio_question = (
                f"Trong {scope_phrase} năm {year} của {ticker}, tỷ số giá trị "
                f"'{new.row_label}' tại cột '{new.column_label}' so với cột "
                f"'{old.column_label}' là bao nhiêu?"
            )
            add_example(
                asset,
                question=ratio_question,
                question_template="same_row_ratio_v1",
                ast={"op": "divide", "args": ["x1", "x0"]},
                bindings=[_binding(old, "x0"), _binding(new, "x1")],
                row_label=new.row_label,
            )

    examples.sort(key=lambda row: str(row["curriculum_id"]))
    split_counts = Counter(str(row["split"]) for row in examples)
    return examples, {
        "asset_count": len(canonical_assets),
        "example_count": len(examples),
        "template_counts": dict(sorted(template_counts.items())),
        "hard_negative_type_counts": dict(sorted(negative_counts.items())),
        "split_counts": dict(sorted(split_counts.items())),
        "skipped_insufficient_hard_negatives": skipped_insufficient_negatives,
    }


def verify_synthetic_example(example: Mapping[str, Any]) -> list[str]:
    """Replay an example without access to its full table file."""

    errors: list[str] = []
    if example.get("protocol") != SYNTHETIC_CURRICULUM_PROTOCOL:
        errors.append("unexpected_protocol")
    if example.get("provenance") != SYNTHETIC_PROVENANCE:
        errors.append("unexpected_provenance")
    if not bool(example.get("training_eligible")):
        errors.append("not_training_eligible")
    positives = example.get("positive_table_uids")
    negatives = example.get("hard_negative_table_uids")
    if not isinstance(positives, list) or len(positives) != 1 or not positives[0]:
        errors.append("invalid_positive_table_uid")
    if not isinstance(negatives, list) or not negatives:
        errors.append("missing_hard_negatives")
    elif set(str(value) for value in negatives).intersection(str(value) for value in positives or []):
        errors.append("positive_negative_overlap")
    ast = example.get("planner_target", {}).get("operation_ast")
    if not isinstance(ast, Mapping):
        errors.append("missing_operation_ast")
        return sorted(set(errors))
    errors.extend(validate_operation_ast(ast))
    bindings = example.get("source_bindings")
    if not isinstance(bindings, list) or not bindings:
        errors.append("missing_source_bindings")
        return sorted(set(errors))
    values: dict[str, Decimal] = {}
    for binding in bindings:
        if not isinstance(binding, Mapping):
            errors.append("invalid_source_binding")
            continue
        operand_id = str(binding.get("operand_id") or "")
        parsed = parse_decimal(binding.get("raw_value"))
        if not operand_id or parsed.value is None or parsed.value != binding.get("parsed_value"):
            errors.append("source_binding_not_replayable")
            continue
        values[operand_id] = Decimal(parsed.value)
    verification = example.get("execution_verification") or {}
    try:
        result = execute_ast(ast, values)
    except (ArithmeticError, TypeError, ValueError, ZeroDivisionError):
        errors.append("execution_replay_failed")
        return sorted(set(errors))
    expected = verification.get("answer_decimal")
    if verification.get("status") != "passed" or not isinstance(result, Decimal):
        errors.append("execution_not_marked_passed")
    elif expected != format(result, "f"):
        errors.append("execution_answer_mismatch")
    lineage = example.get("source_lineage") or {}
    source_hash = lineage.get("tables_sha256") if isinstance(lineage, Mapping) else None
    if not isinstance(source_hash, str) or len(source_hash) != 64:
        errors.append("missing_source_tables_sha256")
    return sorted(set(errors))


def manifest_payload(
    *,
    tables_path: Path,
    tables_sha256: str,
    examples_path: Path,
    examples_sha256: str,
    generation_config: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": SYNTHETIC_CURRICULUM_SCHEMA_VERSION,
        "protocol": SYNTHETIC_CURRICULUM_PROTOCOL,
        "provenance": SYNTHETIC_PROVENANCE,
        "input": {
            "tables_path": str(tables_path),
            "tables_sha256": tables_sha256,
        },
        "output": {
            "examples_path": str(examples_path),
            "examples_sha256": examples_sha256,
        },
        "generation_config": dict(generation_config),
        "summary": dict(summary),
        "source_contract": {
            "derived_from_question_text": False,
            "human_label_required": False,
            "may_promote_provenance": False,
            "submission_eligible": False,
            "training_eligible": True,
        },
    }
