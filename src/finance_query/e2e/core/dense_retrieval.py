"""Resumable dense table-index builder for CPU or CUDA/Kaggle.

Dense results are navigation candidates only and cannot authorize evidence or
answers.  The builder uses the same complete-corpus contract as lexical search.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import json
import os
from pathlib import Path
import time
from typing import Any, Protocol

from .table_retrieval import (
    load_jsonl,
    sha256_file,
    table_only_text,
    validate_asset_closure,
    validate_source_closure,
)


class Encoder(Protocol):
    def encode(self, sentences: list[str], **kwargs: Any) -> Any: ...


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    partial = path.with_name(f".{path.name}.partial")
    partial.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(partial, path)


def resolve_device(requested: str) -> str:
    if requested not in {"auto", "cpu", "cuda"}:
        raise ValueError("device must be auto, cpu, or cuda")
    import torch

    cuda_available = bool(torch.cuda.is_available())
    if requested == "cuda" and not cuda_available:
        raise RuntimeError("CUDA was requested but is not available")
    if requested == "auto":
        return "cuda" if cuda_available else "cpu"
    return requested


def _load_encoder(model_name: str, device: str) -> Encoder:
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name, device=device)


def _materialize_metadata(
    asset_path: Path,
    metadata_partial: Path,
    uids_partial: Path,
    expected_count: int,
) -> None:
    if metadata_partial.exists() or uids_partial.exists():
        raise FileExistsError("partial dense metadata already exists without a resumable state")
    count = 0
    with metadata_partial.open("w", encoding="utf-8") as metadata_file, uids_partial.open(
        "w", encoding="utf-8"
    ) as uid_file:
        for asset in load_jsonl(asset_path):
            uid = str(asset["internal_table_uid"])
            metadata_file.write(
                json.dumps(
                    {
                        "row": count,
                        "internal_table_uid": uid,
                        "document_id": str(asset["document_id"]),
                        "ticker": str(asset["ticker"]),
                        "report_year": asset.get("report_year"),
                        "scope": str(asset.get("scope") or ""),
                        "navigation_metadata_only": True,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
            uid_file.write(json.dumps({"row": count, "internal_table_uid": uid}, sort_keys=True) + "\n")
            count += 1
    if count != expected_count:
        metadata_partial.unlink(missing_ok=True)
        uids_partial.unlink(missing_ok=True)
        raise ValueError(f"dense metadata count mismatch: {count} != {expected_count}")


def build_dense_index(
    *,
    asset_path: Path,
    source_closure_path: Path,
    output_dir: Path,
    contract: Mapping[str, Any],
    model_name: str,
    requested_device: str,
    batch_size: int,
    checkpoint_every_batches: int = 25,
    encoder_factory: Callable[[str, str], Encoder] | None = None,
) -> dict[str, Any]:
    """Build or resume normalized float32 embeddings without partial promotion."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if checkpoint_every_batches < 1:
        raise ValueError("checkpoint_every_batches must be positive")
    import numpy as np

    closure = {
        **validate_asset_closure(asset_path, contract),
        **validate_source_closure(source_closure_path, contract),
    }
    resolved_device = resolve_device(requested_device)
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "dense_build_state_v1.json"
    embeddings_partial = output_dir / "dense_embeddings_v1.npy.partial"
    metadata_partial = output_dir / "dense_metadata_v1.jsonl.partial"
    uids_partial = output_dir / "dense_uids_v1.jsonl.partial"
    embeddings_final = output_dir / "dense_embeddings_v1.npy"
    metadata_final = output_dir / "dense_metadata_v1.jsonl"
    uids_final = output_dir / "dense_uids_v1.jsonl"
    receipt_path = output_dir / "dense_build_receipt_v1.json"
    manifest_path = output_dir / "manifest.json"
    for final_path in (embeddings_final, metadata_final, uids_final, receipt_path, manifest_path):
        if final_path.exists():
            raise FileExistsError(f"refusing to overwrite completed dense output: {final_path}")

    encoder = (encoder_factory or _load_encoder)(model_name, resolved_device)
    total_count = int(closure["table_count"])
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        expected_state = {
            "asset_sha256": closure["asset_sha256"],
            "source_closure_sha256": closure["source_closure_sha256"],
            "model": model_name,
            "resolved_device": resolved_device,
            "batch_size": batch_size,
            "total_count": total_count,
        }
        mismatches = {
            key: {"state": state.get(key), "requested": value}
            for key, value in expected_state.items()
            if state.get(key) != value
        }
        if mismatches:
            raise ValueError(f"resume state mismatch: {json.dumps(mismatches, sort_keys=True)}")
        if not all(path.exists() for path in (embeddings_partial, metadata_partial, uids_partial)):
            raise ValueError("resume state exists but one or more partial files are missing")
        dimension = int(state["dimension"])
        completed_count = int(state["completed_count"])
        started_unix = float(state["started_unix"])
        embeddings = np.lib.format.open_memmap(
            embeddings_partial,
            mode="r+",
            dtype=np.float32,
            shape=(total_count, dimension),
        )
    else:
        # No state means these files cannot prove a completed checkpoint. They
        # may only come from an interrupted initialization and are safe to
        # discard before a clean restart.
        for path in (embeddings_partial, metadata_partial, uids_partial):
            path.unlink(missing_ok=True)
        first_asset = next(iter(load_jsonl(asset_path)), None)
        if first_asset is None:
            raise ValueError("asset file is empty")
        probe = np.asarray(
            encoder.encode(
                [f"passage: {table_only_text(first_asset)}"],
                batch_size=1,
                normalize_embeddings=True,
                show_progress_bar=False,
            ),
            dtype=np.float32,
        )
        if probe.ndim != 2 or probe.shape[0] != 1:
            raise ValueError(f"encoder returned invalid probe shape: {probe.shape}")
        dimension = int(probe.shape[1])
        embeddings = np.lib.format.open_memmap(
            embeddings_partial,
            mode="w+",
            dtype=np.float32,
            shape=(total_count, dimension),
        )
        _materialize_metadata(asset_path, metadata_partial, uids_partial, total_count)
        completed_count = 0
        started_unix = time.time()
        state = {
            "protocol": "vifinqa_dense_build_state_v1",
            "asset_sha256": closure["asset_sha256"],
            "source_closure_sha256": closure["source_closure_sha256"],
            "model": model_name,
            "requested_device": requested_device,
            "resolved_device": resolved_device,
            "batch_size": batch_size,
            "dimension": dimension,
            "total_count": total_count,
            "completed_count": completed_count,
            "started_unix": started_unix,
            "partial_outputs_are_usable": False,
            "navigation_metadata_only": True,
        }
        _write_json_atomic(state_path, state)

    batch_texts: list[str] = []
    batch_start = completed_count
    encoded_batches = 0
    for row_index, asset in enumerate(load_jsonl(asset_path)):
        if row_index < completed_count:
            continue
        batch_texts.append(f"passage: {table_only_text(asset)}")
        if len(batch_texts) < batch_size and row_index + 1 < total_count:
            continue
        encoded = np.asarray(
            encoder.encode(
                batch_texts,
                batch_size=batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
            ),
            dtype=np.float32,
        )
        if encoded.shape != (len(batch_texts), dimension):
            raise ValueError(
                f"encoder shape mismatch: expected {(len(batch_texts), dimension)}, observed {encoded.shape}"
            )
        batch_end = batch_start + len(batch_texts)
        embeddings[batch_start:batch_end] = encoded
        completed_count = batch_end
        batch_start = batch_end
        batch_texts.clear()
        encoded_batches += 1
        if encoded_batches % checkpoint_every_batches == 0 or completed_count == total_count:
            embeddings.flush()
            state["completed_count"] = completed_count
            state["last_checkpoint_unix"] = time.time()
            _write_json_atomic(state_path, state)
            print(
                json.dumps(
                    {
                        "phase": "dense_encode",
                        "device": resolved_device,
                        "completed": completed_count,
                        "total": total_count,
                    }
                ),
                flush=True,
            )

    if completed_count != total_count:
        raise ValueError(f"dense build stopped early: {completed_count} != {total_count}")
    embeddings.flush()
    del embeddings
    os.replace(embeddings_partial, embeddings_final)
    os.replace(metadata_partial, metadata_final)
    os.replace(uids_partial, uids_final)
    state_path.unlink(missing_ok=True)
    receipt = {
        "protocol": "vifinqa_table_only_dense_index_v1",
        "asset_closure": closure,
        "model": model_name,
        "requested_device": requested_device,
        "resolved_device": resolved_device,
        "batch_size": batch_size,
        "dimension": dimension,
        "count": total_count,
        "normalize_embeddings": True,
        "representation": "table_only_headers_and_row_paths",
        "context_in_index": False,
        "elapsed_seconds": time.time() - started_unix,
        "resumable": True,
        "partial_outputs_are_usable": False,
        "navigation_metadata_only": True,
        "may_authorize_answer": False,
        "submission_eligible": False,
    }
    _write_json_atomic(receipt_path, receipt)
    manifest = {
        "protocol": "vifinqa_dense_index_manifest_v1",
        "inputs": {
            "assets": {"path": str(asset_path.resolve()), "sha256": closure["asset_sha256"]},
            "source_closure": {
                "path": str(source_closure_path.resolve()),
                "sha256": closure["source_closure_sha256"],
            },
        },
        "outputs": {
            path.name: {"sha256": sha256_file(path), "size_bytes": path.stat().st_size}
            for path in (embeddings_final, metadata_final, uids_final, receipt_path)
        },
        "navigation_metadata_only": True,
        "may_authorize_answer": False,
        "submission_eligible": False,
    }
    _write_json_atomic(manifest_path, manifest)
    return receipt


