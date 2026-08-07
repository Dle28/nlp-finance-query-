from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


def _dataset_complete(path: Path) -> bool:
    return (
        (path / "questions" / "questions.jsonl").is_file()
        and (path / "financial_statements").is_dir()
    )


@dataclass(frozen=True, slots=True)
class ProjectPaths:
    repository_root: Path
    dataset_root: Path
    questions_path: Path
    reports_root: Path
    artifacts_root: Path
    table_assets_path: Path
    lexical_db_path: Path
    dense_index_path: Path
    dense_uids_path: Path

    @classmethod
    def from_repository(cls, repository_root: Path | None = None) -> "ProjectPaths":
        root = (
            repository_root.resolve()
            if repository_root is not None
            else Path(__file__).resolve().parents[2]
        )
        canonical = root / "data" / "ViFinQA"
        legacy = root / "data" / "data" / "ViFinQA"
        if _dataset_complete(canonical):
            dataset = canonical
        elif _dataset_complete(legacy):
            dataset = legacy
        else:
            dataset = canonical

        artifacts = root / "artifacts"
        return cls(
            repository_root=root,
            dataset_root=dataset,
            questions_path=dataset / "questions" / "questions.jsonl",
            reports_root=dataset / "financial_statements",
            artifacts_root=artifacts,
            table_assets_path=artifacts / "table_assets.jsonl",
            lexical_db_path=artifacts / "lexical_index.sqlite3",
            dense_index_path=artifacts / "dense.index",
            dense_uids_path=artifacts / "dense_uids.jsonl",
        )


@dataclass(slots=True)
class ModelConfig:
    embedding_model: str = "intfloat/multilingual-e5-small"
    reranker_model: str | None = None
    router_model_dir: str | None = None
    dense_index_path: str | None = None
    dense_uids_path: str | None = None
    embedding_batch_size: int = 32
    max_sequence_length: int = 512
    lexical_top_k: int = 100
    dense_top_k: int = 100
    fused_top_k: int = 50
    rerank_top_k: int = 20
    rrf_k: int = 60
    device: str = "auto"

    @classmethod
    def from_yaml(cls, path: Path) -> "ModelConfig":
        with path.open(encoding="utf-8") as file:
            payload = yaml.safe_load(file) or {}
        return cls(**payload.get("model", payload))

    def resolved_device(self) -> str:
        if self.device != "auto":
            return self.device
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda"
            if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                return "mps"
        except ImportError:
            pass
        return "cpu"

    def resolved_router_dir(self, repository_root: Path) -> Path | None:
        if self.router_model_dir is None:
            return None
        path = Path(self.router_model_dir)
        return path if path.is_absolute() else repository_root / path

    def resolved_dense_paths(self, paths: ProjectPaths) -> tuple[Path, Path]:
        index_path = (
            Path(self.dense_index_path)
            if self.dense_index_path is not None
            else paths.dense_index_path
        )
        uids_path = (
            Path(self.dense_uids_path)
            if self.dense_uids_path is not None
            else paths.dense_uids_path
        )
        if not index_path.is_absolute():
            index_path = paths.repository_root / index_path
        if not uids_path.is_absolute():
            uids_path = paths.repository_root / uids_path
        return index_path, uids_path
