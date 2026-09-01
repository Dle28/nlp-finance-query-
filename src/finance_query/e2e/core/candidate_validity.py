"""Small, provenance-safe candidate validity models for submission ranking.

This module deliberately sits between retrieval and evidence execution.  It
turns candidate metadata into numeric vectors and predicts how useful a
candidate is for a question, but it never reads a numeric answer and never
authorizes evidence.  Callers must still hydrate the selected coordinates from
the current V2 table and replay any arithmetic independently.

The implementation has no runtime dependency on scikit-learn.  A native
standardized logistic model can be trained from JSONL and loaded everywhere.
For compatibility with the existing 12-feature human calibrator, the loader
also accepts the legacy ``joblib`` payload when that optional dependency is
available.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import re
from collections.abc import Mapping, Sequence
from typing import Any


CANDIDATE_VALIDITY_SCHEMA_VERSION = 1
CANDIDATE_VALIDITY_PROTOCOL = "vifinqa_candidate_validity_model_v1"

# Keep the original calibrator's names first.  This lets the current primary
# pipeline consume the existing human-trained artifact without retraining it.
LEGACY_FEATURE_NAMES: tuple[str, ...] = (
    "rank_reciprocal",
    "lexical_reciprocal",
    "dense_reciprocal",
    "fused_score",
    "metadata_score",
    "row_score",
    "metric_overlap",
    "question_overlap",
    "numeric",
    "ticker_match",
    "scope_match",
    "year_match",
)

FEATURE_NAMES: tuple[str, ...] = LEGACY_FEATURE_NAMES + (
    "reranker_score",
    "review_score",
    "period_match",
    "table_hydrated",
    "provenance_complete",
    "numeric_cell_density",
    "candidate_filter_passed",
    "research_candidate",
    "model_candidate",
    "entity_coverage",
    "period_coverage",
    "same_table_support",
)

DIRECT_LOOKUP_TOP_K = 10
COMPLEX_TOP_K = 20
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_NUMERIC_RE = re.compile(r"^[\s()\-+\d.,%/]+$")


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _flag(value: Any) -> float:
    return 1.0 if bool(value) else 0.0


def _reciprocal(value: Any) -> float:
    try:
        rank = int(value)
    except (TypeError, ValueError):
        return 0.0
    return 1.0 / rank if rank > 0 else 0.0


def _candidate_ticker(candidate: Mapping[str, Any]) -> str:
    ticker = str(candidate.get("ticker") or "").strip().upper()
    if ticker:
        return ticker
    document_id = str(candidate.get("document_id") or "")
    return document_id.split("_financial_statements", 1)[0].strip().upper()


def _candidate_year(candidate: Mapping[str, Any]) -> int | None:
    value = candidate.get("report_year")
    try:
        if value is not None and str(value).strip():
            return int(value)
    except (TypeError, ValueError):
        pass
    matches = _YEAR_RE.findall(str(candidate.get("document_id") or ""))
    if not matches:
        return None
    return int(matches[-1])


def _numeric_cell_count(candidate: Mapping[str, Any], table: Mapping[str, Any] | None) -> int:
    """Count numeric-looking cells in the bounded candidate context.

    This is a shape feature only.  The cell contents are never returned from
    this function and are not used as an answer or as provenance.
    """

    windows = list(candidate.get("evidence_window") or [])
    if not windows and table is not None:
        rows = list(table.get("rows") or [])[:8]
        windows = [{"row": row} for row in rows]
    count = 0
    for window in windows:
        for cell in window.get("row") or []:
            text = str(cell).strip()
            if text and _NUMERIC_RE.fullmatch(text) and any(char.isdigit() for char in text):
                count += 1
    return count


def candidate_filter_passes(
    candidate: Mapping[str, Any], table: Mapping[str, Any] | None
) -> bool:
    """Apply the lightweight pre-ranking filter contract.

    This mirrors the primary builder's hard filter.  It is intentionally not
    a semantic verifier: a passing candidate still needs exact V2 replay.
    """

    if table is None or not table.get("rows"):
        return False
    if not str(candidate.get("internal_table_uid") or ""):
        return False
    if any(candidate.get(key) is False for key in ("ticker_match", "year_match", "scope_match")):
        return False
    status = str(
        candidate.get("candidate_status")
        or candidate.get("research_candidate_status")
        or ""
    ).upper()
    return status not in {"ABSTAIN", "FILTER_REJECTED", "QUARANTINED", "REJECTED"}


def _plan_context(item: Mapping[str, Any]) -> tuple[list[str], list[int], int]:
    plan = item.get("question_plan") or {}
    tickers = [str(value).upper() for value in plan.get("tickers") or [] if str(value).strip()]
    years: list[int] = []
    for value in plan.get("years") or []:
        try:
            years.append(int(value))
        except (TypeError, ValueError):
            continue
    operands = plan.get("operands") or []
    return list(dict.fromkeys(tickers)), list(dict.fromkeys(years)), len(operands)


def candidate_feature_map(
    item: Mapping[str, Any],
    candidate: Mapping[str, Any],
    table: Mapping[str, Any] | None = None,
    *,
    candidate_rank: int | None = None,
    same_table_support: float = 0.0,
) -> dict[str, float]:
    """Build the stable numeric feature map for one candidate.

    No feature contains the selected answer value.  Numeric features describe
    whether a coordinate/context is numeric and how much bounded context is
    available, which keeps the model useful for retrieval without leaking the
    target.
    """

    evidence = candidate.get("evidence_features") or {}
    ticker_matches, years, operand_count = _plan_context(item)
    candidate_ticker = _candidate_ticker(candidate)
    candidate_year = _candidate_year(candidate)
    table_hydrated = bool(table is not None and table.get("rows"))
    numeric_count = _numeric_cell_count(candidate, table)
    numeric = evidence.get("numeric")
    if numeric is None:
        numeric = numeric_count > 0
    provenance = table.get("source_provenance") if table else None
    cell_provenance = table.get("cell_provenance") if table else None
    provenance_complete = bool(
        str(candidate.get("internal_table_uid") or "")
        and str(candidate.get("document_id") or (table or {}).get("document_id") or "")
        and (provenance or cell_provenance)
    )
    filter_passed = candidate_filter_passes(candidate, table)

    entity_coverage = 0.0
    if ticker_matches:
        entity_coverage = _flag(candidate_ticker in ticker_matches)
    period_coverage = 0.0
    if years:
        period_coverage = _flag(candidate_year in years)

    features = {
        "rank_reciprocal": _reciprocal(candidate_rank if candidate_rank is not None else candidate.get("rank")),
        "lexical_reciprocal": _reciprocal(candidate.get("lexical_rank")),
        "dense_reciprocal": _reciprocal(candidate.get("dense_rank")),
        "fused_score": _finite(candidate.get("fused_score")),
        "metadata_score": _finite(candidate.get("metadata_score")),
        "row_score": _finite(evidence.get("row_score")),
        "metric_overlap": _finite(evidence.get("metric_overlap")),
        "question_overlap": _finite(evidence.get("question_overlap")),
        "numeric": _flag(numeric),
        "ticker_match": _flag(candidate.get("ticker_match")),
        "scope_match": _flag(candidate.get("scope_match")),
        "year_match": _flag(candidate.get("year_match")),
        "reranker_score": _finite(candidate.get("reranker_score")),
        "review_score": _finite(candidate.get("review_score")),
        "period_match": _finite(evidence.get("period_match", candidate.get("period_match"))),
        "table_hydrated": _flag(table_hydrated),
        "provenance_complete": _flag(provenance_complete),
        "numeric_cell_density": min(1.0, numeric_count / 8.0),
        "candidate_filter_passed": _flag(filter_passed),
        "research_candidate": _flag(
            candidate.get("research_candidate_only")
            or str(candidate.get("candidate_source") or "").startswith("research")
            or str(candidate.get("retrieval_source") or "").startswith("research")
        ),
        "model_candidate": _flag(
            candidate.get("model_candidate_only")
            or candidate.get("model_answer_candidate")
            or str(candidate.get("candidate_source") or "").startswith("model")
        ),
        "entity_coverage": entity_coverage,
        "period_coverage": period_coverage,
        "same_table_support": max(0.0, min(1.0, _finite(same_table_support))),
    }
    return {name: _finite(features.get(name)) for name in FEATURE_NAMES}


def feature_vector(
    item: Mapping[str, Any],
    candidate: Mapping[str, Any],
    table: Mapping[str, Any] | None = None,
    *,
    feature_names: Sequence[str] = FEATURE_NAMES,
    candidate_rank: int | None = None,
    same_table_support: float = 0.0,
) -> list[float]:
    features = candidate_feature_map(
        item,
        candidate,
        table,
        candidate_rank=candidate_rank,
        same_table_support=same_table_support,
    )
    unknown = [name for name in feature_names if name not in features]
    if unknown:
        raise ValueError(f"Unknown candidate validity features: {unknown}")
    return [features[name] for name in feature_names]


def _sigmoid(value: float) -> float:
    if value >= 35.0:
        return 1.0
    if value <= -35.0:
        return 0.0
    return 1.0 / (1.0 + math.exp(-value))


def _as_vector(values: Mapping[str, Any] | Sequence[Any], names: Sequence[str]) -> list[float]:
    if isinstance(values, Mapping):
        return [_finite(values.get(name)) for name in names]
    vector = [_finite(value) for value in values]
    if len(vector) != len(names):
        raise ValueError(f"Feature vector has {len(vector)} values; expected {len(names)}")
    return vector


@dataclass(frozen=True)
class CandidateValidityModel:
    """A deterministic standardized logistic classifier."""

    feature_names: tuple[str, ...]
    weights: tuple[float, ...]
    bias: float
    means: tuple[float, ...] = field(default_factory=tuple)
    scales: tuple[float, ...] = field(default_factory=tuple)
    schema_version: int = CANDIDATE_VALIDITY_SCHEMA_VERSION
    model_id: str = CANDIDATE_VALIDITY_PROTOCOL

    def __post_init__(self) -> None:
        if not self.feature_names:
            raise ValueError("candidate validity model needs feature names")
        size = len(self.feature_names)
        if len(self.weights) != size:
            raise ValueError("candidate validity weights do not match feature names")
        means = self.means or tuple(0.0 for _ in range(size))
        scales = self.scales or tuple(1.0 for _ in range(size))
        if len(means) != size or len(scales) != size:
            raise ValueError("candidate validity normalization does not match features")
        if any(not math.isfinite(float(value)) for value in (*self.weights, self.bias, *means, *scales)):
            raise ValueError("candidate validity model contains a non-finite number")
        if any(float(value) <= 0.0 for value in scales):
            raise ValueError("candidate validity scales must be positive")
        object.__setattr__(self, "means", tuple(float(value) for value in means))
        object.__setattr__(self, "scales", tuple(float(value) for value in scales))

    def predict_probability(self, values: Mapping[str, Any] | Sequence[Any]) -> float:
        vector = _as_vector(values, self.feature_names)
        score = float(self.bias)
        for weight, value, mean, scale in zip(self.weights, vector, self.means, self.scales, strict=True):
            score += float(weight) * ((value - mean) / scale)
        return _sigmoid(score)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "model_id": self.model_id,
            "feature_names": list(self.feature_names),
            "weights": list(self.weights),
            "bias": float(self.bias),
            "means": list(self.means),
            "scales": list(self.scales),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CandidateValidityModel":
        names = tuple(str(value) for value in payload.get("feature_names") or [])
        return cls(
            feature_names=names,
            weights=tuple(float(value) for value in payload.get("weights") or []),
            bias=float(payload.get("bias") or 0.0),
            means=tuple(float(value) for value in payload.get("means") or []),
            scales=tuple(float(value) for value in payload.get("scales") or []),
            schema_version=int(payload.get("schema_version") or CANDIDATE_VALIDITY_SCHEMA_VERSION),
            model_id=str(payload.get("model_id") or CANDIDATE_VALIDITY_PROTOCOL),
        )

    @classmethod
    def fit(
        cls,
        feature_rows: Sequence[Mapping[str, Any] | Sequence[Any]],
        labels: Sequence[int],
        *,
        feature_names: Sequence[str] = FEATURE_NAMES,
        sample_weights: Sequence[float] | None = None,
        epochs: int = 500,
        learning_rate: float = 0.12,
        l2: float = 0.01,
    ) -> "CandidateValidityModel":
        if len(feature_rows) != len(labels) or not feature_rows:
            raise ValueError("candidate validity training rows and labels must be non-empty and aligned")
        if epochs < 1 or learning_rate <= 0.0 or l2 < 0.0:
            raise ValueError("invalid candidate validity training parameters")
        names = tuple(str(name) for name in feature_names)
        x = [_as_vector(row, names) for row in feature_rows]
        y = []
        for value in labels:
            integer = int(value)
            if integer not in (0, 1):
                raise ValueError("candidate validity labels must be 0 or 1")
            y.append(integer)
        if len(set(y)) < 2:
            raise ValueError("candidate validity training needs both classes")
        weights = [1.0 for _ in y] if sample_weights is None else [_finite(value, -1.0) for value in sample_weights]
        if len(weights) != len(y) or any(value <= 0.0 for value in weights):
            raise ValueError("candidate validity sample weights must be positive and aligned")

        count = float(len(x))
        means = [sum(row[index] for row in x) / count for index in range(len(names))]
        scales = []
        for index, mean in enumerate(means):
            variance = sum((row[index] - mean) ** 2 for row in x) / count
            scales.append(math.sqrt(variance) if variance > 1e-12 else 1.0)
        z = [[(value - mean) / scale for value, mean, scale in zip(row, means, scales, strict=True)] for row in x]

        class_counts = {0: max(1, y.count(0)), 1: max(1, y.count(1))}
        class_balance = {key: count / (2.0 * value) for key, value in class_counts.items()}
        effective_weights = [weight * class_balance[label] for weight, label in zip(weights, y, strict=True)]
        normalizer = sum(effective_weights)
        fitted_weights = [0.0 for _ in names]
        fitted_bias = 0.0
        for _ in range(epochs):
            gradient = [0.0 for _ in names]
            bias_gradient = 0.0
            for row, label, sample_weight in zip(z, y, effective_weights, strict=True):
                probability = _sigmoid(
                    fitted_bias + sum(weight * value for weight, value in zip(fitted_weights, row, strict=True))
                )
                error = (probability - label) * sample_weight
                bias_gradient += error
                for index, value in enumerate(row):
                    gradient[index] += error * value
            for index, weight in enumerate(fitted_weights):
                gradient[index] = gradient[index] / normalizer + l2 * weight
                fitted_weights[index] -= learning_rate * gradient[index]
            fitted_bias -= learning_rate * bias_gradient / normalizer

        return cls(
            feature_names=names,
            weights=tuple(fitted_weights),
            bias=fitted_bias,
            means=tuple(means),
            scales=tuple(scales),
        )


@dataclass(frozen=True)
class LoadedCandidateValidityModel:
    """Uniform scorer wrapper for native and legacy model artifacts."""

    feature_names: tuple[str, ...]
    kind: str
    model: Any
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def predict_probability(self, features: Mapping[str, Any]) -> float:
        vector = _as_vector(features, self.feature_names)
        if self.kind == "native_logistic":
            probability = self.model.predict_probability(vector)
        else:
            raw = self.model.predict_proba([vector])
            probability = raw[0][1] if hasattr(raw[0], "__getitem__") else raw[0]
        return max(0.0, min(1.0, _finite(probability)))


def load_candidate_validity_model(path: str | Path) -> LoadedCandidateValidityModel:
    """Load a native JSON model or the existing legacy human calibrator."""

    model_path = Path(path).expanduser().resolve()
    if not model_path.is_file():
        raise FileNotFoundError(f"candidate validity model does not exist: {model_path}")
    if model_path.suffix.lower() == ".json":
        payload = json.loads(model_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("candidate validity JSON model must be an object")
        raw_model = payload.get("model") if isinstance(payload.get("model"), dict) else payload
        model = CandidateValidityModel.from_dict(raw_model)
        return LoadedCandidateValidityModel(
            feature_names=model.feature_names,
            kind="native_logistic",
            model=model,
            metadata={key: value for key, value in payload.items() if key != "model"},
        )

    if model_path.suffix.lower() != ".joblib":
        raise ValueError("candidate validity model must be .json or .joblib")
    try:
        import joblib
    except ImportError as exc:  # pragma: no cover - depends on deployment extras
        raise RuntimeError("loading a legacy .joblib model requires joblib") from exc
    payload = joblib.load(model_path)
    if isinstance(payload, dict):
        model = payload.get("model")
        feature_names = tuple(str(value) for value in payload.get("feature_names") or LEGACY_FEATURE_NAMES)
        metadata = {key: value for key, value in payload.items() if key != "model"}
    else:
        model = payload
        feature_names = LEGACY_FEATURE_NAMES
        metadata = {}
    if model is None or not hasattr(model, "predict_proba"):
        raise ValueError("legacy candidate validity artifact has no predict_proba model")
    # The legacy calibrator was serialized with a newer scikit-learn.  Older
    # Kaggle images can unpickle the estimator but miss the LogisticRegression
    # ``multi_class`` attribute that their ``predict_proba`` implementation
    # still reads.  Restore the binary-safe default without changing weights.
    for _, estimator in getattr(model, "steps", ()):
        if estimator.__class__.__name__ == "LogisticRegression" and not hasattr(
            estimator, "multi_class"
        ):
            estimator.multi_class = "auto"
    return LoadedCandidateValidityModel(
        feature_names=feature_names,
        kind="legacy_sklearn",
        model=model,
        metadata=metadata,
    )


def save_candidate_validity_model(
    path: str | Path,
    model: CandidateValidityModel,
    *,
    training: Mapping[str, Any] | None = None,
) -> None:
    output = Path(path).expanduser()
    if output.suffix.lower() != ".json":
        raise ValueError("native candidate validity models must be written as .json")
    payload = {
        "schema_version": CANDIDATE_VALIDITY_SCHEMA_VERSION,
        "protocol": CANDIDATE_VALIDITY_PROTOCOL,
        "model_kind": "standardized_logistic_regression",
        "model": model.to_dict(),
        "training": dict(training or {}),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def candidate_limit_for_item(item: Mapping[str, Any], override: int | None = None) -> int:
    if override is not None:
        if int(override) < 1 or int(override) > 100:
            raise ValueError("candidate top-k override must be between 1 and 100")
        return int(override)
    plan = item.get("question_plan") or {}
    family = str(plan.get("family") or item.get("weak_family") or "")
    operands = plan.get("operands") or []
    return DIRECT_LOOKUP_TOP_K if family == "direct_lookup" and len(operands) <= 1 else COMPLEX_TOP_K


def _heuristic_probability(features: Mapping[str, Any]) -> float:
    """Deterministic prior used when no trained artifact is available."""

    score = -1.1
    score += 1.15 * _finite(features.get("metadata_score"))
    score += 1.25 * _finite(features.get("row_score"))
    score += 0.75 * _finite(features.get("metric_overlap"))
    score += 0.55 * _finite(features.get("period_match"))
    score += 0.45 * _finite(features.get("numeric"))
    score += 0.35 * _finite(features.get("candidate_filter_passed"))
    score += 0.25 * _finite(features.get("provenance_complete"))
    rank_quality = min(1.0, _finite(features.get("rank_reciprocal")) * 5.0)
    score -= 0.45 * (1.0 - rank_quality)
    return _sigmoid(score)


def _group_candidates(
    item: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]]
) -> list[tuple[str, list[Mapping[str, Any]]]]:
    plan = item.get("question_plan") or {}
    operands = list(plan.get("operands") or [])
    tickers, years, _ = _plan_context(item)
    groups: list[tuple[str, list[Mapping[str, Any]]]] = []
    if operands:
        for index, operand in enumerate(operands):
            operand_id = str(operand.get("operand_id") or f"x{index}")
            ticker = str(operand.get("ticker") or "").upper()
            if not ticker and len(tickers) == 1:
                ticker = tickers[0]
            period = operand.get("period")
            try:
                period_value = int(period) if period is not None else None
            except (TypeError, ValueError):
                period_value = None
            matches = [
                candidate
                for candidate in candidates
                if (not ticker or _candidate_ticker(candidate) == ticker)
                and (period_value is None or _candidate_year(candidate) == period_value)
            ]
            groups.append((f"operand:{operand_id}", matches))
        return groups
    if len(tickers) > 1:
        for ticker in tickers:
            groups.append(
                (
                    f"entity:{ticker}",
                    [candidate for candidate in candidates if _candidate_ticker(candidate) == ticker],
                )
            )
        return groups
    if years and len(years) > 1:
        for year in years:
            groups.append(
                (
                    f"period:{year}",
                    [candidate for candidate in candidates if _candidate_year(candidate) == year],
                )
            )
        return groups
    return [("question", list(candidates))]


def summarize_candidate_plan(
    item: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    *,
    selected_candidates: Sequence[Mapping[str, Any]] | None = None,
    top_k: int,
) -> dict[str, Any]:
    """Describe operand/entity coverage without making a semantic decision."""

    plan = item.get("question_plan") or {}
    family = str(plan.get("family") or item.get("weak_family") or "unknown")
    operation = str((plan.get("operation_ast") or {}).get("op") or "")
    operands = list(plan.get("operands") or [])
    tickers, years, _ = _plan_context(item)
    complex_plan = bool(
        len(operands) > 1
        or len(tickers) > 1
        or operation
        in {
            "mean",
            "sum",
            "min",
            "max",
            "count",
            "subtract",
            "divide",
            "percentage_change",
            "plan_required",
        }
    )
    if not complex_plan:
        shape = "single_cell_lookup"
    elif operands:
        shape = "multi_operand_plan"
    elif len(tickers) > 1:
        shape = "multi_entity_plan"
    elif operation in {"mean", "sum", "min", "max", "count"}:
        shape = "aggregation_plan_needs_operands"
    else:
        shape = "decomposition_required"

    groups = _group_candidates(item, candidates)
    selected = list(selected_candidates or [])
    selected_by_uid = {
        str(candidate.get("internal_table_uid") or "") for candidate in selected
    }
    group_rows: list[dict[str, Any]] = []
    group_uids: list[set[str]] = []
    top_probabilities: list[float] = []
    for group_key, group in groups:
        ranked = sorted(
            group,
            key=lambda candidate: (
                not bool(candidate.get("candidate_filter_passed")),
                -_finite(candidate.get("validity_probability")),
                int(candidate.get("rank") or 10**9),
            ),
        )
        top = ranked[:top_k]
        top_probabilities.append(_finite(top[0].get("validity_probability")) if top else 0.0)
        uids = {str(candidate.get("internal_table_uid") or "") for candidate in top if candidate.get("internal_table_uid")}
        group_uids.append(uids)
        covered = bool(uids & selected_by_uid) if selected else bool(uids)
        group_rows.append(
            {
                "group": group_key,
                "candidate_count": len(group),
                "surviving_candidate_count": sum(bool(candidate.get("candidate_filter_passed")) for candidate in group),
                "covered": covered,
                "top_candidates": [
                    {
                        "internal_table_uid": candidate.get("internal_table_uid"),
                        "rank": candidate.get("rank"),
                        "validity_probability": _finite(candidate.get("validity_probability")),
                    }
                    for candidate in top[:3]
                ],
            }
        )

    required = len(groups) if groups and groups[0][0] != "question" else 1
    covered_count = sum(1 for row in group_rows if row["covered"])
    coverage = covered_count / max(1, required)
    mean_probability = sum(top_probabilities) / max(1, len(top_probabilities))
    min_probability = min(top_probabilities) if top_probabilities else 0.0
    same_table_support = 0.0
    if len(group_uids) > 1:
        all_uids = set().union(*group_uids)
        same_table_support = max(
            (sum(1 for group in group_uids if uid in group) / len(group_uids))
            for uid in all_uids
            if uid
        ) if all_uids else 0.0
    plan_score = coverage * (0.65 * mean_probability + 0.35 * min_probability)
    status = "CANDIDATE_PLAN_COVERED" if coverage >= 1.0 else "PARTIAL_CANDIDATE_PLAN"
    # A group having at least one table is retrieval coverage, not proof that
    # the metric/column/condition has been decomposed.  Keep that distinction
    # visible for multi-entity questions whose source plan still has no typed
    # operands.
    if not operands and shape in {"multi_entity_plan", "aggregation_plan_needs_operands"}:
        status = "CANDIDATE_GROUPS_COVERED" if coverage >= 1.0 else "PARTIAL_CANDIDATE_GROUPS"
    if shape == "decomposition_required":
        status = "DECOMPOSITION_REQUIRED"
    elif not candidates:
        status = "NO_CANDIDATES"

    return {
        "schema_version": CANDIDATE_VALIDITY_SCHEMA_VERSION,
        "protocol": CANDIDATE_VALIDITY_PROTOCOL,
        "family": family,
        "operation": operation,
        "plan_shape": shape,
        "plan_status": status,
        "tickers": tickers,
        "years": years,
        "operand_count": len(operands),
        "required_group_count": required,
        "covered_group_count": covered_count,
        "coverage": coverage,
        "candidate_plan_score": plan_score,
        "same_table_support": same_table_support,
        "plan_vector_names": [
            "operand_count",
            "entity_count",
            "period_count",
            "coverage",
            "mean_top_validity_probability",
            "min_top_validity_probability",
            "same_table_support",
        ],
        "plan_vector": [
            float(len(operands)),
            float(len(tickers)),
            float(len(years)),
            coverage,
            mean_probability,
            min_probability,
            same_table_support,
        ],
        "groups": group_rows,
    }


def rank_candidate_pool(
    item: Mapping[str, Any],
    *,
    tables_by_uid: Mapping[str, Mapping[str, Any]],
    model: LoadedCandidateValidityModel | None = None,
    top_k: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Score, vectorize, and keep the best 10/20 candidate set.

    For a multi-entity or multi-operand question, ``top_k`` is retained per
    entity/operand group so one strong company cannot crowd every other
    required company out of the plan.  The result remains navigation metadata;
    the primary builder still performs its hard filter and V2 replay.
    """

    raw_candidates = list(item.get("candidates") or [])
    limit = candidate_limit_for_item(item, top_k)
    annotated: list[dict[str, Any]] = []
    model_status = "trained_model" if model is not None else "heuristic_prior"
    for position, raw_candidate in enumerate(raw_candidates, start=1):
        candidate = dict(raw_candidate)
        table = tables_by_uid.get(str(candidate.get("internal_table_uid") or ""))
        features = candidate_feature_map(item, candidate, table, candidate_rank=position)
        probability = (
            model.predict_probability(features)
            if model is not None
            else _heuristic_probability(features)
        )
        candidate["candidate_filter_passed"] = candidate_filter_passes(candidate, table)
        candidate["validity_probability"] = probability
        candidate["validity_model_status"] = model_status
        candidate["validity_vector"] = [features[name] for name in FEATURE_NAMES]
        candidate["validity_feature_names"] = list(FEATURE_NAMES)
        candidate["validity_original_rank"] = position
        annotated.append(candidate)

    global_ranked = sorted(
        annotated,
        key=lambda candidate: (
            not bool(candidate.get("candidate_filter_passed")),
            -_finite(candidate.get("validity_probability")),
            int(candidate.get("rank") or 10**9),
            str(candidate.get("internal_table_uid") or ""),
        ),
    )
    groups = _group_candidates(item, global_ranked)
    grouped_selection = bool(len(groups) > 1 or (groups and groups[0][0] != "question"))
    selected_ids: set[str] = set()
    if grouped_selection:
        for _, group in groups:
            for candidate in group[:limit]:
                if candidate.get("candidate_filter_passed"):
                    selected_ids.add(str(candidate.get("internal_table_uid") or ""))
        if not selected_ids:
            for candidate in global_ranked[:limit]:
                selected_ids.add(str(candidate.get("internal_table_uid") or ""))
        selected = [
            candidate for candidate in global_ranked
            if str(candidate.get("internal_table_uid") or "") in selected_ids
        ]
    else:
        survivors = [candidate for candidate in global_ranked if candidate.get("candidate_filter_passed")]
        selected = (survivors or global_ranked)[:limit]
    selected = selected[: max(limit, 1) * max(1, len(groups) if grouped_selection else 1)]
    for validity_rank, candidate in enumerate(selected, start=1):
        candidate["validity_rank"] = validity_rank

    summary = summarize_candidate_plan(
        item,
        global_ranked,
        selected_candidates=selected,
        top_k=limit,
    )
    updated = dict(item)
    updated["candidates"] = selected
    updated["candidate_validity"] = {
        "schema_version": CANDIDATE_VALIDITY_SCHEMA_VERSION,
        "protocol": CANDIDATE_VALIDITY_PROTOCOL,
        "model_status": model_status,
        "top_k": limit,
        "selection_scope": "per_operand_or_entity_group" if grouped_selection else "question_pool",
        "candidate_pool_before": len(raw_candidates),
        "candidate_pool_after": len(selected),
        "vectors_emitted": True,
    }
    updated["candidate_plan_summary"] = summary
    return updated, summary


__all__ = [
    "CANDIDATE_VALIDITY_PROTOCOL",
    "CANDIDATE_VALIDITY_SCHEMA_VERSION",
    "COMPLEX_TOP_K",
    "DIRECT_LOOKUP_TOP_K",
    "FEATURE_NAMES",
    "LEGACY_FEATURE_NAMES",
    "CandidateValidityModel",
    "LoadedCandidateValidityModel",
    "candidate_filter_passes",
    "candidate_feature_map",
    "candidate_limit_for_item",
    "feature_vector",
    "load_candidate_validity_model",
    "rank_candidate_pool",
    "save_candidate_validity_model",
    "summarize_candidate_plan",
]
