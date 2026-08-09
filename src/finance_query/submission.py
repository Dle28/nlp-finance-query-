"""Strict, reproducible ViFinQA submission contract utilities.

The contest evaluates not only a numeric answer but also table provenance and a
re-runnable pandas expression.  These helpers deliberately reject partial or
non-grounded records instead of silently producing a leaderboard-looking ZIP.
"""

from __future__ import annotations

import ast
import csv
import hashlib
import json
import math
import re
import zipfile
from decimal import Decimal
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from .binding import column_descriptions, row_label
from .execution import execute_ast, parse_decimal
from .schemas import DirectBinding


VARIABLE_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
TABLE_REF_RE = re.compile(r"[^|]+\|[0-9]+\Z")
UNIT_TO_VND = {
    "vnd": Decimal("1"),
    "thousand_vnd": Decimal("1000"),
    "million_vnd": Decimal("1000000"),
    "billion_vnd": Decimal("1000000000"),
    "trillion_vnd": Decimal("1000000000000"),
}


class SubmissionValidationError(ValueError):
    """Raised when a submission violates the published ViFinQA contract."""


def _asset_rows(asset: Mapping[str, Any], *, question_id: int) -> list[list[str]]:
    """Read an immutable table grid from an index asset or review-bundle row."""
    raw_rows = asset.get("rows")
    if raw_rows is None:
        try:
            raw_rows = json.loads(str(asset.get("rows_json") or "[]"))
        except json.JSONDecodeError as error:
            raise SubmissionValidationError(f"Q{question_id}: invalid source rows") from error
    if not isinstance(raw_rows, list) or not all(isinstance(row, list) for row in raw_rows):
        raise SubmissionValidationError(f"Q{question_id}: source rows must be a table grid")
    return [[str(cell) for cell in row] for row in raw_rows]


def _safe_component(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return normalized or "document"


def _binding_csv_path(
    binding: DirectBinding,
    asset: Mapping[str, Any],
    *,
    question_id: int,
) -> tuple[str, int, Path]:
    document_id = str(asset.get("document_id") or binding.document_id)
    ordinal = asset.get("local_ordinal")
    if not document_id or not isinstance(ordinal, int) or ordinal < 1:
        raise SubmissionValidationError(f"Q{question_id}: source asset lacks document/local ordinal")
    uid = str(asset.get("internal_table_uid") or binding.internal_table_uid)
    digest = hashlib.sha256(uid.encode("utf-8")).hexdigest()[:12]
    filename = f"{_safe_component(document_id)}_table_{ordinal}_{digest}.csv"
    return document_id, ordinal, Path("data") / filename


def _write_binding_table(
    binding: DirectBinding,
    asset: Mapping[str, Any],
    package_root: Path,
    *,
    question_id: int,
    bindings: Iterable[DirectBinding] | None = None,
) -> tuple[str, int, Path]:
    """Write a lossless long CSV and return its immutable report reference."""
    rows = _asset_rows(asset, question_id=question_id)
    selected_bindings = list(bindings or [binding])
    if binding not in selected_bindings:
        selected_bindings.append(binding)
    column_overrides: dict[tuple[int, int], str] = {}
    for selected in selected_bindings:
        if selected.row_index < 0 or selected.row_index >= len(rows):
            raise SubmissionValidationError(f"Q{question_id}: binding row is outside source table")
        if selected.column_index < 0 or selected.column_index >= len(rows[selected.row_index]):
            raise SubmissionValidationError(f"Q{question_id}: binding column is outside source table")
        source_value = str(rows[selected.row_index][selected.column_index])
        if source_value != str(selected.raw_value):
            raise SubmissionValidationError(
                f"Q{question_id}: binding raw value differs from immutable source row"
            )
        parsed_source = parse_decimal(source_value).value
        if parsed_source is None or Decimal(parsed_source) != Decimal(selected.parsed_value):
            raise SubmissionValidationError(
                f"Q{question_id}: binding parsed value differs from immutable source cell"
            )
        coordinates = (selected.row_index, selected.column_index)
        previous = column_overrides.setdefault(coordinates, selected.column_text)
        if previous != selected.column_text:
            raise SubmissionValidationError(
                f"Q{question_id}: same source cell has incompatible binding column labels"
            )

    document_id, ordinal, relative_csv = _binding_csv_path(
        binding, asset, question_id=question_id
    )
    output_csv = package_root / relative_csv
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    with output_csv.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["source_row_index", "row_label", "column_label", "raw_value", "numeric_value"],
        )
        writer.writeheader()
        for row_index, row in enumerate(rows):
            label = row_label(row)
            headers = column_descriptions(rows, row_index)
            for column_index, raw_value in enumerate(row):
                parsed = parse_decimal(raw_value)
                # The binder's explicit header string takes precedence for its
                # selected cell.  It is derived from the original grid and
                # protects re-execution when a legacy OCR table has irregular
                # preceding rows that make generic header heuristics differ.
                column_label = column_overrides.get((row_index, column_index), headers.get(column_index, ""))
                writer.writerow(
                    {
                        "source_row_index": row_index,
                        "row_label": label,
                        "column_label": column_label,
                        "raw_value": raw_value,
                        "numeric_value": parsed.value or "",
                    }
                )
    return document_id, ordinal, relative_csv


