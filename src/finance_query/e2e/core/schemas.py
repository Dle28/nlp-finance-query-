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

FieldBasis = Literal[
    "EXPLICIT_QUERY",
    "SOURCE_DERIVED",
    "MODEL_INFERRED",
    "UNKNOWN",
]


@dataclass(frozen=True, slots=True)
class PlanFieldProvenance:
    """Explain why a planner field may influence downstream routing."""

    value: Any
    basis: FieldBasis
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    entity_role: str | None = None
    requested_unit: str | None = None
    operands: list[OperandSpec] = field(default_factory=list)
    operation_ast: dict[str, Any] = field(default_factory=dict)
    filters: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    field_provenance: dict[str, PlanFieldProvenance] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["operands"] = [operand.to_dict() for operand in self.operands]
        return payload


@dataclass(frozen=True, slots=True)
class RawTableAsset:
    """Immutable source locator and extracted raw grid for one table.

    ``TableAsset`` remains a backwards-compatible retrieval projection. New
    evidence code should retain this raw view separately from interpretations.
    """

    internal_table_uid: str
    document_id: str
    source_path: str
    page_no: int | None
    local_ordinal: int
    char_start: int
    char_end: int
    byte_start: int
    byte_end: int
    source_sha256: str
    table_sha256: str
    rows: tuple[tuple[str, ...], ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TableAsset:
    """Legacy combined retrieval projection.

    It intentionally preserves the JSONL/SQLite contract used by existing
    bundles. ``raw_asset`` and ``derived_assertions`` expose the two planes so
    new code does not mistake a parser interpretation for immutable evidence.
    """

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
    rows: list[list[str]] = field(default_factory=list)
    row_paths: list[str] = field(default_factory=list)
    search_text: str = ""
    structure_version: int = 1
    context_schema_version: int = 1
    header_row_indices: list[int] = field(default_factory=list)
    table_function: dict[str, Any] = field(default_factory=dict)
    table_section: dict[str, Any] = field(default_factory=dict)
    table_purpose: dict[str, Any] = field(default_factory=dict)
    context_trace: dict[str, Any] = field(default_factory=dict)
    structure_quality: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def raw_asset(self) -> RawTableAsset:
        return RawTableAsset(
            internal_table_uid=self.internal_table_uid,
            document_id=self.document_id,
            source_path=self.source_path,
            page_no=self.page_no,
            local_ordinal=self.local_ordinal,
            char_start=self.char_start,
            char_end=self.char_end,
            byte_start=self.byte_start,
            byte_end=self.byte_end,
            source_sha256=self.source_sha256,
            table_sha256=self.table_sha256,
            rows=tuple(tuple(str(cell) for cell in row) for row in self.rows),
        )

    def derived_assertions(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "report_year": self.report_year,
            "scope": self.scope,
            "external_table_ref": self.external_table_ref,
            "unit_hint": self.unit_hint,
            "context_before": self.context_before,
            "headers": self.headers,
            "row_paths": self.row_paths,
            "search_text": self.search_text,
            "structure_version": self.structure_version,
            "context_schema_version": self.context_schema_version,
            "header_row_indices": self.header_row_indices,
            "table_function": self.table_function,
            "table_section": self.table_section,
            "table_purpose": self.table_purpose,
            "context_trace": self.context_trace,
            "structure_quality": self.structure_quality,
        }


@dataclass(slots=True)
class RetrievedTable:
    internal_table_uid: str
    document_id: str
    ticker: str
    report_year: int | None
    scope: str
    lexical_rank: int | None = None
    dense_rank: int | None = None
    hierarchy_rank: int | None = None
    fused_score: float = 0.0
    reranker_score: float | None = None
    external_table_ref: str | None = None
    preview: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DirectBinding:
    internal_table_uid: str
    document_id: str
    row_index: int
    column_index: int
    row_text: str
    column_text: str
    raw_value: str
    parsed_value: str
    source_unit: str | None
    target_unit: str | None
    converted_value: str
    binding_score: float
    warnings: list[str] = field(default_factory=list)

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
