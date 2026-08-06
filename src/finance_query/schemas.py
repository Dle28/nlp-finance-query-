from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


QuestionFamily = Literal[
    "direct_lookup",
    "conditional_analytical",
    "temporal_change",
    "ratio_or_derived",
    "cross_entity_comparison",
    "multi_entity_or_period_aggregation",
    "unknown",
]


@dataclass(slots=True)
class OperandSpec:
    operand_id: str
    metric: str = ""
    entity: str | None = None
    ticker: str | None = None
    period: int | None = None
    scope: str | None = None
    qualifiers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class QuestionPlan:
    question_id: int | None
    original_question: str
    family: QuestionFamily
    family_confidence: float
    tickers: list[str] = field(default_factory=list)
    years: list[int] = field(default_factory=list)
    scope: str | None = None
    requested_unit: str | None = None
    operands: list[OperandSpec] = field(default_factory=list)
    operation_ast: dict[str, Any] = field(default_factory=dict)
    filters: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["operands"] = [operand.to_dict() for operand in self.operands]
        return payload


@dataclass(slots=True)
class TableAsset:
    internal_table_uid: str
    document_id: str
    ticker: str
    report_year: int | None
    scope: str
    source_path: str
    page_no: int | None
    local_ordinal: int
    char_start: int
    char_end: int
    byte_start: int
    byte_end: int
    source_sha256: str
    table_sha256: str
    external_table_ref: str | None = None
    unit_hint: str | None = None
    context_before: str = ""
    headers: list[str] = field(default_factory=list)
    row_paths: list[str] = field(default_factory=list)
    search_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RetrievedTable:
    internal_table_uid: str
    document_id: str
    ticker: str
    report_year: int | None
    scope: str
    lexical_rank: int | None = None
    dense_rank: int | None = None
    fused_score: float = 0.0
    reranker_score: float | None = None
    external_table_ref: str | None = None
    preview: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ParsedNumber:
    raw: str
    normalized: str | None
    value: str | None
    confidence: float
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