def _binding_value_query(variable: str, binding: DirectBinding) -> str:
    """Reference the exact raw table row and column selected by the binder."""
    row_value = json.dumps(binding.row_text, ensure_ascii=False)
    column_value = json.dumps(binding.column_text, ensure_ascii=False)
    return (
        f"{variable}.loc[({variable}['source_row_index'] == {binding.row_index}) "
        f"& ({variable}['row_label'] == {row_value}) "
        f"& ({variable}['column_label'] == {column_value}), 'numeric_value'].iloc[0]"
    )


def _unit_multiplier(
    binding: DirectBinding,
    *,
    question_id: int,
    normalize_to_vnd: bool,
) -> Decimal:
    """Return a scale for one operand without silently mixing table units."""
    source = binding.source_unit
    if not normalize_to_vnd:
        return Decimal("1")
    if source not in UNIT_TO_VND:
        raise SubmissionValidationError(
            f"Q{question_id}: composed execution requires a recognised source unit; got {source!r}"
        )
    return UNIT_TO_VND[source]


def _operation_query(operation_ast: Mapping[str, Any], values: Mapping[str, str]) -> str:
    """Translate the deterministic numeric AST into a pandas expression.

    Only arithmetic operations which retain direct operands are allowed here.
    Screening/ranking programs deliberately remain out of scope until their
    entity-by-entity evidence sets have explicit executable semantics.
    """

    def resolve(value: Any) -> str:
        if isinstance(value, str):
            if value not in values:
                raise SubmissionValidationError(
                    f"Submission expression refers to unbound operand {value!r}"
                )
            return values[value]
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return repr(value)
        if isinstance(value, dict):
            return translate(value)
        raise SubmissionValidationError(f"Unsupported operation argument: {value!r}")

    def translate(node: Mapping[str, Any]) -> str:
        operation = str(node.get("op") or "")
        arguments = list(node.get("args") or [])
        rendered = [resolve(argument) for argument in arguments]
        if operation == "lookup" and len(rendered) == 1:
            return rendered[0]
        if operation == "add" and rendered:
            return "(" + " + ".join(rendered) + ")"
        if operation == "subtract" and len(rendered) == 2:
            return f"({rendered[0]} - {rendered[1]})"
        if operation == "absolute_difference" and len(rendered) == 2:
            return f"abs({rendered[0]} - {rendered[1]})"
        if operation == "multiply" and rendered:
            return "(" + " * ".join(rendered) + ")"
        if operation == "divide" and len(rendered) == 2:
            return f"({rendered[0]} / {rendered[1]})"
        if operation == "percentage_change" and len(rendered) == 2:
            return f"(({rendered[0]} - {rendered[1]}) / abs({rendered[1]}) * 100)"
        raise SubmissionValidationError(
            f"Unsupported or malformed submission operation: {operation!r}"
        )

    return translate(operation_ast)


