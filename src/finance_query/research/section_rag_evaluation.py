"""Source-bounded section RAG assets and hierarchical retrieval evaluation.

The module is deliberately research-only.  A section is a semantic navigation
chunk that points back to immutable tables; it never contains financial cell
values and cannot authorize evidence, answers, or a submission.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from finance_query.e2e.core.dense_retrieval import search_dense_batch
from finance_query.e2e.core.report_segments import build_report_segment
from finance_query.e2e.core.table_retrieval import load_jsonl, sha256_file


SECTION_PROTOCOL = "vifinqa_section_chunk_assets_v1"
EVALUATION_PROTOCOL = "vifinqa_section_hierarchical_rag_evaluation_v1"
TABLE_BASELINE_PROTOCOL = "vifinqa_section_rag_table_baseline_v1"
MATERIALIZED_NAVIGATION_STATUS = "MATERIALIZED_SINGLE_PERIOD_SUBTRACT_EXECUTION_CANDIDATE"
SECTION_CONTRACT = {
    "research_only": True,
    "navigation_metadata_only": True,
    "raw_financial_values_included": False,
    "evidence_eligible": False,
    "may_authorize_answer": False,
    "submission_eligible": False,
    "promotion_allowed": False,
}
FORBIDDEN_KEYS = frozenset(
    {
        "answer",
        "answer_decimal",
        "raw_value",
        "raw_values",
        "cell_value",
        "pandas_query",
        "human_verified",
        "raw_source_cell",
        "raw_source_row",
        "raw_decimal_candidate",
        "rows",
        "context_before",
        "search_text",
    }
)
WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
STATEMENT_SECTION_KINDS = frozenset({"balance_sheet", "income_statement", "cash_flow"})


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _rows(path: Path) -> list[dict[str, Any]]:
    rows = list(load_jsonl(path))
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"{path} must contain JSON objects")
    return rows


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _canonical_sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(str(key) in FORBIDDEN_KEYS or _contains_forbidden(child) for key, child in value.items())
    return any(_contains_forbidden(child) for child in value) if isinstance(value, list) else False


def _require_descriptor(descriptor: Mapping[str, Any], *, name: str, root: Path | None = None) -> Path:
    candidate = Path(str(descriptor.get("path") or ""))
    path = root / candidate if root is not None and not candidate.is_absolute() else candidate
    expected = str(descriptor.get("sha256") or "")
    if not path.is_file() or not expected or sha256_file(path) != expected:
        raise ValueError(f"{name} does not match its pinned manifest")
    return path


def _require_asset_manifest(asset_path: Path, manifest_path: Path) -> None:
    manifest = _json(manifest_path)
    descriptor = (manifest.get("outputs") or {}).get(asset_path.name) or {}
    _require_descriptor({"path": str(asset_path), "sha256": descriptor.get("sha256")}, name="table assets")


def _safe_text(value: object) -> str:
    """Retain lexical labels while removing all digit-bearing source fragments."""
    return " ".join(WORD_RE.findall(str(value or ""))).strip()


def _unique(values: Iterable[str], *, limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = " ".join(str(value).split())
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _semantic_labels(asset: Mapping[str, Any]) -> list[str]:
    labels: list[str] = []
    for path in asset.get("row_paths") or []:
        for part in str(path).split(">"):
            safe = _safe_text(part)
            if safe:
                labels.append(safe)
    return _unique(labels, limit=48)


def _table_descriptor(asset: Mapping[str, Any]) -> dict[str, Any]:
    segment = build_report_segment(asset, asset)
    parent_heading = _safe_text(segment.get("source_parent_heading"))
    reader_heading = _safe_text(segment.get("reader_heading"))
    table_section = segment.get("table_section") or {}
    function = segment.get("table_function") or {}
    section_label = _safe_text(table_section.get("label"))
    function_label = _safe_text(function.get("label"))
    # A table-specific note title is useful content inside a chunk but is too
    # fine-grained to define the parent retrieval unit: OCR commonly emits one
    # such title per table.  The macro boundary is either an explicit parent
    # heading, a source-observed primary statement, or a contiguous run of
    # notes/other tables.  Each child note title remains in ``headers`` for
    # semantic matching within the larger chunk.
    if parent_heading:
        group_kind, group_label = "parent_heading", parent_heading
    elif str(table_section.get("kind") or "") in STATEMENT_SECTION_KINDS and section_label:
        group_kind, group_label = "primary_statement", section_label
    else:
        group_kind, group_label = "notes_or_other_contiguous", ""
    return {
        "group_kind": group_kind,
        "group_label": group_label,
        "reader_heading": reader_heading,
        "function_label": function_label,
        "section_label": section_label,
        "period_labels": [str(value) for value in segment.get("period_labels") or [] if str(value)],
        "unit_labels": [str(value) for value in segment.get("unit_labels") or [] if str(value)],
        "semantic_labels": _semantic_labels(asset),
    }


def _flush_chunk(
    *,
    document_id: str,
    ticker: str,
    report_year: int,
    scope: str,
    run_index: int,
    part_index: int,
    descriptor: Mapping[str, Any],
    members: list[Mapping[str, Any]],
) -> dict[str, Any]:
    member_ids = [str(member["internal_table_uid"]) for member in members]
    identity = {
        "document_id": document_id,
        "group_kind": descriptor["group_kind"],
        "group_label": descriptor["group_label"],
        "run_index": run_index,
        "part_index": part_index,
        "child_table_uids": member_ids,
    }
    chunk_id = _canonical_sha(identity)
    semantic_headers = _unique(
        [
            str(descriptor.get("group_label") or ""),
            *[str(member.get("reader_heading") or "") for member in members],
            *[str(member.get("function_label") or "") for member in members],
            *[str(member.get("section_label") or "") for member in members],
        ],
        limit=32,
    )
    semantic_labels = _unique(
        (label for member in members for label in member.get("semantic_labels") or []), limit=160
    )
    period_labels = _unique(
        (label for member in members for label in member.get("period_labels") or []), limit=12
    )
    unit_labels = _unique(
        (label for member in members for label in member.get("unit_labels") or []), limit=8
    )
    child_tables = [
        {
            "internal_table_uid": member["internal_table_uid"],
            "local_ordinal": member["local_ordinal"],
            "table_sha256": member["table_sha256"],
            "source_sha256": member["source_sha256"],
            "char_start": member["char_start"],
        }
        for member in members
    ]
    row = {
        "schema_version": 1,
        "protocol": SECTION_PROTOCOL,
        "section_chunk_id": chunk_id,
        # This compatibility field lets the existing dense builder store the
        # chunk in its metadata index without treating it as a source table.
        "internal_table_uid": chunk_id,
        "document_id": document_id,
        "ticker": ticker,
        "report_year": report_year,
        "scope": scope,
        "section_kind": descriptor["group_kind"],
        "section_label": descriptor["group_label"],
        "section_run_index": run_index,
        "section_part_index": part_index,
        "headers": semantic_headers,
        "row_paths": semantic_labels,
        "period_labels": period_labels,
        "unit_labels": unit_labels,
        "child_tables": child_tables,
        "source_contract": dict(SECTION_CONTRACT),
    }
    if _contains_forbidden(row):
        raise ValueError("section chunk leaked a raw value or reviewer field")
    return row


def build_section_chunk_assets(
    *,
    assets_path: Path,
    assets_manifest_path: Path,
    output_dir: Path,
    max_tables_per_chunk: int = 8,
) -> dict[str, Any]:
    """Build contiguous, heading-bounded semantic section chunks from all tables."""
    if max_tables_per_chunk < 1 or max_tables_per_chunk > 32:
        raise ValueError("max_tables_per_chunk must be between 1 and 32")
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    _require_asset_manifest(assets_path, assets_manifest_path)
    # The full corpus contains 146k tables and several large OCR contexts.
    # Process a single document at a time instead of retaining the complete
    # JSONL in memory; a source document is already contiguous in the frozen
    # asset closure.
    source_table_count = 0
    document_count = 0
    ticker_values: set[str] = set()
    asset_uids: set[str] = set()
    chunk_uids: set[str] = set()
    covered_table_uids: set[str] = set()
    chunk_count = 0
    kind_counts: Counter[str] = Counter()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        chunk_path = temporary / "section_chunk_assets_v1.jsonl"
        closure_path = temporary / "section_source_closure_v1.jsonl"
        with chunk_path.open("w", encoding="utf-8") as chunk_file, closure_path.open("w", encoding="utf-8") as closure_file:
            def emit_document(document_id: str, document_assets: list[dict[str, Any]]) -> None:
                nonlocal chunk_count, document_count
                if not document_assets:
                    return
                document_count += 1
                document_chunk_count = 0
                document_assets.sort(key=lambda row: int(row.get("local_ordinal") or 0))
                ticker_values.add(str(document_assets[0].get("ticker") or ""))
                for asset in document_assets:
                    uid = str(asset.get("internal_table_uid") or "")
                    if not uid or uid in asset_uids:
                        raise ValueError(f"table UID is missing or duplicated: {uid!r}")
                    asset_uids.add(uid)
                first = document_assets[0]
                ticker = str(first.get("ticker") or "")
                report_year = int(first["report_year"])
                scope = str(first.get("scope") or "unknown")
                if any(
                    str(asset.get("ticker") or "") != ticker
                    or int(asset["report_year"]) != report_year
                    or str(asset.get("scope") or "unknown") != scope
                    for asset in document_assets
                ):
                    raise ValueError(f"document metadata is inconsistent: {document_id}")
                run_index = 0
                current_key: tuple[str, str] | None = None
                current_descriptor: dict[str, Any] | None = None
                current_members: list[dict[str, Any]] = []
                part_index = 0

                def flush() -> None:
                    nonlocal part_index, chunk_count, document_chunk_count
                    if not current_members or current_descriptor is None:
                        return
                    chunk = _flush_chunk(
                        document_id=document_id,
                        ticker=ticker,
                        report_year=report_year,
                        scope=scope,
                        run_index=run_index,
                        part_index=part_index,
                        descriptor=current_descriptor,
                        members=current_members,
                    )
                    chunk_id = str(chunk["section_chunk_id"])
                    if chunk_id in chunk_uids:
                        raise ValueError("section chunk IDs are not unique")
                    chunk_uids.add(chunk_id)
                    for child in chunk["child_tables"]:
                        child_uid = str(child["internal_table_uid"])
                        if child_uid in covered_table_uids:
                            raise ValueError("one source table was assigned to multiple section chunks")
                        covered_table_uids.add(child_uid)
                    chunk_file.write(json.dumps(chunk, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
                    kind_counts[str(current_descriptor["group_kind"])] += 1
                    part_index += 1
                    chunk_count += 1
                    document_chunk_count += 1

                for asset in document_assets:
                    descriptor = _table_descriptor(asset)
                    key = (str(descriptor["group_kind"]), str(descriptor["group_label"]))
                    if current_key != key:
                        flush()
                        current_members = []
                        current_key = key
                        current_descriptor = descriptor
                        run_index += 1
                        part_index = 0
                    current_members.append(
                        {
                            "internal_table_uid": str(asset["internal_table_uid"]),
                            "local_ordinal": int(asset.get("local_ordinal") or 0),
                            "table_sha256": str(asset.get("table_sha256") or ""),
                            "source_sha256": str(asset.get("source_sha256") or ""),
                            "char_start": int(asset.get("char_start") or 0),
                            **descriptor,
                        }
                    )
                    if len(current_members) == max_tables_per_chunk:
                        flush()
                        current_members = []
                flush()
                closure_file.write(json.dumps({"document_id": document_id, "table_count": document_chunk_count}, ensure_ascii=False, sort_keys=True) + "\n")

            current_document_id = ""
            current_document_assets: list[dict[str, Any]] = []
            finished_documents: set[str] = set()
            for asset in load_jsonl(assets_path):
                source_table_count += 1
                document_id = str(asset.get("document_id") or "")
                if not document_id or not asset.get("internal_table_uid"):
                    raise ValueError("table asset is missing a document or table UID")
                if not current_document_id:
                    current_document_id = document_id
                if document_id != current_document_id:
                    emit_document(current_document_id, current_document_assets)
                    finished_documents.add(current_document_id)
                    if document_id in finished_documents:
                        raise ValueError("full table assets are not contiguous by document")
                    current_document_id = document_id
                    current_document_assets = []
                current_document_assets.append(asset)
            emit_document(current_document_id, current_document_assets)
        if source_table_count == 0:
            raise ValueError("table assets are empty")
        if covered_table_uids != asset_uids:
            raise ValueError("section chunks do not preserve one-to-one table coverage")
        ticker_values.discard("")
        contract = {
            "expected_asset_sha256": sha256_file(chunk_path),
            "expected_source_closure_sha256": sha256_file(closure_path),
            "expected_table_count": chunk_count,
            "expected_document_count": document_count,
            "expected_source_report_count": document_count,
            "expected_zero_table_report_count": 0,
            "expected_ticker_count": len(ticker_values),
        }
        summary = {
            "schema_version": 1,
            "protocol": SECTION_PROTOCOL,
            "source_table_count": source_table_count,
            "section_chunk_count": chunk_count,
            "document_count": document_count,
            "section_kind_counts": dict(sorted(kind_counts.items())),
            "max_tables_per_chunk": max_tables_per_chunk,
            "source_contract": dict(SECTION_CONTRACT),
        }
        contract_path = temporary / "section_dense_contract_v1.json"
        summary_path = temporary / "coverage_report_v1.json"
        _write_json(contract_path, contract)
        _write_json(summary_path, summary)
        _write_json(
            temporary / "manifest.json",
            {
                "schema_version": 1,
                "protocol": SECTION_PROTOCOL,
                "inputs": {
                    "table_assets": {"path": str(assets_path), "sha256": sha256_file(assets_path)},
                    "table_assets_manifest": {"path": str(assets_manifest_path), "sha256": sha256_file(assets_manifest_path)},
                },
                "outputs": {
                    name: {"path": name, "sha256": sha256_file(temporary / name)}
                    for name in (chunk_path.name, closure_path.name, contract_path.name, summary_path.name)
                },
                "source_contract": dict(SECTION_CONTRACT),
            },
        )
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return summary


def validate_section_chunk_assets(artifact_dir: Path) -> dict[str, Any]:
    manifest = _json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != SECTION_PROTOCOL or manifest.get("source_contract") != SECTION_CONTRACT:
        raise ValueError("unexpected section chunk protocol or contract")
    assets_path = _require_descriptor((manifest.get("inputs") or {}).get("table_assets") or {}, name="source table assets")
    _require_descriptor((manifest.get("inputs") or {}).get("table_assets_manifest") or {}, name="source asset manifest")
    for descriptor in (manifest.get("outputs") or {}).values():
        _require_descriptor(descriptor, name="section output", root=artifact_dir)
    chunks = _rows(artifact_dir / "section_chunk_assets_v1.jsonl")
    closure = _rows(artifact_dir / "section_source_closure_v1.jsonl")
    contract = _json(artifact_dir / "section_dense_contract_v1.json")
    summary = _json(artifact_dir / "coverage_report_v1.json")
    if not chunks or any(_contains_forbidden(chunk) or chunk.get("source_contract") != SECTION_CONTRACT for chunk in chunks):
        raise ValueError("section chunks lost their value-blind contract")
    if len({str(chunk["section_chunk_id"]) for chunk in chunks}) != len(chunks):
        raise ValueError("duplicate section chunk ID")
    child_uids = [str(child["internal_table_uid"]) for chunk in chunks for child in chunk.get("child_tables") or []]
    asset_uids = [str(asset["internal_table_uid"]) for asset in load_jsonl(assets_path)]
    if len(child_uids) != len(set(child_uids)) or set(child_uids) != set(asset_uids):
        raise ValueError("section chunks do not preserve exact table coverage")
    if int(contract.get("expected_table_count") or 0) != len(chunks) or int(summary.get("section_chunk_count") or 0) != len(chunks):
        raise ValueError("section count mismatch")
    if len(closure) != int(contract.get("expected_source_report_count") or 0):
        raise ValueError("section source closure mismatch")
    return {
        "status": "PASS",
        "source_table_count": len(asset_uids),
        "section_chunk_count": len(chunks),
        "answer_eligible": False,
        "submission_eligible": False,
    }


def build_section_rag_table_baseline(
    *,
    hybrid_artifact_dir: Path,
    output_dir: Path,
    full_corpus_artifact_dir: Path | None = None,
    route_materialization_dir: Path | None = None,
    navigation_extension_question_ids: Iterable[int] = (),
) -> dict[str, Any]:
    """Make a minimal, value-blind table-only baseline for section-RAG comparison.

    The older hybrid candidate artifact carries a legacy reviewer flag even
    though it is not an authorization source. This projection preserves only
    the navigation fields needed for a fair lane comparison and binds them to
    the frozen upstream manifest. It deliberately does not copy that flag.
    """
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    extension_question_ids = tuple(sorted({int(question_id) for question_id in navigation_extension_question_ids}))
    if (full_corpus_artifact_dir is None) != (route_materialization_dir is None):
        raise ValueError("full-corpus and route-materialization artifacts must be provided together")
    if extension_question_ids and full_corpus_artifact_dir is None:
        raise ValueError("navigation extension IDs require source-bound materialization artifacts")
    source_manifest_path = hybrid_artifact_dir / "manifest.json"
    source_manifest = _json(source_manifest_path)
    source_routes = _artifact_output(
        source_manifest, hybrid_artifact_dir, "route_comparison_v1.jsonl", name="source hybrid route comparison"
    )
    source_candidates = _artifact_output(
        source_manifest, hybrid_artifact_dir, "hybrid_table_candidates_v1.jsonl", name="source hybrid candidates"
    )
    routes: list[dict[str, Any]] = []
    route_ids: set[str] = set()
    for source in _rows(source_routes):
        route_id = str(source.get("route_id") or "")
        ticker = str(source.get("ticker") or "").strip().upper()
        query = str(source.get("metric_core_query") or "").strip()
        year = source.get("report_year")
        if not route_id or route_id in route_ids or not ticker or not query or isinstance(year, bool) or not isinstance(year, int):
            raise ValueError("source hybrid route is incomplete or duplicated")
        route_ids.add(route_id)
        route = {
            "schema_version": 1,
            "protocol": TABLE_BASELINE_PROTOCOL,
            "route_id": route_id,
            "question_id": int(source["question_id"]),
            "operand_id": str(source["operand_id"]),
            "metric_core_query": query,
            "ticker": ticker,
            "report_year": year,
            "requested_scope": source.get("requested_scope"),
            "source_contract": dict(SECTION_CONTRACT),
        }
        if _contains_forbidden(route):
            raise ValueError("baseline route leaked a reviewer or value field")
        routes.append(route)
    candidates: list[dict[str, Any]] = []
    observed_ranks: dict[str, set[int]] = defaultdict(set)
    observed_routes: set[str] = set()
    for source in _rows(source_candidates):
        route_id = str(source.get("route_id") or "")
        table_uid = str(source.get("internal_table_uid") or "")
        rank = source.get("hybrid_rank")
        if route_id not in route_ids or not table_uid or isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
            raise ValueError("source hybrid candidate is incomplete")
        if rank in observed_ranks[route_id]:
            raise ValueError("source hybrid candidate ranks are duplicated")
        observed_ranks[route_id].add(rank)
        observed_routes.add(route_id)
        candidate = {
            "schema_version": 1,
            "protocol": TABLE_BASELINE_PROTOCOL,
            "route_id": route_id,
            "internal_table_uid": table_uid,
            "hybrid_rank": rank,
            "source_contract": dict(SECTION_CONTRACT),
        }
        if _contains_forbidden(candidate):
            raise ValueError("baseline candidate leaked a reviewer or value field")
        candidates.append(candidate)
    if observed_routes != route_ids:
        raise ValueError("source hybrid candidates do not cover every route")

    extension_inputs: dict[str, Path] = {}
    extension_route_count = 0
    if extension_question_ids:
        assert full_corpus_artifact_dir is not None
        assert route_materialization_dir is not None
        full_manifest_path = full_corpus_artifact_dir / "manifest.json"
        materialization_manifest_path = route_materialization_dir / "manifest.json"
        full_manifest = _json(full_manifest_path)
        materialization_manifest = _json(materialization_manifest_path)
        if materialization_manifest.get("source_contract") != {
            "research_only": True,
            "machine_recheck_only": True,
            "evidence_eligible": False,
            "may_materialize_answer": False,
            "training_eligible": False,
            "submission_eligible": False,
            "promotion_allowed": False,
        }:
            raise ValueError("navigation extension requires a non-authorizing materialization artifact")
        full_candidates_path = _artifact_output(
            full_manifest,
            full_corpus_artifact_dir,
            "table_candidates_v1.jsonl",
            name="full-corpus navigation candidates",
        )
        audit_descriptor = (
            (materialization_manifest.get("outputs") or {}).get("audit")
            or (materialization_manifest.get("outputs") or {}).get("single_period_subtract_route_audit_v1.jsonl")
            or {}
        )
        audit_path = _require_descriptor(
            {
                "path": str(audit_descriptor.get("path") or "single_period_subtract_route_audit_v1.jsonl"),
                "sha256": audit_descriptor.get("sha256"),
            },
            name="materialized navigation audit",
            root=route_materialization_dir,
        )
        materialized_ids = {
            int(row["question_id"])
            for row in _rows(audit_path)
            if int(row.get("question_id") or 0) in extension_question_ids
            and row.get("materialization_status") == MATERIALIZED_NAVIGATION_STATUS
            and row.get("raw_numeric_values_included") is False
            and row.get("source_contract") == materialization_manifest.get("source_contract")
        }
        if materialized_ids != set(extension_question_ids):
            raise ValueError("every navigation extension question must be materialized and value-blind")
        extension_candidates: defaultdict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
        for source in _rows(full_candidates_path):
            question_id = int(source.get("question_id") or 0)
            if question_id not in materialized_ids:
                continue
            if (
                source.get("navigation_metadata_only") is not True
                or source.get("may_authorize_answer") is not False
                or source.get("may_authorize_evidence") is not False
                or source.get("submission_eligible") is not False
                or source.get("training_eligible") is not False
            ):
                raise ValueError("full-corpus extension candidate is not navigation-only")
            operand_id = str(source.get("operand_id") or "")
            query = str(source.get("metric_core_query") or "").strip()
            ticker = str(source.get("ticker") or "").strip().upper()
            report_year = source.get("report_year")
            scope = source.get("requested_scope")
            table_uid = str(source.get("internal_table_uid") or "")
            rank = source.get("candidate_rank")
            if (
                not operand_id
                or not query
                or not ticker
                or isinstance(report_year, bool)
                or not isinstance(report_year, int)
                or not table_uid
                or isinstance(rank, bool)
                or not isinstance(rank, int)
                or rank < 1
            ):
                raise ValueError("full-corpus extension candidate is incomplete")
            extension_candidates[(question_id, operand_id)].append(
                {
                    "question_id": question_id,
                    "operand_id": operand_id,
                    "metric_core_query": query,
                    "ticker": ticker,
                    "report_year": report_year,
                    "requested_scope": scope,
                    "internal_table_uid": table_uid,
                    "hybrid_rank": rank,
                }
            )
        if not extension_candidates or {question_id for question_id, _ in extension_candidates} != materialized_ids:
            raise ValueError("materialized navigation extension has no source candidates")
        for (question_id, operand_id), source_rows in sorted(extension_candidates.items()):
            source_rows.sort(key=lambda row: int(row["hybrid_rank"]))
            observed_extension_ranks = [int(row["hybrid_rank"]) for row in source_rows]
            if len(observed_extension_ranks) != len(set(observed_extension_ranks)):
                raise ValueError("navigation extension candidate ranks are duplicated")
            first = source_rows[0]
            if any(
                row["metric_core_query"] != first["metric_core_query"]
                or row["ticker"] != first["ticker"]
                or row["report_year"] != first["report_year"]
                or row["requested_scope"] != first["requested_scope"]
                for row in source_rows
            ):
                raise ValueError("navigation extension route metadata is inconsistent")
            route_id = f"materialized_navigation:q{question_id}:{operand_id}"
            if route_id in route_ids:
                raise ValueError("navigation extension route collides with the frozen baseline")
            route_ids.add(route_id)
            route = {
                "schema_version": 1,
                "protocol": TABLE_BASELINE_PROTOCOL,
                "route_id": route_id,
                "question_id": question_id,
                "operand_id": operand_id,
                "metric_core_query": first["metric_core_query"],
                "ticker": first["ticker"],
                "report_year": first["report_year"],
                "requested_scope": first["requested_scope"],
                "route_origin": "materialized_source_candidate_navigation_only",
                "source_contract": dict(SECTION_CONTRACT),
            }
            if _contains_forbidden(route):
                raise ValueError("navigation extension route leaked a reviewer or value field")
            routes.append(route)
            for source_row in source_rows:
                candidate = {
                    "schema_version": 1,
                    "protocol": TABLE_BASELINE_PROTOCOL,
                    "route_id": route_id,
                    "internal_table_uid": source_row["internal_table_uid"],
                    "hybrid_rank": source_row["hybrid_rank"],
                    "source_contract": dict(SECTION_CONTRACT),
                }
                if _contains_forbidden(candidate):
                    raise ValueError("navigation extension candidate leaked a reviewer or value field")
                candidates.append(candidate)
            extension_route_count += 1
        extension_inputs = {
            "full_corpus_manifest": full_manifest_path,
            "full_corpus_candidates": full_candidates_path,
            "route_materialization_manifest": materialization_manifest_path,
            "route_materialization_audit": audit_path,
        }
    candidates.sort(key=lambda row: (str(row["route_id"]), int(row["hybrid_rank"])))
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        route_path = temporary / "route_comparison_v1.jsonl"
        candidate_path = temporary / "hybrid_table_candidates_v1.jsonl"
        _write_jsonl(route_path, routes)
        _write_jsonl(candidate_path, candidates)
        _write_json(
            temporary / "manifest.json",
            {
                "schema_version": 1,
                "protocol": TABLE_BASELINE_PROTOCOL,
                "inputs": {
                    "source_hybrid_manifest": {"path": str(source_manifest_path), "sha256": sha256_file(source_manifest_path)},
                    "source_hybrid_routes": {"path": str(source_routes), "sha256": sha256_file(source_routes)},
                    "source_hybrid_candidates": {"path": str(source_candidates), "sha256": sha256_file(source_candidates)},
                    **{
                        name: {"path": str(path), "sha256": sha256_file(path)}
                        for name, path in extension_inputs.items()
                    },
                },
                "outputs": {
                    route_path.name: {"path": route_path.name, "sha256": sha256_file(route_path)},
                    candidate_path.name: {"path": candidate_path.name, "sha256": sha256_file(candidate_path)},
                },
                "source_contract": dict(SECTION_CONTRACT),
            },
        )
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {
        "status": "BUILT",
        "route_count": len(routes),
        "candidate_count": len(candidates),
        "navigation_extension_route_count": extension_route_count,
        "answer_eligible": False,
        "submission_eligible": False,
    }


def validate_section_rag_table_baseline(artifact_dir: Path, *, expected_route_count: int | None = None) -> dict[str, Any]:
    """Validate the minimal comparison baseline and its no-reviewer contract."""
    manifest = _json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != TABLE_BASELINE_PROTOCOL or manifest.get("source_contract") != SECTION_CONTRACT:
        raise ValueError("unexpected section RAG table baseline protocol or contract")
    for descriptor in (manifest.get("inputs") or {}).values():
        _require_descriptor(descriptor, name="table baseline input")
    routes_path = _artifact_output(manifest, artifact_dir, "route_comparison_v1.jsonl", name="table baseline routes")
    candidates_path = _artifact_output(manifest, artifact_dir, "hybrid_table_candidates_v1.jsonl", name="table baseline candidates")
    routes = _rows(routes_path)
    candidates = _rows(candidates_path)
    if not routes or (expected_route_count is not None and len(routes) != expected_route_count):
        raise ValueError("table baseline route count is unexpected")
    route_ids = [str(row.get("route_id") or "") for row in routes]
    route_id_set = set(route_ids)
    if not all(route_ids) or len(route_ids) != len(route_id_set):
        raise ValueError("table baseline routes are missing or duplicated")
    if any(_contains_forbidden(row) or row.get("source_contract") != SECTION_CONTRACT for row in [*routes, *candidates]):
        raise ValueError("table baseline leaked a reviewer or value field")
    ranks: dict[str, set[int]] = defaultdict(set)
    candidate_routes: set[str] = set()
    for row in candidates:
        route_id = str(row.get("route_id") or "")
        rank = row.get("hybrid_rank")
        if route_id not in route_id_set or not str(row.get("internal_table_uid") or "") or isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
            raise ValueError("table baseline candidate is invalid")
        if rank in ranks[route_id]:
            raise ValueError("table baseline candidate rank is duplicated")
        ranks[route_id].add(rank)
        candidate_routes.add(route_id)
    if candidate_routes != route_id_set:
        raise ValueError("table baseline candidates do not cover every route")
    return {
        "status": "PASS",
        "route_count": len(routes),
        "candidate_count": len(candidates),
        "answer_eligible": False,
        "submission_eligible": False,
    }


def _artifact_output(manifest: Mapping[str, Any], artifact_dir: Path, filename: str, *, name: str) -> Path:
    descriptor = (manifest.get("outputs") or {}).get(filename) or {}
    return _require_descriptor({"path": filename, "sha256": descriptor.get("sha256")}, name=name, root=artifact_dir)


def _merge_parent_results(
    scoped: list[Mapping[str, Any]], unscoped: list[Mapping[str, Any]], *, scope: str | None, limit: int
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for lane, rows in (("explicit_scope", scoped), ("unscoped_supplement", unscoped)):
        for row in rows:
            chunk_id = str(row["internal_table_uid"])
            if chunk_id in seen or len(selected) >= limit:
                continue
            observed_scope = str(row.get("scope") or "unknown")
            selected.append(
                {
                    "section_chunk_id": chunk_id,
                    "section_rank": len(selected) + 1,
                    "section_lane": lane if scope is not None else "unscoped",
                    "scope_match": None if scope is None else observed_scope == scope,
                }
            )
            seen.add(chunk_id)
    return selected


def _section_table_ranks(
    parent_rows: Iterable[Mapping[str, Any]], sections: Mapping[str, Mapping[str, Any]]
) -> dict[str, tuple[int, int]]:
    """Expand navigation-only section parents into deterministically ranked tables."""
    ranks: dict[str, tuple[int, int]] = {}
    for parent in parent_rows:
        chunk = sections.get(str(parent["section_chunk_id"]))
        if chunk is None:
            raise ValueError("dense search returned an unknown section chunk")
        for child_position, child in enumerate(chunk.get("child_tables") or [], start=1):
            uid = str(child["internal_table_uid"])
            proposed = (int(parent["section_rank"]), child_position)
            if uid not in ranks or proposed < ranks[uid]:
                ranks[uid] = proposed
    return ranks


def _diagnostic_targets(
    *, bindings_path: Path | None, execution_path: Path | None
) -> dict[int, set[str]]:
    if bindings_path is None or execution_path is None:
        return {}
    execution = {int(row["question_id"]): row for row in _rows(execution_path)}
    targets: defaultdict[int, set[str]] = defaultdict(set)
    for row in _rows(bindings_path):
        question_id = int(row["question_id"])
        if execution.get(question_id, {}).get("execution_status") != "execution_replay_ready":
            continue
        for stage in row.get("stages") or []:
            for operand in (stage.get("required_operands") or []) if isinstance(stage, Mapping) else []:
                if isinstance(operand, Mapping) and operand.get("binding_status") == "binding_ready":
                    uid = str(operand.get("internal_table_uid") or "")
                    if uid:
                        targets[question_id].add(uid)
    return dict(targets)


def evaluate_section_hierarchical_rag(
    *,
    config_path: Path,
    section_artifact_dir: Path,
    section_dense_index_dir: Path,
    hybrid_artifact_dir: Path,
    output_dir: Path,
    requested_device: str = "cpu",
    bindings_path: Path | None = None,
    execution_path: Path | None = None,
    encoder_factory: Any | None = None,
) -> dict[str, Any]:
    """Compare table-only, section-only, and section→table retrieval candidates."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    config = _json(config_path)
    if config.get("protocol") != EVALUATION_PROTOCOL:
        raise ValueError("unexpected section evaluation config")
    section_manifest = _json(section_artifact_dir / "manifest.json")
    if section_manifest.get("protocol") != SECTION_PROTOCOL:
        raise ValueError("unexpected section artifact")
    section_assets_path = _artifact_output(section_manifest, section_artifact_dir, "section_chunk_assets_v1.jsonl", name="section chunks")
    section_closure_path = _artifact_output(section_manifest, section_artifact_dir, "section_source_closure_v1.jsonl", name="section closure")
    section_dense_contract = _artifact_output(section_manifest, section_artifact_dir, "section_dense_contract_v1.json", name="section dense contract")
    dense_manifest = _json(section_dense_index_dir / "manifest.json")
    dense_inputs = dense_manifest.get("inputs") or {}
    if str((dense_inputs.get("assets") or {}).get("sha256") or "") != sha256_file(section_assets_path):
        raise ValueError("section dense index is not bound to these section chunks")
    if str((dense_inputs.get("source_closure") or {}).get("sha256") or "") != sha256_file(section_closure_path):
        raise ValueError("section dense index is not bound to this section closure")
    for filename, descriptor in (dense_manifest.get("outputs") or {}).items():
        _require_descriptor({"path": str(section_dense_index_dir / filename), "sha256": descriptor.get("sha256")}, name="section dense output")
    if not section_dense_contract.is_file():
        raise ValueError("section dense contract is missing")

    hybrid_manifest = _json(hybrid_artifact_dir / "manifest.json")
    if hybrid_manifest.get("protocol") != TABLE_BASELINE_PROTOCOL or hybrid_manifest.get("source_contract") != SECTION_CONTRACT:
        raise ValueError("section evaluation requires the sanitized table-only baseline")
    route_path = _artifact_output(hybrid_manifest, hybrid_artifact_dir, "route_comparison_v1.jsonl", name="hybrid route comparison")
    candidate_path = _artifact_output(hybrid_manifest, hybrid_artifact_dir, "hybrid_table_candidates_v1.jsonl", name="hybrid table candidates")
    routes = _rows(route_path)
    if len(routes) != int(config["expected_route_count"]):
        raise ValueError("route count does not match section evaluation config")
    candidates = _rows(candidate_path)
    sections = {str(row["section_chunk_id"]): row for row in _rows(section_assets_path)}
    if not sections:
        raise ValueError("section chunks are empty")
    table_candidates: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        route_id = str(candidate["route_id"])
        table_candidates[route_id].append(candidate)
    for values in table_candidates.values():
        values.sort(key=lambda item: int(item["hybrid_rank"]))
    if {str(route["route_id"]) for route in routes} != set(table_candidates):
        raise ValueError("section evaluation requires table candidates for every route")

    parent_limit = int(config["retrieval"]["parent_top_k"])
    output_limit = int(config["retrieval"]["output_table_top_k"])
    rrf_constant = int(config["retrieval"]["rrf_constant"])
    period_neighbor = config["retrieval"].get("period_neighbor") or {}
    if not isinstance(period_neighbor, Mapping):
        raise ValueError("period_neighbor must be an object when provided")
    period_neighbor_enabled = bool(period_neighbor.get("enabled", False))
    period_neighbor_offset = int(period_neighbor.get("report_year_offset", 1))
    period_neighbor_limit = int(period_neighbor.get("parent_top_k", parent_limit))
    period_aware_quota = period_neighbor.get("two_period_quota") or {}
    if not isinstance(period_aware_quota, Mapping):
        raise ValueError("two_period_quota must be an object when provided")
    period_aware_quota_enabled = bool(period_aware_quota.get("enabled", False))
    standard_quota_slots = int(period_aware_quota.get("standard_hierarchical_slots", 0))
    neighbor_quota_slots = int(period_aware_quota.get("period_neighbor_slots", 0))
    if period_neighbor_enabled and (period_neighbor_offset < 1 or period_neighbor_offset > 2):
        raise ValueError("research period-neighbor offset must be one or two report years")
    if period_neighbor_enabled and (period_neighbor_limit < 1 or period_neighbor_limit > 20):
        raise ValueError("research period-neighbor parent limit must be between 1 and 20")
    if period_aware_quota_enabled and not period_neighbor_enabled:
        raise ValueError("two-period quota requires period-neighbor retrieval")
    if period_aware_quota_enabled and (
        standard_quota_slots < 1
        or neighbor_quota_slots < 1
        or standard_quota_slots + neighbor_quota_slots > output_limit
    ):
        raise ValueError("two-period quota must reserve positive slots within the output table limit")
    scoped_requests: list[dict[str, Any]] = []
    scoped_positions: dict[str, int] = {}
    unscoped_requests: list[dict[str, Any]] = []
    neighbor_scoped_requests: list[dict[str, Any]] = []
    neighbor_scoped_positions: dict[str, int] = {}
    neighbor_unscoped_requests: list[dict[str, Any]] = []
    for route in routes:
        route_id = str(route["route_id"])
        scope = route.get("requested_scope")
        if scope is not None:
            scoped_positions[route_id] = len(scoped_requests)
            scoped_requests.append({"query": route["metric_core_query"], "ticker": route["ticker"], "report_year": int(route["report_year"]), "scope": scope})
        unscoped_requests.append({"query": route["metric_core_query"], "ticker": route["ticker"], "report_year": int(route["report_year"])})
        if period_neighbor_enabled:
            neighbor_year = int(route["report_year"]) + period_neighbor_offset
            if scope is not None:
                neighbor_scoped_positions[route_id] = len(neighbor_scoped_requests)
                neighbor_scoped_requests.append({"query": route["metric_core_query"], "ticker": route["ticker"], "report_year": neighbor_year, "scope": scope})
            neighbor_unscoped_requests.append({"query": route["metric_core_query"], "ticker": route["ticker"], "report_year": neighbor_year})
    scoped_results = search_dense_batch(
        index_dir=section_dense_index_dir,
        requests=scoped_requests,
        limit=parent_limit,
        requested_device=requested_device,
        encode_batch_size=int(config["retrieval"]["encode_batch_size"]),
        encoder_factory=encoder_factory,
    ) if scoped_requests else []
    unscoped_results = search_dense_batch(
        index_dir=section_dense_index_dir,
        requests=unscoped_requests,
        limit=parent_limit,
        requested_device=requested_device,
        encode_batch_size=int(config["retrieval"]["encode_batch_size"]),
        encoder_factory=encoder_factory,
    )
    neighbor_scoped_results = search_dense_batch(
        index_dir=section_dense_index_dir,
        requests=neighbor_scoped_requests,
        limit=period_neighbor_limit,
        requested_device=requested_device,
        encode_batch_size=int(config["retrieval"]["encode_batch_size"]),
        encoder_factory=encoder_factory,
    ) if neighbor_scoped_requests else []
    neighbor_unscoped_results = search_dense_batch(
        index_dir=section_dense_index_dir,
        requests=neighbor_unscoped_requests,
        limit=period_neighbor_limit,
        requested_device=requested_device,
        encode_batch_size=int(config["retrieval"]["encode_batch_size"]),
        encoder_factory=encoder_factory,
    ) if period_neighbor_enabled else []
    ready_targets = _diagnostic_targets(bindings_path=bindings_path, execution_path=execution_path)
    evaluations: list[dict[str, Any]] = []
    question_hits: defaultdict[int, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for position, route in enumerate(routes):
        route_id = str(route["route_id"])
        scope = route.get("requested_scope")
        parent_rows = _merge_parent_results(
            scoped_results[scoped_positions[route_id]] if route_id in scoped_positions else [],
            unscoped_results[position],
            scope=str(scope) if scope is not None else None,
            limit=parent_limit,
        )
        section_table_ranks = _section_table_ranks(parent_rows, sections)
        period_neighbor_parent_rows: list[dict[str, Any]] = []
        period_neighbor_table_ranks: dict[str, tuple[int, int, int]] = {}
        if period_neighbor_enabled:
            next_parent_rows = _merge_parent_results(
                neighbor_scoped_results[neighbor_scoped_positions[route_id]] if route_id in neighbor_scoped_positions else [],
                neighbor_unscoped_results[position],
                scope=str(scope) if scope is not None else None,
                limit=period_neighbor_limit,
            )
            period_neighbor_parent_rows = [
                {**parent, "report_year_offset": period_neighbor_offset} for parent in next_parent_rows
            ]
            next_table_ranks = _section_table_ranks(period_neighbor_parent_rows, sections)
            # Round-robin child depth first. Otherwise a single broad section
            # can consume all ten table slots before another high-ranked parent
            # is represented; that defeats the purpose of section navigation.
            for uid, (parent_rank, child_rank) in section_table_ranks.items():
                period_neighbor_table_ranks[uid] = (child_rank, parent_rank, 0)
            for uid, (parent_rank, child_rank) in next_table_ranks.items():
                proposed = (child_rank, parent_rank, period_neighbor_offset)
                if uid not in period_neighbor_table_ranks or proposed < period_neighbor_table_ranks[uid]:
                    period_neighbor_table_ranks[uid] = proposed
        table_rank = {str(row["internal_table_uid"]): int(row["hybrid_rank"]) for row in table_candidates[route_id]}
        universe = set(table_rank) | set(section_table_ranks)
        scored = []
        for uid in universe:
            score = 0.0
            if uid in table_rank:
                score += 1.0 / (rrf_constant + table_rank[uid])
            if uid in section_table_ranks:
                score += 1.0 / (rrf_constant + section_table_ranks[uid][0])
            scored.append((uid, score))
        scored.sort(key=lambda pair: (-pair[1], table_rank.get(pair[0], 10**9), section_table_ranks.get(pair[0], (10**9, 10**9)), pair[0]))
        section_only = sorted(section_table_ranks, key=lambda uid: (section_table_ranks[uid], uid))[:output_limit]
        table_only = [str(row["internal_table_uid"]) for row in table_candidates[route_id][:output_limit]]
        hierarchical = [uid for uid, _score in scored[:output_limit]]
        period_neighbor_section_only = sorted(
            period_neighbor_table_ranks, key=lambda uid: (period_neighbor_table_ranks[uid], uid)
        )[:output_limit] if period_neighbor_enabled else []
        period_aware_quota_tables: list[str] = []
        if period_aware_quota_enabled:
            for candidate_uids, quota in (
                (hierarchical, standard_quota_slots),
                (period_neighbor_section_only, neighbor_quota_slots),
                (hierarchical, output_limit),
                (period_neighbor_section_only, output_limit),
            ):
                for uid in candidate_uids[:quota]:
                    if uid not in period_aware_quota_tables:
                        period_aware_quota_tables.append(uid)
                    if len(period_aware_quota_tables) >= output_limit:
                        break
                if len(period_aware_quota_tables) >= output_limit:
                    break
        lane_values: list[tuple[str, list[str]]] = [
            ("table_only", table_only),
            ("section_only", section_only),
            ("hierarchical", hierarchical),
        ]
        if period_neighbor_enabled:
            lane_values.append(("period_neighbor_section_only", period_neighbor_section_only))
        if period_aware_quota_enabled:
            lane_values.append(("period_aware_quota", period_aware_quota_tables))
        for lane, uids in lane_values:
            question_hits[int(route["question_id"])][lane].update(uids)
        row = {
            "schema_version": 1,
            "protocol": EVALUATION_PROTOCOL,
            "route_id": route_id,
            "question_id": int(route["question_id"]),
            "operand_id": route["operand_id"],
            "parent_sections": parent_rows,
            "table_only_table_uids": table_only,
            "section_only_table_uids": section_only,
            "hierarchical_table_uids": hierarchical,
            "candidate_counts": {
                "table_only": len(table_rank),
                "section_only": len(section_table_ranks),
                "hierarchical_union": len(universe),
            },
            "source_contract": dict(SECTION_CONTRACT),
        }
        if period_neighbor_enabled:
            row["period_neighbor_parent_sections"] = period_neighbor_parent_rows
            row["period_neighbor_section_only_table_uids"] = period_neighbor_section_only
            row["candidate_counts"]["period_neighbor_section_only"] = len(period_neighbor_table_ranks)
        if period_aware_quota_enabled:
            row["period_aware_quota_table_uids"] = period_aware_quota_tables
            row["candidate_counts"]["period_aware_quota_union"] = len(
                set(hierarchical) | set(period_neighbor_section_only)
            )
        if _contains_forbidden(row):
            raise ValueError("section evaluation leaked a value or reviewer field")
        evaluations.append(row)
    diagnostic_lanes = ["table_only", "section_only", "hierarchical"]
    if period_neighbor_enabled:
        diagnostic_lanes.append("period_neighbor_section_only")
    if period_aware_quota_enabled:
        diagnostic_lanes.append("period_aware_quota")
    diagnostic = {
        "kind": "exact_v2_execution_replay_ready_navigation_targets",
        "warning": "diagnostic target recall only; semantic authorization remains incomplete, so this is not answer or factual accuracy",
        "question_count": len(ready_targets),
        "target_table_count": sum(len(values) for values in ready_targets.values()),
        "lane_question_hit_counts": {
            lane: sum(bool(question_hits[question_id][lane] & targets) for question_id, targets in ready_targets.items())
            for lane in diagnostic_lanes
        },
        "lane_question_full_target_coverage_counts": {
            lane: sum(targets <= question_hits[question_id][lane] for question_id, targets in ready_targets.items())
            for lane in diagnostic_lanes
        },
        "lane_target_table_hit_counts": {
            lane: sum(len(question_hits[question_id][lane] & targets) for question_id, targets in ready_targets.items())
            for lane in diagnostic_lanes
        },
    }
    coverage = {
        "schema_version": 1,
        "protocol": EVALUATION_PROTOCOL,
        "route_count": len(evaluations),
        "parent_section_candidate_count": sum(len(row["parent_sections"]) for row in evaluations),
        "table_only_candidate_count": sum(len(row["table_only_table_uids"]) for row in evaluations),
        "section_only_candidate_count": sum(len(row["section_only_table_uids"]) for row in evaluations),
        "hierarchical_candidate_count": sum(len(row["hierarchical_table_uids"]) for row in evaluations),
        **({
            "period_neighbor_parent_section_candidate_count": sum(len(row["period_neighbor_parent_sections"]) for row in evaluations),
            "period_neighbor_section_only_candidate_count": sum(len(row["period_neighbor_section_only_table_uids"]) for row in evaluations),
        } if period_neighbor_enabled else {}),
        **({
            "period_aware_quota_candidate_count": sum(len(row["period_aware_quota_table_uids"]) for row in evaluations),
        } if period_aware_quota_enabled else {}),
        "diagnostic_target_recall": diagnostic,
        "accuracy_claim": "NOT_AVAILABLE_WITHOUT_INDEPENDENT_SOURCE_ADJUDICATED_GOLD",
        "source_contract": dict(SECTION_CONTRACT),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        evaluation_path = temporary / "section_hierarchical_route_evaluation_v1.jsonl"
        coverage_path = temporary / "coverage_report_v1.json"
        _write_jsonl(evaluation_path, evaluations)
        _write_json(coverage_path, coverage)
        input_paths: dict[str, Path] = {
            "config": config_path,
            "section_manifest": section_artifact_dir / "manifest.json",
            "section_dense_manifest": section_dense_index_dir / "manifest.json",
            "hybrid_manifest": hybrid_artifact_dir / "manifest.json",
        }
        if bindings_path is not None:
            input_paths["bindings"] = bindings_path
        if execution_path is not None:
            input_paths["execution"] = execution_path
        _write_json(
            temporary / "manifest.json",
            {
                "schema_version": 1,
                "protocol": EVALUATION_PROTOCOL,
                "inputs": {name: {"path": str(path), "sha256": sha256_file(path)} for name, path in input_paths.items()},
                "outputs": {
                    evaluation_path.name: {"path": evaluation_path.name, "sha256": sha256_file(evaluation_path)},
                    coverage_path.name: {"path": coverage_path.name, "sha256": sha256_file(coverage_path)},
                },
                "source_contract": dict(SECTION_CONTRACT),
            },
        )
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return coverage


def validate_section_hierarchical_rag(artifact_dir: Path, *, expected_route_count: int) -> dict[str, Any]:
    manifest = _json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != EVALUATION_PROTOCOL or manifest.get("source_contract") != SECTION_CONTRACT:
        raise ValueError("unexpected section evaluation protocol or contract")
    for descriptor in (manifest.get("inputs") or {}).values():
        _require_descriptor(descriptor, name="section evaluation input")
    for descriptor in (manifest.get("outputs") or {}).values():
        _require_descriptor(descriptor, name="section evaluation output", root=artifact_dir)
    rows = _rows(artifact_dir / "section_hierarchical_route_evaluation_v1.jsonl")
    coverage = _json(artifact_dir / "coverage_report_v1.json")
    if len(rows) != expected_route_count or int(coverage.get("route_count") or 0) != expected_route_count:
        raise ValueError("section evaluation route coverage mismatch")
    route_ids = [str(row["route_id"]) for row in rows]
    if len(route_ids) != len(set(route_ids)):
        raise ValueError("section evaluation has duplicate routes")
    for row in rows:
        if _contains_forbidden(row) or row.get("source_contract") != SECTION_CONTRACT:
            raise ValueError("section evaluation lost value-blind contract")
        parent_ids = [str(parent["section_chunk_id"]) for parent in row.get("parent_sections") or []]
        if len(parent_ids) != len(set(parent_ids)):
            raise ValueError("section evaluation has duplicate parents")
    if coverage.get("accuracy_claim") != "NOT_AVAILABLE_WITHOUT_INDEPENDENT_SOURCE_ADJUDICATED_GOLD":
        raise ValueError("section evaluation overclaims accuracy")
    return {
        "status": "PASS",
        "route_count": len(rows),
        "answer_eligible": False,
        "submission_eligible": False,
    }
