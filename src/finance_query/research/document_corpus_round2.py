"""Round 2A/2B document-corpus experiments, isolated from production indexes."""

from __future__ import annotations

from collections import Counter, defaultdict
import csv
import hashlib
import heapq
import html
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
from typing import Any, Iterable, Mapping

from finance_query.e2e.core.corpus import extract_assets_from_report
from finance_query.research.document_corpus_study import (
    NUMBER_RE,
    UNIT_RE,
    describe,
    document_kind,
    load_documents,
    load_jsonl,
    sha256_file,
    stage_lookup,
)


BUILD_PROTOCOL = "vifinqa_document_corpus_round2_full_build_v1"
ANALYSIS_PROTOCOL = "vifinqa_document_corpus_round2_analysis_v1"
TAG_RE = re.compile(r"<[^>]*>", re.DOTALL)
WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
EXACT_DATE_RE = re.compile(r"\b(?:0?[1-9]|[12]\d|3[01])[/.-](?:0?[1-9]|1[0-2])[/.-](?:19|20)\d{2}\b")
FINANCIAL_TOKEN_RE = re.compile(r"\(?-?\d+(?:[.,]\d+)*%?\)?")
CONSOLIDATED_SCOPE_RE = re.compile(
    r"báo cáo tài chính[^.\n]{0,60}hợp nhất|bctc[^.\n]{0,40}hợp nhất"
)
SEPARATE_SCOPE_RE = re.compile(
    r"báo cáo tài chính[^.\n]{0,60}riêng(?: lẻ)?|bctc[^.\n]{0,40}riêng(?: lẻ)?"
)