def write_execution_record(
    *,
    question_id: int,
    question: str,
    operand_bindings: Mapping[str, tuple[DirectBinding, Mapping[str, Any]]],
    operation_ast: Mapping[str, Any],
    package_root: Path,
    normalize_operands_to_vnd: bool = False,
) -> dict[str, Any]:
    """Create one exact-row multi-operand submission record.

    The function is intentionally formula-agnostic: callers must provide a
    controlled operation AST and pre-validated bindings.  It refuses missing
    operands and (when requested) unresolved units, recomputes the answer with
    :func:`execute_ast`, then emits a pandas expression that performs the same
    arithmetic from CSV-backed exact cells.
    """
    if not operand_bindings:
        raise SubmissionValidationError(f"Q{question_id}: no grounded operands")

    evidence: list[dict[str, str]] = []
    docs: list[str] = []
    tables: list[str] = []
    value_queries: dict[str, str] = {}
    numeric_values: dict[str, Decimal] = {}
    variables_by_table: dict[Path, str] = {}
    prepared: list[tuple[str, DirectBinding, Mapping[str, Any], str, int, Path]] = []
    bindings_by_table: dict[Path, list[tuple[DirectBinding, Mapping[str, Any]]]] = {}

    for operand_id, pair in operand_bindings.items():
        if not isinstance(operand_id, str) or not operand_id:
            raise SubmissionValidationError(f"Q{question_id}: invalid operand identifier")
        try:
            binding, asset = pair
        except (TypeError, ValueError) as error:
            raise SubmissionValidationError(
                f"Q{question_id}: operand {operand_id!r} must contain binding and asset"
            ) from error
        if not isinstance(binding, DirectBinding):
            raise SubmissionValidationError(f"Q{question_id}: operand {operand_id!r} lacks DirectBinding")

        document_id, ordinal, relative_csv = _binding_csv_path(
            binding, asset, question_id=question_id
        )
        prepared.append((operand_id, binding, asset, document_id, ordinal, relative_csv))
        bindings_by_table.setdefault(relative_csv, []).append((binding, asset))

    for relative_csv, grouped in bindings_by_table.items():
        first_binding, first_asset = grouped[0]
        first_uid = str(first_asset.get("internal_table_uid") or first_binding.internal_table_uid)
        if any(
            str(asset.get("internal_table_uid") or binding.internal_table_uid) != first_uid
            for binding, asset in grouped
        ):
            raise SubmissionValidationError(
                f"Q{question_id}: one evidence CSV cannot combine distinct source tables"
            )
        _write_binding_table(
            first_binding,
            first_asset,
            package_root,
            question_id=question_id,
            bindings=[binding for binding, _ in grouped],
        )
        variable = f"df{len(variables_by_table) + 1}"
        variables_by_table[relative_csv] = variable
        evidence.append({"variable": variable, "csv_path": relative_csv.as_posix()})

    for operand_id, binding, _asset, document_id, ordinal, relative_csv in prepared:
        variable = variables_by_table[relative_csv]
        if document_id not in docs:
            docs.append(document_id)
        table_ref = f"{document_id}|{ordinal}"
        if table_ref not in tables:
            tables.append(table_ref)

        multiplier = _unit_multiplier(
            binding,
            question_id=question_id,
            normalize_to_vnd=normalize_operands_to_vnd,
        )
        query = _binding_value_query(variable, binding)
        if multiplier != 1:
            query = f"({query} * {format(multiplier, 'f')})"
        value_queries[operand_id] = query
        try:
            numeric_values[operand_id] = Decimal(binding.parsed_value) * multiplier
        except Exception as error:
            raise SubmissionValidationError(
                f"Q{question_id}: operand {operand_id!r} has invalid parsed value"
            ) from error

    expression = _operation_query(operation_ast, value_queries)
    try:
        answer = execute_ast(operation_ast, numeric_values)
    except Exception as error:
        raise SubmissionValidationError(
            f"Q{question_id}: controlled operation cannot execute from grounded operands: {error}"
        ) from error
    answer_value = float(answer)
    if not math.isfinite(answer_value):
        raise SubmissionValidationError(f"Q{question_id}: computed answer is not finite")
    return {
        "id": question_id,
        "question": question,
        "answer": answer_value,
        "relevant_docs": docs,
        "relevant_tables": tables,
        "evidence": evidence,
        "pandas_query": f"float({expression})",
    }


