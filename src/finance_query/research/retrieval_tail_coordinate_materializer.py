"""Materialize value-blind coordinates for the lexical retrieval tail.

The full-corpus retrieval artifact stops at table/row navigation.  This
adapter resolves one candidate column from the current builder's period/header
resolver, but emits only coordinates and source hashes.  The primary builder
must hydrate and replay every hint again; this artifact cannot authorize an
answer or a submission.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from finance_query.research.full_corpus_candidate_retrieval import sha256_file
from finance_query.research.full_corpus_candidate_retrieval import validate_candidate_artifact


PROTOCOL = "vifinqa_retrieval_tail_coordinate_materializer_v1"
RETRIEVAL_PROTOCOL = "vifinqa_full_corpus_candidate_retrieval_v1"
CONTRACT = {
    "research_only": True,
    "navigation_metadata_only": True,
    "coordinates_are_unverified_hints": True,
    "numeric_values_emitted": False,
    "source_values_emitted": False,
    "may_authorize_evidence": False,
    "may_authorize_answer": False,
    "training_eligible": False,
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
        "row_label",
        "rows",
        "source_value_cell",
        "source_cell",
        "raw_source_cell",
        "raw_source_row",
    }
)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} must contain a JSON object")
            yield value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, values: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for value in values
        ),
        encoding="utf-8",
    )


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key) in FORBIDDEN_KEYS or _contains_forbidden(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden(child) for child in value)
    return False


def _canonical_id(value: object) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return str(int(text)) if text.isdigit() else text


def _int_or_none(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _load_builder(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("tail_coordinate_builder", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load builder: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _candidate_tail_keys(
    candidate_path: Path,
    *,
    min_rank: int,
    max_rank: int,
) -> tuple[set[tuple[str, str, int, str, int]], dict[str, Any]]:
    keys: set[tuple[str, str, int, str, int]] = set()
    candidate_uid_set: set[str] = set()
    route_set: set[tuple[str, str, int]] = set()
    rank_counts: Counter[int] = Counter()
    row_count = 0
    for row in _read_jsonl(candidate_path):
        rank = _int_or_none(row.get("candidate_rank"))
        if rank is None or not min_rank <= rank <= max_rank:
            continue
        question_id = _canonical_id(row.get("question_id"))
        operand_id = str(row.get("operand_id") or "").strip()
        report_year = _int_or_none(row.get("report_year"))
        uid = str(row.get("internal_table_uid") or "").strip()
        if not question_id or not operand_id or report_year is None or not uid:
            raise ValueError("tail candidate is missing route identity")
        keys.add((question_id, operand_id, report_year, uid, rank))
        candidate_uid_set.add(uid)
        route_set.add((question_id, operand_id, report_year))
        rank_counts[rank] += 1
        row_count += 1
    return keys, {
        "tail_candidate_table_row_count": row_count,
        "tail_candidate_unique_uid_count": len(candidate_uid_set),
        "tail_route_count": len(route_set),
        "tail_rank_counts": dict(sorted(rank_counts.items())),
        "min_rank": min_rank,
        "max_rank": max_rank,
    }


def _load_questions(path: Path) -> dict[str, str]:
    questions: dict[str, str] = {}
    for row in _read_jsonl(path):
        question_id = _canonical_id(row.get("id", row.get("question_id")))
        question = str(row.get("question") or "").strip()
        if not question_id or question_id in questions or not question:
            raise ValueError(f"invalid question record in {path}")
        questions[question_id] = question
    return questions


def _load_tail_packets(
    row_review_path: Path,
    *,
    tail_keys: set[tuple[str, str, int, str, int]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    packets_by_uid: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    matched_keys: set[tuple[str, str, int, str, int]] = set()
    matched_tail_keys: set[tuple[str, str, int, str, int]] = set()
    packet_count = 0
    duplicate_count = 0
    for row in _read_jsonl(row_review_path):
        question_id = _canonical_id(row.get("question_id"))
        operand_id = str(row.get("operand_id") or "").strip()
        report_year = _int_or_none(row.get("report_year"))
        uid = str(row.get("internal_table_uid") or "").strip()
        rank = _int_or_none(row.get("candidate_rank"))
        row_index = _int_or_none(row.get("row_index"))
        key = (question_id, operand_id, report_year, uid, rank)
        if key not in tail_keys:
            continue
        if row_index is None or row_index < 0:
            raise ValueError("tail row-review packet has an invalid row index")
        packet = {
            "question_id": question_id,
            "operand_id": operand_id,
            "report_year": report_year,
            "internal_table_uid": uid,
            "candidate_rank": rank,
            "row_index": row_index,
            "table_sha256": str(row.get("table_sha256") or ""),
        }
        packet_key = (*key, row_index)
        if packet_key in matched_keys:
            duplicate_count += 1
            continue
        matched_keys.add(packet_key)
        matched_tail_keys.add(key)
        packets_by_uid[uid].append(packet)
        packet_count += 1
    missing_tail_keys = len(tail_keys - matched_tail_keys)
    return dict(packets_by_uid), {
        "tail_row_review_packet_count": packet_count,
        "tail_packet_duplicate_count": duplicate_count,
        "tail_candidate_keys_without_review_packet": missing_tail_keys,
        "tail_unique_uid_count_in_review": len(packets_by_uid),
    }


def _normalized_table(builder: Any, raw_table: Mapping[str, Any]) -> dict[str, Any]:
    return builder.normalize_structured_table(raw_table)


def _hint_row(
    *,
    builder: Any,
    table: Mapping[str, Any],
    packet: Mapping[str, Any],
    question: str,
) -> tuple[dict[str, Any] | None, str | None]:
    row_index = int(packet["row_index"])
    table_rows = table.get("rows") or []
    if row_index >= len(table_rows) or not isinstance(table_rows[row_index], list):
        return None, "ROW_INDEX_OUT_OF_RANGE"
    row = table_rows[row_index]
    evidence = builder.candidate_evidence_window(dict(packet), dict(table))
    chosen = builder.choose_year_column(
        evidence,
        row_index,
        row,
        int(packet["report_year"]),
        question,
    )
    if chosen is None:
        return None, "YEAR_COLUMN_UNRESOLVED"
    column_index = _int_or_none(chosen[0])
    if column_index is None or column_index < 0 or column_index >= len(row):
        return None, "YEAR_COLUMN_INVALID"
    table_sha256 = str(table.get("table_sha256") or "").strip()
    packet_table_sha256 = str(packet.get("table_sha256") or "").strip()
    if packet_table_sha256 and table_sha256 and packet_table_sha256 != table_sha256:
        return None, "TABLE_HASH_MISMATCH"
    hint = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "question_id": int(packet["question_id"]),
        "operand_id": str(packet["operand_id"]),
        "report_year": int(packet["report_year"]),
        "candidate_rank": int(packet["candidate_rank"]),
        "internal_table_uid": str(packet["internal_table_uid"]),
        "document_id": str(table.get("document_id") or ""),
        "row_index": row_index,
        "column_index": column_index,
        "source_table_sha256": table_sha256,
        "candidate_status": "TAIL_RETRIEVAL_COORDINATE_CANDIDATE",
        "reason_codes": [
            "retrieval_candidate_rank_11_to_20",
            "column_selected_by_period_header_resolver",
            "requires_current_table_hydration_and_replay",
        ],
        "navigation_metadata_only": True,
        "may_authorize_answer": False,
        "submission_eligible": False,
        "research_candidate_only": True,
    }
    if _contains_forbidden(hint):
        raise ValueError("tail coordinate hint leaked a forbidden field")
    return hint, None


def build_tail_coordinate_hints(
    *,
    builder_path: Path,
    questions_path: Path,
    candidate_artifact_dir: Path,
    structured_assets_path: Path,
    output_dir: Path,
    min_rank: int = 11,
    max_rank: int = 20,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    """Build coordinate hints for every generic retrieval tail packet."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    if min_rank < 1 or max_rank < min_rank:
        raise ValueError("invalid tail rank bounds")
    retrieval_manifest = _read_json(candidate_artifact_dir / "manifest.json")
    if retrieval_manifest.get("protocol") != RETRIEVAL_PROTOCOL:
        raise ValueError("unexpected retrieval artifact protocol")
    validate_candidate_artifact(candidate_artifact_dir, expected_question_count=expected_question_count)
    candidate_path = candidate_artifact_dir / "table_candidates_v1.jsonl"
    row_review_path = candidate_artifact_dir / "row_review_queue_v1.jsonl"
    tail_keys, candidate_stats = _candidate_tail_keys(
        candidate_path, min_rank=min_rank, max_rank=max_rank
    )
    questions = _load_questions(questions_path)
    if len(questions) != expected_question_count:
        raise ValueError("question population does not match expected count")
    packets_by_uid, packet_stats = _load_tail_packets(
        row_review_path,
        tail_keys=tail_keys,
    )
    builder = _load_builder(builder_path)
    hints: list[dict[str, Any]] = []
    seen_coordinates: set[tuple[int, str, int, int]] = set()
    rejection_counts: Counter[str] = Counter()
    asset_lines_scanned = 0
    asset_uid_hits = 0
    selected_uids = set(packets_by_uid)
    with structured_assets_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            asset_lines_scanned += 1
            raw_table = json.loads(line)
            uid = str(raw_table.get("internal_table_uid") or "").strip()
            if uid not in selected_uids:
                continue
            asset_uid_hits += 1
            table = _normalized_table(builder, raw_table)
            for packet in packets_by_uid[uid]:
                question = questions.get(str(packet["question_id"]))
                if not question:
                    rejection_counts["QUESTION_NOT_FOUND"] += 1
                    continue
                hint, rejection = _hint_row(
                    builder=builder,
                    table=table,
                    packet=packet,
                    question=question,
                )
                if rejection:
                    rejection_counts[rejection] += 1
                    continue
                coordinate = (
                    int(hint["question_id"]),
                    str(hint["internal_table_uid"]),
                    int(hint["row_index"]),
                    int(hint["column_index"]),
                )
                if coordinate in seen_coordinates:
                    rejection_counts["DUPLICATE_COORDINATE"] += 1
                    continue
                seen_coordinates.add(coordinate)
                hints.append(hint)
    rejection_counts["UID_NOT_FOUND_IN_STRUCTURED_ASSET"] += len(selected_uids) - asset_uid_hits
    summary: dict[str, Any] = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "builder_path": str(builder_path),
        "builder_sha256": sha256_file(builder_path),
        "structured_asset_path": str(structured_assets_path),
        "structured_asset_sha256": sha256_file(structured_assets_path),
        "candidate_stats": candidate_stats,
        "packet_stats": packet_stats,
        "asset_lines_scanned": asset_lines_scanned,
        "selected_uid_count": len(selected_uids),
        "selected_uids_found_in_structured_asset": asset_uid_hits,
        "coordinate_hint_count": len(hints),
        "questions_with_coordinate_hints": len({hint["question_id"] for hint in hints}),
        "rejection_counts": dict(sorted(rejection_counts.items())),
        "answer_accuracy": "NOT_MEASURED",
        "execution_accuracy": "NOT_MEASURED",
        "scorer_or_gold": "NOT_AVAILABLE; coordinate hints are navigation candidates",
        "decision": "INVESTIGATE_FURTHER",
        "authority_status": "CANDIDATE_ONLY",
        "source_contract": dict(CONTRACT),
    }
    if _contains_forbidden(summary):
        raise ValueError("tail coordinate summary leaked a forbidden field")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        hints_path = temporary / "coordinate_hints_v1.jsonl"
        summary_path = temporary / "tail_coordinate_summary_v1.json"
        _write_jsonl(hints_path, hints)
        _write_json(summary_path, summary)
        inputs = {
            "builder": {"path": str(builder_path), "sha256": sha256_file(builder_path)},
            "questions": {"path": str(questions_path), "sha256": sha256_file(questions_path)},
            "structured_assets": {"path": str(structured_assets_path), "sha256": sha256_file(structured_assets_path)},
            "retrieval_manifest": {"path": str(candidate_artifact_dir / "manifest.json"), "sha256": sha256_file(candidate_artifact_dir / "manifest.json")},
            "retrieval_candidates": {"path": str(candidate_path), "sha256": sha256_file(candidate_path)},
            "retrieval_row_review_queue": {"path": str(row_review_path), "sha256": sha256_file(row_review_path)},
        }
        outputs = {
            hints_path.name: {"path": hints_path.name, "sha256": sha256_file(hints_path)},
            summary_path.name: {"path": summary_path.name, "sha256": sha256_file(summary_path)},
        }
        _write_json(
            temporary / "manifest.json",
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "inputs": inputs,
                "outputs": outputs,
                "source_contract": dict(CONTRACT),
            },
        )
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return summary


