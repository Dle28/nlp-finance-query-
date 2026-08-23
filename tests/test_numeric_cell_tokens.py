from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from finance_query.numeric_cell_tokens import (
    NumericCellTokenError,
    load_numeric_cell_tokens,
    materialize_numeric_cell_tokens,
    verify_operand_token,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, dict]:
    tables = tmp_path / "tables.jsonl"
    table = {
        "document_id": "ABC-2024",
        "internal_table_uid": "table-1",
        "rows": [["Chỉ tiêu", "2024"], ["Doanh thu", "1.234"]],
        "source_provenance": {
            "source_sha256": "a" * 64,
            "table_sha256": "b" * 64,
        },
    }
    tables.write_text(json.dumps(table, ensure_ascii=False) + "\n", encoding="utf-8")
    operand = {
        "binding_status": "binding_ready",
        "role": "revenue",
        "document_id": "ABC-2024",
        "internal_table_uid": "table-1",
        "row_index": 1,
        "column_index": 1,
        "raw_source_cell": "1.234",
        "raw_decimal_candidate": "1234",
    }
    bindings = tmp_path / "bindings.jsonl"
    bindings.write_text(
        json.dumps(
            {
                "question_id": 7,
                "stages": [{"stage_id": "stage-1", "required_operands": [operand]}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "bindings.manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "inputs": {"structured_tables": {"sha256": _sha(tables)}},
                "outputs": {"bindings": {"sha256": _sha(bindings)}},
            }
        ),
        encoding="utf-8",
    )
    return bindings, manifest, tables, operand


def test_numeric_cell_tokens_split_public_view_from_executor_registry(tmp_path: Path) -> None:
    bindings, manifest, tables, operand = _fixture(tmp_path)
    registry = tmp_path / "registry.jsonl"
    public = tmp_path / "public.jsonl"
    result = materialize_numeric_cell_tokens(
        bindings=bindings,
        bindings_manifest=manifest,
        structured_tables=tables,
        registry_output=registry,
        public_view_output=public,
    )
    assert result["counts"]["token_count"] == 1
    assert "1234" not in public.read_text(encoding="utf-8")
    assert "1234" in registry.read_text(encoding="utf-8")
    index = load_numeric_cell_tokens(registry, Path(result["manifest_path"]))
    token = index[(7, "stage-1", "revenue")]
    assert verify_operand_token(
        token,
        question_id=7,
        stage_id="stage-1",
        operand=operand,
    ) == "1234"


def test_numeric_cell_token_rejects_coordinate_drift(tmp_path: Path) -> None:
    bindings, manifest, tables, _operand = _fixture(tmp_path)
    registry = tmp_path / "registry.jsonl"
    public = tmp_path / "public.jsonl"
    result = materialize_numeric_cell_tokens(
        bindings=bindings,
        bindings_manifest=manifest,
        structured_tables=tables,
        registry_output=registry,
        public_view_output=public,
    )
    token = next(iter(load_numeric_cell_tokens(registry, Path(result["manifest_path"])).values()))
    with pytest.raises(NumericCellTokenError, match="lineage mismatch"):
        verify_operand_token(
            token,
            question_id=7,
            stage_id="stage-1",
            operand={
                "role": "revenue",
                "document_id": "ABC-2024",
                "internal_table_uid": "table-1",
                "row_index": 1,
                "column_index": 0,
                "raw_decimal_candidate": "1234",
            },
        )


def test_numeric_cell_token_rejects_raw_cell_drift(tmp_path: Path) -> None:
    bindings, manifest, tables, operand = _fixture(tmp_path)
    registry = tmp_path / "registry.jsonl"
    public = tmp_path / "public.jsonl"
    result = materialize_numeric_cell_tokens(
        bindings=bindings,
        bindings_manifest=manifest,
        structured_tables=tables,
        registry_output=registry,
        public_view_output=public,
    )
    token = next(iter(load_numeric_cell_tokens(registry, Path(result["manifest_path"])).values()))
    changed = {**operand, "raw_source_cell": "9.999"}
    with pytest.raises(NumericCellTokenError, match="raw cell hash mismatch"):
        verify_operand_token(
            token,
            question_id=7,
            stage_id="stage-1",
            operand=changed,
        )


def test_numeric_cell_token_rejects_registry_or_manifest_tampering(tmp_path: Path) -> None:
    bindings, manifest, tables, operand = _fixture(tmp_path)
    registry = tmp_path / "registry.jsonl"
    public = tmp_path / "public.jsonl"
    result = materialize_numeric_cell_tokens(
        bindings=bindings,
        bindings_manifest=manifest,
        structured_tables=tables,
        registry_output=registry,
        public_view_output=public,
    )
    registry_manifest = Path(result["manifest_path"])
    token = json.loads(registry.read_text(encoding="utf-8"))
    token["token_id"] = "CELL_" + "0" * 32
    registry.write_text(json.dumps(token) + "\n", encoding="utf-8")
    with pytest.raises(NumericCellTokenError, match="SHA-256 mismatch"):
        load_numeric_cell_tokens(registry, registry_manifest)

    # Even if an attacker recomputes the local output hash, token identity is
    # derived independently from the complete source lineage.
    payload = json.loads(registry_manifest.read_text(encoding="utf-8"))
    payload["outputs"]["executor_registry"]["sha256"] = _sha(registry)
    registry_manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(NumericCellTokenError, match="identity is invalid|ID mismatch"):
        index = load_numeric_cell_tokens(registry, registry_manifest)
        verify_operand_token(
            index[(7, "stage-1", "revenue")],
            question_id=7,
            stage_id="stage-1",
            operand=operand,
        )


def test_numeric_cell_token_manifest_is_bound_to_current_bindings(tmp_path: Path) -> None:
    bindings, manifest, tables, _operand = _fixture(tmp_path)
    registry = tmp_path / "registry.jsonl"
    public = tmp_path / "public.jsonl"
    result = materialize_numeric_cell_tokens(
        bindings=bindings,
        bindings_manifest=manifest,
        structured_tables=tables,
        registry_output=registry,
        public_view_output=public,
    )
    bindings.write_text(bindings.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(NumericCellTokenError, match="stale for bindings"):
        load_numeric_cell_tokens(
            registry,
            Path(result["manifest_path"]),
            bindings_path=bindings,
            bindings_manifest_path=manifest,
        )
