"""Corpus-wide, answer-ineligible study of the ViFinQA source documents."""

from __future__ import annotations

from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from statistics import mean, median
from typing import Any, Iterable, Mapping


PROTOCOL = "vifinqa_document_corpus_study_v1"
UNIT_RE = re.compile(
    r"(?:đơn\s+vị(?:\s+tính)?\s*[:：]?\s*(?:vnd|vnđ|đồng|"
    r"nghìn\s+đồng|triệu\s+đồng|tỷ\s+đồng|usd|eur|%|phần\s+trăm))|"
    r"(?:\b(?:nghìn\s+tỷ|nghìn|ngàn|triệu|tỷ)\s*(?:đồng|vnd)\b|"
    r"\b(?:vnd|vnđ|usd|eur)\b|%|\bphần\s+trăm\b)",
    re.IGNORECASE,
)
CORE_RE = re.compile(
    r"bảng\s+cân\s+đối|tình\s+hình\s+tài\s+chính|"
    r"kết\s+quả\s+(?:hoạt\s+động\s+)?kinh\s+doanh|"
    r"lưu\s+chuyển\s+tiền",
    re.IGNORECASE,
)
NUMBER_RE = re.compile(r"(?:\(?-?\d[\d.,\s]*\)?|\d+(?:[.,]\d+)?%)")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as file:
        for line in file:
            if line.strip():
                yield json.loads(line)