def write_direct_lookup_record(
    *,
    question_id: int,
    question: str,
    binding: DirectBinding,
    asset: dict[str, Any],
    package_root: Path,
    variable: str = "df1",
) -> dict[str, Any]:
    """Materialize one exact source table and a re-runnable direct lookup.

    The exported CSV is a lossless *long* normalization of every source cell:
    raw text is retained and numeric cells receive a parsed companion column.
    This avoids row/column-position-only queries while keeping a concrete link
    to the table used by the binder.
    """
    if not VARIABLE_RE.fullmatch(variable):
        raise SubmissionValidationError(f"Q{question_id}: invalid evidence variable")
    document_id, ordinal, relative_csv = _write_binding_table(
        binding,
        asset,
        package_root,
        question_id=question_id,
    )

    source_unit = binding.source_unit
    target_unit = binding.target_unit
    if source_unit != target_unit and source_unit and target_unit:
        if source_unit not in UNIT_TO_VND or target_unit not in UNIT_TO_VND:
            raise SubmissionValidationError(
                f"Q{question_id}: unsupported unit conversion {source_unit} -> {target_unit}"
            )
        multiplier = UNIT_TO_VND[source_unit] / UNIT_TO_VND[target_unit]
    else:
        multiplier = Decimal("1")

    expression = f"float({_binding_value_query(variable, binding)}"
    if multiplier != 1:
        expression += f" * {format(multiplier, 'f')}"
    expression += ")"
    return {
        "id": question_id,
        "question": question,
        "answer": float(Decimal(binding.converted_value)),
        "relevant_docs": [document_id],
        "relevant_tables": [f"{document_id}|{ordinal}"],
        "evidence": [{"variable": variable, "csv_path": relative_csv.as_posix()}],
        "pandas_query": expression,
    }


@dataclass(frozen=True)
class SubmissionValidationResult:
    record_count: int
    csv_count: int
    executed_queries: int

    def to_dict(self) -> dict[str, int]:
        return {
            "record_count": self.record_count,
            "csv_count": self.csv_count,
            "executed_queries": self.executed_queries,
        }


def _finite_float(value: Any, *, field: str, record_id: int) -> float:
    if isinstance(value, bool):
        raise SubmissionValidationError(f"Q{record_id}: {field} must be numeric, not bool")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise SubmissionValidationError(f"Q{record_id}: {field} must be numeric") from error
    if not math.isfinite(result):
        raise SubmissionValidationError(f"Q{record_id}: {field} must be finite")
    return result


def _safe_csv_path(value: Any, *, record_id: int) -> Path:
    if not isinstance(value, str) or not value.startswith("data/"):
        raise SubmissionValidationError(f"Q{record_id}: csv_path must start with data/")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or relative.suffix.casefold() != ".csv":
        raise SubmissionValidationError(f"Q{record_id}: unsafe or non-CSV csv_path: {value!r}")
    return relative


def _query_names(query: str, *, record_id: int) -> set[str]:
    if not isinstance(query, str) or not query.strip():
        raise SubmissionValidationError(f"Q{record_id}: pandas_query must be a non-empty string")
    try:
        expression = ast.parse(query, mode="eval")
    except SyntaxError as error:
        raise SubmissionValidationError(f"Q{record_id}: pandas_query is not an expression") from error
    return {node.id for node in ast.walk(expression) if isinstance(node, ast.Name)}


def _execute_query(
    query: str,
    frames: dict[str, pd.DataFrame],
    *,
    record_id: int,
) -> float:
    allowed = {"float": float, "int": int, "abs": abs, "round": round, "min": min, "max": max}
    try:
        value = eval(query, {"__builtins__": {}}, {**allowed, **frames})  # noqa: S307 - restricted expression
    except Exception as error:  # execution evidence is intentionally surfaced with its question ID
        raise SubmissionValidationError(f"Q{record_id}: pandas_query failed: {error}") from error
    return _finite_float(value, field="pandas_query result", record_id=record_id)