def search_dense(
    *,
    index_dir: Path,
    query: str,
    ticker: str,
    report_year: int,
    scope: str | None = None,
    limit: int = 10,
    requested_device: str = "cpu",
    encoder_factory: Callable[[str, str], Encoder] | None = None,
) -> list[dict[str, Any]]:
    """Brute-force cosine search over the metadata-filtered candidate rows."""
    if not ticker.strip() or not isinstance(report_year, int):
        raise ValueError("ticker and report_year are required retrieval filters")
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    import numpy as np

    receipt = json.loads((index_dir / "dense_build_receipt_v1.json").read_text(encoding="utf-8"))
    if receipt.get("navigation_metadata_only") is not True:
        raise ValueError("dense receipt is not navigation-only")
    embeddings = np.load(index_dir / "dense_embeddings_v1.npy", mmap_mode="r")
    candidate_rows: list[int] = []
    candidate_meta: list[dict[str, Any]] = []
    for row in load_jsonl(index_dir / "dense_metadata_v1.jsonl"):
        if str(row["ticker"]).upper() != ticker.strip().upper():
            continue
        if int(row["report_year"]) != report_year:
            continue
        if scope is not None and row["scope"] != scope:
            continue
        candidate_rows.append(int(row["row"]))
        candidate_meta.append(row)
    if not candidate_rows:
        return []
    device = resolve_device(requested_device)
    encoder = (encoder_factory or _load_encoder)(str(receipt["model"]), device)
    query_vector = np.asarray(
        encoder.encode(
            [f"query: {query}"],
            batch_size=1,
            normalize_embeddings=True,
            show_progress_bar=False,
        ),
        dtype=np.float32,
    )[0]
    scores = np.asarray(embeddings[candidate_rows]) @ query_vector
    order = np.argsort(-scores)[:limit]
    return [
        {
            **candidate_meta[int(position)],
            "rank": rank,
            "score": float(scores[int(position)]),
            "may_authorize_answer": False,
        }
        for rank, position in enumerate(order, start=1)
    ]


