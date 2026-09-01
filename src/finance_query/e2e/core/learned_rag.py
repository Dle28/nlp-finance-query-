"""Fine-tuned navigation models for the primary submission pipeline.

The reranker in this module is deliberately navigation-only: it can change
candidate ordering, but never writes a numeric answer or provenance.  The
primary submission builder has a separate staged-result adapter that accepts a
model answer only after replaying its cited cells against the current V2 table.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from .table_retrieval import table_only_text


class PairScorer(Protocol):
    def predict(self, sentences: list[list[str]], **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class FineTunedArtifactManifest:
    """Resolved V10/V11 artifact paths and promotion metadata.

    ``fast_dev_run`` is retained explicitly: a smoke checkpoint may be used
    for a measured ablation but can never silently become a production gate.
    """

    root: Path
    retriever_path: Path | None
    reranker_path: Path | None
    generator_path: Path | None
    metrics_path: Path | None
    metrics: Mapping[str, Any]
    fast_dev_run: bool
    promotion_allowed: bool
    reranker_promotion_allowed: bool = False
    generator_promotion_allowed: bool = False


def resolve_finetuned_artifacts(root: str | Path) -> FineTunedArtifactManifest:
    """Resolve a Kaggle fine-tune output directory without loading weights."""
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        raise FileNotFoundError(f"fine-tuned model root is not a directory: {root_path}")
    metrics_path = root_path / "metrics.json"
    metrics: Mapping[str, Any] = {}
    if metrics_path.exists():
        payload = json.loads(metrics_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("fine-tuned metrics.json must be an object")
        metrics = payload
    retriever_path = root_path / "retriever_finetuned"
    reranker_path = root_path / "reranker_finetuned"
    generator_path = root_path / "generator_lora"
    return FineTunedArtifactManifest(
        root=root_path,
        retriever_path=retriever_path if retriever_path.is_dir() else None,
        reranker_path=reranker_path if reranker_path.is_dir() else None,
        generator_path=generator_path if generator_path.is_dir() else None,
        metrics_path=metrics_path if metrics_path.exists() else None,
        metrics=metrics,
        fast_dev_run=bool(metrics.get("fast_dev_run", False)),
        promotion_allowed=bool(metrics.get("retriever_promotion_allowed", False)),
        reranker_promotion_allowed=bool(metrics.get("reranker_promotion_allowed", False)),
        generator_promotion_allowed=bool(metrics.get("generator_promotion_allowed", False)),
    )


def load_finetuned_reranker(
    root_or_path: str | Path,
    *,
    device: str = "cpu",
) -> tuple[FineTunedArtifactManifest, PairScorer]:
    """Load the fine-tuned CrossEncoder from a V10/V11 output.

    The returned manifest is carried into diagnostics so a smoke model is
    visibly marked as an ablation.  Loading a reranker does not grant answer
    authority; callers must still bind the selected cell to V2/source data.
    """
    path = Path(root_or_path).expanduser()
    manifest = resolve_finetuned_artifacts(path)
    reranker_path = manifest.reranker_path
    if reranker_path is None:
        # Also accept passing the reranker directory directly.
        if (path / "config.json").exists() or (path / "config_sentence_transformers.json").exists():
            reranker_path = path
        else:
            raise FileNotFoundError(
                f"no reranker_finetuned directory found below {manifest.root}"
            )
    scorer = load_cross_encoder(str(reranker_path), device)
    return manifest, scorer


def rerank_review_item_candidates(
    *,
    item: Mapping[str, Any],
    assets_by_uid: Mapping[str, Mapping[str, Any]],
    scorer: PairScorer,
    limit: int = 40,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Apply a fine-tuned reranker to one review item.

    The returned item is a copy.  Candidate rows retain their original rank
    and are annotated as navigation-only; no evidence or answer fields are
    created by this function.
    """
    candidates = list(item.get("candidates") or [])
    ranked = rerank_table_candidates(
        query=str(item.get("question") or ""),
        candidates=candidates,
        assets_by_uid=assets_by_uid,
        limit=min(limit, max(1, len(candidates))),
        scorer=scorer,
    )
    updated = dict(item)
    if ranked:
        updated["candidates"] = ranked
    return updated, ranked


