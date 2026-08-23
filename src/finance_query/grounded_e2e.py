"""Read-only orchestration for the deterministic grounded-execution path.

The project has several research producers.  This module is the one canonical
consumer for the hand-off below:

``route/period packets -> exact V2 cells -> Decimal replay``.

It deliberately has no promotion, training, submission, or LLM authority.  A
successful run proves that the supplied hash-bound inputs can be replayed
together; it does *not* turn the resulting rows into answers.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

import yaml

from . import exact_cell_bindings
from . import exact_cell_bindings_v2
from . import exact_cell_bindings_v3
from . import binding_conflict_workbench
from . import financial_taxonomy
from . import evidence_binding
from . import answer_certificates
from . import grounded_authorization
from . import semantic_approvals
from . import semantic_binding_corrections
from . import navigation_binding_promotions
from . import cross_entity_binding_promotions
from . import grounded_execution
from . import grounded_execution_v2
from . import execution_sandbox
from . import numeric_cell_tokens
from .exact_cell_bindings_v2 import build as build_exact_cell_bindings
from .exact_cell_bindings_v3 import upgrade_context_unit_bindings
from .grounded_authorization import materialize_authorization_replay
from .grounded_execution_v2 import run as run_grounded_execution
from .numeric_cell_tokens import materialize_numeric_cell_tokens
from .semantic_binding_corrections import apply_semantic_binding_corrections
from .navigation_binding_promotions import apply_navigation_binding_promotions
from .cross_entity_binding_promotions import apply_cross_entity_binding_promotions


GROUNDED_E2E_PROTOCOL = "vifinqa_grounded_e2e_v1"
GROUNDED_E2E_SCHEMA_VERSION = 1
SOURCE_CONTRACT = {
    "research_only": True,
    "evidence_eligible": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
    "may_materialize_answer": False,
}
REQUIRED_PATHS = (
    "period_packets",
    "period_manifest",
    "route_overlay",
    "route_overlay_manifest",
    "structured_tables",
    "evidence_context",
    "evidence_context_manifest",
    "semantic_review_queue",
    "semantic_review_manifest",
    "semantic_human_decisions",
    "metric_registry",
)
OPTIONAL_REFERENCE_PATHS = (
    "expected_bindings",
    "expected_execution",
    "expected_evidence_bindings",
    "expected_answer_certificates",
    "expected_authorization_readiness",
)
SEMANTIC_CORRECTION_PATHS = (
    "semantic_correction_packets",
    "semantic_correction_decisions",
    "semantic_correction_manifest",
)
NAVIGATION_PROMOTION_PATHS = (
    "navigation_promotion_packets",
    "navigation_promotion_decisions",
    "navigation_promotion_manifest",
)
CROSS_ENTITY_PROMOTION_PATHS = (
    "cross_entity_promotion_packets",
    "cross_entity_promotion_decisions",
    "cross_entity_promotion_manifest",
)
RUN_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{2,79}\Z")


class GroundedE2EError(ValueError):
    """Raised when an end-to-end replay cannot be proven safe."""


@dataclass(frozen=True, slots=True)
class GroundedE2EInputs:
    run_name: str
    config_path: Path
    config_sha256: str
    period_packets: Path
    period_manifest: Path
    route_overlay: Path
    route_overlay_manifest: Path
    structured_tables: Path
    evidence_context: Path
    evidence_context_manifest: Path
    semantic_review_queue: Path
    semantic_review_manifest: Path
    semantic_human_decisions: Path
    metric_registry: Path
    binding_protocol: str = "v2"
    semantic_correction_packets: Path | None = None
    semantic_correction_decisions: Path | None = None
    semantic_correction_manifest: Path | None = None
    navigation_promotion_packets: Path | None = None
    navigation_promotion_decisions: Path | None = None
    navigation_promotion_manifest: Path | None = None
    cross_entity_promotion_packets: Path | None = None
    cross_entity_promotion_decisions: Path | None = None
    cross_entity_promotion_manifest: Path | None = None
    expected_bindings: Path | None = None
    expected_execution: Path | None = None
    expected_evidence_bindings: Path | None = None
    expected_answer_certificates: Path | None = None
    expected_authorization_readiness: Path | None = None

    def required_paths(self) -> dict[str, Path]:
        paths = {
            "period_packets": self.period_packets,
            "period_manifest": self.period_manifest,
            "route_overlay": self.route_overlay,
            "route_overlay_manifest": self.route_overlay_manifest,
            "structured_tables": self.structured_tables,
            "evidence_context": self.evidence_context,
            "evidence_context_manifest": self.evidence_context_manifest,
            "semantic_review_queue": self.semantic_review_queue,
            "semantic_review_manifest": self.semantic_review_manifest,
            "semantic_human_decisions": self.semantic_human_decisions,
            "metric_registry": self.metric_registry,
        }
        paths.update(
            {
                name: path
                for name, path in {
                    "semantic_correction_packets": self.semantic_correction_packets,
                    "semantic_correction_decisions": self.semantic_correction_decisions,
                    "semantic_correction_manifest": self.semantic_correction_manifest,
                    "navigation_promotion_packets": self.navigation_promotion_packets,
                    "navigation_promotion_decisions": self.navigation_promotion_decisions,
                    "navigation_promotion_manifest": self.navigation_promotion_manifest,
                    "cross_entity_promotion_packets": self.cross_entity_promotion_packets,
                    "cross_entity_promotion_decisions": self.cross_entity_promotion_decisions,
                    "cross_entity_promotion_manifest": self.cross_entity_promotion_manifest,
                }.items()
                if path is not None
            }
        )
        return paths

    def reference_paths(self) -> dict[str, Path]:
        return {
            name: path
            for name, path in {
                "expected_bindings": self.expected_bindings,
                "expected_execution": self.expected_execution,
                "expected_evidence_bindings": self.expected_evidence_bindings,
                "expected_answer_certificates": self.expected_answer_certificates,
                "expected_authorization_readiness": self.expected_authorization_readiness,
            }.items()
            if path is not None
        }

    def source_code_paths(self) -> dict[str, Path]:
        """Return the small, explicit source closure for this deterministic run."""

        modules = {
            "grounded_e2e": Path(__file__),
            "evidence_binding": Path(evidence_binding.__file__ or ""),
            "answer_certificates": Path(answer_certificates.__file__ or ""),
            "exact_cell_bindings_v2": Path(exact_cell_bindings_v2.__file__ or ""),
            "exact_cell_bindings_v3": Path(exact_cell_bindings_v3.__file__ or ""),
            "binding_conflict_workbench": Path(binding_conflict_workbench.__file__ or ""),
            "exact_cell_bindings": Path(exact_cell_bindings.__file__ or ""),
            "grounded_authorization": Path(grounded_authorization.__file__ or ""),
            "semantic_approvals": Path(semantic_approvals.__file__ or ""),
            "semantic_binding_corrections": Path(semantic_binding_corrections.__file__ or ""),
            "navigation_binding_promotions": Path(navigation_binding_promotions.__file__ or ""),
            "cross_entity_binding_promotions": Path(cross_entity_binding_promotions.__file__ or ""),
            "grounded_execution_v2": Path(grounded_execution_v2.__file__ or ""),
            "grounded_execution": Path(grounded_execution.__file__ or ""),
            "execution_sandbox": Path(execution_sandbox.__file__ or ""),
            "numeric_cell_tokens": Path(numeric_cell_tokens.__file__ or ""),
            "financial_taxonomy": Path(financial_taxonomy.__file__ or ""),
        }
        if any(not path.is_file() for path in modules.values()):
            raise GroundedE2EError("Grounded E2E source closure is incomplete")
        return modules


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_path(value: object, *, config_path: Path, name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise GroundedE2EError(f"Config requires a non-empty paths.{name}")
    path = Path(value)
    if not path.is_absolute():
        path = config_path.parent / path
    return path.resolve()


def load_inputs(config_path: Path) -> GroundedE2EInputs:
    """Load a small, explicit path manifest; never infer artifacts by filename."""

    config_path = config_path.resolve()
    if not config_path.is_file():
        raise FileNotFoundError(config_path)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise GroundedE2EError("Grounded E2E config must be a mapping")
    if payload.get("protocol") != GROUNDED_E2E_PROTOCOL:
        raise GroundedE2EError("Unexpected grounded E2E config protocol")
    if payload.get("schema_version") != GROUNDED_E2E_SCHEMA_VERSION:
        raise GroundedE2EError("Unexpected grounded E2E config schema version")
    run_name = payload.get("run_name")
    if not isinstance(run_name, str) or not RUN_NAME_RE.fullmatch(run_name):
        raise GroundedE2EError(
            "run_name must use 3-80 lowercase letters, digits, or hyphens"
        )
    paths = payload.get("paths")
    if not isinstance(paths, Mapping):
        raise GroundedE2EError("Grounded E2E config requires a paths mapping")

    values = {
        name: _resolve_path(paths.get(name), config_path=config_path, name=name)
        for name in REQUIRED_PATHS
    }
    for name in OPTIONAL_REFERENCE_PATHS:
        raw = paths.get(name)
        values[name] = (
            _resolve_path(raw, config_path=config_path, name=name)
            if raw is not None
            else None
        )
    for name in SEMANTIC_CORRECTION_PATHS:
        raw = paths.get(name)
        values[name] = (
            _resolve_path(raw, config_path=config_path, name=name)
            if raw is not None
            else None
        )
    for name in NAVIGATION_PROMOTION_PATHS:
        raw = paths.get(name)
        values[name] = (
            _resolve_path(raw, config_path=config_path, name=name)
            if raw is not None
            else None
        )
    for name in CROSS_ENTITY_PROMOTION_PATHS:
        raw = paths.get(name)
        values[name] = (
            _resolve_path(raw, config_path=config_path, name=name)
            if raw is not None
            else None
        )
    binding_protocol = str(payload.get("binding_protocol") or "v2")
    if binding_protocol not in {"v2", "v3_context_unit", "v4_semantic_correction", "v5_navigation_promotion", "v6_cross_entity_composition"}:
        raise GroundedE2EError(
            "binding_protocol must be v2, v3_context_unit, v4_semantic_correction, v5_navigation_promotion, or v6_cross_entity_composition"
        )
    correction_values = [values[name] for name in SEMANTIC_CORRECTION_PATHS]
    if binding_protocol in {"v4_semantic_correction", "v5_navigation_promotion", "v6_cross_entity_composition"} and not all(correction_values):
        raise GroundedE2EError("v4/v5/v6 binding protocols require all semantic correction paths")
    if binding_protocol not in {"v4_semantic_correction", "v5_navigation_promotion", "v6_cross_entity_composition"} and any(correction_values):
        raise GroundedE2EError("semantic correction paths require binding_protocol v4, v5, or v6")
    promotion_values = [values[name] for name in NAVIGATION_PROMOTION_PATHS]
    if binding_protocol in {"v5_navigation_promotion", "v6_cross_entity_composition"} and not all(promotion_values):
        raise GroundedE2EError("v5/v6 binding protocols require all navigation promotion paths")
    if binding_protocol not in {"v5_navigation_promotion", "v6_cross_entity_composition"} and any(promotion_values):
        raise GroundedE2EError("navigation promotion paths require binding_protocol v5 or v6")
    cross_entity_values = [values[name] for name in CROSS_ENTITY_PROMOTION_PATHS]
    if binding_protocol == "v6_cross_entity_composition" and not all(cross_entity_values):
        raise GroundedE2EError("v6_cross_entity_composition requires all cross-entity promotion paths")
    if binding_protocol != "v6_cross_entity_composition" and any(cross_entity_values):
        raise GroundedE2EError("cross-entity promotion paths require binding_protocol v6_cross_entity_composition")
    return GroundedE2EInputs(
        run_name=run_name,
        config_path=config_path,
        config_sha256=sha256_file(config_path),
        binding_protocol=binding_protocol,
        **values,
    )


def _check_files(inputs: GroundedE2EInputs) -> None:
    missing = [
        f"{name}={path}"
        for name, path in {**inputs.required_paths(), **inputs.reference_paths()}.items()
        if not path.is_file()
    ]
    if missing:
        raise FileNotFoundError("Grounded E2E input is missing: " + ", ".join(missing))


def _hashes(paths: Mapping[str, Path]) -> dict[str, str]:
    return {name: sha256_file(path) for name, path in paths.items()}


def _write_json_exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite end-to-end receipt: {path}")
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_grounded_e2e(inputs: GroundedE2EInputs, *, output_dir: Path) -> dict[str, Any]:
    """Replay all deterministic grounded stages into a new, immutable run directory.

    The function snapshots every caller-provided input before execution and
    rejects the run if any input changed.  Existing source artifacts are thus
    never used as output targets. Optional expected artifacts make the V2,
    execution, Evidence Binding and Answer Certificate outputs byte-for-byte
    reproducible.
    """

    _check_files(inputs)
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(
            f"Grounded E2E output directory already exists: {output_dir}. "
            "Choose a new run directory."
        )
    input_paths = {**inputs.required_paths(), **inputs.reference_paths()}
    if any(output_dir == path or output_dir in path.parents for path in input_paths.values()):
        raise GroundedE2EError("Output directory cannot contain an input artifact")
    before = _hashes(input_paths)
    source_code = _hashes(inputs.source_code_paths())
    run_identity_payload = {
        "protocol": GROUNDED_E2E_PROTOCOL,
        "run_name": inputs.run_name,
        "config_sha256": inputs.config_sha256,
        "binding_protocol": inputs.binding_protocol,
        "inputs": before,
        "source_code": source_code,
    }
    run_id = hashlib.sha256(
        json.dumps(run_identity_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    output_dir.mkdir(parents=True, exist_ok=False)
    bindings_path = output_dir / {
        "v2": "exact_cell_unit_binding_candidates_v2.jsonl",
        "v3_context_unit": "exact_cell_unit_binding_candidates_v3.jsonl",
        "v4_semantic_correction": "exact_cell_unit_binding_candidates_v4.jsonl",
        "v5_navigation_promotion": "exact_cell_unit_binding_candidates_v5.jsonl",
        "v6_cross_entity_composition": "exact_cell_unit_binding_candidates_v6.jsonl",
    }[inputs.binding_protocol]
    bindings_manifest = bindings_path.with_suffix(".manifest.json")
    bindings_v2_base_path = output_dir / "exact_cell_unit_binding_candidates_v2_base.jsonl"
    bindings_v3_base_path = output_dir / "exact_cell_unit_binding_candidates_v3_base.jsonl"
    bindings_v4_base_path = output_dir / "exact_cell_unit_binding_candidates_v4_base.jsonl"
    bindings_v5_base_path = output_dir / "exact_cell_unit_binding_candidates_v5_base.jsonl"
    cell_token_registry_path = output_dir / "numeric_cell_token_registry_v1.jsonl"
    cell_token_public_view_path = output_dir / "numeric_cell_token_view_v1.jsonl"
    cell_token_manifest = cell_token_registry_path.with_suffix(".manifest.json")
    execution_path = output_dir / "grounded_execution_replay_v2.jsonl"
    execution_manifest = execution_path.with_suffix(".manifest.json")
    telemetry_path = output_dir / "execution_telemetry_v1.jsonl"
    telemetry_manifest = telemetry_path.with_suffix(".manifest.json")
    evidence_bindings_path = output_dir / "evidence_bindings_v1.jsonl"
    answer_certificates_path = output_dir / "answer_certificates_v1.jsonl"

    v2_output = bindings_path if inputs.binding_protocol == "v2" else bindings_v2_base_path
    binding_result = build_exact_cell_bindings(
        period_packets=inputs.period_packets,
        period_manifest=inputs.period_manifest,
        route_overlay=inputs.route_overlay,
        route_overlay_manifest=inputs.route_overlay_manifest,
        structured_tables=inputs.structured_tables,
        metric_registry=inputs.metric_registry,
        output=v2_output,
    )
    if inputs.binding_protocol in {"v3_context_unit", "v4_semantic_correction", "v5_navigation_promotion", "v6_cross_entity_composition"}:
        v3_output = (
            bindings_path
            if inputs.binding_protocol == "v3_context_unit"
            else bindings_v3_base_path
        )
        binding_result = upgrade_context_unit_bindings(
            bindings_v2=v2_output,
            bindings_v2_manifest=v2_output.with_suffix(".manifest.json"),
            structured_tables=inputs.structured_tables,
            evidence_context=inputs.evidence_context,
            evidence_context_manifest=inputs.evidence_context_manifest,
            output=v3_output,
        )
    else:
        v3_output = bindings_path
    if inputs.binding_protocol in {"v4_semantic_correction", "v5_navigation_promotion", "v6_cross_entity_composition"}:
        semantic_output = (
            bindings_path
            if inputs.binding_protocol == "v4_semantic_correction"
            else bindings_v4_base_path
        )
        binding_result = apply_semantic_binding_corrections(
            base_bindings=v3_output,
            base_bindings_manifest=v3_output.with_suffix(".manifest.json"),
            structured_tables=inputs.structured_tables,
            packets=inputs.semantic_correction_packets,
            decisions=inputs.semantic_correction_decisions,
            correction_manifest=inputs.semantic_correction_manifest,
            output=semantic_output,
        )
    else:
        semantic_output = v3_output
    if inputs.binding_protocol in {"v5_navigation_promotion", "v6_cross_entity_composition"}:
        navigation_output = (
            bindings_path
            if inputs.binding_protocol == "v5_navigation_promotion"
            else bindings_v5_base_path
        )
        binding_result = apply_navigation_binding_promotions(
            base_bindings=semantic_output,
            base_bindings_manifest=semantic_output.with_suffix(".manifest.json"),
            structured_tables=inputs.structured_tables,
            evidence_context=inputs.evidence_context,
            packets=inputs.navigation_promotion_packets,
            decisions=inputs.navigation_promotion_decisions,
            promotion_manifest=inputs.navigation_promotion_manifest,
            output=navigation_output,
        )
    else:
        navigation_output = semantic_output
    if inputs.binding_protocol == "v6_cross_entity_composition":
        binding_result = apply_cross_entity_binding_promotions(
            base_bindings=navigation_output,
            base_bindings_manifest=navigation_output.with_suffix(".manifest.json"),
            structured_tables=inputs.structured_tables,
            evidence_context=inputs.evidence_context,
            packets=inputs.cross_entity_promotion_packets,
            decisions=inputs.cross_entity_promotion_decisions,
            promotion_manifest=inputs.cross_entity_promotion_manifest,
            repository_root=inputs.config_path.parent.parent,
            output=bindings_path,
        )
    cell_token_result = materialize_numeric_cell_tokens(
        bindings=bindings_path,
        bindings_manifest=bindings_manifest,
        structured_tables=inputs.structured_tables,
        registry_output=cell_token_registry_path,
        public_view_output=cell_token_public_view_path,
    )
    execution_result = run_grounded_execution(
        bindings=bindings_path,
        bindings_manifest=bindings_manifest,
        metric_registry=inputs.metric_registry,
        output=execution_path,
        cell_tokens=cell_token_registry_path,
        cell_tokens_manifest=cell_token_manifest,
        telemetry_output=telemetry_path,
    )
    authorization_result = materialize_authorization_replay(
        bindings=bindings_path,
        bindings_manifest=bindings_manifest,
        execution=execution_path,
        execution_manifest=execution_manifest,
        structured_tables=inputs.structured_tables,
        evidence_context=inputs.evidence_context,
        evidence_context_manifest=inputs.evidence_context_manifest,
        semantic_review_queue=inputs.semantic_review_queue,
        semantic_review_manifest=inputs.semantic_review_manifest,
        semantic_human_decisions=inputs.semantic_human_decisions,
        metric_registry=inputs.metric_registry,
        evidence_bindings_output=evidence_bindings_path,
        answer_certificates_output=answer_certificates_path,
        semantic_approval_bindings=(
            v3_output if inputs.binding_protocol in {"v4_semantic_correction", "v5_navigation_promotion", "v6_cross_entity_composition"} else None
        ),
        semantic_correction_packets=inputs.semantic_correction_packets,
        semantic_correction_decisions=inputs.semantic_correction_decisions,
        semantic_correction_manifest=inputs.semantic_correction_manifest,
        navigation_promotion_base_bindings=(
            semantic_output if inputs.binding_protocol in {"v5_navigation_promotion", "v6_cross_entity_composition"} else None
        ),
        navigation_promotion_packets=inputs.navigation_promotion_packets,
        navigation_promotion_decisions=inputs.navigation_promotion_decisions,
        navigation_promotion_manifest=inputs.navigation_promotion_manifest,
        cross_entity_promotion_base_bindings=(
            navigation_output if inputs.binding_protocol == "v6_cross_entity_composition" else None
        ),
        cross_entity_promotion_packets=inputs.cross_entity_promotion_packets,
        cross_entity_promotion_decisions=inputs.cross_entity_promotion_decisions,
        cross_entity_promotion_manifest=inputs.cross_entity_promotion_manifest,
        repository_root=(
            inputs.config_path.parent.parent
            if inputs.binding_protocol == "v6_cross_entity_composition"
            else None
        ),
    )

    after = _hashes(input_paths)
    changed = sorted(name for name, digest in before.items() if after[name] != digest)
    if changed:
        raise GroundedE2EError(
            "Grounded E2E producer changed an input artifact: " + ", ".join(changed)
        )

    reproducibility: dict[str, bool] = {}
    if inputs.expected_bindings is not None:
        reproducibility["bindings_match"] = (
            sha256_file(bindings_path) == sha256_file(inputs.expected_bindings)
        )
    if inputs.expected_execution is not None:
        reproducibility["execution_match"] = (
            sha256_file(execution_path) == sha256_file(inputs.expected_execution)
        )
    if inputs.expected_evidence_bindings is not None:
        reproducibility["evidence_bindings_match"] = (
            sha256_file(evidence_bindings_path)
            == sha256_file(inputs.expected_evidence_bindings)
        )
    if inputs.expected_answer_certificates is not None:
        reproducibility["answer_certificates_match"] = (
            sha256_file(answer_certificates_path)
            == sha256_file(inputs.expected_answer_certificates)
        )
    if inputs.expected_authorization_readiness is not None:
        reproducibility["authorization_readiness_match"] = (
            sha256_file(Path(authorization_result["readiness_path"]))
            == sha256_file(inputs.expected_authorization_readiness)
        )
    if reproducibility and not all(reproducibility.values()):
        raise GroundedE2EError(
            "Grounded E2E replay diverged from the declared reference artifact"
        )

    receipt = {
        "schema_version": GROUNDED_E2E_SCHEMA_VERSION,
        "protocol": GROUNDED_E2E_PROTOCOL,
        "run_name": inputs.run_name,
        "binding_protocol": inputs.binding_protocol,
        "run_id": run_id,
        "run_status": "complete_research_only",
        "config": {
            "path": str(inputs.config_path),
            "sha256": inputs.config_sha256,
        },
        "inputs": {
            name: {"path": str(path), "sha256": before[name]}
            for name, path in input_paths.items()
        },
        "outputs": {
            "bindings": {
                "path": str(bindings_path),
                "sha256": sha256_file(bindings_path),
                "manifest_path": str(bindings_manifest),
                "manifest_sha256": sha256_file(bindings_manifest),
                "counts": binding_result["counts"],
            },
            "numeric_cell_tokens": {
                "executor_registry_path": str(cell_token_registry_path),
                "executor_registry_sha256": sha256_file(cell_token_registry_path),
                "public_view_path": str(cell_token_public_view_path),
                "public_view_sha256": sha256_file(cell_token_public_view_path),
                "manifest_path": str(cell_token_manifest),
                "manifest_sha256": sha256_file(cell_token_manifest),
                "counts": cell_token_result["counts"],
            },
            "execution": {
                "path": str(execution_path),
                "sha256": sha256_file(execution_path),
                "manifest_path": str(execution_path.with_suffix(".manifest.json")),
                "manifest_sha256": sha256_file(
                    execution_path.with_suffix(".manifest.json")
                ),
                "counts": execution_result["counts"],
                "telemetry_path": str(telemetry_path),
                "telemetry_sha256": sha256_file(telemetry_path),
                "telemetry_manifest_path": str(telemetry_manifest),
                "telemetry_manifest_sha256": sha256_file(telemetry_manifest),
            },
            "authorization": {
                "evidence_bindings_path": str(evidence_bindings_path),
                "evidence_bindings_sha256": sha256_file(evidence_bindings_path),
                "answer_certificates_path": str(answer_certificates_path),
                "answer_certificates_sha256": sha256_file(answer_certificates_path),
                "readiness_path": authorization_result["readiness_path"],
                "readiness_sha256": sha256_file(Path(authorization_result["readiness_path"])),
                "manifest_path": authorization_result["manifest_path"],
                "manifest_sha256": sha256_file(Path(authorization_result["manifest_path"])),
                "counts": authorization_result["counts"],
            },
        },
        "reproducibility": reproducibility,
        "source_code": source_code,
        "source_contract": dict(SOURCE_CONTRACT),
    }
    _write_json_exclusive(output_dir / "grounded_e2e_run_v1.json", receipt)
    return receipt
