"""Research-grade Vietnamese financial concept taxonomy support.

The taxonomy is navigation metadata, not financial evidence.  It can propose
concepts and semantic axes for an exact source row, but it cannot select a
value cell, repair OCR, or promote a review/submission status.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

import yaml

from .financial_metrics import fold_text


TAXONOMY_PROTOCOL = "vietnamese_financial_concept_taxonomy_v1"
ALLOWED_PERIOD_TYPES = {"instant", "duration", "mixed", "not_applicable"}
ACCOUNT_CODE_RE = re.compile(r"^\s*(\d{1,3})\s*$")


def normalize_label(value: object) -> str:
    """Normalize typography only; never add, remove, or infer financial meaning."""
    return " ".join(fold_text(str(value or "")).replace("\\", " ").split()).strip(" -:;,.()")


def source_account_codes(row: Iterable[Any]) -> frozenset[str]:
    """Collect literal accounting codes from the first three source cells."""
    values = [str(value or "") for value in row]
    return frozenset(
        match.group(1)
        for value in values[:3]
        if (match := ACCOUNT_CODE_RE.fullmatch(value)) is not None
    )


@dataclass(frozen=True, slots=True)
class ConceptDefinition:
    concept_id: str
    labels_vi: tuple[str, ...]
    statement_types: tuple[str, ...]
    sectors: tuple[str, ...]
    period_type: str
    parent_concept_id: str | None = None
    account_codes: tuple[str, ...] = ()
    definition: str = ""


@dataclass(frozen=True, slots=True)
class AxisValue:
    value_id: str
    labels_vi: tuple[str, ...]


class FinancialTaxonomy:
    """Validated, exact-match taxonomy with explicit ambiguity preservation."""

    def __init__(self, payload: Mapping[str, Any], *, source_path: Path | None = None):
        if int(payload.get("schema_version") or 0) != 1:
            raise ValueError("Unsupported financial taxonomy schema version")
        if str(payload.get("protocol") or "") != TAXONOMY_PROTOCOL:
            raise ValueError("Unsupported financial taxonomy protocol")
        self.source_path = source_path
        self.source_references = tuple(str(value) for value in payload.get("source_references") or [])
        self.sectors = tuple(str(value) for value in payload.get("sectors") or [])
        if "unknown" not in self.sectors:
            raise ValueError("Taxonomy sectors must include unknown")

        concepts: list[ConceptDefinition] = []
        seen_ids: set[str] = set()
        for raw in payload.get("concepts") or []:
            concept_id = str(raw.get("concept_id") or "").strip()
            if not concept_id or concept_id in seen_ids:
                raise ValueError(f"Invalid or duplicate concept_id: {concept_id!r}")
            period_type = str(raw.get("period_type") or "")
            if period_type not in ALLOWED_PERIOD_TYPES:
                raise ValueError(f"Invalid period_type for {concept_id}: {period_type!r}")
            labels = tuple(dict.fromkeys(normalize_label(value) for value in raw.get("labels_vi") or []))
            if not labels or any(not value for value in labels):
                raise ValueError(f"Concept {concept_id} requires non-empty Vietnamese labels")
            concepts.append(
                ConceptDefinition(
                    concept_id=concept_id,
                    labels_vi=labels,
                    statement_types=tuple(str(value) for value in raw.get("statement_types") or []),
                    sectors=tuple(str(value) for value in raw.get("sectors") or ["all"]),
                    period_type=period_type,
                    parent_concept_id=str(raw.get("parent_concept_id") or "") or None,
                    account_codes=tuple(str(value) for value in raw.get("account_codes") or []),
                    definition=str(raw.get("definition") or ""),
                )
            )
            seen_ids.add(concept_id)
        for concept in concepts:
            if concept.parent_concept_id and concept.parent_concept_id not in seen_ids:
                raise ValueError(
                    f"Unknown parent {concept.parent_concept_id!r} for {concept.concept_id!r}"
                )
        self.concepts = tuple(concepts)
        self.by_id = {concept.concept_id: concept for concept in concepts}
        for concept in concepts:
            visited = {concept.concept_id}
            parent_id = concept.parent_concept_id
            while parent_id:
                if parent_id in visited:
                    raise ValueError(f"Concept hierarchy contains a cycle at {parent_id!r}")
                visited.add(parent_id)
                parent_id = self.by_id[parent_id].parent_concept_id
        aliases: dict[str, list[ConceptDefinition]] = {}
        for concept in concepts:
            for label in concept.labels_vi:
                aliases.setdefault(label, []).append(concept)
        self.aliases = {label: tuple(values) for label, values in aliases.items()}

        axes: dict[str, tuple[AxisValue, ...]] = {}
        for axis_name, raw_values in (payload.get("semantic_axes") or {}).items():
            values: list[AxisValue] = []
            for raw in raw_values or []:
                value_id = str(raw.get("value_id") or "").strip()
                labels = tuple(dict.fromkeys(normalize_label(value) for value in raw.get("labels_vi") or []))
                if not value_id or not labels:
                    raise ValueError(f"Invalid {axis_name} axis entry")
                values.append(AxisValue(value_id=value_id, labels_vi=labels))
            axes[str(axis_name)] = tuple(values)
        self.semantic_axes = axes

        signals: dict[str, tuple[str, ...]] = {}
        for sector, values in (payload.get("sector_signals") or {}).items():
            if str(sector) not in self.sectors:
                raise ValueError(f"Unknown sector signal group: {sector}")
            signals[str(sector)] = tuple(dict.fromkeys(normalize_label(value) for value in values or []))
        self.sector_signals = signals

    def concept_path(self, concept_id: str) -> list[str]:
        """Return the root-to-leaf concept path without inventing a source relation."""
        if concept_id not in self.by_id:
            raise KeyError(concept_id)
        path: list[str] = []
        current: str | None = concept_id
        while current:
            path.append(current)
            current = self.by_id[current].parent_concept_id
        return list(reversed(path))

    @classmethod
    def load(cls, path: Path) -> "FinancialTaxonomy":
        path = path.resolve()
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("Financial taxonomy root must be a mapping")
        return cls(payload, source_path=path)

    def _concept_allowed(
        self,
        concept: ConceptDefinition,
        *,
        table_type: str | None,
        sector: str | None,
        account_codes: frozenset[str],
    ) -> tuple[bool, list[str]]:
        constraints: list[str] = []
        if concept.statement_types:
            if table_type and table_type not in concept.statement_types:
                return False, ["table_type_mismatch"]
            constraints.append("statement_type")
        if concept.sectors and "all" not in concept.sectors:
            if sector and sector != "unknown" and sector not in concept.sectors:
                return False, ["sector_mismatch"]
            constraints.append("sector")
        if concept.account_codes:
            if not account_codes.intersection(concept.account_codes):
                return False, ["required_account_code_missing"]
            constraints.append("account_code")
        return True, constraints

    def classify_row(
        self,
        row: Iterable[Any],
        *,
        source_label: str,
        table_type: str | None = None,
        sector: str | None = None,
    ) -> dict[str, Any]:
        normalized = normalize_label(source_label)
        codes = source_account_codes(row)
        candidates: list[dict[str, Any]] = []
        rejected_constraints: Counter[str] = Counter()
        for concept in self.aliases.get(normalized, ()):
            allowed, constraints = self._concept_allowed(
                concept,
                table_type=table_type,
                sector=sector,
                account_codes=codes,
            )
            if not allowed:
                rejected_constraints.update(constraints)
                continue
            candidates.append(
                {
                    "concept_id": concept.concept_id,
                    "parent_concept_id": concept.parent_concept_id,
                    "concept_path": self.concept_path(concept.concept_id),
                    "period_type": concept.period_type,
                    "matched_label": normalized,
                    "constraints_applied": constraints,
                }
            )

        axis_matches: dict[str, list[str]] = {}
        for axis_name, values in self.semantic_axes.items():
            matches = [value.value_id for value in values if normalized in value.labels_vi]
            if matches:
                axis_matches[axis_name] = sorted(set(matches))
        if len(candidates) == 1:
            status = "exact_unique"
        elif len(candidates) > 1:
            status = "exact_ambiguous"
        elif axis_matches:
            status = "axis_only"
        elif rejected_constraints:
            status = "constraint_blocked"
        else:
            status = "unmatched"
        return {
            "normalized_source_label": normalized,
            "source_account_codes": sorted(codes),
            "match_status": status,
            "concept_candidates": candidates,
            "semantic_axes": axis_matches,
            "rejected_constraint_counts": dict(sorted(rejected_constraints.items())),
            "source_contract": {
                "navigation_metadata_only": True,
                "evidence_eligible": False,
                "training_eligible": False,
                "submission_eligible": False,
                "may_select_value_cell": False,
                "may_repair_ocr": False,
            },
        }

    def infer_sector(self, labels: Iterable[str], *, minimum_signals: int = 2) -> dict[str, Any]:
        normalized_labels = tuple(normalize_label(label) for label in labels if normalize_label(label))
        scores: Counter[str] = Counter()
        evidence: dict[str, set[str]] = {sector: set() for sector in self.sector_signals}
        for sector, signals in self.sector_signals.items():
            for signal in signals:
                if any(signal == label or signal in label for label in normalized_labels):
                    scores[sector] += 1
                    evidence[sector].add(signal)
        if not scores:
            return {"sector": "unknown", "status": "no_signal", "scores": {}, "matched_signals": {}}
        ranked = scores.most_common()
        best_sector, best_score = ranked[0]
        tied = len(ranked) > 1 and ranked[1][1] == best_score
        if best_score < minimum_signals:
            status, sector = "insufficient_signal", "unknown"
        elif tied:
            status, sector = "ambiguous", "unknown"
        else:
            status, sector = "candidate", best_sector
        return {
            "sector": sector,
            "status": status,
            "scores": dict(sorted(scores.items())),
            "matched_signals": {
                key: sorted(values) for key, values in sorted(evidence.items()) if values
            },
        }
