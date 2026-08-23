from __future__ import annotations

import json
import re
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from typing import Callable, Iterable

import faiss
import numpy as np
from sentence_transformers import CrossEncoder, SentenceTransformer
from tqdm import tqdm

from .config import ModelConfig
from .corpus import iter_assets
from .hierarchical_retrieval import rank_hierarchy_candidates
from .schemas import QuestionPlan, RetrievedTable


TOKEN_RE = re.compile(r"[\w%]+", re.UNICODE)
_HARD_CONSTRAINT_BASES = frozenset({"EXPLICIT_QUERY", "SOURCE_DERIVED"})


def _hard_constraint(plan: QuestionPlan, field: str) -> bool:
    """Allow only source-backed planner fields to eliminate candidates."""
    provenance = plan.field_provenance.get(field)
    return provenance is not None and provenance.basis in _HARD_CONSTRAINT_BASES


def _fts_query(text: str) -> str:
    tokens = [token for token in TOKEN_RE.findall(text.casefold()) if len(token) > 1]
    if not tokens:
        return '"__empty_query__"'
    quoted = [f'"{token.replace(chr(34), "")}"' for token in tokens]
    return " OR ".join(quoted)


def _model_text(model_name: str, text: str, *, query: bool) -> str:
    if "e5" in model_name.casefold():
        return f"{'query' if query else 'passage'}: {text}"
    return text


class AssetStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def build(self, assets_path: Path) -> int:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        if self.database_path.exists():
            self.database_path.unlink()

        with self.connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=NORMAL;
                CREATE TABLE assets (
                    uid TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    ticker TEXT,
                    report_year INTEGER,
                    scope TEXT,
                    external_table_ref TEXT,
                    page_no INTEGER,
                    local_ordinal INTEGER,
                    unit_hint TEXT,
                    source_path TEXT,
                    headers_json TEXT NOT NULL,
                    rows_json TEXT NOT NULL,
                    context_before TEXT,
                    search_text TEXT NOT NULL,
                    structure_version INTEGER NOT NULL DEFAULT 1,
                    context_schema_version INTEGER NOT NULL DEFAULT 1,
                    header_row_indices_json TEXT NOT NULL DEFAULT '[]',
                    table_function_json TEXT NOT NULL DEFAULT '{}',
                    table_section_json TEXT NOT NULL DEFAULT '{}',
                    table_purpose_json TEXT NOT NULL DEFAULT '{}',
                    context_trace_json TEXT NOT NULL DEFAULT '{}',
                    structure_quality_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX idx_assets_ticker_year_scope
                    ON assets(ticker, report_year, scope);
                CREATE VIRTUAL TABLE assets_fts USING fts5(
                    uid UNINDEXED,
                    search_text,
                    tokenize='unicode61 remove_diacritics 2'
                );
                """
            )

            count = 0
            asset_rows: list[tuple] = []
            fts_rows: list[tuple] = []
            for asset in tqdm(iter_assets(assets_path), desc="Building lexical index"):
                uid = asset["internal_table_uid"]
                asset_rows.append(
                    (
                        uid,
                        asset["document_id"],
                        asset.get("ticker"),
                        asset.get("report_year"),
                        asset.get("scope"),
                        asset.get("external_table_ref"),
                        asset.get("page_no"),
                        asset.get("local_ordinal"),
                        asset.get("unit_hint"),
                        asset.get("source_path"),
                        json.dumps(asset.get("headers") or [], ensure_ascii=False),
                        json.dumps(asset.get("rows") or [], ensure_ascii=False),
                        asset.get("context_before", ""),
                        asset.get("search_text", ""),
                        int(asset.get("structure_version") or 1),
                        int(asset.get("context_schema_version") or 1),
                        json.dumps(asset.get("header_row_indices") or []),
                        json.dumps(asset.get("table_function") or {}, ensure_ascii=False),
                        json.dumps(asset.get("table_section") or {}, ensure_ascii=False),
                        json.dumps(asset.get("table_purpose") or {}, ensure_ascii=False),
                        json.dumps(asset.get("context_trace") or {}, ensure_ascii=False),
                        json.dumps(asset.get("structure_quality") or {}, ensure_ascii=False),
                    )
                )
                fts_rows.append((uid, asset.get("search_text", "")))
                count += 1

                if len(asset_rows) >= 2000:
                    connection.executemany(
                        "INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        asset_rows,
                    )
                    connection.executemany(
                        "INSERT INTO assets_fts(uid, search_text) VALUES (?, ?)",
                        fts_rows,
                    )
                    asset_rows.clear()
                    fts_rows.clear()

            if asset_rows:
                connection.executemany(
                    "INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    asset_rows,
                )
                connection.executemany(
                    "INSERT INTO assets_fts(uid, search_text) VALUES (?, ?)",
                    fts_rows,
                )
            connection.commit()
        return count

    def search_lexical(
        self,
        query: str,
        *,
        top_k: int,
        tickers: list[str] | None = None,
        years: list[int] | None = None,
        scope: str | None = None,
        allowed_uids: Iterable[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Run FTS only inside an optional source-derived UID allow-list.

        The allow-list is supplied by the metric stage router, never by an
        LLM.  SQLite permits a bounded number of bind variables, so a large
        allow-list is queried in chunks and fused deterministically.
        """
        permitted = list(dict.fromkeys(str(uid) for uid in allowed_uids or [] if str(uid)))
        if allowed_uids is not None and not permitted:
            return []

        def search_chunk(chunk: list[str] | None) -> list[tuple[str, float]]:
            conditions = ["assets_fts.search_text MATCH ?"]
            parameters: list[object] = [_fts_query(query)]

            if tickers:
                placeholders = ",".join("?" for _ in tickers)
                conditions.append(f"a.ticker IN ({placeholders})")
                parameters.extend(tickers)
            if years:
                placeholders = ",".join("?" for _ in years)
                conditions.append(f"a.report_year IN ({placeholders})")
                parameters.extend(years)
            if scope:
                conditions.append("a.scope = ?")
                parameters.append(scope)
            if chunk is not None:
                placeholders = ",".join("?" for _ in chunk)
                conditions.append(f"a.uid IN ({placeholders})")
                parameters.extend(chunk)

            parameters.append(top_k)
            sql = f"""
                SELECT assets_fts.uid AS uid, bm25(assets_fts) AS bm25_score
                FROM assets_fts
                JOIN assets AS a ON a.uid = assets_fts.uid
                WHERE {' AND '.join(conditions)}
                ORDER BY bm25_score ASC
                LIMIT ?
            """
            with self.connect() as connection:
                rows = connection.execute(sql, parameters).fetchall()
            return [(row["uid"], -float(row["bm25_score"])) for row in rows]

        if not permitted:
            return search_chunk(None)
        # Keep below SQLite's default 999-variable limit after static query
        # parameters are added. A stage candidate set is normally tiny.
        result: dict[str, float] = {}
        for start in range(0, len(permitted), 900):
            for uid, score in search_chunk(permitted[start : start + 900]):
                result[uid] = max(result.get(uid, float("-inf")), score)
        return sorted(result.items(), key=lambda item: (-item[1], item[0]))[:top_k]

    def get_assets(self, uids: Iterable[str]) -> dict[str, dict]:
        uid_list = list(dict.fromkeys(uids))
        if not uid_list:
            return {}
        output: dict[str, dict] = {}
        with self.connect() as connection:
            for start in range(0, len(uid_list), 500):
                batch = uid_list[start : start + 500]
                placeholders = ",".join("?" for _ in batch)
                rows = connection.execute(
                    f"SELECT * FROM assets WHERE uid IN ({placeholders})",
                    batch,
                ).fetchall()
                for row in rows:
                    output[row["uid"]] = dict(row)
        return output

    def get_asset(self, uid: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM assets WHERE uid = ?",
                (uid,),
            ).fetchone()
        return dict(row) if row is not None else None


class DenseIndex:
    def __init__(
        self,
        index_path: Path,
        uids_path: Path,
        model_name: str,
        device: str,
        max_sequence_length: int = 512,
    ) -> None:
        self.index_path = index_path
        self.uids_path = uids_path
        self.model_name = model_name
        self.device = device
        self.max_sequence_length = max_sequence_length
        self._model: SentenceTransformer | None = None
        self._index: faiss.Index | None = None
        self._uids: list[str] | None = None

    @property
    def model(self) -> SentenceTransformer:
        if self._model is None:
            self._model = SentenceTransformer(self.model_name, device=self.device)
            self._model.max_seq_length = self.max_sequence_length
        return self._model

    def build(
        self,
        assets_path: Path,
        batch_size: int = 32,
        encode_chunk_size: int = 4096,
        max_wall_seconds: float | None = None,
        progress_callback: Callable[[int, float], None] | None = None,
    ) -> int:
        """Build a dense index without paying model-startup overhead per mini-batch.

        ``SentenceTransformer.encode`` already micro-batches according to
        ``batch_size``.  Accumulating only one such micro-batch before each
        call made a full ViFinQA corpus issue thousands of encode calls.  A
        bounded outer chunk keeps memory predictable while letting the model
        process many micro-batches in one invocation.
        """
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if encode_chunk_size < batch_size:
            raise ValueError("encode_chunk_size must be at least batch_size")
        if max_wall_seconds is not None and max_wall_seconds <= 0:
            raise ValueError("max_wall_seconds must be positive when supplied")
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        uid_file = self.uids_path.open("w", encoding="utf-8")
        index: faiss.IndexFlatIP | None = None
        batch_texts: list[str] = []
        batch_uids: list[str] = []
        count = 0
        started_at = time.monotonic()

        def flush() -> None:
            nonlocal index, count
            if not batch_texts:
                return
            elapsed = time.monotonic() - started_at
            if max_wall_seconds is not None and elapsed >= max_wall_seconds:
                raise TimeoutError(
                    "Dense build time budget exhausted before a complete index "
                    f"was written ({elapsed:.1f}s >= {max_wall_seconds:.1f}s)."
                )
            embeddings = self.model.encode(
                batch_texts,
                batch_size=min(batch_size, len(batch_texts)),
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            ).astype("float32")
            if index is None:
                index = faiss.IndexFlatIP(embeddings.shape[1])
            index.add(embeddings)
            for uid in batch_uids:
                uid_file.write(json.dumps({"uid": uid}) + "\n")
            count += len(batch_uids)
            batch_texts.clear()
            batch_uids.clear()
            if progress_callback is not None:
                progress_callback(count, time.monotonic() - started_at)

        try:
            for asset in tqdm(iter_assets(assets_path), desc="Building dense index"):
                batch_uids.append(asset["internal_table_uid"])
                batch_texts.append(
                    _model_text(
                        self.model_name,
                        asset.get("search_text", ""),
                        query=False,
                    )
                )
                if len(batch_texts) >= encode_chunk_size:
                    flush()
            flush()
        finally:
            uid_file.close()

        if index is None:
            raise ValueError("No table assets were available for dense indexing.")
        faiss.write_index(index, str(self.index_path))
        self.index_path.with_suffix(self.index_path.suffix + ".meta.json").write_text(
            json.dumps(
                {
                    "model_name": self.model_name,
                    "count": count,
                    "dimension": index.d,
                    "normalized": True,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return count

    def load(self) -> None:
        if self._index is None:
            self._index = faiss.read_index(str(self.index_path))
        if self._uids is None:
            with self.uids_path.open(encoding="utf-8") as file:
                self._uids = [json.loads(line)["uid"] for line in file if line.strip()]
        if self._index.ntotal != len(self._uids):
            raise ValueError(
                f"Dense index/uids mismatch: {self._index.ntotal} != {len(self._uids)}"
            )

    def search(self, query: str, top_k: int) -> list[tuple[str, float]]:
        self.load()
        assert self._index is not None and self._uids is not None
        vector = self.model.encode(
            [_model_text(self.model_name, query, query=True)],
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        ).astype("float32")
        scores, positions = self._index.search(vector, top_k)
        output: list[tuple[str, float]] = []
        for score, position in zip(scores[0], positions[0], strict=True):
            if position < 0:
                continue
            output.append((self._uids[position], float(score)))
        return output


class HybridRetriever:
    def __init__(
        self,
        store: AssetStore,
        config: ModelConfig,
        dense_index: DenseIndex | None = None,
    ) -> None:
        self.store = store
        self.config = config
        self.dense_index = dense_index
        self._reranker: CrossEncoder | None = None

    @property
    def reranker(self) -> CrossEncoder | None:
        if self.config.reranker_model is None:
            return None
        if self._reranker is None:
            self._reranker = CrossEncoder(
                self.config.reranker_model,
                device=self.config.resolved_device(),
                max_length=self.config.max_sequence_length,
            )
        return self._reranker

    def retrieve(
        self,
        question: str,
        plan: QuestionPlan,
        *,
        allowed_uids: Iterable[str] | None = None,
        apply_plan_metadata_filters: bool = True,
    ) -> list[RetrievedTable]:
        """Retrieve normally or rank only a metric-router candidate allow-list.

        A routed candidate can expose a comparative period in its canonical
        V3 header even when the document's publication year differs. In that
        mode callers set ``apply_plan_metadata_filters=False`` because the
        stage router has already constrained company/year/scope from the
        source catalog. Exact V2/V3 binding remains mandatory downstream.
        """
        permitted = (
            set(str(uid) for uid in allowed_uids if str(uid))
            if allowed_uids is not None
            else None
        )
        hard_tickers = plan.tickers if _hard_constraint(plan, "tickers") else None
        hard_years = plan.years if _hard_constraint(plan, "years") else None
        hard_scope = plan.scope if _hard_constraint(plan, "scope") else None
        lexical = self.store.search_lexical(
            question,
            top_k=self.config.lexical_top_k,
            tickers=hard_tickers if apply_plan_metadata_filters else None,
            years=hard_years if apply_plan_metadata_filters else None,
            scope=hard_scope if apply_plan_metadata_filters else None,
            allowed_uids=permitted,
        )

        dense: list[tuple[str, float]] = []
        if self.dense_index is not None and self.dense_index.index_path.is_file():
            raw_dense = self.dense_index.search(
                question,
                max(self.config.dense_top_k * 10, self.config.dense_top_k),
            )
            raw_assets = self.store.get_assets(uid for uid, _ in raw_dense)
            for uid, score in raw_dense:
                asset = raw_assets.get(uid)
                if asset is None:
                    continue
                if permitted is not None and uid not in permitted:
                    continue
                if apply_plan_metadata_filters and hard_tickers and asset.get("ticker") not in hard_tickers:
                    continue
                if apply_plan_metadata_filters and hard_years and asset.get("report_year") not in hard_years:
                    continue
                if apply_plan_metadata_filters and hard_scope and asset.get("scope") != hard_scope:
                    continue
                dense.append((uid, score))
                if len(dense) >= self.config.dense_top_k:
                    break

        fused_scores: defaultdict[str, float] = defaultdict(float)
        lexical_ranks: dict[str, int] = {}
        dense_ranks: dict[str, int] = {}

        for rank, (uid, _) in enumerate(lexical, start=1):
            lexical_ranks[uid] = rank
            fused_scores[uid] += 1.0 / (self.config.rrf_k + rank)
        for rank, (uid, _) in enumerate(dense, start=1):
            dense_ranks[uid] = rank
            fused_scores[uid] += 1.0 / (self.config.rrf_k + rank)

        ranked_uids = [
            uid
            for uid, _ in sorted(
                fused_scores.items(),
                key=lambda item: item[1],
                reverse=True,
            )[: self.config.fused_top_k]
        ]
        assets = self.store.get_assets(ranked_uids)

        hierarchy_ranks: dict[str, int] = {}
        if self.config.hierarchy_rrf_enabled and assets:
            hierarchy = rank_hierarchy_candidates(question, assets)[
                : self.config.hierarchy_top_k
            ]
            for rank, (uid, _score) in enumerate(hierarchy, start=1):
                hierarchy_ranks[uid] = rank
                fused_scores[uid] += 1.0 / (self.config.rrf_k + rank)
            ranked_uids = sorted(
                ranked_uids,
                key=lambda uid: (-fused_scores[uid], uid),
            )[: self.config.fused_top_k]

        reranker_scores: dict[str, float] = {}
        if self.reranker is not None and ranked_uids:
            pair_uids = [uid for uid in ranked_uids if uid in assets]
            pairs = [
                (question, assets[uid]["search_text"][:5000])
                for uid in pair_uids
            ]
            scores = np.asarray(
                self.reranker.predict(pairs, show_progress_bar=False)
            ).reshape(-1)
            reranker_scores = {
                uid: float(score)
                for uid, score in zip(pair_uids, scores, strict=True)
            }
            ranked_uids = sorted(
                pair_uids,
                key=lambda uid: reranker_scores[uid],
                reverse=True,
            )[: self.config.rerank_top_k]

        output: list[RetrievedTable] = []
        for uid in ranked_uids:
            asset = assets.get(uid)
            if asset is None:
                continue
            output.append(
                RetrievedTable(
                    internal_table_uid=uid,
                    document_id=asset["document_id"],
                    ticker=asset.get("ticker") or "",
                    report_year=asset.get("report_year"),
                    scope=asset.get("scope") or "unknown",
                    lexical_rank=lexical_ranks.get(uid),
                    dense_rank=dense_ranks.get(uid),
                    hierarchy_rank=hierarchy_ranks.get(uid),
                    fused_score=fused_scores[uid],
                    reranker_score=reranker_scores.get(uid),
                    external_table_ref=asset.get("external_table_ref"),
                    preview=asset.get("search_text", "")[:500],
                )
            )
        return output