def validate_submission_directory(
    directory: Path,
    expected_questions: Iterable[dict[str, Any]],
) -> SubmissionValidationResult:
    """Validate and execute every record from an unpacked submission directory."""
    directory = directory.resolve()
    submission_path = directory / "submission.json"
    if not submission_path.is_file():
        raise SubmissionValidationError("submission.json is missing at the package root")
    try:
        records = json.loads(submission_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SubmissionValidationError("submission.json is not valid JSON") from error
    if not isinstance(records, list):
        raise SubmissionValidationError("submission.json must contain a JSON list")

    expected = {int(item["id"]): str(item["question"]) for item in expected_questions}
    received: set[int] = set()
    referenced_csvs: set[Path] = set()
    executed_queries = 0

    for record in records:
        if not isinstance(record, dict):
            raise SubmissionValidationError("each submission item must be an object")
        record_id = record.get("id")
        if not isinstance(record_id, int) or isinstance(record_id, bool):
            raise SubmissionValidationError("submission id must be an integer")
        if record_id in received:
            raise SubmissionValidationError(f"Q{record_id}: duplicate id")
        received.add(record_id)
        if record_id not in expected:
            raise SubmissionValidationError(f"Q{record_id}: unexpected id")
        if record.get("question") != expected[record_id]:
            raise SubmissionValidationError(f"Q{record_id}: question text differs from source")

        answer = _finite_float(record.get("answer"), field="answer", record_id=record_id)
        docs = record.get("relevant_docs")
        tables = record.get("relevant_tables")
        evidence = record.get("evidence")
        if not isinstance(docs, list) or not docs or not all(isinstance(value, str) and value for value in docs):
            raise SubmissionValidationError(f"Q{record_id}: relevant_docs must be a non-empty string list")
        if not isinstance(tables, list) or not tables or not all(
            isinstance(value, str) and TABLE_REF_RE.fullmatch(value) for value in tables
        ):
            raise SubmissionValidationError(f"Q{record_id}: invalid relevant_tables format")
        if not isinstance(evidence, list) or not evidence:
            raise SubmissionValidationError(f"Q{record_id}: evidence must be non-empty")

        frames: dict[str, pd.DataFrame] = {}
        for item in evidence:
            if not isinstance(item, dict):
                raise SubmissionValidationError(f"Q{record_id}: evidence item must be an object")
            variable = item.get("variable")
            if not isinstance(variable, str) or not VARIABLE_RE.fullmatch(variable):
                raise SubmissionValidationError(f"Q{record_id}: invalid evidence variable")
            if variable in frames:
                raise SubmissionValidationError(f"Q{record_id}: duplicate evidence variable {variable}")
            relative_csv = _safe_csv_path(item.get("csv_path"), record_id=record_id)
            csv_path = (directory / relative_csv).resolve()
            if directory not in csv_path.parents or not csv_path.is_file():
                raise SubmissionValidationError(f"Q{record_id}: missing evidence CSV {relative_csv}")
            frames[variable] = pd.read_csv(csv_path)
            referenced_csvs.add(relative_csv)

        query = record.get("pandas_query")
        names = _query_names(query, record_id=record_id)
        missing_variables = set(frames) - names
        unknown_variables = (names - set(frames)) - {"float", "int", "abs", "round", "min", "max"}
        if missing_variables:
            raise SubmissionValidationError(f"Q{record_id}: query omits evidence variable(s): {sorted(missing_variables)}")
        if unknown_variables:
            raise SubmissionValidationError(f"Q{record_id}: query references unknown name(s): {sorted(unknown_variables)}")
        computed = _execute_query(query, frames, record_id=record_id)
        if not math.isclose(answer, computed, rel_tol=1e-9, abs_tol=1e-6):
            raise SubmissionValidationError(
                f"Q{record_id}: answer {answer} differs from pandas_query result {computed}"
            )
        executed_queries += 1

    if received != set(expected):
        missing = sorted(set(expected) - received)
        raise SubmissionValidationError(f"submission is incomplete; missing {len(missing)} ids, first: {missing[:10]}")
    return SubmissionValidationResult(len(records), len(referenced_csvs), executed_queries)


def validate_submission_zip(
    archive: Path,
    expected_questions: Iterable[dict[str, Any]],
    workspace: Path,
) -> SubmissionValidationResult:
    """Safely unpack and validate a ZIP with exactly the required root layout."""
    archive = archive.resolve()
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        names = [Path(info.filename) for info in bundle.infolist() if not info.is_dir()]
        if Path("submission.json") not in names:
            raise SubmissionValidationError("ZIP does not contain root submission.json")
        if sum(path.suffix.casefold() == ".json" for path in names) != 1:
            raise SubmissionValidationError("ZIP must contain exactly one JSON file")
        for path in names:
            if path.is_absolute() or ".." in path.parts:
                raise SubmissionValidationError(f"unsafe ZIP member: {path}")
            if path != Path("submission.json") and (not path.parts or path.parts[0] != "data"):
                raise SubmissionValidationError(f"unexpected ZIP member outside data/: {path}")
        bundle.extractall(workspace)
    return validate_submission_directory(workspace, expected_questions)