def validate_tail_coordinate_hints(artifact_dir: Path) -> dict[str, Any]:
    manifest = _read_json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != CONTRACT:
        raise ValueError("unexpected tail-coordinate materializer contract")
    for descriptor in (manifest.get("inputs") or {}).values():
        path = Path(str(descriptor.get("path") or ""))
        if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
            raise ValueError("tail-coordinate input hash mismatch")
    for descriptor in (manifest.get("outputs") or {}).values():
        path = artifact_dir / str(descriptor.get("path") or "")
        if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
            raise ValueError("tail-coordinate output hash mismatch")
    summary = _read_json(artifact_dir / "tail_coordinate_summary_v1.json")
    hints = list(_read_jsonl(artifact_dir / "coordinate_hints_v1.jsonl"))
    if _contains_forbidden(summary) or any(_contains_forbidden(hint) for hint in hints):
        raise ValueError("tail-coordinate artifact leaked a forbidden field")
    if any(
        hint.get("may_authorize_answer") is not False
        or hint.get("submission_eligible") is not False
        or hint.get("navigation_metadata_only") is not True
        for hint in hints
    ):
        raise ValueError("tail-coordinate hint has unsafe authorization flags")
    if int(summary.get("coordinate_hint_count") or 0) != len(hints):
        raise ValueError("tail-coordinate hint count mismatch")
    if summary.get("answer_accuracy") != "NOT_MEASURED" or summary.get("execution_accuracy") != "NOT_MEASURED":
        raise ValueError("tail-coordinate artifact must not report answer accuracy")
    return {
        "status": "PASS",
        "coordinate_hint_count": len(hints),
        "questions_with_coordinate_hints": summary.get("questions_with_coordinate_hints"),
        "authority_status": summary.get("authority_status"),
    }