def load_documents(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    for row in rows:
        for key in (
            "file_size_bytes",
            "character_count",
            "utf8_byte_count",
            "page_count",
            "table_count",
        ):
            row[key] = int(row[key])
        row["year"] = int(row["year"])
    return rows


def quantile(values: list[int | float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)


def describe(values: list[int | float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "min": None, "median": None, "mean": None, "p95": None, "max": None}
    return {
        "count": len(values),
        "min": min(values),
        "median": median(values),
        "mean": mean(values),
        "p95": quantile(values, 0.95),
        "max": max(values),
    }


def freeze_ticker_split(tickers: Iterable[str], seed: str) -> dict[str, list[str]]:
    ordered = sorted(
        set(tickers),
        key=lambda ticker: hashlib.sha256(f"{seed}\n{ticker}".encode()).hexdigest(),
    )
    if len(ordered) != 100:
        raise ValueError(f"expected 100 tickers, found {len(ordered)}")
    return {
        "discovery": ordered[:20],
        "development": ordered[20:50],
        "untouched_evaluation": ordered[50:],
    }


def stage_lookup(split: Mapping[str, list[str]]) -> dict[str, str]:
    return {ticker: stage for stage, tickers in split.items() for ticker in tickers}


def document_kind(document_id: str, scope: str) -> str:
    lowered = document_id.casefold()
    if scope in {"consolidated", "separate"}:
        return scope
    if "aggregated" in lowered:
        return "aggregated"
    if "explanation" in lowered or "thuyet_minh" in lowered:
        return "explanation_fragment"
    if re.search(r"_[12]$", lowered):
        return "numbered_fragment"
    return "scope_unspecified"


def _path_consistent(row: Mapping[str, Any]) -> bool:
    path = Path(str(row["source_path"]))
    parts = path.parts
    try:
        ticker_index = parts.index(str(row["ticker"]))
    except ValueError:
        return False
    expected_year = str(row["year"])
    return (
        ticker_index + 2 < len(parts)
        and parts[ticker_index + 1] == expected_year
        and str(row["document_id"]) in {path.parent.name, path.stem.removesuffix("_extracted")}
    )


def document_inventory(
    documents: list[dict[str, Any]], split: Mapping[str, list[str]]
) -> tuple[dict[str, Any], dict[tuple[str, int, str], list[str]], dict[tuple[str, int], list[dict[str, Any]]]]:
    stages = stage_lookup(split)
    document_ids = [str(row["document_id"]) for row in documents]
    exact_keys: defaultdict[tuple[str, int, str], list[str]] = defaultdict(list)
    slots: defaultdict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    kinds = Counter()
    by_stage: dict[str, Counter[str]] = defaultdict(Counter)
    for row in documents:
        ticker = str(row["ticker"])
        year = int(row["year"])
        scope = str(row["scope"])
        stage = stages[ticker]
        kind = document_kind(str(row["document_id"]), scope)
        kinds[kind] += 1
        by_stage[stage]["documents"] += 1
        by_stage[stage][f"kind::{kind}"] += 1
        by_stage[stage]["pages"] += int(row["page_count"])
        by_stage[stage]["tables"] += int(row["table_count"])
        if scope in {"consolidated", "separate"}:
            exact_keys[(ticker, year, scope)].append(str(row["document_id"]))
        slots[(ticker, year)].append(row)

    slot_types = Counter()
    for rows in slots.values():
        scopes = {str(row["scope"]) for row in rows}
        if {"consolidated", "separate"}.issubset(scopes):
            slot_types["both_known_scopes"] += 1
        elif "consolidated" in scopes:
            slot_types["consolidated_only"] += 1
        elif "separate" in scopes:
            slot_types["separate_only"] += 1
        else:
            slot_types["unknown_or_special_only"] += 1

    stage_summary = {}
    for stage, counts in by_stage.items():
        docs = counts["documents"]
        stage_summary[stage] = {
            "document_count": docs,
            "page_count": counts["pages"],
            "table_count": counts["tables"],
            "mean_pages_per_document": counts["pages"] / docs,
            "mean_tables_per_document": counts["tables"] / docs,
            "kind_counts": {
                key.removeprefix("kind::"): value
                for key, value in counts.items()
                if key.startswith("kind::")
            },
        }
    summary = {
        "document_count": len(documents),
        "ticker_count": len({str(row["ticker"]) for row in documents}),
        "year_count": len({int(row["year"]) for row in documents}),
        "years": sorted({int(row["year"]) for row in documents}),
        "path_consistent_count": sum(_path_consistent(row) for row in documents),
        "path_consistency_rate": sum(_path_consistent(row) for row in documents) / len(documents),
        "unique_document_id_count": len(set(document_ids)),
        "duplicate_document_id_count": len(document_ids) - len(set(document_ids)),
        "known_scope_key_count": len(exact_keys),
        "known_scope_key_collision_count": sum(len(values) > 1 for values in exact_keys.values()),
        "kind_counts": dict(sorted(kinds.items())),
        "slot_count": len(slots),
        "slot_type_counts": dict(sorted(slot_types.items())),
        "zero_table_document_count": sum(int(row["table_count"]) == 0 for row in documents),
        "pages_per_document": describe([int(row["page_count"]) for row in documents]),
        "tables_per_document": describe([int(row["table_count"]) for row in documents]),
        "by_stage": stage_summary,
    }
    return summary, dict(exact_keys), dict(slots)


def scan_raw_table_inventory(
    path: Path, split: Mapping[str, list[str]]
) -> dict[str, Any]:
    """Scan the complete HTML-table inventory without assuming asset coverage."""
    stages = stage_lookup(split)
    count = 0
    hashes: defaultdict[str, list[tuple[str, str, int, str]]] = defaultdict(list)
    by_stage: dict[str, Counter[str]] = defaultdict(Counter)
    lengths: dict[str, list[int]] = defaultdict(list)
    pages: dict[str, list[int]] = defaultdict(list)
    with path.open(encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            ticker = str(row["ticker"])
            stage = stages[ticker]
            count += 1
            by_stage[stage]["tables"] += 1
            lengths[stage].append(int(row["html_length_chars"]))
            if row.get("page_no") not in {None, "", "None"}:
                pages[stage].append(int(row["page_no"]))
            hashes[str(row["table_sha1"])].append(
                (
                    str(row["document_id"]),
                    ticker,
                    int(row["year"]),
                    str(row["scope"]),
                )
            )
    duplicate_groups = 0
    cross_document_groups = 0
    duplicate_instances = 0
    adjacent_year_groups = 0
    for locations in hashes.values():
        if len(locations) < 2:
            continue
        duplicate_groups += 1
        duplicate_instances += len(locations)
        if len({item[0] for item in locations}) > 1:
            cross_document_groups += 1
        ticker_scope_years: defaultdict[tuple[str, str], set[int]] = defaultdict(set)
        for _document, ticker, year, scope in locations:
            ticker_scope_years[(ticker, scope)].add(year)
        if any(any(year + 1 in years for year in years) for years in ticker_scope_years.values()):
            adjacent_year_groups += 1
    return {
        "raw_table_count": count,
        "unique_table_hash_count": len(hashes),
        "duplicate_table_hash_group_count": duplicate_groups,
        "cross_document_duplicate_group_count": cross_document_groups,
        "duplicate_table_instance_count": duplicate_instances,
        "adjacent_year_duplicate_group_count": adjacent_year_groups,
        "by_stage": {
            stage: {
                "table_count": by_stage[stage]["tables"],
                "table_length_chars": describe(lengths[stage]),
                "page_no": describe(pages[stage]),
            }
            for stage in ("discovery", "development", "untouched_evaluation")
        },
    }


def diagnose_asset_coverage(
    documents: list[Mapping[str, Any]],
    asset_document_counts: Mapping[str, int],
    *,
    dense_meta_path: Path,
    lexical_index_path: Path,
    asset_count: int,
) -> dict[str, Any]:
    document_ids = [str(row["document_id"]) for row in documents]
    asset_document_ids = list(asset_document_counts)
    prefix_match = asset_document_ids == document_ids[: len(asset_document_ids)]
    dense_meta = json.loads(dense_meta_path.read_text(encoding="utf-8"))
    with sqlite3.connect(lexical_index_path) as connection:
        lexical_asset_count = int(connection.execute("SELECT count(*) FROM assets").fetchone()[0])
        lexical_fts_count = int(connection.execute("SELECT count(*) FROM assets_fts").fetchone()[0])
    next_missing = (
        document_ids[len(asset_document_ids)]
        if len(asset_document_ids) < len(document_ids)
        else None
    )
    return {
        "asset_documents_form_exact_sorted_prefix": prefix_match,
        "indexed_document_count": len(asset_document_ids),
        "total_document_count": len(document_ids),
        "last_indexed_document_id": asset_document_ids[-1] if asset_document_ids else None,
        "next_missing_document_id": next_missing,
        "table_asset_count": asset_count,
        "dense_index_count": int(dense_meta["count"]),
        "lexical_asset_count": lexical_asset_count,
        "lexical_fts_count": lexical_fts_count,
        "all_retrieval_indexes_aligned_to_partial_assets": (
            asset_count == int(dense_meta["count"]) == lexical_asset_count == lexical_fts_count
        ),
        "interpretation": (
            "The current asset file is an exact leading prefix of the sorted report list, "
            "which is consistent with an incomplete build rather than a balanced corpus sample."
            if prefix_match and len(asset_document_ids) < len(document_ids)
            else "No exact-prefix truncation pattern detected."
        ),
    }


def _cells(asset: Mapping[str, Any]) -> list[str]:
    return [str(cell) for row in asset.get("rows") or [] for cell in row]


def _table_features(asset: Mapping[str, Any]) -> dict[str, Any]:
    rows = list(asset.get("rows") or [])
    cells = _cells(asset)
    numeric_cells = sum(bool(NUMBER_RE.search(cell)) for cell in cells)
    table_text = " ".join(cells)
    header_text = " ".join(str(value) for value in asset.get("headers") or [])
    context = str(asset.get("context_before") or "")
    widths = [len(row) for row in rows if row]
    ticker_year = int(asset.get("report_year") or 0)
    numeric_usable = len(rows) >= 2 and numeric_cells >= 2
    current_year = re.search(rf"(?<!\d){ticker_year}(?!\d)", header_text + " " + table_text) is not None
    prior_year = re.search(rf"(?<!\d){ticker_year - 1}(?!\d)", header_text + " " + table_text) is not None
    return {
        "row_count": len(rows),
        "max_columns": max(widths, default=0),
        "numeric_cell_count": numeric_cells,
        "numeric_usable": numeric_usable,
        "table_unit": UNIT_RE.search(header_text + " " + table_text) is not None,
        "context_unit": UNIT_RE.search(context) is not None,
        "current_and_prior_year": numeric_usable and current_year and prior_year,
        "replacement_character": "�" in (context + table_text + header_text),
        "irregular_row_width": len(set(widths)) > 1,
        "core_statement": CORE_RE.search(context + " " + " ".join(cells[:30])) is not None,
    }


def scan_table_assets(
    path: Path,
    split: Mapping[str, list[str]],
    *,
    seed: str,
    sample_per_stage: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, int]]:
    stages = stage_lookup(split)
    counters: dict[str, Counter[str]] = defaultdict(Counter)
    distributions: dict[str, defaultdict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    uid_count = 0
    uids: set[str] = set()
    asset_tickers: set[str] = set()
    year_counts: Counter[int] = Counter()
    scope_counts: Counter[str] = Counter()
    document_asset_counts: Counter[str] = Counter()
    source_hashes: defaultdict[str, set[str]] = defaultdict(set)
    table_hash_locations: defaultdict[str, list[tuple[str, str, int, str]]] = defaultdict(list)
    samples: defaultdict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)

    for asset in load_jsonl(path):
        ticker = str(asset["ticker"])
        asset_tickers.add(ticker)
        year_counts[int(asset["report_year"])] += 1
        scope_counts[str(asset["scope"])] += 1
        stage = stages[ticker]
        features = _table_features(asset)
        uid = str(asset["internal_table_uid"])
        uid_count += 1
        uids.add(uid)
        document_id = str(asset["document_id"])
        document_asset_counts[document_id] += 1
        source_hashes[document_id].add(str(asset["source_sha256"]))
        table_hash_locations[str(asset["table_sha256"])].append(
            (document_id, ticker, int(asset["report_year"]), str(asset["scope"]))
        )
        counters[stage]["tables"] += 1
        for name in (
            "numeric_usable",
            "table_unit",
            "context_unit",
            "current_and_prior_year",
            "replacement_character",
            "irregular_row_width",
            "core_statement",
        ):
            counters[stage][name] += int(features[name])
        counters[stage]["unit_combined"] += int(features["table_unit"] or features["context_unit"])
        if features["core_statement"]:
            counters[stage]["core_page_20_or_less"] += int(int(asset.get("page_no") or 9999) <= 20)
        for name in ("row_count", "max_columns"):
            distributions[stage][name].append(int(features[name]))
        distributions[stage]["page_no"].append(int(asset.get("page_no") or 0))
        distributions[stage]["table_length_chars"].append(
            int(asset.get("char_end") or 0) - int(asset.get("char_start") or 0)
        )
        selection_hash = hashlib.sha256(f"{seed}\n{uid}".encode()).hexdigest()
        samples[stage].append(
            (
                selection_hash,
                {
                    "stage": stage,
                    "internal_table_uid": uid,
                    "document_id": document_id,
                    "ticker": ticker,
                    "report_year": int(asset["report_year"]),
                    "scope": str(asset["scope"]),
                    "source_path": str(asset["source_path"]),
                    "source_sha256": str(asset["source_sha256"]),
                    "table_sha256": str(asset["table_sha256"]),
                    "char_start": int(asset["char_start"]),
                    "char_end": int(asset["char_end"]),
                    "question_materialized": False,
                },
            )
        )

    duplicate_groups = 0
    cross_document_groups = 0
    duplicate_table_instances = 0
    adjacent_year_groups = 0
    for locations in table_hash_locations.values():
        if len(locations) < 2:
            continue
        duplicate_groups += 1
        duplicate_table_instances += len(locations)
        documents = {item[0] for item in locations}
        if len(documents) > 1:
            cross_document_groups += 1
        ticker_scope_years: defaultdict[tuple[str, str], set[int]] = defaultdict(set)
        for _document, ticker, year, scope in locations:
            ticker_scope_years[(ticker, scope)].add(year)
        if any(any(year + 1 in years for year in years) for years in ticker_scope_years.values()):
            adjacent_year_groups += 1

    by_stage = {}
    for stage in ("discovery", "development", "untouched_evaluation"):
        count = counters[stage]["tables"]
        numeric = counters[stage]["numeric_usable"]
        core = counters[stage]["core_statement"]
        by_stage[stage] = {
            "table_count": count,
            "numeric_usable_count": numeric,
            "numeric_usable_rate": numeric / count,
            "non_numeric_rate": 1 - numeric / count,
            "table_unit_rate": counters[stage]["table_unit"] / count,
            "context_unit_rate": counters[stage]["context_unit"] / count,
            "combined_unit_rate": counters[stage]["unit_combined"] / count,
            "context_unit_lift_over_table_only": (
                counters[stage]["unit_combined"] - counters[stage]["table_unit"]
            ) / count,
            "current_and_prior_year_rate_among_numeric": (
                counters[stage]["current_and_prior_year"] / numeric if numeric else 0
            ),
            "replacement_character_rate": counters[stage]["replacement_character"] / count,
            "irregular_row_width_rate": counters[stage]["irregular_row_width"] / count,
            "core_statement_table_count": core,
            "core_page_20_or_less_rate": counters[stage]["core_page_20_or_less"] / core if core else 0,
            "row_count": describe(distributions[stage]["row_count"]),
            "max_columns": describe(distributions[stage]["max_columns"]),
            "page_no": describe(distributions[stage]["page_no"]),
            "table_length_chars": describe(distributions[stage]["table_length_chars"]),
        }
    selected_samples = [
        row
        for stage in ("discovery", "development", "untouched_evaluation")
        for _score, row in sorted(samples[stage], key=lambda item: item[0])[:sample_per_stage]
    ]
    summary = {
        "asset_count": uid_count,
        "asset_ticker_count": len(asset_tickers),
        "asset_tickers": sorted(asset_tickers),
        "asset_year_counts": {str(key): value for key, value in sorted(year_counts.items())},
        "asset_scope_counts": dict(sorted(scope_counts.items())),
        "unique_uid_count": len(uids),
        "duplicate_uid_count": uid_count - len(uids),
        "document_count_with_assets": len(document_asset_counts),
        "document_source_hash_conflict_count": sum(len(values) != 1 for values in source_hashes.values()),
        "duplicate_table_hash_group_count": duplicate_groups,
        "cross_document_duplicate_group_count": cross_document_groups,
        "duplicate_table_instance_count": duplicate_table_instances,
        "adjacent_year_duplicate_group_count": adjacent_year_groups,
        "by_stage": by_stage,
    }
    return summary, selected_samples, dict(document_asset_counts)


def verify_offsets(samples: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_cache: dict[str, str] = {}
    source_hash_cache: dict[str, str] = {}
    rows = []
    by_stage: dict[str, Counter[str]] = defaultdict(Counter)
    for sample in samples:
        source_path = str(sample["source_path"])
        if source_path not in source_cache:
            path = Path(source_path)
            source_cache[source_path] = path.read_text(encoding="utf-8", errors="replace")
            source_hash_cache[source_path] = hashlib.sha256(path.read_bytes()).hexdigest()
        text = source_cache[source_path]
        fragment = text[int(sample["char_start"]): int(sample["char_end"])]
        table_match = hashlib.sha256(fragment.encode("utf-8")).hexdigest() == sample["table_sha256"]
        source_match = source_hash_cache[source_path] == sample["source_sha256"]
        stage = str(sample["stage"])
        by_stage[stage]["sample_count"] += 1
        by_stage[stage]["table_hash_match"] += int(table_match)
        by_stage[stage]["source_hash_match"] += int(source_match)
        rows.append(
            {
                **sample,
                "source_hash_match": source_match,
                "table_hash_match": table_match,
                "verified": source_match and table_match,
            }
        )
    summary = {
        "sample_count": len(rows),
        "verified_count": sum(row["verified"] for row in rows),
        "match_rate": sum(row["verified"] for row in rows) / len(rows),
        "by_stage": {
            stage: {
                "sample_count": counts["sample_count"],
                "source_hash_match_count": counts["source_hash_match"],
                "table_hash_match_count": counts["table_hash_match"],
                "verified_rate": counts["table_hash_match"] / counts["sample_count"],
            }
            for stage, counts in sorted(by_stage.items())
        },
        "question_materialized": False,
    }
    return rows, summary


def question_document_routes(
    question_path: Path,
    exact_keys: Mapping[tuple[str, int, str], list[str]],
    slots: Mapping[tuple[str, int], list[Mapping[str, Any]]],
) -> dict[str, Any]:
    counts = Counter()
    by_family: dict[str, Counter[str]] = defaultdict(Counter)
    for record in load_jsonl(question_path):
        taxonomy = record["taxonomy"]
        entities = list(taxonomy.get("entities_resolved") or [])
        years = [int(value) for value in taxonomy.get("years_mentioned") or []]
        scopes = list(taxonomy.get("reporting_scopes") or [])
        family = str(taxonomy.get("question_type") or "unknown")
        counts["questions"] += 1
        by_family[family]["questions"] += 1
        if not entities or not years:
            counts["missing_route_keys"] += 1
            by_family[family]["missing_route_keys"] += 1
            continue
        requirement_sizes = []
        for ticker in entities:
            for year in years:
                if scopes:
                    candidates = sorted(
                        {
                            document_id
                            for scope in scopes
                            for document_id in exact_keys.get((ticker, year, scope), [])
                        }
                    )
                else:
                    candidates = sorted(
                        str(row["document_id"]) for row in slots.get((ticker, year), [])
                    )
                requirement_sizes.append(len(candidates))
        if requirement_sizes and all(size >= 1 for size in requirement_sizes):
            counts["all_requirements_covered"] += 1
            by_family[family]["all_requirements_covered"] += 1
        if requirement_sizes and all(size == 1 for size in requirement_sizes):
            counts["all_requirements_unique"] += 1
            by_family[family]["all_requirements_unique"] += 1
        if any(size > 1 for size in requirement_sizes):
            counts["has_document_ambiguity"] += 1
            by_family[family]["has_document_ambiguity"] += 1
        if any(size == 0 for size in requirement_sizes):
            counts["has_missing_document"] += 1
            by_family[family]["has_missing_document"] += 1
    evaluable = counts["questions"] - counts["missing_route_keys"]
    return {
        "question_count": counts["questions"],
        "evaluable_route_question_count": evaluable,
        "missing_route_key_count": counts["missing_route_keys"],
        "covered_question_count": counts["all_requirements_covered"],
        "covered_rate_on_evaluable": counts["all_requirements_covered"] / evaluable if evaluable else 0,
        "unique_route_question_count": counts["all_requirements_unique"],
        "unique_route_rate_on_evaluable": counts["all_requirements_unique"] / evaluable if evaluable else 0,
        "ambiguous_document_question_count": counts["has_document_ambiguity"],
        "missing_document_question_count": counts["has_missing_document"],
        "by_question_family": {
            family: dict(sorted(values.items())) for family, values in sorted(by_family.items())
        },
        "actual_table_relevance_proven": False,
    }


def _verdict(supported: bool, positive: str = "KEEP", negative: str = "REJECT") -> str:
    return positive if supported else negative


def evaluate_hypotheses(
    *,
    document: Mapping[str, Any],
    tables: Mapping[str, Any],
    raw_tables: Mapping[str, Any],
    coverage_diagnosis: Mapping[str, Any],
    offsets: Mapping[str, Any],
    routes: Mapping[str, Any],
    audit_table_count: int,
    asset_document_counts: Mapping[str, int],
    documents: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    development = tables["by_stage"]["development"]
    untouched = tables["by_stage"]["untouched_evaluation"]
    slot_counts = document["slot_type_counts"]
    non_pair = sum(value for key, value in slot_counts.items() if key != "both_known_scopes")
    asset_count_match = int(tables["asset_count"]) == int(audit_table_count)
    per_doc_count_match = all(
        int(row["table_count"]) == int(asset_document_counts.get(str(row["document_id"]), 0))
        for row in documents
    )
    early = [row for row in documents if int(row["year"]) <= 2021]
    late = [row for row in documents if int(row["year"]) >= 2022]
    early_tables = mean([int(row["table_count"]) for row in early])
    late_tables = mean([int(row["table_count"]) for row in late])
    early_pages = mean([int(row["page_count"]) for row in early])
    late_pages = mean([int(row["page_count"]) for row in late])
    drift = max(abs(late_tables / early_tables - 1), abs(late_pages / early_pages - 1))
    hypotheses = [
        ("H01_METADATA_KEYS", document["path_consistency_rate"] == 1 and document["duplicate_document_id_count"] == 0, "path consistency và document_id uniqueness", {"path_consistency_rate": document["path_consistency_rate"], "duplicate_document_id_count": document["duplicate_document_id_count"]}),
        ("H02_SCOPE_IS_NOT_BINARY", document["kind_counts"].get("scope_unspecified", 0) + document["kind_counts"].get("aggregated", 0) + document["kind_counts"].get("numbered_fragment", 0) + document["kind_counts"].get("explanation_fragment", 0) > 0, "số tài liệu ngoài hai scope chuẩn", {"kind_counts": document["kind_counts"]}),
        ("H03_SCOPE_PAIR_GAPS", non_pair > 0, "slot công ty-năm không đủ hai scope", {"slot_type_counts": slot_counts, "non_pair_slot_count": non_pair}),
        ("H04_QUESTION_DOCUMENT_ROUTE", routes["covered_rate_on_evaluable"] >= 0.9, "coverage nhóm tài liệu theo khóa câu hỏi", {"covered_rate_on_evaluable": routes["covered_rate_on_evaluable"], "unique_route_rate_on_evaluable": routes["unique_route_rate_on_evaluable"], "actual_table_relevance_proven": False}),
        ("H05_EXACT_SOURCE_OFFSETS", offsets["match_rate"] == 1, "hash nguồn và đoạn bảng trên 300 mẫu", offsets),
        ("H06_ASSET_COMPLETENESS", asset_count_match and per_doc_count_match and tables["duplicate_uid_count"] == 0, "asset count, company/document coverage, index alignment và UID", {"raw_table_count": raw_tables["raw_table_count"], "asset_count": tables["asset_count"], "asset_coverage_rate": tables["asset_count"] / raw_tables["raw_table_count"], "asset_ticker_count": tables["asset_ticker_count"], "asset_document_count": tables["document_count_with_assets"], "asset_count_match": asset_count_match, "per_document_count_match": per_doc_count_match, "duplicate_uid_count": tables["duplicate_uid_count"], "exact_sorted_prefix": coverage_diagnosis["asset_documents_form_exact_sorted_prefix"], "dense_index_count": coverage_diagnosis["dense_index_count"], "lexical_index_count": coverage_diagnosis["lexical_asset_count"]}),
        ("H07_NUMERIC_PREFILTER", development["non_numeric_rate"] >= 0.05 and untouched["non_numeric_rate"] >= 0.05, "tỉ lệ bảng không đủ tín hiệu số", {"development": development["non_numeric_rate"], "untouched": untouched["non_numeric_rate"]}),
        ("H08_CONTEXT_FOR_UNIT", development["context_unit_lift_over_table_only"] >= 0.10 and untouched["context_unit_lift_over_table_only"] >= 0, "mức tăng phát hiện đơn vị khi thêm context", {"development_lift": development["context_unit_lift_over_table_only"], "untouched_lift": untouched["context_unit_lift_over_table_only"], "development_combined_rate": development["combined_unit_rate"], "untouched_combined_rate": untouched["combined_unit_rate"]}),
        ("H09_COMPARATIVE_YEAR_COLUMNS", development["current_and_prior_year_rate_among_numeric"] >= 0.4 and untouched["current_and_prior_year_rate_among_numeric"] >= 0.4, "tỉ lệ bảng số có năm hiện tại và năm trước", {"development": development["current_and_prior_year_rate_among_numeric"], "untouched": untouched["current_and_prior_year_rate_among_numeric"]}),
        ("H10_DUPLICATE_TABLES", raw_tables["cross_document_duplicate_group_count"] > 0, "duplicate table hash xuyên tài liệu", {"duplicate_groups": raw_tables["duplicate_table_hash_group_count"], "cross_document_groups": raw_tables["cross_document_duplicate_group_count"], "adjacent_year_groups": raw_tables["adjacent_year_duplicate_group_count"]}),
        ("H11_OCR_QUALITY_GATE", (development["replacement_character_rate"] > 0 or development["irregular_row_width_rate"] > 0) and (untouched["replacement_character_rate"] > 0 or untouched["irregular_row_width_rate"] > 0), "ký tự lỗi hoặc dòng lệch cột", {"development_replacement_rate": development["replacement_character_rate"], "untouched_replacement_rate": untouched["replacement_character_rate"], "development_irregular_rate": development["irregular_row_width_rate"], "untouched_irregular_rate": untouched["irregular_row_width_rate"]}),
        ("H12_TABLE_SIZE_HETEROGENEITY", development["row_count"]["p95"] / max(1, development["row_count"]["median"]) >= 3 and untouched["row_count"]["p95"] / max(1, untouched["row_count"]["median"]) >= 3, "tỉ lệ P95/median số dòng", {"development_ratio": development["row_count"]["p95"] / max(1, development["row_count"]["median"]), "untouched_ratio": untouched["row_count"]["p95"] / max(1, untouched["row_count"]["median"])}),
        ("H13_PAGE_PRIOR", development["core_page_20_or_less_rate"] >= 0.7 and untouched["core_page_20_or_less_rate"] >= 0.7, "bảng báo cáo chính trong 20 trang đầu", {"development": development["core_page_20_or_less_rate"], "untouched": untouched["core_page_20_or_less_rate"]}),
        ("H14_LATE_YEAR_DRIFT", drift >= 0.2, "độ lệch trang/bảng trung bình giữa hai giai đoạn", {"early_mean_tables": early_tables, "late_mean_tables": late_tables, "early_mean_pages": early_pages, "late_mean_pages": late_pages, "maximum_relative_drift": drift}),
    ]
    results = []
    for hypothesis_id, supported, measurement, evidence in hypotheses:
        verdict = _verdict(supported)
        if hypothesis_id in {"H04_QUESTION_DOCUMENT_ROUTE", "H13_PAGE_PRIOR"} and supported:
            verdict = "KEEP_AS_SOFT_GATE"
        if hypothesis_id == "H06_ASSET_COMPLETENESS" and not supported:
            verdict = "REJECT_CRITICAL_COVERAGE_GAP"
        if hypothesis_id == "H11_OCR_QUALITY_GATE" and supported:
            verdict = "KEEP_AS_STRUCTURE_RISK"
        if hypothesis_id == "H14_LATE_YEAR_DRIFT" and supported:
            verdict = "KEEP_MONITORING"
        results.append({"hypothesis_id": hypothesis_id, "verdict": verdict, "supported": supported, "measurement": measurement, "evidence": evidence})
    return results


def validate_study(report: Mapping[str, Any]) -> None:
    if report.get("protocol") != PROTOCOL:
        raise ValueError("protocol mismatch")
    if report.get("document_inventory", {}).get("document_count") != 1973:
        raise ValueError("document population changed")
    if report.get("raw_table_inventory", {}).get("raw_table_count") != 146246:
        raise ValueError("raw table population changed")
    asset_count = int(report.get("table_asset_inventory", {}).get("asset_count") or 0)
    if not 0 < asset_count <= 146246:
        raise ValueError("invalid table asset population")
    if len(report.get("hypothesis_results") or []) != 14:
        raise ValueError("14 hypotheses required")
    if report.get("offset_verification", {}).get("sample_count") != 300:
        raise ValueError("offset sample must contain 300 tables")
    if report.get("question_routes", {}).get("question_count") != 1012:
        raise ValueError("question route population changed")
    if report.get("source_contract", {}).get("submission_eligible") is not False:
        raise ValueError("document study cannot authorize submission")


def render_report_vi(report: Mapping[str, Any]) -> str:
    documents = report["document_inventory"]
    raw_tables = report["raw_table_inventory"]
    tables = report["table_asset_inventory"]
    coverage = report["asset_coverage_diagnosis"]
    routes = report["question_routes"]
    offsets = report["offset_verification"]
    results = report["hypothesis_results"]
    definitions = {
        row["id"]: row for row in report.get("hypothesis_definitions") or []
    }
    lines = [
        "# Báo cáo nghiên cứu kho tài liệu ViFinQA",
        "",
        "## 1. Kết luận ngắn",
        "",
        f"Kho dữ liệu có **{documents['document_count']:,} tài liệu**, **{raw_tables['raw_table_count']:,} bảng nguồn**, "
        f"**{documents['ticker_count']} công ty** và **{documents['year_count']} năm**. Phần lớn dữ liệu có thể "
        "được định vị lại chính xác, nhưng cấu trúc tài liệu không đồng nhất và không thể coi tên file, scope, "
        "đơn vị hoặc bảng trùng là bằng chứng đủ để trả lời.",
        "",
        f"Trong 14 giả thuyết, **{sum(row['supported'] for row in results)} được ủng hộ** và "
        f"**{sum(not row['supported'] for row in results)} không đạt ngưỡng đã khóa**. Kết quả hữu ích nhất cho E2E "
        "là: giữ định vị nguyên văn bằng vị trí nguồn; thêm nhóm tài liệu đặc biệt; không tự đoán scope; "
        "dùng phần chữ trước bảng để tìm đơn vị; gắn cờ bảng trùng và hình dạng dòng; dùng tín hiệu số để xếp hạng mềm.",
        "",
        "## 2. Dữ liệu đã nghiên cứu",
        "",
        f"- Tài liệu: {documents['document_count']:,}.",
        f"- Trang OCR: {sum(stage['page_count'] for stage in documents['by_stage'].values()):,}.",
        f"- Bảng HTML nguồn: {raw_tables['raw_table_count']:,}.",
        f"- Bảng hiện có trong table asset index: {tables['asset_count']:,} ({tables['asset_count'] / raw_tables['raw_table_count']:.1%}).",
        f"- Table asset index hiện chỉ phủ {tables['asset_ticker_count']}/{documents['ticker_count']} công ty và {tables['document_count_with_assets']}/{documents['document_count']} tài liệu.",
        f"- Công ty: {documents['ticker_count']}.",
        f"- Năm: {', '.join(map(str, documents['years']))}.",
        f"- Câu hỏi dùng để kiểm tra khả năng nối tới nhóm tài liệu: {routes['question_count']:,}.",
        "",
        "Nghiên cứu này không dùng đáp án ẩn và không tự tạo nhãn đúng/sai cho từng câu. Vì vậy, các số về "
        "routing chỉ nói rằng tài liệu phù hợp về công ty/năm/scope có tồn tại; chúng chưa chứng minh bảng hay ô số là đúng.",
        "",
        "## 3. Bức tranh tài liệu",
        "",
        f"- Báo cáo hợp nhất: {documents['kind_counts'].get('consolidated', 0):,}; riêng lẻ: {documents['kind_counts'].get('separate', 0):,}.",
        f"- Tài liệu tổng hợp: {documents['kind_counts'].get('aggregated', 0)}; phần thuyết minh tách riêng: {documents['kind_counts'].get('explanation_fragment', 0)}; file đánh số: {documents['kind_counts'].get('numbered_fragment', 0)}; chưa rõ scope: {documents['kind_counts'].get('scope_unspecified', 0)}.",
        f"- Trong {documents['slot_count']:,} ô công ty-năm: {documents['slot_type_counts'].get('both_known_scopes', 0)} ô có đủ hai scope; {documents['slot_type_counts'].get('consolidated_only', 0)} chỉ có hợp nhất; {documents['slot_type_counts'].get('separate_only', 0)} chỉ có riêng lẻ; {documents['slot_type_counts'].get('unknown_or_special_only', 0)} chỉ có tài liệu đặc biệt/chưa rõ.",
        f"- Tài liệu không có bảng: {documents['zero_table_document_count']}.",
        f"- Trung vị số trang/tài liệu: {documents['pages_per_document']['median']:.1f}; P95: {documents['pages_per_document']['p95']:.1f}.",
        f"- Trung vị số bảng/tài liệu: {documents['tables_per_document']['median']:.1f}; P95: {documents['tables_per_document']['p95']:.1f}.",
        "",
        "Điểm quan trọng là ngoài `consolidated` và `separate` còn có báo cáo tổng hợp, phần thuyết minh tách riêng, "
        "file đánh số và file không ghi scope. Các file này cần một nhãn `document_kind` riêng; không được ép thành "
        "hợp nhất hoặc riêng lẻ chỉ để pipeline chạy tiếp.",
        "",
        "## 4. Bức tranh bảng dữ liệu",
        "",
        f"Audit nguồn ghi nhận {raw_tables['raw_table_count']:,} bảng, trong khi index E2E hiện có "
        f"{tables['asset_count']:,} bảng. Như vậy còn {raw_tables['raw_table_count'] - tables['asset_count']:,} bảng "
        "chưa xuất hiện trong `table_assets`. Đây là khoảng trống coverage cần giải thích hoặc tái lập trước khi "
        "đánh giá retrieval trên toàn kho.",
        "",
        f"Index hiện chỉ bao phủ {tables['asset_ticker_count']} công ty ({', '.join(tables['asset_tickers'])}). "
        "Vì vậy các thống kê về nội dung hàng/cột, đơn vị và context bên dưới chỉ đại diện cho phần đã index, "
        "không đại diện cho toàn bộ 100 công ty.",
        "",
        f"624 tài liệu trong asset tạo thành đúng phần đầu của danh sách tài liệu đã sắp xếp. Tài liệu cuối cùng "
        f"là `{coverage['last_indexed_document_id']}`; tài liệu kế tiếp bị thiếu là "
        f"`{coverage['next_missing_document_id']}`. Dense index và lexical index cũng đều có "
        f"{coverage['dense_index_count']:,} mục. Mẫu cắt đúng theo thứ tự chữ cái này phù hợp với một lần build "
        "chưa hoàn tất hơn là một tập con được chọn cân bằng.",
        "",
    ]
    for stage, values in tables["by_stage"].items():
        lines.append(
            f"- **{stage}**: {values['table_count']:,} bảng; bảng có tín hiệu số {values['numeric_usable_rate']:.1%}; "
            f"đơn vị thấy được khi cộng context {values['combined_unit_rate']:.1%}; bảng có năm hiện tại và năm trước "
            f"{values['current_and_prior_year_rate_among_numeric']:.1%}; dòng lệch số cột {values['irregular_row_width_rate']:.1%}."
        )
    lines.extend([
        "",
        f"Trên toàn bộ inventory có {raw_tables['duplicate_table_hash_group_count']:,} nhóm bảng trùng hash, trong đó "
        f"{raw_tables['cross_document_duplicate_group_count']:,} nhóm nằm ở nhiều tài liệu. Do đó hash bảng chỉ giúp "
        "phát hiện trùng; nó không thay thế document_id, scope, năm và vị trí nguồn.",
        "",
        "## 5. Kết quả 14 giả thuyết",
        "",
        "| Giả thuyết | Kết luận | Điều đã đo | Ý nghĩa thực tế |",
        "|---|---|---|---|",
    ])
    practical = {
        "H01_METADATA_KEYS": "Có thể dùng ticker/năm từ path để kiểm tra chéo, nhưng vẫn phải giữ manifest.",
        "H02_SCOPE_IS_NOT_BINARY": "Mở rộng taxonomy tài liệu; không ép file đặc biệt vào hai scope chuẩn.",
        "H03_SCOPE_PAIR_GAPS": "Không mặc định scope còn lại khi thiếu tài liệu.",
        "H04_QUESTION_DOCUMENT_ROUTE": "Dùng khóa công ty-năm-scope để thu hẹp kho; chưa phải chọn bảng.",
        "H05_EXACT_SOURCE_OFFSETS": "Có thể trích dẫn lại đúng nguyên văn bảng bằng char_start/char_end.",
        "H06_ASSET_COMPLETENESS": "Index hiện thiếu 67 công ty; phải rebuild đầy đủ trước khi dùng retrieval cho toàn cuộc thi.",
        "H07_NUMERIC_PREFILTER": "Dùng mật độ số để hạ hạng mục lục/bảng chữ, không xóa cứng.",
        "H08_CONTEXT_FOR_UNIT": "Đưa phần chữ ngay trước bảng vào gói retrieval và kiểm tra đơn vị.",
        "H09_COMPARATIVE_YEAR_COLUMNS": "Không được giả định luôn có cột năm trước; phải đọc header từng bảng và chỉ fallback khi header xác nhận.",
        "H10_DUPLICATE_TABLES": "Mọi evidence phải kèm tài liệu và offset, không chỉ table hash.",
        "H11_OCR_QUALITY_GATE": "Không thấy ký tự thay thế trong asset, nhưng hình dạng dòng rất không đều; gắn cờ cấu trúc, không gọi tất cả là lỗi OCR.",
        "H12_TABLE_SIZE_HETEROGENEITY": "Chunk theo kích thước bảng, không dùng một giới hạn duy nhất.",
        "H13_PAGE_PRIOR": "Ngưỡng 20 trang bị bác bỏ; không dùng vị trí đầu tài liệu như luật chung.",
        "H14_LATE_YEAR_DRIFT": "Theo dõi chỉ số theo giai đoạn và không dùng một ngưỡng vĩnh viễn.",
    }
    verdict_labels = {
        "KEEP": "GIỮ",
        "KEEP_AS_SOFT_GATE": "GIỮ NHƯ BỘ LỌC MỀM",
        "KEEP_AS_STRUCTURE_RISK": "GIỮ NHƯ CỜ RỦI RO CẤU TRÚC",
        "KEEP_MONITORING": "TIẾP TỤC THEO DÕI",
        "REJECT": "KHÔNG ĐẠT",
        "REJECT_CRITICAL_COVERAGE_GAP": "KHÔNG ĐẠT – LỖ HỔNG NGHIÊM TRỌNG",
    }
    for row in results:
        evidence = json.dumps(row["evidence"], ensure_ascii=False, separators=(",", ":"))
        if len(evidence) > 190:
            evidence = evidence[:187] + "..."
        plain_question = definitions.get(row["hypothesis_id"], {}).get(
            "plain_question", row["hypothesis_id"]
        )
        lines.append(
            f"| {row['hypothesis_id']} – {plain_question} | "
            f"**{verdict_labels.get(row['verdict'], row['verdict'])}** | "
            f"{evidence} | {practical[row['hypothesis_id']]} |"
        )
    lines.extend([
        "",
        "## 6. Khả năng nối câu hỏi tới tài liệu",
        "",
        f"Trong {routes['evaluable_route_question_count']:,} câu có đủ công ty và năm để kiểm tra, "
        f"{routes['covered_question_count']:,} câu ({routes['covered_rate_on_evaluable']:.1%}) có đủ nhóm tài liệu theo khóa; "
        f"{routes['unique_route_question_count']:,} câu ({routes['unique_route_rate_on_evaluable']:.1%}) chỉ còn một tài liệu cho từng yêu cầu; "
        f"{routes['ambiguous_document_question_count']:,} câu vẫn có nhiều lựa chọn và "
        f"{routes['missing_document_question_count']:,} câu có ít nhất một yêu cầu thiếu tài liệu.",
        "",
        "Kết quả này giúp document retrieval nhưng không được diễn giải thành table retrieval accuracy. Không có gold "
        "`relevant_docs` hoặc `relevant_tables`, nên chưa thể biết tài liệu còn lại có thực sự chứa đúng số cần hỏi hay không.",
        "",
        "## 7. Kiểm tra khả năng truy nguyên",
        "",
        f"Đã chọn cố định {offsets['sample_count']} bảng, 100 bảng ở mỗi nhóm công ty discovery/development/untouched. "
        f"Kết quả khớp cả file nguồn và đoạn bảng: {offsets['verified_count']}/{offsets['sample_count']} "
        f"({offsets['match_rate']:.1%}).",
        "",
        "Điều này cho phép E2E quay lại đúng nguyên văn nguồn. Nó không chứng minh hàng/cột được chọn là hàng/cột trả lời câu hỏi.",
        "",
        "## 8. Tác động đề xuất tới E2E",
        "",
        "1. Thêm `document_kind`: consolidated, separate, aggregated, numbered fragment, explanation fragment, scope unspecified.",
        "2. Rebuild table assets, lexical index và dense index trên đủ 1.973 tài liệu trước mọi benchmark retrieval mới.",
        "3. Document routing dùng ticker + year + requested scope; nhiều hoặc không có kết quả thì abstain/đưa vào queue.",
        "4. Table ranking kết hợp nội dung bảng, context trước bảng, tín hiệu đơn vị, năm và loại báo cáo.",
        "5. Numeric density chỉ là ưu tiên mềm; không loại thuyết minh hoặc bảng chữ một cách tuyệt đối. Không dùng ngưỡng 20 trang.",
        "6. Evidence bắt buộc giữ document_id, source hash, table hash, char_start và char_end.",
        "7. Duplicate và row-shape risk làm tăng mức kiểm tra; không tự sửa hoặc gọi mọi lệch cột là lỗi OCR.",
        "8. Với so sánh năm, chỉ xét cột Y-1 hoặc báo cáo Y+1 khi header nguồn xác nhận chính xác.",
        "",
        "## 9. Những điều nghiên cứu chưa chứng minh",
        "",
        "- Chưa đo được độ đúng của table retrieval vì không có relevant-table gold.",
        "- Chưa chứng minh một row label hay numeric cell cụ thể trả lời đúng câu hỏi.",
        "- Chưa đánh giá đầy đủ lỗi OCR làm thay đổi dấu âm, dấu ngoặc hoặc đơn vị.",
        "- Chưa tạo đáp án, pandas query, CSV evidence hay submission ZIP.",
        "- Các tỷ lệ là của snapshot corpus hiện tại; thay dữ liệu phải chạy lại toàn bộ báo cáo.",
        "",
        "## 10. Thứ tự nghiên cứu tiếp theo",
        "",
        "1. Xử lý trước nhóm 414 câu reported-value lookup: khóa document rồi tìm row/table exact-cell.",
        "2. Tạo benchmark review nhỏ, phân tầng theo company/year/scope/document_kind/OCR risk; human chỉ xác nhận bảng và ô nguồn.",
        "3. So sánh retrieval table-only với table+context trên benchmark đã khóa.",
        "4. Kiểm tra period/scope/unit compatibility trước khi tính toán.",
        "5. Chỉ sau khi direct lookup có unseen evidence mới mở ratio, comparison và multi-table composition.",
        "",
        "## 11. Tái lập",
        "",
        "Báo cáo đi kèm manifest SHA-256 cho hypothesis freeze, document inventory, table assets và question taxonomy. "
        "Validator kiểm tra lại toàn bộ hash đầu vào/đầu ra và các population count đã khóa.",
        "",
    ])
    return "\n".join(lines)
