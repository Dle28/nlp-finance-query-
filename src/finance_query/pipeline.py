from __future__ import annotations

from pathlib import Path

from .binding import candidate_bindings
from .config import ModelConfig, ProjectPaths
from .questions import RuleQuestionPlanner
from .retrieval import AssetStore, DenseIndex, HybridRetriever
from .router_model import ModelBackedQuestionPlanner


class ViFinQARetrievalPipeline:
    """Runnable retrieval and conservative direct-lookup baseline.

    Composed questions remain retrieval/planning outputs until their operands
    are explicitly grounded. Direct lookup may return an answer only when the
    row/column/value binder produces a sufficiently strong candidate.
    """

    def __init__(
        self,
        paths: ProjectPaths | None = None,
        config: ModelConfig | None = None,
        use_dense: bool = True,
    ) -> None:
        self.paths = paths or ProjectPaths.from_repository()
        self.config = config or ModelConfig()

        router_dir = self.config.resolved_router_dir(self.paths.repository_root)
        if router_dir is not None and router_dir.is_dir():
            self.planner = ModelBackedQuestionPlanner(
                code_stock_path=self.paths.dataset_root / "code_stock.csv",
                router_dir=router_dir,
                device=self.config.resolved_device(),
            )
        else:
            self.planner = RuleQuestionPlanner(self.paths.dataset_root / "code_stock.csv")

        self.store = AssetStore(self.paths.lexical_db_path)

        dense_index: DenseIndex | None = None
        if use_dense:
            dense_index_path, dense_uids_path = self.config.resolved_dense_paths(self.paths)
            dense_index = DenseIndex(
                index_path=dense_index_path,
                uids_path=dense_uids_path,
                model_name=self.config.embedding_model,
                device=self.config.resolved_device(),
                max_sequence_length=self.config.max_sequence_length,
            )

        self.retriever = HybridRetriever(
            store=self.store,
            config=self.config,
            dense_index=dense_index,
        )

    def retrieve(self, question: str, question_id: int | None = None) -> dict:
        plan = self.planner.plan(question, question_id)
        candidates = self.retriever.retrieve(question, plan)
        return {
            "question_plan": plan.to_dict(),
            "retrieved_tables": [candidate.to_dict() for candidate in candidates],
            "status": "retrieval_only",
            "next_required_stage": "row_column_unit_binding",
        }

    def answer_direct(
        self,
        question: str,
        question_id: int | None = None,
        *,
        minimum_binding_score: float = 0.48,
    ) -> dict:
        plan = self.planner.plan(question, question_id)
        candidates = self.retriever.retrieve(question, plan)

        if plan.family != "direct_lookup":
            return {
                "question_plan": plan.to_dict(),
                "retrieved_tables": [candidate.to_dict() for candidate in candidates],
                "status": "requires_composed_reasoning",
                "answer": None,
            }

        bindings = candidate_bindings(self.store, plan, candidates)
        best = bindings[0] if bindings else None
        accepted = best is not None and best.binding_score >= minimum_binding_score

        return {
            "question_plan": plan.to_dict(),
            "retrieved_tables": [candidate.to_dict() for candidate in candidates],
            "binding_candidates": [binding.to_dict() for binding in bindings[:5]],
            "status": "answered_direct_baseline" if accepted else "abstained_low_confidence",
            "answer": best.converted_value if accepted and best is not None else None,
            "answer_unit": plan.requested_unit if accepted else None,
            "selected_binding": best.to_dict() if accepted and best is not None else None,
        }


def load_config(path: str | Path | None) -> ModelConfig:
    if path is None:
        return ModelConfig()
    return ModelConfig.from_yaml(Path(path))