def rerank_review_items_candidates(
    *,
    items: Sequence[Mapping[str, Any]],
    assets_by_uid: Mapping[str, Mapping[str, Any]],
    scorer: PairScorer,
    limit: int = 40,
    batch_size: int = 64,
) -> dict[int, tuple[dict[str, Any], list[dict[str, Any]]]]:
    """Rerank many review items in one CrossEncoder call.

    Per-question model calls are needlessly expensive on Kaggle.  This batch
    variant keeps each item's candidate boundaries, but sends all pairs to the
    same model invocation so the GPU stays saturated.  It still returns only
    navigation metadata and never touches answer/evidence fields.
    """
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    prepared: list[tuple[int, dict[str, Any], list[tuple[int, Mapping[str, Any], Mapping[str, Any]]]]] = []
    pairs: list[list[str]] = []
    for raw_item in items:
        item = dict(raw_item)
        hydrated: list[tuple[int, Mapping[str, Any], Mapping[str, Any]]] = []
        for original_rank, candidate in enumerate(item.get("candidates") or [], start=1):
            uid = str(candidate.get("internal_table_uid") or "")
            asset = assets_by_uid.get(uid)
            if not uid or asset is None:
                continue
            hydrated.append((original_rank, candidate, asset))
        if not hydrated:
            continue
        # The review item is already a grounded shortlist.  Score only the
        # requested prefix: scoring all 40 rows and then keeping 12 wastes GPU
        # time without improving the navigation result used by the builder.
        hydrated = hydrated[:limit]
        prepared.append((int(item.get("id")), item, hydrated))
        query = str(item.get("question") or "")
        pairs.extend([[query, table_only_text(asset)] for _, _, asset in hydrated])
    if not pairs:
        return {}
    raw_scores = scorer.predict(
        pairs,
        show_progress_bar=False,
        batch_size=batch_size,
    )
    scores = [float(value) for value in raw_scores]
    if len(scores) != len(pairs):
        raise ValueError("reranker score count mismatch in batch")
    output: dict[int, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    offset = 0
    for item_id, item, hydrated in prepared:
        item_scores = scores[offset : offset + len(hydrated)]
        offset += len(hydrated)
        ranked = sorted(
            zip(hydrated, item_scores, strict=True),
            key=lambda value: (-value[1], value[0][0]),
        )[:limit]
        rows = [
            {
                **dict(candidate),
                "pre_rerank_rank": original_rank,
                "rank": rank,
                "reranker_score": score,
                "navigation_metadata_only": True,
                "may_authorize_answer": False,
                "submission_eligible": False,
            }
            for rank, ((original_rank, candidate, _), score) in enumerate(ranked, start=1)
        ]
        updated = dict(item)
        updated["candidates"] = rows
        output[item_id] = (updated, rows)
    return output


def load_cross_encoder(model_name_or_path: str, device: str) -> PairScorer:
    from sentence_transformers import CrossEncoder

    return CrossEncoder(model_name_or_path, device=device, max_length=512)


def rerank_table_candidates(
    *,
    query: str,
    candidates: Sequence[Mapping[str, Any]],
    assets_by_uid: Mapping[str, Mapping[str, Any]],
    limit: int,
    scorer: PairScorer,
) -> list[dict[str, Any]]:
    """Rerank an existing grounded shortlist and preserve navigation-only status."""
    if not query.strip():
        raise ValueError("query must not be empty")
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    hydrated: list[tuple[int, Mapping[str, Any], Mapping[str, Any]]] = []
    for original_rank, candidate in enumerate(candidates, start=1):
        uid = str(candidate.get("internal_table_uid") or "")
        asset = assets_by_uid.get(uid)
        if not uid or asset is None:
            continue
        hydrated.append((original_rank, candidate, asset))
    if not hydrated:
        return []
    pairs = [[query, table_only_text(asset)] for _, _, asset in hydrated]
    raw_scores = scorer.predict(pairs, show_progress_bar=False)
    scores = [float(value) for value in raw_scores]
    if len(scores) != len(hydrated):
        raise ValueError("reranker score count mismatch")
    ranked = sorted(
        zip(hydrated, scores, strict=True),
        key=lambda item: (-item[1], item[0][0]),
    )[:limit]
    return [
        {
            **dict(candidate),
            "pre_rerank_rank": original_rank,
            "rank": rank,
            "reranker_score": score,
            "navigation_metadata_only": True,
            "may_authorize_answer": False,
            "submission_eligible": False,
        }
        for rank, ((original_rank, candidate, _), score) in enumerate(ranked, start=1)
    ]
