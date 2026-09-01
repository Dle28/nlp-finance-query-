from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from finance_query.e2e.core.dense_retrieval import build_dense_index, search_dense_batch
from finance_query.e2e.core.table_retrieval import (
    build_lexical_index,
    make_fts_query,
    search_lexical,
    validate_asset_closure,
)


def _write_assets(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = [
        {
            "internal_table_uid": "u1",
            "document_id": "AAA_financial_statements_2023_consolidated",
            "ticker": "AAA",
            "report_year": 2023,
            "scope": "consolidated",
            "headers": ["Chỉ tiêu", "Năm 2023"],
            "row_paths": ["Doanh thu thuần > 100"],
            "context_before": "từ chỉ xuất hiện ở context",
        },
        {
            "internal_table_uid": "u2",
            "document_id": "AAA_financial_statements_2023_separate",
            "ticker": "AAA",
            "report_year": 2023,
            "scope": "separate",
            "headers": ["Chỉ tiêu", "Năm 2023"],
            "row_paths": ["Lợi nhuận sau thuế > 20"],
            "context_before": "doanh thu thuần ngoài bảng",
        },
        {
            "internal_table_uid": "u3",
            "document_id": "BBB_financial_statements_2022_consolidated",
            "ticker": "BBB",
            "report_year": 2022,
            "scope": "consolidated",
            "headers": ["Chỉ tiêu", "Năm 2022"],
            "row_paths": ["Doanh thu thuần > 80"],
            "context_before": "",
        },
    ]
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    return rows


def _contract(path: Path, *, tables: int = 3) -> dict[str, object]:
    source_closure = path.with_name("source_closure.jsonl")
    if not source_closure.exists():
        source_closure.write_text(
            "".join(
                json.dumps({"document_id": document_id, "table_count": 1}) + "\n"
                for document_id in (
                    "AAA_financial_statements_2023_consolidated",
                    "AAA_financial_statements_2023_separate",
                    "BBB_financial_statements_2022_consolidated",
                )
            ),
            encoding="utf-8",
        )
    return {
        "expected_asset_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "expected_source_closure_sha256": hashlib.sha256(source_closure.read_bytes()).hexdigest(),
        "expected_table_count": tables,
        "expected_document_count": 3,
        "expected_source_report_count": 3,
        "expected_zero_table_report_count": 0,
        "expected_ticker_count": 2,
    }


def test_closure_rejects_a_pinned_but_incomplete_population(tmp_path: Path) -> None:
    assets = tmp_path / "assets.jsonl"
    _write_assets(assets)
    with pytest.raises(ValueError, match="asset closure mismatch"):
        validate_asset_closure(assets, _contract(assets, tables=4))


def test_lexical_build_uses_table_only_text_and_metadata_filters(tmp_path: Path) -> None:
    assets = tmp_path / "assets.jsonl"
    _write_assets(assets)
    index = tmp_path / "lexical.sqlite"
    receipt = build_lexical_index(
        asset_path=assets,
        source_closure_path=tmp_path / "source_closure.jsonl",
        index_path=index,
        contract=_contract(assets),
        progress_every=0,
    )
    assert receipt["indexed_count"] == 3
    assert receipt["context_in_index"] is False
    rows = search_lexical(
        index_path=index,
        query="Doanh thu thuần là bao nhiêu?",
        ticker="AAA",
        report_year=2023,
    )
    assert [row["internal_table_uid"] for row in rows] == ["u1"]
    assert all(row["may_authorize_answer"] is False for row in rows)
    assert search_lexical(
        index_path=index,
        query="xuất hiện context",
        ticker="AAA",
        report_year=2023,
    ) == []


def test_lexical_any_mode_preserves_metadata_filters(tmp_path: Path) -> None:
    assets = tmp_path / "assets.jsonl"
    _write_assets(assets)
    index = tmp_path / "lexical.sqlite"
    build_lexical_index(
        asset_path=assets,
        source_closure_path=tmp_path / "source_closure.jsonl",
        index_path=index,
        contract=_contract(assets),
        progress_every=0,
    )
    rows = search_lexical(
        index_path=index,
        query="doanh thu thương hiệu không có trong bảng",
        ticker="AAA",
        report_year=2023,
        match_mode="any",
    )
    assert [row["internal_table_uid"] for row in rows] == ["u1"]
    assert make_fts_query("doanh thu lợi nhuận", operator="OR") == (
        '"doanh" OR "thu" OR "lợi" OR "nhuận"'
    )


class _FakeEncoder:
    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.calls = 0
        self.fail_on_call = fail_on_call

    def encode(self, sentences: list[str], **_: object) -> np.ndarray:
        self.calls += 1
        if self.fail_on_call == self.calls:
            raise RuntimeError("simulated encoder interruption")
        values = []
        for sentence in sentences:
            seed = float(sum(ord(character) for character in sentence) % 17 + 1)
            vector = np.asarray([seed, seed + 1, seed + 2, seed + 3], dtype=np.float32)
            vector /= np.linalg.norm(vector)
            values.append(vector)
        return np.stack(values)


def test_dense_cpu_builder_resumes_without_promoting_partial_output(tmp_path: Path) -> None:
    assets = tmp_path / "assets.jsonl"
    _write_assets(assets)
    output = tmp_path / "dense"
    first_encoder = _FakeEncoder(fail_on_call=3)
    with pytest.raises(RuntimeError, match="simulated encoder interruption"):
        build_dense_index(
            asset_path=assets,
            source_closure_path=tmp_path / "source_closure.jsonl",
            output_dir=output,
            contract=_contract(assets),
            model_name="fake-open-model",
            requested_device="cpu",
            batch_size=2,
            checkpoint_every_batches=1,
            encoder_factory=lambda _model, _device: first_encoder,
        )
    assert not (output / "dense_embeddings_v1.npy").exists()
    state = json.loads((output / "dense_build_state_v1.json").read_text(encoding="utf-8"))
    assert state["completed_count"] == 2

    receipt = build_dense_index(
        asset_path=assets,
        source_closure_path=tmp_path / "source_closure.jsonl",
        output_dir=output,
        contract=_contract(assets),
        model_name="fake-open-model",
        requested_device="cpu",
        batch_size=2,
        checkpoint_every_batches=1,
        encoder_factory=lambda _model, _device: _FakeEncoder(),
    )
    embeddings = np.load(output / "dense_embeddings_v1.npy")
    assert embeddings.shape == (3, 4)
    assert receipt["resolved_device"] == "cpu"
    assert receipt["count"] == 3
    assert receipt["partial_outputs_are_usable"] is False
    assert not (output / "dense_build_state_v1.json").exists()


def test_dense_batch_loads_once_and_preserves_route_filters(tmp_path: Path) -> None:
    assets = tmp_path / "assets.jsonl"
    _write_assets(assets)
    output = tmp_path / "dense"
    encoder = _FakeEncoder()
    build_dense_index(
        asset_path=assets,
        source_closure_path=tmp_path / "source_closure.jsonl",
        output_dir=output,
        contract=_contract(assets),
        model_name="fake-open-model",
        requested_device="cpu",
        batch_size=2,
        checkpoint_every_batches=1,
        encoder_factory=lambda _model, _device: encoder,
    )
    calls_after_build = encoder.calls
    results = search_dense_batch(
        index_dir=output,
        requests=[
            {
                "query": "Doanh thu thuần",
                "ticker": "AAA",
                "report_year": 2023,
                "scope": "consolidated",
            },
            {
                "query": "Doanh thu thuần",
                "ticker": "AAA",
                "report_year": 2023,
            },
            {
                "query": "Doanh thu thuần",
                "ticker": "ZZZ",
                "report_year": 2023,
            },
        ],
        limit=10,
        encoder_factory=lambda _model, _device: encoder,
    )
    assert encoder.calls == calls_after_build + 1
    assert [[row["internal_table_uid"] for row in rows] for rows in results] == [
        ["u1"],
        ["u1", "u2"],
        [],
    ]
    assert all(
        row["may_authorize_answer"] is False
        for rows in results
        for row in rows
    )
