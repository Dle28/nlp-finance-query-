"""Complete-corpus lexical table retrieval with fail-closed build gates.

The index is navigation metadata only.  A returned table UID cannot authorize
an answer, an evidence row, a training record, or a submission.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any


WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
STOPWORDS = frozenset(
    {
        "bao",
        "nhiêu",
        "là",
        "của",
        "các",
        "cho",
        "trong",
        "tại",
        "theo",
        "năm",
        "công",
        "ty",
        "tổng",
        "số",
    }
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid asset JSONL at line {line_number}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"asset line {line_number} is not an object")
            yield row


def table_only_text(asset: Mapping[str, Any]) -> str:
    """Return the frozen retrieval projection without surrounding context."""
    # Review bundles use the compact ``headers``/``row_paths`` projection,
    # while ``tables_structured_v2.jsonl`` stores the same information as
    # ``column_labels`` and full ``rows``.  Keep this adapter numeric-free so
    # a reranker cannot learn or copy an answer from a table value.
    headers = asset.get("headers") or asset.get("column_labels") or []
    row_paths = asset.get("row_paths")
    if row_paths is None:
        row_paths = [
            row[0]
            for row in asset.get("rows") or []
            if isinstance(row, (list, tuple)) and row
        ]
    values = [
        str(asset.get("document_id") or ""),
        str(asset.get("table_section") or ""),
        str(asset.get("table_purpose") or ""),
        *[str(value) for value in headers],
        *[str(value) for value in row_paths],
    ]
    return " ".join(" ".join(values).split())


def make_fts_query(
    value: str,
    *,
    max_terms: int = 12,
    operator: str = "AND",
) -> str:
    if operator not in {"AND", "OR"}:
        raise ValueError("operator must be AND or OR")
    terms: list[str] = []
    for token in WORD_RE.findall(value.casefold()):
        if len(token) < 2 or token in STOPWORDS or token in terms:
            continue
        terms.append(token)
        if len(terms) >= max_terms:
            break
    if not terms:
        raise ValueError("retrieval query has no usable terms")
    return f" {operator} ".join(f'"{term}"' for term in terms)


def validate_asset_closure(
    asset_path: Path,
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Reject partial, duplicated, or unpinned table-asset populations."""
    expected_sha = str(contract["expected_asset_sha256"])
    observed_sha = sha256_file(asset_path)
    if observed_sha != expected_sha:
        raise ValueError(
            f"asset SHA-256 mismatch: expected {expected_sha}, observed {observed_sha}"
        )

    uids: set[str] = set()
    documents: set[str] = set()
    tickers: set[str] = set()
    count = 0
    duplicate_uid_count = 0
    for asset in load_jsonl(asset_path):
        uid = str(asset.get("internal_table_uid") or "")
        document_id = str(asset.get("document_id") or "")
        ticker = str(asset.get("ticker") or "")
        if not uid or not document_id or not ticker:
            raise ValueError(f"asset {count + 1} is missing UID/document/ticker")
        duplicate_uid_count += int(uid in uids)
        uids.add(uid)
        documents.add(document_id)
        tickers.add(ticker)
        count += 1

    observed = {
        "asset_sha256": observed_sha,
        "table_count": count,
        "document_count": len(documents),
        "ticker_count": len(tickers),
        "duplicate_uid_count": duplicate_uid_count,
    }
    expected = {
        "table_count": int(contract["expected_table_count"]),
        "document_count": int(contract["expected_document_count"]),
        "ticker_count": int(contract["expected_ticker_count"]),
        "duplicate_uid_count": 0,
    }
    mismatches = {
        key: {"expected": value, "observed": observed[key]}
        for key, value in expected.items()
        if observed[key] != value
    }
    if mismatches:
        raise ValueError(f"asset closure mismatch: {json.dumps(mismatches, sort_keys=True)}")
    return {**observed, "complete": True, "prefix_or_partial_allowed": False}


