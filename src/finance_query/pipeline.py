from __future__ import annotations

from pathlib import Path

from .binding import candidate_bindings
from .config import ModelConfig, ProjectPaths
from .questions import RuleQuestionPlanner
from .retrieval import AssetStore, DenseIndex, HybridRetriever
from .router_model import ModelBackedQuestionPlanner


class ViFinQARetrievalPipeline:
    """Runnable retrieval plus a non-answering legacy binding probe.

    The deterministic V2/V3 replay path is the only route that can progress to
    an answer certificate. This class can expose retrieval and old heuristic
    binding candidates for diagnostics, never a final numeric answer.
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
        self.planner_mode = "rule_text_only"
        self.planner_warning: str | None = None
        if router_dir is not None and router_dir.is_dir():
            try:
                self.planner = ModelBackedQuestionPlanner(
                    code_stock_path=self.paths.dataset_root / "code_stock.csv",
                    router_dir=router_dir,
                    device=self.config.resolved_device(),
                )
                self.planner_mode = "reviewed_embedding_router"
            except (FileNotFoundError, ValueError) as exc:
                self.planner = RuleQuestionPlanner(self.paths.dataset_root / "code_stock.csv")
                self.planner_warning = str(exc)
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
            "planner_mode": self.planner_mode,
            "planner_warning": self.planner_warning,
            "next_required_stage": "row_column_unit_binding",
        }

    def legacy_binding_probe(
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
                "status": "legacy_probe_requires_composed_reasoning",
                "answer": None,
                "answer_unit": None,
                "submission_eligible": False,
                "training_eligible": False,
                "provenance_promotion_allowed": False,
                "next_required_stage": "typed_plan_then_exact_v2_v3_binding",
            }

        bindings = candidate_bindings(self.store, plan, candidates)
        best = bindings[0] if bindings else None
        threshold_met = best is not None and best.binding_score >= minimum_binding_score

        return {
            "question_plan": plan.to_dict(),
            "retrieved_tables": [candidate.to_dict() for candidate in candidates],
            "binding_candidates": [binding.to_dict() for binding in bindings[:5]],
            "status": "legacy_candidate_probe",
            "planner_mode": self.planner_mode,
            "planner_warning": self.planner_warning,
            "legacy_score_threshold_met": threshold_met,
            "top_candidate": best.to_dict() if best is not None else None,
            "answer": None,
            "answer_unit": None,
            "submission_eligible": False,
            "training_eligible": False,
            "provenance_promotion_allowed": False,
            "next_required_stage": "exact_v2_v3_binding_then_independent_replay",
        }

    def answer_direct(
        self,
        question: str,
        question_id: int | None = None,
        *,
        minimum_binding_score: float = 0.48,
    ) -> dict:
        """Deprecated compatibility wrapper that never returns an answer."""
        return self.legacy_binding_probe(
            question,
            question_id,
            minimum_binding_score=minimum_binding_score,
        )


def load_config(path: str | Path | None) -> ModelConfig:
    if path is None:
        return ModelConfig()
    return ModelConfig.from_yaml(Path(path))