def search_dense_batch(
    *,
    index_dir: Path,
    requests: Sequence[Mapping[str, Any]],
    limit: int = 10,
    requested_device: str = "cpu",
    encode_batch_size: int = 64,
    encoder_factory: Callable[[str, str], Encoder] | None = None,
) -> list[list[dict[str, Any]]]:
    """Search many grounded routes while loading the model and index once.

    Results stay aligned with ``requests``.  Every request must provide a
    non-empty query and ticker plus an integer ``report_year``.  ``scope`` is
    optional; callers that need an explicit-scope-first policy can issue one
    scoped and one unscoped request, then merge them visibly.
    """
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    if encode_batch_size < 1:
        raise ValueError("encode_batch_size must be positive")
    if not requests:
        return []

    normalized: list[dict[str, Any]] = []
    allowed_scopes = {"consolidated", "separate", "aggregated", "unknown"}
    for position, request in enumerate(requests):
        query = str(request.get("query") or "").strip()
        ticker = str(request.get("ticker") or "").strip().upper()
        report_year = request.get("report_year")
        scope_value = request.get("scope")
        scope = str(scope_value) if scope_value is not None else None
        if not query or not ticker or isinstance(report_year, bool) or not isinstance(report_year, int):
            raise ValueError(f"invalid dense request at position {position}")
        if scope is not None and scope not in allowed_scopes:
            raise ValueError(f"unsupported explicit scope: {scope}")
        normalized.append(
            {
                "query": query,
                "ticker": ticker,
                "report_year": report_year,
                "scope": scope,
            }
        )

    import numpy as np

    receipt = json.loads((index_dir / "dense_build_receipt_v1.json").read_text(encoding="utf-8"))
    if receipt.get("navigation_metadata_only") is not True:
        raise ValueError("dense receipt is not navigation-only")
    embeddings = np.load(index_dir / "dense_embeddings_v1.npy", mmap_mode="r")
    expected_count = int(receipt.get("count") or 0)
    expected_dimension = int(receipt.get("dimension") or 0)
    if embeddings.shape != (expected_count, expected_dimension):
        raise ValueError(
            f"dense embedding shape mismatch: {embeddings.shape} != "
            f"{(expected_count, expected_dimension)}"
        )

    metadata = list(load_jsonl(index_dir / "dense_metadata_v1.jsonl"))
    if len(metadata) != expected_count:
        raise ValueError("dense metadata count does not match receipt")
    grouped_rows: dict[tuple[str, int], list[int]] = {}
    grouped_scoped_rows: dict[tuple[str, int, str], list[int]] = {}
    for metadata_position, row in enumerate(metadata):
        row_index = int(row.get("row", -1))
        if row_index != metadata_position:
            raise ValueError("dense metadata row order is not contiguous")
        ticker = str(row.get("ticker") or "").upper()
        year = int(row["report_year"])
        scope = str(row.get("scope") or "unknown")
        grouped_rows.setdefault((ticker, year), []).append(row_index)
        grouped_scoped_rows.setdefault((ticker, year, scope), []).append(row_index)

    device = resolve_device(requested_device)
    encoder = (encoder_factory or _load_encoder)(str(receipt["model"]), device)
    query_vectors = np.asarray(
        encoder.encode(
            [f"query: {request['query']}" for request in normalized],
            batch_size=encode_batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        ),
        dtype=np.float32,
    )
    if query_vectors.shape != (len(normalized), expected_dimension):
        raise ValueError(
            f"dense query shape mismatch: {query_vectors.shape} != "
            f"{(len(normalized), expected_dimension)}"
        )

    outputs: list[list[dict[str, Any]]] = []
    for request, query_vector in zip(normalized, query_vectors, strict=True):
        if request["scope"] is None:
            candidate_rows = grouped_rows.get((request["ticker"], request["report_year"]), [])
        else:
            candidate_rows = grouped_scoped_rows.get(
                (request["ticker"], request["report_year"], request["scope"]), []
            )
        if not candidate_rows:
            outputs.append([])
            continue
        scores = np.asarray(embeddings[candidate_rows]) @ query_vector
        order = np.argsort(-scores)[:limit]
        outputs.append(
            [
                {
                    **metadata[candidate_rows[int(local_position)]],
                    "rank": rank,
                    "score": float(scores[int(local_position)]),
                    "navigation_metadata_only": True,
                    "may_authorize_answer": False,
                }
                for rank, local_position in enumerate(order, start=1)
            ]
        )
    return outputs