def validate_source_closure(
    source_closure_path: Path,
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate all source reports, including reports that contain zero tables."""
    expected_sha = str(contract["expected_source_closure_sha256"])
    observed_sha = sha256_file(source_closure_path)
    if observed_sha != expected_sha:
        raise ValueError(
            f"source closure SHA-256 mismatch: expected {expected_sha}, observed {observed_sha}"
        )
    documents: set[str] = set()
    report_count = 0
    table_count = 0
    zero_table_report_count = 0
    for row in load_jsonl(source_closure_path):
        document_id = str(row.get("document_id") or "")
        if not document_id or document_id in documents:
            raise ValueError(f"invalid or duplicate source-closure document: {document_id!r}")
        documents.add(document_id)
        observed_tables = int(row.get("table_count") or 0)
        report_count += 1
        table_count += observed_tables
        zero_table_report_count += int(observed_tables == 0)
    observed = {
        "source_closure_sha256": observed_sha,
        "source_report_count": report_count,
        "source_table_count": table_count,
        "zero_table_report_count": zero_table_report_count,
    }
    expected = {
        "source_report_count": int(contract["expected_source_report_count"]),
        "source_table_count": int(contract["expected_table_count"]),
        "zero_table_report_count": int(contract["expected_zero_table_report_count"]),
    }
    mismatches = {
        key: {"expected": value, "observed": observed[key]}
        for key, value in expected.items()
        if observed[key] != value
    }
    if mismatches:
        raise ValueError(f"source closure mismatch: {json.dumps(mismatches, sort_keys=True)}")
    return {**observed, "complete": True}


def build_lexical_index(
    *,
    asset_path: Path,
    source_closure_path: Path,
    index_path: Path,
    contract: Mapping[str, Any],
    progress_every: int = 25_000,
) -> dict[str, Any]:
    """Build an immutable table-only FTS index and atomic completion receipt."""
    if index_path.exists():
        raise FileExistsError(f"refusing to overwrite: {index_path}")
    closure = {
        **validate_asset_closure(asset_path, contract),
        **validate_source_closure(source_closure_path, contract),
    }
    index_path.parent.mkdir(parents=True, exist_ok=True)
    partial = index_path.with_name(f".{index_path.name}.partial")
    partial.unlink(missing_ok=True)
    started = time.monotonic()
    completed = False
    count = 0
    try:
        connection = sqlite3.connect(partial)
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.execute(
            "CREATE VIRTUAL TABLE tables_fts USING fts5("
            "uid UNINDEXED, document_id UNINDEXED, ticker UNINDEXED, "
            "report_year UNINDEXED, scope UNINDEXED, body, "
            "tokenize='unicode61 remove_diacritics 2')"
        )
        batch: list[tuple[str, str, str, str, str, str]] = []
        for asset in load_jsonl(asset_path):
            batch.append(
                (
                    str(asset["internal_table_uid"]),
                    str(asset["document_id"]),
                    str(asset["ticker"]),
                    str(asset.get("report_year") or ""),
                    str(asset.get("scope") or ""),
                    table_only_text(asset),
                )
            )
            count += 1
            if len(batch) >= 1000:
                connection.executemany("INSERT INTO tables_fts VALUES (?,?,?,?,?,?)", batch)
                connection.commit()
                batch.clear()
            if progress_every and count % progress_every == 0:
                print(json.dumps({"phase": "lexical", "tables": count}), flush=True)
        if batch:
            connection.executemany("INSERT INTO tables_fts VALUES (?,?,?,?,?,?)", batch)
        connection.commit()
        indexed_count = int(connection.execute("SELECT count(*) FROM tables_fts").fetchone()[0])
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        connection.close()
        if indexed_count != closure["table_count"] or integrity != "ok":
            raise ValueError(
                f"lexical parity/integrity failed: assets={closure['table_count']}, "
                f"indexed={indexed_count}, integrity={integrity}"
            )
        os.replace(partial, index_path)
        completed = True
    finally:
        if not completed:
            partial.unlink(missing_ok=True)

    return {
        "protocol": "vifinqa_table_only_metadata_lexical_v1",
        "asset_closure": closure,
        "indexed_count": count,
        "index_sha256": sha256_file(index_path),
        "index_size_bytes": index_path.stat().st_size,
        "elapsed_seconds": time.monotonic() - started,
        "representation": "table_only_headers_and_row_paths",
        "context_in_index": False,
        "required_filters": ["ticker", "report_year"],
        "scope_filter": "optional_explicit_only",
        "navigation_metadata_only": True,
        "may_authorize_answer": False,
        "submission_eligible": False,
        "atomic_finalization": True,
    }


def search_lexical(
    *,
    index_path: Path,
    query: str,
    ticker: str,
    report_year: int,
    scope: str | None = None,
    limit: int = 10,
    match_mode: str = "all",
    connection: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Retrieve navigation candidates with mandatory ticker/year grounding."""
    if not ticker.strip() or not isinstance(report_year, int):
        raise ValueError("ticker and report_year are required retrieval filters")
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    if match_mode not in {"all", "any"}:
        raise ValueError("match_mode must be all or any")
    fts_query = make_fts_query(query, operator="AND" if match_mode == "all" else "OR")
    clauses = ["tables_fts MATCH ?", "ticker = ?", "report_year = ?"]
    parameters: list[Any] = [fts_query, ticker.strip().upper(), str(report_year)]
    if scope is not None:
        if scope not in {"consolidated", "separate", "aggregated", "unknown"}:
            raise ValueError(f"unsupported explicit scope: {scope}")
        clauses.append("scope = ?")
        parameters.append(scope)
    parameters.append(limit)
    sql = (
        "SELECT uid, document_id, ticker, report_year, scope, bm25(tables_fts) AS score "
        f"FROM tables_fts WHERE {' AND '.join(clauses)} "
        "ORDER BY score LIMIT ?"
    )
    owns_connection = connection is None
    active_connection = connection or sqlite3.connect(index_path)
    try:
        rows = active_connection.execute(sql, parameters).fetchall()
    finally:
        if owns_connection:
            active_connection.close()
    return [
        {
            "internal_table_uid": row[0],
            "document_id": row[1],
            "ticker": row[2],
            "report_year": int(row[3]),
            "scope": row[4],
            "rank": rank,
            "score": float(row[5]),
            "navigation_metadata_only": True,
            "may_authorize_answer": False,
        }
        for rank, row in enumerate(rows, start=1)
    ]
