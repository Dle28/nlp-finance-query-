#!/usr/bin/env python3
"""Compare two semantic-contract audit manifests without comparing answers.

The comparator is deliberately narrower than an answer evaluator.  It reads
only audit metadata needed to compare family/status/locator transitions and
contract reason codes.  Candidate answers, raw cells, model/research values,
and hidden-gold fields are never read into the comparison record or emitted to
the output.

Both ``--before`` and ``--after`` may point to an audit directory or directly
to a JSONL/JSON record file.  For a directory the comparator prefers
``semantic_contract_reviews.jsonl`` and then ``question_reviews.jsonl``.

The JSON output contains an overall summary, family-level aggregates, and
value-free per-question metadata.  The TSV is a compact navigation view of
the same metadata.  A family is taken from an explicit audit field when one
exists; otherwise the current rule classifier in
``src/finance_query/e2e/core/questions.py`` is used as a transparent,
non-answer grouping fallback.  No Question-ID-specific rule is present.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTRACT = ROOT / "scripts/e2e/build_competition_submission_v1.py"
DEFAULT_FAMILY_MODULE = ROOT / "src/finance_query/e2e/core/questions.py"

PROTOCOL = "vifinqa_semantic_contract_audit_comparison_v1"
CONTRACT_PROTOCOL = "semantic_cell_contract_v1"

BEFORE_FILE_NAMES = (
    "semantic_contract_reviews.jsonl",
    "question_reviews.jsonl",
)

STATUS_FIELDS_BEFORE = (
    "review_status",
    "baseline_review_status",
    "semantic_contract_status",
    "status",
    "answer_status",
)
STATUS_FIELDS_AFTER = (
    "semantic_contract_status",
    "status",
    "answer_status",
    "review_status",
)

FAMILY_FIELDS = (
    "family",
    "input_family",
    "question_family",
    "planner_family",
    "semantic_family",
)

REASON_FIELDS = (
    "semantic_guard_reason_codes",
    "semantic_rejection_reason_codes",
    "abstain_reason_codes",
    "contract_reason_codes",
    "reason_codes",
)

EXPLICIT_ABSTAIN_REASON_FIELDS = (
    "abstain_reason_codes",
    "semantic_abstain_reason_codes",
)

REJECTION_CLASS_FIELDS = (
    "semantic_rejection_class_counts",
    "rejection_class_counts",
)

REASON_CLASS_COUNT_FIELDS = (
    "semantic_rejection_reason_counts_by_class",
    "reason_code_class_counts",
)

ABSTAIN_STATUSES = frozenset(
    {
        "ABSTAIN",
        "SEMANTIC_ABSTAIN",
        "UNRESOLVED",
    }
)

# These are intentionally not traversed while extracting records.  The
# comparator must not accidentally turn a value-bearing audit field into an
# evaluation input.  Keeping the list in code also makes the output contract
# auditable if a future audit schema adds a similarly named field.
FORBIDDEN_VALUE_KEYS = frozenset(
    {
        "answer",
        "answer_decimal",
        "candidate_answer",
        "candidate_answer_decimal",
        "baseline_candidate_answer_decimal",
        "gold_answer",
        "gold_value",
        "model_answer",
        "research_answer",
        "predicted_answer",
        "predicted_value",
        "raw_answer",
        "raw_cell_value",
        "cell_value",
        "source_value",
        "target_answer",
        "evidence_value",
        "evidence_raw_value",
        "raw_value",
        "selected_cell",
        "value",
    }
)

_CONTRACT_REASON_RE = re.compile(
    r"(?:reject|reason_codes\.append|require(?:_all|_row_any|_row_all|_raw_row_any)?)"
    r"\(\s*[\"']([A-Z][A-Z0-9_]+)[\"']"
)
_INTEGER_RE = re.compile(r"^[+-]?\d+$")
QUESTION_ID_EXCEPTION_KEYS = frozenset(
    {
        "question_id_exception",
        "question_id_exceptions",
        "qid_exception",
        "qid_exceptions",
        "question_overrides",
        "question_id_overrides",
        "qid_overrides",
        "per_question_rules",
        "per_question_overrides",
        "special_question_cases",
        "question_specific_rules",
        "manual_question_fix",
        "manual_question_fixes",
    }
)
_FAMILY_IMPORT_ERROR: str | None = None
_FAMILY_CLASSIFIER: Any = None


def _nonempty_text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None


def _record_id(record: Mapping[str, Any]) -> int | str:
    value = record.get("question_id", record.get("id"))
    if value is None:
        raise ValueError("audit record has no question_id/id")
    if isinstance(value, bool):
        raise ValueError("boolean question_id is invalid")
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        raise ValueError("empty question_id/id")
    if _INTEGER_RE.fullmatch(text):
        return int(text)
    return text


def _question_id_exception_paths(value: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key).strip().casefold()
            child_path = f"{path}.{key_text}"
            if key_text in QUESTION_ID_EXCEPTION_KEYS:
                found.append(child_path)
            found.extend(_question_id_exception_paths(nested, child_path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found.extend(_question_id_exception_paths(nested, f"{path}[{index}]"))
    return found


def _assert_no_question_id_exceptions(
    records: Iterable[Mapping[str, Any]], label: str
) -> None:
    paths: list[str] = []
    for index, record in enumerate(records):
        paths.extend(_question_id_exception_paths(record, f"{label}[{index}]"))
    if paths:
        raise ValueError(
            "Question-ID-specific exceptions are forbidden in this comparison: "
            + ", ".join(paths[:8])
        )


def _resolve_record_path(path: Path, label: str) -> Path:
    path = path.expanduser()
    if path.is_file():
        return path
    if not path.is_dir():
        raise FileNotFoundError(f"{label} path does not exist: {path}")

    for name in BEFORE_FILE_NAMES:
        candidate = path / name
        if candidate.is_file():
            return candidate

    candidates = sorted(
        candidate
        for candidate in path.glob("*.jsonl")
        if candidate.name not in {"summary.json"}
    )
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise FileNotFoundError(
            f"{label} directory contains no supported audit JSONL: {path}"
        )
    names = ", ".join(candidate.name for candidate in candidates)
    raise ValueError(
        f"{label} directory has multiple JSONL manifests; use the file path "
        f"explicitly: {names}"
    )


def _iter_json_records(path: Path) -> Iterable[dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        with path.open(encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSON at {path}:{line_number}: {exc}") from exc
                if not isinstance(record, Mapping):
                    raise ValueError(f"audit record at {path}:{line_number} is not an object")
                yield dict(record)
        return

    with path.open(encoding="utf-8") as file:
        payload = json.load(file)
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, Mapping):
        records = None
        for key in ("records", "reviews", "items"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                records = candidate
                break
        if records is None:
            raise ValueError(
                f"JSON input {path} is not a record list; pass the audit JSONL "
                "or a JSON object containing records/reviews/items"
            )
    else:
        raise ValueError(f"JSON input {path} is neither an object nor a list")

    for index, record in enumerate(records, start=1):
        if not isinstance(record, Mapping):
            raise ValueError(f"audit record {path}[{index}] is not an object")
        yield dict(record)


def _load_record_map(path: Path, label: str) -> dict[int | str, dict[str, Any]]:
    records: dict[int | str, dict[str, Any]] = {}
    resolved = _resolve_record_path(path, label)
    for raw in _iter_json_records(resolved):
        _assert_no_question_id_exceptions((raw,), f"{label}:{resolved}")
        question_id = _record_id(raw)
        if question_id in records:
            raise ValueError(f"duplicate question_id {question_id!r} in {resolved}")
        records[question_id] = raw
    if not records:
        raise ValueError(f"audit manifest is empty: {resolved}")
    return records


def _as_coordinate(value: Any) -> int | str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    if _INTEGER_RE.fullmatch(text):
        return int(text)
    return text


def _locator_from_container(container: Any) -> dict[str, Any] | None:
    if not isinstance(container, Mapping):
        return None
    present = any(field in container for field in ("internal_table_uid", "row_index", "column_index"))
    if not present:
        return None
    return {
        "internal_table_uid": _nonempty_text(container.get("internal_table_uid")),
        "row_index": _as_coordinate(container.get("row_index")),
        "column_index": _as_coordinate(container.get("column_index")),
    }


def _locator(record: Mapping[str, Any], side: str) -> dict[str, Any] | None:
    if side == "before":
        containers = (
            record.get("baseline_locator"),
            record.get("source"),
            record.get("locator"),
            record,
        )
    else:
        containers = (
            record.get("new_locator"),
            record.get("selected"),
            record.get("locator"),
            record,
        )
    candidates = [
        candidate
        for container in containers
        if (candidate := _locator_from_container(container)) is not None
    ]
    if not candidates:
        return None
    # Prefer the most complete locator.  This avoids treating a placeholder
    # ``new_locator`` with null coordinates as stronger than selected metadata
    # in older audit schemas.
    return max(
        candidates,
        key=lambda candidate: sum(value is not None for value in candidate.values()),
    )


def _locator_tuple(locator: Mapping[str, Any] | None) -> tuple[Any, Any, Any] | None:
    if locator is None:
        return None
    return (
        locator.get("internal_table_uid"),
        locator.get("row_index"),
        locator.get("column_index"),
    )


def _locator_complete(locator: Mapping[str, Any] | None) -> bool:
    values = _locator_tuple(locator)
    return values is not None and all(value is not None for value in values)


def _status(record: Mapping[str, Any], side: str) -> str:
    fields = STATUS_FIELDS_BEFORE if side == "before" else STATUS_FIELDS_AFTER
    for field in fields:
        value = _nonempty_text(record.get(field))
        if value:
            return value.upper().replace(" ", "_")
    return "UNSPECIFIED_STATUS"


def _collect_reason_codes(value: Any, result: set[str]) -> None:
    if isinstance(value, str):
        for part in re.split(r"[,;]", value):
            code = part.strip().upper()
            if code:
                result.add(code)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            _collect_reason_codes(item, result)


def _reason_codes(record: Mapping[str, Any]) -> list[str]:
    """Read only named reason-code fields, never arbitrary nested values."""

    found: set[str] = set()
    containers: list[Mapping[str, Any]] = [record]
    for nested_name in ("selected", "semantic_guard"):
        nested = record.get(nested_name)
        if isinstance(nested, Mapping):
            containers.append(nested)
            guard = nested.get("semantic_guard")
            if isinstance(guard, Mapping):
                containers.append(guard)
    for container in containers:
        for field in REASON_FIELDS:
            if field in container:
                _collect_reason_codes(container.get(field), found)
    return sorted(found)


def _explicit_abstain_reason_codes(record: Mapping[str, Any]) -> list[str]:
    """Read the dedicated abstain field, not a generic rejection trace."""

    found: set[str] = set()
    for field in EXPLICIT_ABSTAIN_REASON_FIELDS:
        if field in record:
            _collect_reason_codes(record.get(field), found)
    return sorted(found)


def _count_mapping(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, int] = {}
    for key, raw_count in value.items():
        if isinstance(raw_count, bool):
            continue
        try:
            count = int(raw_count)
        except (TypeError, ValueError):
            continue
        if count < 0:
            continue
        result[str(key).strip().upper()] = count
    return {key: result[key] for key in sorted(result) if key}


def _rejection_class_counts(record: Mapping[str, Any]) -> dict[str, int]:
    for field in REJECTION_CLASS_FIELDS:
        counts = _count_mapping(record.get(field))
        if counts:
            return counts
    return {}


def _reason_class_counts(record: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    """Read value-free reason -> rejection-class counts from an audit row."""

    for field in REASON_CLASS_COUNT_FIELDS:
        raw = record.get(field)
        if not isinstance(raw, Mapping):
            continue
        result: dict[str, dict[str, int]] = {}
        for reason, classes in raw.items():
            reason_text = str(reason).strip().upper()
            counts = _count_mapping(classes)
            if reason_text and counts:
                result[reason_text] = counts
        if result:
            return {key: result[key] for key in sorted(result)}
    return {}


def _policy_flags(record: Mapping[str, Any]) -> list[str]:
    """Return metadata flags that make a record unsafe to use in a compare."""

    flags: set[str] = set()
    if record.get("research_or_model_inputs_consumed") is True:
        flags.add("RESEARCH_OR_MODEL_INPUTS_CONSUMED")
    selected = record.get("selected")
    if isinstance(selected, Mapping):
        if selected.get("research_candidate_only") is True:
            flags.add("RESEARCH_CANDIDATE_ONLY")
        source = _nonempty_text(
            selected.get("candidate_source")
            or selected.get("retrieval_source")
            or selected.get("source_type")
        )
        if source and re.search(r"\b(?:research|model)\b", source, re.IGNORECASE):
            flags.add("RESEARCH_OR_MODEL_SOURCE")
    return sorted(flags)


def _explicit_family(record: Mapping[str, Any]) -> str | None:
    for field in FAMILY_FIELDS:
        value = _nonempty_text(record.get(field))
        if value:
            return value
    for nested_name in ("question_plan", "plan", "routing"):
        nested = record.get(nested_name)
        if isinstance(nested, Mapping):
            for field in FAMILY_FIELDS:
                value = _nonempty_text(nested.get(field))
                if value:
                    return value
    return None


def _load_family_classifier() -> Any:
    global _FAMILY_CLASSIFIER, _FAMILY_IMPORT_ERROR
    if _FAMILY_CLASSIFIER is not None or _FAMILY_IMPORT_ERROR is not None:
        return _FAMILY_CLASSIFIER
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from src.finance_query.e2e.core.questions import infer_family

        _FAMILY_CLASSIFIER = infer_family
    except Exception as exc:  # pragma: no cover - fallback is for lean installs
        _FAMILY_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
    return _FAMILY_CLASSIFIER


def _family(record: Mapping[str, Any]) -> tuple[str, str]:
    explicit = _explicit_family(record)
    if explicit:
        return explicit, "audit_record"
    question = _nonempty_text(record.get("question"))
    classifier = _load_family_classifier()
    if question and classifier is not None:
        try:
            inferred = classifier(question)
            inferred_name = inferred[0] if isinstance(inferred, tuple) else inferred
            value = _nonempty_text(inferred_name)
            if value:
                return value, "current_question_classifier"
        except Exception:
            pass
    return "UNSPECIFIED", "unavailable"


def _is_abstain(status: str) -> bool:
    return status in ABSTAIN_STATUSES


def _status_transition(before: str, after: str) -> str:
    if before == after:
        return "UNCHANGED_STATUS"
    if _is_abstain(after):
        return "BECAME_ABSTAIN"
    if _is_abstain(before):
        return "RECOVERED_FROM_ABSTAIN"
    if after == "SEMANTIC_CANDIDATE_RETAINED":
        return "MOVED_TO_SEMANTIC_RETAINED"
    if after == "SEMANTIC_CANDIDATE_CHANGED":
        return "MOVED_TO_SEMANTIC_CHANGED"
    return "STATUS_CHANGED"


def _question_id_sort_key(value: int | str) -> tuple[int, int | str]:
    if isinstance(value, int):
        return (0, value)
    return (1, str(value))


def _locator_change(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
) -> str:
    before_tuple = _locator_tuple(before)
    after_tuple = _locator_tuple(after)
    if before_tuple is None and after_tuple is None:
        return "BOTH_MISSING"
    if before_tuple is None:
        return "BEFORE_MISSING"
    if after_tuple is None:
        return "AFTER_MISSING"
    if before_tuple == after_tuple:
        return "UNCHANGED"
    return "CHANGED"


def _counter_dict(counter: Counter[str] | Mapping[str, int]) -> dict[str, int]:
    return {str(key): int(counter[key]) for key in sorted(counter)}


def _nested_counter_dict(value: Mapping[str, Counter[str]]) -> dict[str, dict[str, int]]:
    return {key: _counter_dict(value[key]) for key in sorted(value)}


def _contract_metadata(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {
            "path": str(path),
            "available": False,
            "protocol": CONTRACT_PROTOCOL,
            "sha256": None,
            "known_reason_code_count": 0,
        }
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
            chunks.append(chunk)
    source = b"".join(chunks).decode("utf-8", errors="replace")
    reason_codes = sorted(set(_CONTRACT_REASON_RE.findall(source)))
    return {
        "path": str(path),
        "available": True,
        "protocol": CONTRACT_PROTOCOL,
        "sha256": digest.hexdigest(),
        "known_reason_code_count": len(reason_codes),
        "known_reason_codes": reason_codes,
    }


def _normalize_record(record: Mapping[str, Any], side: str) -> dict[str, Any]:
    question_id = _record_id(record)
    family, family_source = _family(record)
    status = _status(record, side)
    locator = _locator(record, side)
    reasons = _reason_codes(record)
    explicit_abstain_reasons = _explicit_abstain_reason_codes(record)
    policy_flags = _policy_flags(record)
    return {
        "question_id": question_id,
        "family": family,
        "family_source": family_source,
        "status": status,
        "locator": locator,
        "reason_codes": reasons,
        "explicit_abstain_reason_codes": explicit_abstain_reasons,
        "rejection_class_counts": _rejection_class_counts(record),
        "reason_class_counts": _reason_class_counts(record),
        "policy_flags": policy_flags,
    }


def _empty_family_bucket() -> dict[str, Any]:
    return {
        "question_count": 0,
        "before_status_counts": Counter(),
        "after_status_counts": Counter(),
        "status_transition_counts": Counter(),
        "locator_change_counts": Counter(),
        "after_reason_code_counts": Counter(),
        "after_reason_code_status_counts": defaultdict(Counter),
        "after_rejection_class_counts": Counter(),
        "after_reason_code_class_counts": defaultdict(Counter),
        "abstain_count": 0,
        "abstain_from_before_status_counts": Counter(),
        "abstain_without_explicit_reason_code_count": 0,
        "unknown_after_reason_code_counts": Counter(),
    }


def _finalize_family_bucket(bucket: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "question_count": int(bucket["question_count"]),
        "before_status_counts": _counter_dict(bucket["before_status_counts"]),
        "after_status_counts": _counter_dict(bucket["after_status_counts"]),
        "status_transition_counts": _counter_dict(bucket["status_transition_counts"]),
        "locator_change_counts": _counter_dict(bucket["locator_change_counts"]),
        "after_reason_code_counts": _counter_dict(bucket["after_reason_code_counts"]),
        "after_reason_code_status_counts": _nested_counter_dict(
            bucket["after_reason_code_status_counts"]
        ),
        "after_rejection_class_counts": _counter_dict(
            bucket["after_rejection_class_counts"]
        ),
        "after_reason_code_class_counts": _nested_counter_dict(
            bucket["after_reason_code_class_counts"]
        ),
        "abstain_count": int(bucket["abstain_count"]),
        "abstain_from_before_status_counts": _counter_dict(
            bucket["abstain_from_before_status_counts"]
        ),
        "abstain_without_explicit_reason_code_count": int(
            bucket["abstain_without_explicit_reason_code_count"]
        ),
        "unknown_after_reason_code_counts": _counter_dict(
            bucket["unknown_after_reason_code_counts"]
        ),
    }


def compare(args: argparse.Namespace) -> dict[str, Any]:
    before_path = _resolve_record_path(args.before, "before")
    after_path = _resolve_record_path(args.after, "after")
    before_raw = _load_record_map(before_path, "before")
    after_raw = _load_record_map(after_path, "after")

    before = {
        question_id: _normalize_record(record, "before")
        for question_id, record in before_raw.items()
    }
    after = {
        question_id: _normalize_record(record, "after")
        for question_id, record in after_raw.items()
    }

    before_ids = set(before)
    after_ids = set(after)
    matched_ids = sorted(before_ids & after_ids, key=_question_id_sort_key)
    before_only = before_ids - after_ids
    after_only = after_ids - before_ids

    policy_excluded: Counter[str] = Counter()
    eligible_ids: list[int | str] = []
    for question_id in matched_ids:
        flags = sorted(set(before[question_id]["policy_flags"] + after[question_id]["policy_flags"]))
        if flags:
            for flag in flags:
                policy_excluded[flag] += 1
        else:
            eligible_ids.append(question_id)

    contract = _contract_metadata(args.contract)
    known_reason_codes = set(contract.get("known_reason_codes") or [])
    records: list[dict[str, Any]] = []
    overall_before_status: Counter[str] = Counter()
    overall_after_status: Counter[str] = Counter()
    overall_transitions: Counter[str] = Counter()
    overall_locator_changes: Counter[str] = Counter()
    overall_reasons: Counter[str] = Counter()
    overall_reason_status: defaultdict[str, Counter[str]] = defaultdict(Counter)
    overall_rejection_classes: Counter[str] = Counter()
    overall_reason_classes: defaultdict[str, Counter[str]] = defaultdict(Counter)
    overall_abstain_from: Counter[str] = Counter()
    overall_unknown_reasons: Counter[str] = Counter()
    family_buckets: defaultdict[str, dict[str, Any]] = defaultdict(_empty_family_bucket)

    for question_id in eligible_ids:
        previous = before[question_id]
        current = after[question_id]
        # Family is taken from the after audit when present.  The fallback is
        # deterministic and text-based; it never branches on a Question-ID.
        family = current["family"]
        family_source = current["family_source"]
        if family == "UNSPECIFIED" and previous["family"] != "UNSPECIFIED":
            family = previous["family"]
            family_source = previous["family_source"]

        before_status = previous["status"]
        after_status = current["status"]
        transition = _status_transition(before_status, after_status)
        locator_change = _locator_change(previous["locator"], current["locator"])
        after_reasons = list(current["reason_codes"])
        after_abstain_reasons = list(current["explicit_abstain_reason_codes"])
        after_rejection_classes = dict(current["rejection_class_counts"])
        after_reason_classes = {
            reason: dict(classes)
            for reason, classes in current["reason_class_counts"].items()
        }
        diagnostic_flags: list[str] = []
        if _is_abstain(after_status) and not after_abstain_reasons:
            diagnostic_flags.append("ABSTAIN_WITHOUT_EXPLICIT_REASON_CODES")

        row = {
            "question_id": question_id,
            "family": family,
            "family_source": family_source,
            "before_status": before_status,
            "after_status": after_status,
            "status_transition": transition,
            "before_locator": previous["locator"],
            "after_locator": current["locator"],
            "locator_change": locator_change,
            "before_locator_complete": _locator_complete(previous["locator"]),
            "after_locator_complete": _locator_complete(current["locator"]),
            "before_reason_codes": previous["reason_codes"],
            "after_reason_codes": after_reasons,
            "after_abstain_reason_codes": after_abstain_reasons,
            "after_rejection_class_counts": after_rejection_classes,
            "after_reason_code_class_counts": after_reason_classes,
            "after_reason_codes_known_to_contract": sorted(
                code for code in after_reasons if code in known_reason_codes
            ),
            "diagnostic_flags": diagnostic_flags,
            "after_is_abstain": _is_abstain(after_status),
        }
        records.append(row)

        overall_before_status[before_status] += 1
        overall_after_status[after_status] += 1
        overall_transitions[transition] += 1
        overall_locator_changes[locator_change] += 1
        for code in after_reasons:
            overall_reasons[code] += 1
            overall_reason_status[code][after_status] += 1
            if code not in known_reason_codes:
                overall_unknown_reasons[code] += 1
        for rejection_class, class_count in after_rejection_classes.items():
            overall_rejection_classes[rejection_class] += class_count
        for reason, classes in after_reason_classes.items():
            for rejection_class, class_count in classes.items():
                overall_reason_classes[reason][rejection_class] += class_count
        if _is_abstain(after_status):
            overall_abstain_from[before_status] += 1

        bucket = family_buckets[family]
        bucket["question_count"] += 1
        bucket["before_status_counts"][before_status] += 1
        bucket["after_status_counts"][after_status] += 1
        bucket["status_transition_counts"][transition] += 1
        bucket["locator_change_counts"][locator_change] += 1
        for code in after_reasons:
            bucket["after_reason_code_counts"][code] += 1
            bucket["after_reason_code_status_counts"][code][after_status] += 1
            if code not in known_reason_codes:
                bucket["unknown_after_reason_code_counts"][code] += 1
        for rejection_class, class_count in after_rejection_classes.items():
            bucket["after_rejection_class_counts"][rejection_class] += class_count
        for reason, classes in after_reason_classes.items():
            for rejection_class, class_count in classes.items():
                bucket["after_reason_code_class_counts"][reason][rejection_class] += class_count
        if _is_abstain(after_status):
            bucket["abstain_count"] += 1
            bucket["abstain_from_before_status_counts"][before_status] += 1
            if not after_abstain_reasons:
                bucket["abstain_without_explicit_reason_code_count"] += 1

    abstain_without_reason_count = sum(
        1
        for record in records
        if record["after_is_abstain"] and not record["after_abstain_reason_codes"]
    )
    summary = {
        "protocol": PROTOCOL,
        "inputs": {
            "before": str(before_path),
            "after": str(after_path),
        },
        "contract": contract,
        "alignment": {
            "before_record_count": len(before_raw),
            "after_record_count": len(after_raw),
            "matched_record_count": len(matched_ids),
            "eligible_record_count": len(eligible_ids),
            "before_only_record_count": len(before_only),
            "after_only_record_count": len(after_only),
            "policy_excluded_record_count": len(matched_ids) - len(eligible_ids),
        },
        "counts": {
            "before_status_counts": _counter_dict(overall_before_status),
            "after_status_counts": _counter_dict(overall_after_status),
            "status_transition_counts": _counter_dict(overall_transitions),
            "locator_change_counts": _counter_dict(overall_locator_changes),
            "after_reason_code_counts": _counter_dict(overall_reasons),
            "after_reason_code_status_counts": _nested_counter_dict(overall_reason_status),
            "after_rejection_class_counts": _counter_dict(overall_rejection_classes),
            "after_reason_code_class_counts": _nested_counter_dict(overall_reason_classes),
            "abstain_count": sum(1 for record in records if record["after_is_abstain"]),
            "abstain_from_before_status_counts": _counter_dict(overall_abstain_from),
            "abstain_without_explicit_reason_code_count": abstain_without_reason_count,
            "unknown_after_reason_code_counts": _counter_dict(overall_unknown_reasons),
        },
        "family_level": {
            family: _finalize_family_bucket(bucket)
            for family, bucket in sorted(family_buckets.items())
        },
        "policy": {
            "family_level_contracts_only": True,
            "question_id_exceptions": False,
            "question_id_used_for_tracking_only": True,
            "candidate_values_used": False,
            "model_or_research_candidate_values_used": False,
            "hidden_gold_accessed": False,
            "accuracy_measured": False,
            "value_free_output": True,
            "policy_excluded_reason_counts": _counter_dict(policy_excluded),
            "abstain_reason_codes_are_emitted_by_audit": abstain_without_reason_count == 0,
        },
        "diagnostics": {
            "family_classifier": "src.finance_query.e2e.core.questions.infer_family",
            "family_classifier_import_error": _FAMILY_IMPORT_ERROR,
            "missing_abstain_reason_code_is_comparator_diagnostic": True,
            "answer_or_value_fields_compared": False,
            "guard_rejection_and_missing_evidence_are_separate": True,
        },
        "records": records,
    }
    _assert_value_free_output(summary)
    return summary


def _assert_value_free_output(payload: Mapping[str, Any]) -> None:
    """Fail closed if a future edit accidentally adds an answer-bearing key."""

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if str(key) in FORBIDDEN_VALUE_KEYS:
                    raise AssertionError(f"value-bearing output key is forbidden: {key}")
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(payload)


def _tsv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, dict)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    else:
        text = str(value)
    return text.replace("\t", " ").replace("\r", " ").replace("\n", " ")


def _write_outputs(summary: dict[str, Any], output: Path) -> tuple[Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    _assert_value_free_output(summary)
    json_path = output / "semantic_audit_comparison.json"
    json_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    tsv_path = output / "semantic_audit_comparison.tsv"
    header = [
        "question_id",
        "family",
        "family_source",
        "before_status",
        "after_status",
        "status_transition",
        "locator_change",
        "before_locator_complete",
        "after_locator_complete",
        "before_table_uid",
        "before_row_index",
        "before_column_index",
        "after_table_uid",
        "after_row_index",
        "after_column_index",
        "after_rejection_class_counts",
        "after_abstain_reason_codes",
        "after_reason_codes",
        "after_is_abstain",
        "diagnostic_flags",
    ]
    lines = ["\t".join(header)]
    for record in summary["records"]:
        before_locator = record.get("before_locator") or {}
        after_locator = record.get("after_locator") or {}
        lines.append(
            "\t".join(
                _tsv_cell(value)
                for value in (
                    record.get("question_id"),
                    record.get("family"),
                    record.get("family_source"),
                    record.get("before_status"),
                    record.get("after_status"),
                    record.get("status_transition"),
                    record.get("locator_change"),
                    record.get("before_locator_complete"),
                    record.get("after_locator_complete"),
                    before_locator.get("internal_table_uid"),
                    before_locator.get("row_index"),
                    before_locator.get("column_index"),
                    after_locator.get("internal_table_uid"),
                    after_locator.get("row_index"),
                    after_locator.get("column_index"),
                    record.get("after_rejection_class_counts"),
                    ",".join(record.get("after_abstain_reason_codes") or []),
                    ",".join(record.get("after_reason_codes") or []),
                    record.get("after_is_abstain"),
                    ",".join(record.get("diagnostic_flags") or []),
                )
            )
        )
    tsv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, tsv_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Compare semantic audit statuses, family-level reason codes and "
            "source locators without comparing candidate answers."
        )
    )
    parser.add_argument("--before", type=Path, required=True, help="before audit directory or JSONL/JSON")
    parser.add_argument("--after", type=Path, required=True, help="after audit directory or JSONL/JSON")
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="output directory for semantic_audit_comparison.json/.tsv",
    )
    parser.add_argument(
        "--contract",
        type=Path,
        default=DEFAULT_CONTRACT,
        help="current production semantic-contract source used for fingerprint/reason-code inventory",
    )
    args = parser.parse_args()
    summary = compare(args)
    json_path, tsv_path = _write_outputs(summary, args.output)
    print(
        json.dumps(
            {
                "protocol": summary["protocol"],
                "json": str(json_path),
                "tsv": str(tsv_path),
                "alignment": summary["alignment"],
                "counts": summary["counts"],
                "policy": summary["policy"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
