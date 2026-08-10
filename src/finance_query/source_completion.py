"""Read-only raw-source audit helpers for missing formula operands.

These helpers identify source tables which exist in a report but were not
included in an immutable review bundle.  They intentionally do *not* append
tables to the bundle, repair OCR, bind an operand, calculate a formula, or
change a review/training status.  A later source-completion step must validate
the candidate again against its raw table hash and the entity/scope contract.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .corpus import extract_assets_from_report, infer_document_id, infer_path_metadata
from .evidence_context import EVIDENCE_CONTEXT_VERSION, build_evidence_context
from .financial_metrics import fold_text
from .table_structure import parse_html_table


SOURCE_COMPLETION_PROTOCOL = "raw_source_completion_v1"
SOURCE_COMPLETION_CANDIDATE_SOURCE = "raw_source_completion_v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_report_index(reports_root: Path) -> dict[tuple[str, int], list[Path]]:
    """Index canonical raw reports by the ticker/year encoded in their path."""
    index: dict[tuple[str, int], list[Path]] = {}
    for path in sorted(reports_root.rglob("*_extracted.txt")):
        if not path.is_file():
            continue
        ticker, year = infer_path_metadata(path, reports_root)
        if ticker and year is not None:
            index.setdefault((ticker.casefold(), int(year)), []).append(path)
    return index


def operand_is_missing(evidence_set: Mapping[str, Any], operand_id: str) -> bool:
    """Whether a required operand has no exact V2 candidate at all."""
    return not bool((evidence_set.get("operand_matches") or {}).get(operand_id))


def operand_requires_scope_gap_probe(
    evidence_set: Mapping[str, Any], operand: Mapping[str, Any]
) -> bool:
    """Whether a staged multi-entity operand may audit an omitted scope table.

    An operand can be present in (for example) a ``separate`` report while
    another entity is provable only on a consolidated basis. Treating it as
    simply "not missing" hides the raw table needed to determine whether a
    common scope exists. This function does not bind the operand or choose a
    scope: it only permits a read-only raw-source audit for explicit staged,
    multi-entity programs with a declared entity per operand.
    """
    formula = evidence_set.get("formula") or {}
    if str(formula.get("execution_status") or "") != "stage_binding_required":
        return False
    entities = {
        str(value).strip()
        for value in formula.get("entities") or []
        if str(value).strip()
    }
    entity = str(operand.get("entity") or "").strip()
    return len(entities) >= 2 and bool(entity) and entity in entities


def _row_matches_metric(row: Iterable[Any], metric_hints: Iterable[str]) -> bool:
    folded_row = fold_text(" ".join(str(cell) for cell in row))
    return any(fold_text(hint) in folded_row for hint in metric_hints if fold_text(hint))


def source_audit_blockers(operand: Mapping[str, Any]) -> list[str]:
    """Explain why a missing operand cannot yet enter raw-source audit.

    This is diagnostic metadata only.  It does not relax the source-completion
    gate: a missing entity, year, literal metric, or structural table-function
    allow-list still prevents raw scanning from becoming evidence.
    """
    blockers: list[str] = []
    if not str(operand.get("entity") or "").strip():
        blockers.append("entity_not_resolved")
    years = [year for year in operand.get("years") or [] if str(year).isdigit()]
    if len(years) != 1:
        blockers.append("exactly_one_operand_year_required")
    if not any(str(value).strip() for value in operand.get("metric_hints") or []):
        blockers.append("literal_metric_hint_missing")
    if not any(str(value).strip() for value in operand.get("allowed_table_functions") or []):
        blockers.append("allowed_table_functions_missing")
    return blockers


def raw_source_candidates(
    operand: Mapping[str, Any],
    *,
    reports_root: Path,
    reports_by_ticker_year: Mapping[tuple[str, int], list[Path]],
    bundled_uids: set[str],
) -> tuple[str, list[dict[str, Any]]]:
    """Return exact raw-table candidates for a missing source operand.

    The first return value is a fail-closed audit finding, not a validation
    result.  Only raw tables whose structural `table_function` matches the
    operand allow-list and whose *row* contains an exact metric hint are
    returned.  No fuzzy recovery or label rewriting is performed.
    """
    blockers = source_audit_blockers(operand)
    entity = str(operand.get("entity") or "").strip()
    years = [int(year) for year in operand.get("years") or [] if str(year).isdigit()]
    metric_hints = [str(value) for value in operand.get("metric_hints") or [] if str(value).strip()]
    allowed_functions = {
        str(value) for value in operand.get("allowed_table_functions") or [] if str(value)
    }
    if blockers:
        return "operand_not_source_auditable", []

    paths = list(reports_by_ticker_year.get((entity.casefold(), years[0]), []))
    if not paths:
        return "raw_report_missing", []

    candidates: list[dict[str, Any]] = []
    for path in paths:
        for asset in extract_assets_from_report(path, reports_root):
            function = dict(asset.table_function)
            # Source completion may expose a raw table, but a title-derived
            # classifier is not enough to treat a note as a primary statement.
            # Require the existing independent row-signature classification.
            if (
                str(function.get("kind") or "") not in allowed_functions
                or str(function.get("specificity") or "") != "structural"
            ):
                continue
            matching_rows = [
                {"row_index": index, "row": list(row)}
                for index, row in enumerate(asset.rows)
                if _row_matches_metric(row, metric_hints)
            ]
            if not matching_rows:
                continue
            candidates.append(
                {
                    "raw_document_id": infer_document_id(path),
                    "ticker": asset.ticker,
                    "scope": asset.scope,
                    "report_year": asset.report_year,
                    "raw_table_uid": asset.internal_table_uid,
                    "raw_table_ordinal": asset.local_ordinal,
                    "raw_table_sha256": asset.table_sha256,
                    "raw_report_sha256": asset.source_sha256,
                    "raw_report_path": str(path.relative_to(reports_root)),
                    "page_no": asset.page_no,
                    "table_function": function,
                    "column_labels": list(asset.headers),
                    "matching_rows": matching_rows,
                    "already_in_immutable_bundle": asset.internal_table_uid in bundled_uids,
                }
            )

    if not candidates:
        return "raw_metric_or_statement_not_found", []
    # A scope-gap audit can find both a bundled `separate` table and an
    # omitted `consolidated` counterpart.  The omitted source must remain
    # materializable; do not let the existing sibling hide it behind the
    # generic already-in-bundle status.
    if any(not candidate["already_in_immutable_bundle"] for candidate in candidates):
        return "raw_source_present_not_in_bundle", candidates
    if any(candidate["already_in_immutable_bundle"] for candidate in candidates):
        return "raw_table_already_in_bundle_but_unbound", candidates
    raise AssertionError("raw source candidate state was not classified")


def _source_path_from_relative(reports_root: Path, relative_path: str) -> Path:
    """Resolve one audit-recorded source path without allowing traversal."""
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Source-completion audit path must be relative to reports root")
    root = reports_root.resolve()
    candidate = (root / relative).resolve()
    if root not in candidate.parents or not candidate.is_file():
        raise FileNotFoundError(candidate)
    return candidate


def revalidate_raw_source_candidate(
    candidate: Mapping[str, Any],
    *,
    reports_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Rebuild a completion table and V3 context from one audited raw source.

    The audit row is merely a locator.  This function independently recomputes
    report/table hashes, deterministic UID, parsed grid, source cell
    provenance, table function, canonical headers and context quality from the
    raw report before returning it.  It rejects a non-structural statement or a
    table that cannot become `review_ready`.
    """
    reports_root = reports_root.resolve()
    source_path = _source_path_from_relative(
        reports_root, str(candidate.get("raw_report_path") or "")
    )
    expected_report_sha = str(candidate.get("raw_report_sha256") or "")
    observed_report_sha = sha256_file(source_path)
    if not expected_report_sha or observed_report_sha != expected_report_sha:
        raise ValueError("Raw report SHA-256 differs from source-completion audit")

    expected_uid = str(candidate.get("raw_table_uid") or "")
    expected_ordinal = int(candidate.get("raw_table_ordinal") or 0)
    expected_table_sha = str(candidate.get("raw_table_sha256") or "")
    assets = extract_assets_from_report(source_path, reports_root)
    asset = next(
        (
            value
            for value in assets
            if value.internal_table_uid == expected_uid
            and int(value.local_ordinal) == expected_ordinal
        ),
        None,
    )
    if asset is None:
        raise ValueError("Raw source UID/ordinal no longer matches source-completion audit")
    if asset.table_sha256 != expected_table_sha:
        raise ValueError("Raw source table SHA-256 differs from source-completion audit")
    if str(candidate.get("raw_document_id") or "") != asset.document_id:
        raise ValueError("Raw source document ID differs from source-completion audit")
    if str(candidate.get("ticker") or "") != asset.ticker:
        raise ValueError("Raw source ticker differs from source-completion audit")
    if str(candidate.get("scope") or "") != asset.scope:
        raise ValueError("Raw source scope differs from source-completion audit")
    if int(candidate.get("report_year") or 0) != int(asset.report_year or 0):
        raise ValueError("Raw source report year differs from source-completion audit")

    expected_function = dict(candidate.get("table_function") or {})
    actual_function = dict(asset.table_function)
    if (
        expected_function.get("kind") != actual_function.get("kind")
        or actual_function.get("specificity") != "structural"
    ):
        raise ValueError("Raw source statement classification is not independently structural")

    raw_text = source_path.read_text(encoding="utf-8")
    table_html = raw_text[int(asset.char_start) : int(asset.char_end)]
    structure = parse_html_table(table_html, context=str(asset.context_before))
    if structure.get("rows") != asset.rows:
        raise ValueError("Raw source grid differs between audit and completion parse")
    if dict(structure.get("table_function") or {}) != actual_function:
        raise ValueError("Raw source table function changed during completion parse")

    table = {
        "internal_table_uid": asset.internal_table_uid,
        "document_id": asset.document_id,
        "ticker": asset.ticker,
        "report_year": asset.report_year,
        "scope": asset.scope,
        "local_ordinal": asset.local_ordinal,
        "source_provenance": {
            "source_path": str(source_path.relative_to(reports_root)),
            "source_sha256": observed_report_sha,
            "char_start": asset.char_start,
            "table_sha256": asset.table_sha256,
        },
        "source_completion": {
            "protocol": SOURCE_COMPLETION_PROTOCOL,
            "candidate_source": SOURCE_COMPLETION_CANDIDATE_SOURCE,
            "audit_only": False,
            "answer_eligible": False,
            "training_eligible": False,
            "review_status_promotion_allowed": False,
        },
        **structure,
    }
    context = build_evidence_context(table)
    if str((context.get("quality") or {}).get("status") or "") != "review_ready":
        raise ValueError("Raw source completion table is not canonical-header review_ready")
    return table, context