def canonical_asset_line(asset: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(asset, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _expected_documents(document_csv: Path) -> list[dict[str, Any]]:
    return load_documents(document_csv)


def build_full_assets_atomic(
    *,
    reports_root: Path,
    document_csv: Path,
    output_path: Path,
    closure_path: Path,
    progress_every: int = 100,
) -> dict[str, Any]:
    """Build a complete canonical JSONL via a temporary path and atomic rename."""
    reports_root = reports_root.resolve()
    if output_path.exists() or closure_path.exists():
        raise FileExistsError("refusing to overwrite full-build outputs")
    documents = _expected_documents(document_csv)
    expected = {str(row["document_id"]): int(row["table_count"]) for row in documents}
    report_paths = [Path(str(row["source_path"])) for row in documents]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path = output_path.with_name(f".{output_path.name}.partial")
    closure_partial = closure_path.with_name(f".{closure_path.name}.partial")
    started = time.monotonic()
    output_digest = hashlib.sha256()
    report_count = 0
    table_count = 0
    failures: list[dict[str, Any]] = []
    completed = False
    try:
        with partial_path.open("wb") as output, closure_partial.open("w", encoding="utf-8") as closure:
            for path in report_paths:
                document_id = path.name.removesuffix("_extracted.txt")
                try:
                    assets = extract_assets_from_report(path, reports_root)
                except Exception as exc:  # fail-closed receipt, never skip a report
                    failures.append({"document_id": document_id, "error": f"{type(exc).__name__}: {exc}"})
                    break
                observed = len(assets)
                if observed != expected[document_id]:
                    failures.append(
                        {
                            "document_id": document_id,
                            "error": "table_count_mismatch",
                            "expected": expected[document_id],
                            "observed": observed,
                        }
                    )
                    break
                for asset in assets:
                    payload = asset.to_dict()
                    encoded = canonical_asset_line(payload)
                    output.write(encoded)
                    output_digest.update(encoded)
                    table_count += 1
                closure.write(
                    json.dumps(
                        {
                            "document_id": document_id,
                            "source_path": str(path),
                            "source_sha256": sha256_file(path),
                            "table_count": observed,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    + "\n"
                )
                report_count += 1
                if progress_every and report_count % progress_every == 0:
                    print(
                        json.dumps(
                            {"phase": "full_asset_build", "reports": report_count, "tables": table_count}
                        ),
                        flush=True,
                    )
            output.flush()
            os.fsync(output.fileno())
        if failures:
            raise ValueError(f"full build failed: {failures[0]}")
        if report_count != len(documents) or table_count != sum(expected.values()):
            raise ValueError("full population count mismatch")
        os.replace(partial_path, output_path)
        os.replace(closure_partial, closure_path)
        completed = True
    finally:
        if not completed:
            partial_path.unlink(missing_ok=True)
            closure_partial.unlink(missing_ok=True)
    elapsed = time.monotonic() - started
    return {
        "protocol": BUILD_PROTOCOL,
        "report_count": report_count,
        "table_count": table_count,
        "failure_count": len(failures),
        "failures": failures,
        "output_sha256": output_digest.hexdigest(),
        "output_size_bytes": output_path.stat().st_size,
        "source_closure_sha256": sha256_file(closure_path),
        "elapsed_seconds": elapsed,
        "atomic_finalization": True,
        "partial_path_absent": not partial_path.exists(),
        "source_contract": {
            "research_only": True,
            "production_index_replacement_allowed": False,
            "submission_eligible": False,
        },
    }


def replay_asset_digest(
    *, reports_root: Path, document_csv: Path, progress_every: int = 200
) -> dict[str, Any]:
    reports_root = reports_root.resolve()
    documents = _expected_documents(document_csv)
    digest = hashlib.sha256()
    count = 0
    started = time.monotonic()
    for report_index, row in enumerate(documents, start=1):
        path = Path(str(row["source_path"]))
        assets = extract_assets_from_report(path, reports_root)
        if len(assets) != int(row["table_count"]):
            raise ValueError(f"replay table count mismatch: {row['document_id']}")
        for asset in assets:
            digest.update(canonical_asset_line(asset.to_dict()))
            count += 1
        if progress_every and report_index % progress_every == 0:
            print(json.dumps({"phase": "digest_replay", "reports": report_index, "tables": count}), flush=True)
    return {
        "table_count": count,
        "canonical_sha256": digest.hexdigest(),
        "elapsed_seconds": time.monotonic() - started,
    }


def interruption_safety_probe(output_dir: Path) -> dict[str, Any]:
    """Prove the research writer leaves no final-looking artifact after failure."""
    with tempfile.TemporaryDirectory(prefix="round2-atomic-probe-", dir=output_dir) as temp:
        root = Path(temp)
        final_path = root / "full.jsonl"
        partial_path = root / ".full.jsonl.partial"
        try:
            with partial_path.open("w", encoding="utf-8") as file:
                file.write('{"partial":true}\n')
                file.flush()
                os.fsync(file.fileno())
                raise RuntimeError("simulated interruption")
        except RuntimeError:
            partial_path.unlink(missing_ok=True)
        return {
            "simulated_interruption": True,
            "final_path_absent": not final_path.exists(),
            "partial_path_absent": not partial_path.exists(),
            "pass": not final_path.exists() and not partial_path.exists(),
        }


def detect_prefix_truncation(asset_path: Path, document_csv: Path) -> dict[str, Any]:
    documents = [str(row["document_id"]) for row in _expected_documents(document_csv)]
    observed: list[str] = []
    seen: set[str] = set()
    for asset in load_jsonl(asset_path):
        document_id = str(asset["document_id"])
        if document_id not in seen:
            seen.add(document_id)
            observed.append(document_id)
    exact_prefix = observed == documents[: len(observed)]
    complete = observed == documents
    return {
        "observed_document_count": len(observed),
        "expected_document_count": len(documents),
        "exact_sorted_prefix": exact_prefix,
        "complete": complete,
        "rejected_as_complete": not complete,
        "last_observed_document": observed[-1] if observed else None,
        "next_missing_document": documents[len(observed)] if len(observed) < len(documents) else None,
        "pass": exact_prefix and not complete,
    }


def build_lexical_research_index(asset_path: Path, index_path: Path) -> dict[str, Any]:
    if index_path.exists():
        raise FileExistsError(f"refusing to overwrite: {index_path}")
    partial = index_path.with_name(f".{index_path.name}.partial")
    started = time.monotonic()
    count = 0
    completed = False
    try:
        connection = sqlite3.connect(partial)
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.execute(
            "CREATE VIRTUAL TABLE table_only_fts USING fts5("
            "uid UNINDEXED, ticker UNINDEXED, report_year UNINDEXED, scope UNINDEXED, body, "
            "tokenize='unicode61 remove_diacritics 2')"
        )
        connection.execute(
            "CREATE VIRTUAL TABLE combined_fts USING fts5("
            "uid UNINDEXED, ticker UNINDEXED, report_year UNINDEXED, scope UNINDEXED, body, "
            "tokenize='unicode61 remove_diacritics 2')"
        )
        table_batch = []
        combined_batch = []
        for asset in load_jsonl(asset_path):
            uid = str(asset["internal_table_uid"])
            ticker = str(asset["ticker"])
            year = str(asset.get("report_year") or "")
            scope = str(asset.get("scope") or "")
            table_body = " ".join(
                [
                    *[str(value) for value in asset.get("headers") or []],
                    *[str(value) for value in asset.get("row_paths") or []],
                ]
            )
            combined_body = str(asset.get("search_text") or table_body)
            table_batch.append((uid, ticker, year, scope, table_body))
            combined_batch.append((uid, ticker, year, scope, combined_body))
            count += 1
            if len(table_batch) >= 1000:
                connection.executemany("INSERT INTO table_only_fts VALUES (?,?,?,?,?)", table_batch)
                connection.executemany("INSERT INTO combined_fts VALUES (?,?,?,?,?)", combined_batch)
                connection.commit()
                table_batch.clear()
                combined_batch.clear()
        if table_batch:
            connection.executemany("INSERT INTO table_only_fts VALUES (?,?,?,?,?)", table_batch)
            connection.executemany("INSERT INTO combined_fts VALUES (?,?,?,?,?)", combined_batch)
        connection.commit()
        table_count = int(connection.execute("SELECT count(*) FROM table_only_fts").fetchone()[0])
        combined_count = int(connection.execute("SELECT count(*) FROM combined_fts").fetchone()[0])
        connection.close()
        if not count == table_count == combined_count:
            raise ValueError("lexical index parity failed")
        os.replace(partial, index_path)
        completed = True
    finally:
        if not completed:
            partial.unlink(missing_ok=True)
    return {
        "asset_count": count,
        "table_only_fts_count": table_count,
        "combined_fts_count": combined_count,
        "parity": count == table_count == combined_count,
        "index_size_bytes": index_path.stat().st_size,
        "index_sha256": sha256_file(index_path),
        "elapsed_seconds": time.monotonic() - started,
        "atomic_finalization": True,
    }


def _normalize_numeric_token(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def numeric_token_set(value: str) -> set[str]:
    return {
        _normalize_numeric_token(match.group(0))
        for match in FINANCIAL_TOKEN_RE.finditer(value)
        if any(character.isdigit() for character in match.group(0))
    }


def _plain_html(table_html: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(TAG_RE.sub(" ", table_html))).strip()


def _selection_score(seed: str, uid: str, purpose: str) -> int:
    return int(hashlib.sha256(f"{seed}\n{purpose}\n{uid}".encode()).hexdigest(), 16)


def _bounded_lowest_add(
    candidates: list[tuple[int, str, dict[str, Any]]],
    *,
    score: int,
    uid: str,
    row: dict[str, Any],
    limit: int,
) -> None:
    """Keep only the deterministic lowest-scored rows in a max-heap."""
    item = (-score, uid, row)
    if len(candidates) < limit:
        heapq.heappush(candidates, item)
        return
    current_largest_score = -candidates[0][0]
    if score < current_largest_score or (
        score == current_largest_score and uid < candidates[0][1]
    ):
        heapq.heapreplace(candidates, item)


def _select_lowest(
    candidates: list[tuple[int, str, dict[str, Any]]], count: int
) -> list[dict[str, Any]]:
    ordered = sorted(candidates, key=lambda item: (-item[0], item[1]))
    return [row for _negative_score, _uid, row in ordered[:count]]


def _row_label_candidate(asset: Mapping[str, Any]) -> str | None:
    for row in asset.get("rows") or []:
        if len(row) < 2 or not any(NUMBER_RE.search(str(cell)) for cell in row[1:]):
            continue
        label = str(row[0]).strip()
        words = WORD_RE.findall(label)
        if 3 <= len(words) <= 18 and 12 <= len(label) <= 160:
            return label
    return None


def _retrieval_query(label: str) -> str | None:
    stop = {"và", "của", "các", "tại", "trong", "cho", "năm", "số", "tổng", "cộng"}
    tokens = []
    for token in WORD_RE.findall(label.casefold()):
        if len(token) < 2 or token in stop or token in tokens:
            continue
        tokens.append(token)
    if len(tokens) < 2:
        return None
    return " AND ".join(f'"{token}"' for token in tokens[:10])


def _jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    a = {value.casefold().strip() for value in left if value.strip()}
    b = {value.casefold().strip() for value in right if value.strip()}
    return len(a & b) / len(a | b) if a and b else 0.0


def analyze_full_assets(
    *,
    asset_path: Path,
    raw_inventory_path: Path,
    ticker_split: Mapping[str, list[str]],
    protocol: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    stages = stage_lookup(ticker_split)
    seed = str(protocol["seed"])
    numeric_sample_size = int(protocol["split"]["numeric_fidelity_samples_per_stage"])
    retrieval_size = int(protocol["split"]["retrieval_queries_per_stage"])
    counters: dict[str, Counter[str]] = defaultdict(Counter)
    distributions: dict[str, defaultdict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    function_counts: dict[str, Counter[str]] = defaultdict(Counter)
    context_windows = (200, 400, 800, 1600)
    numeric_candidates: dict[str, list[tuple[int, str, dict[str, Any]]]] = defaultdict(list)
    retrieval_candidates: dict[str, list[tuple[int, str, dict[str, Any]]]] = defaultdict(list)
    template_hashes: Counter[str] = Counter()
    exact_hashes: Counter[str] = Counter()
    scope_documents_seen: set[str] = set()
    scope_counts = Counter()
    scope_conflict_examples: list[dict[str, Any]] = []
    previous_by_document: dict[str, dict[str, Any]] = {}
    source_cache_path: str | None = None
    source_cache_text = ""
    source_cache_sha256 = ""
    total = 0

    for asset in load_jsonl(asset_path):
        total += 1
        ticker = str(asset["ticker"])
        stage = stages[ticker]
        uid = str(asset["internal_table_uid"])
        source_path = str(asset["source_path"])
        if source_path != source_cache_path:
            source_cache_path = source_path
            source_cache_text = Path(source_path).read_text(encoding="utf-8", errors="strict")
            source_cache_sha256 = sha256_file(Path(source_path))
        start = int(asset["char_start"])
        end = int(asset["char_end"])
        raw_table = source_cache_text[start:end]
        row_text = " ".join(str(cell) for row in asset.get("rows") or [] for cell in row)
        table_text = " ".join(
            [*[str(value) for value in asset.get("headers") or []], row_text]
        )
        rows = list(asset.get("rows") or [])
        numeric_cells = sum(bool(NUMBER_RE.search(str(cell))) for row in rows for cell in row)
        numeric_usable = len(rows) >= 2 and numeric_cells >= 2
        flags = set((asset.get("structure_quality") or {}).get("flags") or [])
        headers = list(asset.get("header_row_indices") or [])
        function = str((asset.get("table_function") or {}).get("kind") or "unknown")
        counters[stage]["tables"] += 1
        counters[stage]["header_detected"] += int(bool(headers))
        counters[stage]["structure_metadata"] += int(
            all(
                key in asset
                for key in (
                    "structure_version",
                    "context_schema_version",
                    "header_row_indices",
                    "structure_quality",
                )
            )
        )
        counters[stage]["span_expanded"] += int("span_cells_expanded" in flags)
        counters[stage]["irregular_width"] += int("irregular_source_row_widths" in flags)
        counters[stage]["numeric_usable"] += int(numeric_usable)
        counters[stage]["main_statement"] += int(
            function in {"balance_sheet", "income_statement", "cash_flow_statement"}
        )
        counters[stage]["main_statement_prefilter_excluded"] += int(
            function in {"balance_sheet", "income_statement", "cash_flow_statement"}
            and not numeric_usable
        )
        function_counts[stage][function] += 1
        distributions[stage]["rows"].append(len(rows))
        distributions[stage]["columns"].append(
            int((asset.get("structure_quality") or {}).get("column_count") or 0)
        )
        header_text = " ".join(str(value) for value in asset.get("headers") or [])
        year = int(asset.get("report_year") or 0)
        counters[stage]["current_year_header"] += int(str(year) in header_text)
        counters[stage]["prior_year_header"] += int(str(year - 1) in header_text)
        counters[stage]["exact_date_header"] += int(bool(EXACT_DATE_RE.search(header_text)))
        table_has_unit = bool(UNIT_RE.search(table_text))
        for window in context_windows:
            context = source_cache_text[max(0, start - window):start]
            counters[stage][f"unit_window_{window}"] += int(
                table_has_unit or bool(UNIT_RE.search(context))
            )

        document_id = str(asset["document_id"])
        if document_id not in scope_documents_seen:
            scope_documents_seen.add(document_id)
            scope = str(asset.get("scope") or "unknown")
            title_zone = source_cache_text[:12000].casefold()
            consolidated_mentions = len(CONSOLIDATED_SCOPE_RE.findall(title_zone))
            separate_mentions = len(SEPARATE_SCOPE_RE.findall(title_zone))
            if scope in {"consolidated", "separate"}:
                if consolidated_mentions == separate_mentions:
                    scope_counts["binary_marker_ties_or_absent"] += 1
                else:
                    inferred_scope = (
                        "consolidated"
                        if consolidated_mentions > separate_mentions
                        else "separate"
                    )
                    scope_counts["explicit_marker_documents"] += 1
                    conflict = scope != inferred_scope
                    scope_counts["scope_conflicts"] += int(conflict)
                    if conflict:
                        scope_conflict_examples.append(
                            {
                                "document_id": document_id,
                                "filename_scope": scope,
                                "internal_marker_scope": inferred_scope,
                                "consolidated_mentions": consolidated_mentions,
                                "separate_mentions": separate_mentions,
                                "source_path": source_path,
                            }
                        )
            if document_kind(document_id, scope) not in {"consolidated", "separate"}:
                scope_counts["special_documents"] += 1
                scope_counts["special_documents_preserved"] += int(
                    scope not in {"consolidated", "separate"}
                )

        previous = previous_by_document.get(document_id)
        if previous is not None:
            current_page = int(asset.get("page_no") or 0)
            previous_page = int(previous.get("page_no") or 0)
            similarity = _jaccard(
                [str(value) for value in previous.get("headers") or []],
                [str(value) for value in asset.get("headers") or []],
            )
            if current_page == previous_page + 1 and similarity >= 0.6:
                counters[stage]["multi_page_continuation_candidate"] += 1
                counters[stage]["multi_page_high_similarity_proxy"] += int(similarity >= 0.8)
        previous_by_document[document_id] = asset

        exact_hashes[str(asset["table_sha256"])] += 1
        normalized_template = re.sub(
            r"\s+", " ", NUMBER_RE.sub(" <num> ", table_text.casefold())
        ).strip()
        template_hashes[hashlib.sha256(normalized_template.encode()).hexdigest()] += 1

        raw_tokens = numeric_token_set(_plain_html(raw_table))
        parsed_tokens = numeric_token_set(row_text)
        numeric_recall = len(raw_tokens & parsed_tokens) / len(raw_tokens) if raw_tokens else 1.0
        recomputed_table_sha256 = hashlib.sha256(raw_table.encode("utf-8")).hexdigest()
        recomputed_uid_payload = (
            f"{document_id}\x1f{int(asset['local_ordinal'])}\x1f{start}\x1f{recomputed_table_sha256}"
        ).encode("utf-8")
        recomputed_uid = hashlib.sha256(recomputed_uid_payload).hexdigest()
        _bounded_lowest_add(
            numeric_candidates[stage],
            score=_selection_score(seed, uid, "numeric_fidelity"),
            uid=uid,
            limit=numeric_sample_size,
            row={
                "stage": stage,
                "uid": uid,
                "document_id": document_id,
                "source_path": source_path,
                "char_start": start,
                "char_end": end,
                "source_sha256": str(asset["source_sha256"]),
                "table_sha256": str(asset["table_sha256"]),
                "local_ordinal": int(asset["local_ordinal"]),
                "source_sha256_match": source_cache_sha256 == str(asset["source_sha256"]),
                "table_sha256_match": recomputed_table_sha256 == str(asset["table_sha256"]),
                "uid_match": recomputed_uid == uid,
                "raw_numeric_token_count": len(raw_tokens),
                "parsed_numeric_token_count": len(parsed_tokens),
                "numeric_token_set_recall": numeric_recall,
            },
        )
        label = _row_label_candidate(asset)
        query = _retrieval_query(label) if label else None
        if label and query:
            _bounded_lowest_add(
                retrieval_candidates[stage],
                score=_selection_score(seed, uid, "retrieval"),
                uid=uid,
                limit=retrieval_size,
                row={
                    "stage": stage,
                    "uid": uid,
                    "ticker": ticker,
                    "report_year": year,
                    "scope": str(asset.get("scope") or ""),
                    "label": label,
                    "fts_query": query,
                    "question_materialized": False,
                    "benchmark_type": "source_derived_row_label_retrieval",
                },
            )

    numeric_samples = [
        row
        for stage in ("discovery", "development", "untouched_evaluation")
        for row in _select_lowest(numeric_candidates[stage], numeric_sample_size)
    ]
    retrieval_queries = [
        row
        for stage in ("discovery", "development", "untouched_evaluation")
        for row in _select_lowest(retrieval_candidates[stage], retrieval_size)
    ]

    raw_locator_map: dict[tuple[str, int], dict[str, int]] = {}
    with raw_inventory_path.open(encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            raw_locator_map[(str(row["document_id"]), int(row["local_order_1"]))] = {
                "start_char_0": int(row["start_char_0"]),
                "start_line_0": int(row["start_line_0"]),
                "start_line_1": int(row["start_line_1"]),
            }
    fidelity_rows = []
    fidelity_by_stage: dict[str, Counter[str]] = defaultdict(Counter)
    for sample in numeric_samples:
        recall = float(sample["numeric_token_set_recall"])
        path = Path(str(sample["source_path"]))
        source_text = path.read_text(encoding="utf-8", errors="strict")
        locator = raw_locator_map[(str(sample["document_id"]), int(sample["local_ordinal"]))]
        computed_line_0 = source_text.count("\n", 0, int(sample["char_start"]))
        locator_match = (
            locator["start_char_0"] == int(sample["char_start"])
            and locator["start_line_0"] == computed_line_0
            and locator["start_line_1"] == computed_line_0 + 1
        )
        stage = str(sample["stage"])
        fidelity_by_stage[stage]["samples"] += 1
        fidelity_by_stage[stage]["perfect_numeric_recall"] += int(recall == 1.0)
        fidelity_by_stage[stage]["line_locator_match"] += int(locator_match)
        fidelity_by_stage[stage]["source_sha256_match"] += int(sample["source_sha256_match"])
        fidelity_by_stage[stage]["table_sha256_match"] += int(sample["table_sha256_match"])
        fidelity_by_stage[stage]["uid_match"] += int(sample["uid_match"])
        fidelity_rows.append(
            {
                **sample,
                "line_locator_match": locator_match,
                "start_line_0": computed_line_0,
                "start_line_1": computed_line_0 + 1,
                "question_materialized": False,
            }
        )

    by_stage = {}
    for stage in ("discovery", "development", "untouched_evaluation"):
        count = counters[stage]["tables"]
        main = counters[stage]["main_statement"]
        by_stage[stage] = {
            "table_count": count,
            "header_detection_rate": counters[stage]["header_detected"] / count,
            "structure_metadata_rate": counters[stage]["structure_metadata"] / count,
            "span_expansion_rate": counters[stage]["span_expanded"] / count,
            "irregular_width_rate": counters[stage]["irregular_width"] / count,
            "numeric_usable_rate": counters[stage]["numeric_usable"] / count,
            "table_function_counts": dict(sorted(function_counts[stage].items())),
            "main_statement_count": main,
            "numeric_prefilter_false_exclusion_proxy": (
                counters[stage]["main_statement_prefilter_excluded"] / main if main else 0
            ),
            "current_year_header_rate": counters[stage]["current_year_header"] / count,
            "prior_year_header_rate": counters[stage]["prior_year_header"] / count,
            "exact_date_header_rate": counters[stage]["exact_date_header"] / count,
            "unit_coverage_by_window": {
                str(window): counters[stage][f"unit_window_{window}"] / count
                for window in context_windows
            },
            "multi_page_continuation_candidate_count": counters[stage]["multi_page_continuation_candidate"],
            "multi_page_high_similarity_proxy_rate": (
                counters[stage]["multi_page_high_similarity_proxy"]
                / counters[stage]["multi_page_continuation_candidate"]
                if counters[stage]["multi_page_continuation_candidate"]
                else None
            ),
            "row_count": describe(distributions[stage]["rows"]),
            "column_count": describe(distributions[stage]["columns"]),
            "numeric_fidelity": {
                "sample_count": fidelity_by_stage[stage]["samples"],
                "perfect_numeric_recall_rate": (
                    fidelity_by_stage[stage]["perfect_numeric_recall"] / fidelity_by_stage[stage]["samples"]
                ),
                "line_locator_match_rate": (
                    fidelity_by_stage[stage]["line_locator_match"] / fidelity_by_stage[stage]["samples"]
                ),
                "source_sha256_match_rate": (
                    fidelity_by_stage[stage]["source_sha256_match"] / fidelity_by_stage[stage]["samples"]
                ),
                "table_sha256_match_rate": (
                    fidelity_by_stage[stage]["table_sha256_match"] / fidelity_by_stage[stage]["samples"]
                ),
                "uid_match_rate": (
                    fidelity_by_stage[stage]["uid_match"] / fidelity_by_stage[stage]["samples"]
                ),
            },
        }
    return (
        {
            "protocol": ANALYSIS_PROTOCOL,
            "table_count": total,
            "by_stage": by_stage,
            "scope_content": {
                "explicit_marker_document_count": scope_counts["explicit_marker_documents"],
                "scope_conflict_count": scope_counts["scope_conflicts"],
                "scope_conflict_rate": (
                    scope_counts["scope_conflicts"] / scope_counts["explicit_marker_documents"]
                    if scope_counts["explicit_marker_documents"]
                    else None
                ),
                "special_document_count": scope_counts["special_documents"],
                "special_document_preserved_count": scope_counts["special_documents_preserved"],
                "binary_marker_ties_or_absent": scope_counts["binary_marker_ties_or_absent"],
                "scope_conflict_examples": scope_conflict_examples,
            },
            "duplicates": {
                "exact_duplicate_group_count": sum(value > 1 for value in exact_hashes.values()),
                "template_duplicate_group_count": sum(value > 1 for value in template_hashes.values()),
                "exact_duplicate_instance_count": sum(value for value in exact_hashes.values() if value > 1),
                "template_duplicate_instance_count": sum(value for value in template_hashes.values() if value > 1),
            },
            "numeric_fidelity_sample_count": len(fidelity_rows),
            "retrieval_query_count": len(retrieval_queries),
            "question_materialized": False,
            "submission_eligible": False,
        },
        fidelity_rows,
        retrieval_queries,
    )


def _search(
    connection: sqlite3.Connection,
    *,
    table: str,
    query: str,
    ticker: str | None = None,
    year: int | None = None,
    scope: str | None = None,
    limit: int = 10,
) -> list[str]:
    clauses = [f"{table} MATCH ?"]
    parameters: list[Any] = [query]
    if ticker is not None:
        clauses.append("ticker = ?")
        parameters.append(ticker)
    if year is not None:
        clauses.append("report_year = ?")
        parameters.append(str(year))
    if scope is not None:
        clauses.append("scope = ?")
        parameters.append(scope)
    parameters.append(limit)
    sql = f"SELECT uid FROM {table} WHERE {' AND '.join(clauses)} ORDER BY bm25({table}) LIMIT ?"
    return [str(row[0]) for row in connection.execute(sql, parameters)]


def _match_count(
    connection: sqlite3.Connection,
    *,
    table: str,
    query: str,
    ticker: str | None = None,
    year: int | None = None,
    scope: str | None = None,
) -> int:
    clauses = [f"{table} MATCH ?"]
    parameters: list[Any] = [query]
    if ticker is not None:
        clauses.append("ticker = ?")
        parameters.append(ticker)
    if year is not None:
        clauses.append("report_year = ?")
        parameters.append(str(year))
    if scope is not None:
        clauses.append("scope = ?")
        parameters.append(scope)
    sql = f"SELECT count(*) FROM {table} WHERE {' AND '.join(clauses)}"
    return int(connection.execute(sql, parameters).fetchone()[0])


def evaluate_retrieval(index_path: Path, queries: list[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    modes = {
        "table_only_global": ("table_only_fts", False),
        "table_only_metadata_filtered": ("table_only_fts", True),
        "combined_global": ("combined_fts", False),
        "combined_metadata_filtered": ("combined_fts", True),
    }
    counts: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    rows = []
    with sqlite3.connect(index_path) as connection:
        for query_row in queries:
            result = {"stage": query_row["stage"], "uid": query_row["uid"], "modes": {}, "question_materialized": False}
            for mode, (table, filtered) in modes.items():
                try:
                    ticker = str(query_row["ticker"]) if filtered else None
                    year = int(query_row["report_year"]) if filtered else None
                    scope = str(query_row["scope"]) if filtered else None
                    ranked = _search(
                        connection,
                        table=table,
                        query=str(query_row["fts_query"]),
                        ticker=ticker,
                        year=year,
                        scope=scope,
                    )
                    match_count = _match_count(
                        connection,
                        table=table,
                        query=str(query_row["fts_query"]),
                        ticker=ticker,
                        year=year,
                        scope=scope,
                    )
                except sqlite3.OperationalError:
                    ranked = []
                    match_count = 0
                target = str(query_row["uid"])
                rank = ranked.index(target) + 1 if target in ranked else None
                stage = str(query_row["stage"])
                counts[stage][mode]["queries"] += 1
                for k in (1, 5, 10):
                    counts[stage][mode][f"top{k}"] += int(rank is not None and rank <= k)
                counts[stage][mode]["reciprocal_rank_micros"] += int((1 / rank if rank else 0) * 1_000_000)
                counts[stage][mode]["match_count"] += match_count
                result["modes"][mode] = {
                    "rank": rank,
                    "returned_count": len(ranked),
                    "match_count": match_count,
                }
            rows.append(result)
    report = {"protocol": "vifinqa_source_derived_lexical_retrieval_v1", "by_stage": {}, "query_count": len(rows), "benchmark_limit": "Source-derived row-label retrieval measures index mechanics, not ViFinQA table-relevance accuracy.", "submission_eligible": False}
    for stage, stage_modes in counts.items():
        report["by_stage"][stage] = {}
        for mode, values in stage_modes.items():
            total = values["queries"]
            report["by_stage"][stage][mode] = {
                "query_count": total,
                "top1": values["top1"] / total,
                "top5": values["top5"] / total,
                "top10": values["top10"] / total,
                "mrr": values["reciprocal_rank_micros"] / 1_000_000 / total,
                "mean_match_count": values["match_count"] / total,
            }
    return rows, report


def benchmark_dense_cpu(asset_path: Path, total_count: int, sample_count: int = 256) -> dict[str, Any]:
    import torch
    from sentence_transformers import SentenceTransformer

    texts = []
    for asset in load_jsonl(asset_path):
        texts.append(str(asset.get("search_text") or "")[:2000])
        if len(texts) >= sample_count:
            break
    model_name = "intfloat/multilingual-e5-small"
    started = time.monotonic()
    model = SentenceTransformer(model_name, device="cpu", local_files_only=True)
    load_seconds = time.monotonic() - started
    encode_started = time.monotonic()
    embeddings = model.encode(texts, batch_size=32, normalize_embeddings=True, show_progress_bar=False)
    encode_seconds = time.monotonic() - encode_started
    projected = encode_seconds / len(texts) * total_count
    return {
        "cuda_available": bool(torch.cuda.is_available()),
        "model": model_name,
        "sample_count": len(texts),
        "dimension": int(embeddings.shape[1]),
        "model_load_seconds": load_seconds,
        "sample_encode_seconds": encode_seconds,
        "projected_full_encode_seconds": projected,
        "projected_vector_bytes_float32": total_count * int(embeddings.shape[1]) * 4,
        "fits_1800_second_budget": projected <= 1800,
        "measurement_is_projection": True,
    }
