from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
from sentence_transformers import SentenceTransformer

from .questions import (
    RuleQuestionPlanner,
    infer_operation_ast,
    metric_hint,
    reported_value_lookup_reason,
)
from .schemas import OperandSpec, QuestionFamily, QuestionPlan


class EmbeddingQuestionRouter:
    """Embedding encoder plus a lightweight probabilistic classifier."""

    def __init__(self, model_dir: Path, device: str | None = None) -> None:
        metadata_path = model_dir / "metadata.json"
        classifier_path = model_dir / "classifier.joblib"
        if not metadata_path.is_file() or not classifier_path.is_file():
            raise FileNotFoundError(
                f"Router bundle must contain metadata.json and classifier.joblib: {model_dir}"
            )
        self.metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        self.classifier = joblib.load(classifier_path)
        self.encoder_model = str(self.metadata["encoder_model"])
        self.encoder = SentenceTransformer(self.encoder_model, device=device)

    def predict(self, question: str) -> tuple[QuestionFamily, float]:
        text = f"query: {question}" if "e5" in self.encoder_model.casefold() else question
        embedding = self.encoder.encode(
            [text],
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        ).astype("float32")
        probabilities = self.classifier.predict_proba(embedding)[0]
        index = int(np.argmax(probabilities))
        family = str(self.classifier.classes_[index])
        return family, float(probabilities[index])  # type: ignore[return-value]


class ModelBackedQuestionPlanner:
    """Use the trained router while retaining deterministic metadata parsing."""

    def __init__(
        self,
        code_stock_path: Path,
        router_dir: Path,
        device: str | None = None,
    ) -> None:
        self.rule_planner = RuleQuestionPlanner(code_stock_path)
        self.router = EmbeddingQuestionRouter(router_dir, device=device)

    def plan(self, question: str, question_id: int | None = None) -> QuestionPlan:
        plan = self.rule_planner.plan(question, question_id)
        reported_lookup_reason = reported_value_lookup_reason(question)
        if reported_lookup_reason:
            # A trained coarse family router may reasonably confuse these row
            # labels with arithmetic wording.  Preserve the high-precision
            # lexical override and let subsequent source/cell validation
            # decide whether a direct answer is actually admissible.
            family, confidence = "direct_lookup", 0.98
        else:
            family, confidence = self.router.predict(question)
        plan.family = family
        plan.family_confidence = confidence
        plan.operation_ast = infer_operation_ast(family, question)
        plan.operands = []

        hint = metric_hint(question)
        if family == "direct_lookup":
            plan.operands.append(
                OperandSpec(
                    operand_id="x0",
                    metric=hint,
                    ticker=plan.tickers[0] if len(plan.tickers) == 1 else None,
                    period=plan.years[0] if len(plan.years) == 1 else None,
                    scope=plan.scope,
                )
            )
        elif family == "temporal_change" and len(plan.years) >= 2:
            plan.operands.extend(
                [
                    OperandSpec(
                        operand_id="x_old",
                        metric=hint,
                        period=plan.years[0],
                        scope=plan.scope,
                    ),
                    OperandSpec(
                        operand_id="x_new",
                        metric=hint,
                        period=plan.years[-1],
                        scope=plan.scope,
                    ),
                ]
            )
        else:
            warning = "Model router selected a composed family; semantic operand decomposition is required."
            if warning not in plan.warnings:
                plan.warnings.append(warning)

        if reported_lookup_reason:
            warning = (
                "Model router was constrained to direct_lookup for one disclosed report row: "
                + reported_lookup_reason
            )
            if warning not in plan.warnings:
                plan.warnings.append(warning)

        return plan