def source_completion_manifest_path(tables_path: Path) -> Path:
    """Return a manifest path without colliding with another shadow snapshot.

    The original V1 artifact name is part of the persisted bundle contract and
    therefore keeps its historical manifest name.  Any explicitly versioned or
    targeted completion table file receives an adjacent manifest, so a new
    audit can be materialized alongside the prior snapshot instead of
    overwriting it.
    """
    if tables_path.name == "source_completion_tables_v1.jsonl":
        return tables_path.with_name("source_completion_v1.manifest.json")
    return tables_path.with_suffix(".manifest.json")


def validate_source_completion_sidecar(
    bundle_dir: Path,
    tables_path: Path,
    contexts_path: Path,
) -> dict[str, Any]:
    """Validate an immutable-bundle-external source completion sidecar.

    This is intentionally a different contract from the V2 bundle sidecar: it
    verifies that the supplemental UIDs do not overlap the immutable bundle,
    and recomputes every V3 context from the supplemental raw grid.  It never
    treats the supplemental tables as a corpus/index replacement.
    """
    bundle_dir = bundle_dir.resolve()
    tables_path = tables_path.resolve()
    contexts_path = contexts_path.resolve()
    manifest_path = source_completion_manifest_path(tables_path)
    if not tables_path.is_file() or not contexts_path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError("Source-completion sidecar, context, or manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if str(manifest.get("protocol") or "") != SOURCE_COMPLETION_PROTOCOL:
        raise ValueError("Unsupported source-completion protocol")
    bundle_tables_path = bundle_dir / "tables.jsonl"
    if str(manifest.get("bundle_tables_sha256") or "") != sha256_file(bundle_tables_path):
        raise ValueError("Source-completion sidecar belongs to another immutable bundle")
    if str(manifest.get("tables_sidecar_sha256") or "") != sha256_file(tables_path):
        raise ValueError("Source-completion table sidecar checksum mismatch")
    if str(manifest.get("contexts_sidecar_sha256") or "") != sha256_file(contexts_path):
        raise ValueError("Source-completion context sidecar checksum mismatch")

    def load_jsonl(path: Path) -> list[dict[str, Any]]:
        with path.open(encoding="utf-8-sig") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    bundle_uids = {str(row["internal_table_uid"]) for row in load_jsonl(bundle_tables_path)}
    tables = load_jsonl(tables_path)
    contexts = load_jsonl(contexts_path)
    table_uids = {str(row.get("internal_table_uid") or "") for row in tables}
    context_by_uid = {str(row.get("internal_table_uid") or ""): row for row in contexts}
    if not table_uids or "" in table_uids or len(table_uids) != len(tables):
        raise ValueError("Source-completion table UIDs must be unique and non-empty")
    if table_uids & bundle_uids:
        raise ValueError("Source-completion table UID overlaps immutable bundle")
    if set(context_by_uid) != table_uids or len(contexts) != len(context_by_uid):
        raise ValueError("Source-completion context UID set differs from tables")
    for table in tables:
        source_completion = table.get("source_completion") or {}
        if str(source_completion.get("protocol") or "") != SOURCE_COMPLETION_PROTOCOL:
            raise ValueError("Source-completion table lacks protocol provenance")
        if bool(source_completion.get("answer_eligible")) or bool(source_completion.get("training_eligible")):
            raise ValueError("Source-completion table cannot be answer/training eligible")
        context = context_by_uid[str(table["internal_table_uid"])]
        if int(context.get("evidence_context_version") or 0) != EVIDENCE_CONTEXT_VERSION:
            raise ValueError("Source-completion context is not current V3")
        if build_evidence_context(table) != context:
            raise ValueError("Source-completion context does not reproduce from raw grid")
    return manifest
