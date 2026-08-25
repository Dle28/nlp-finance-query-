#!/usr/bin/env python3
"""Build the allowlisted, numeric-value-free public V13 pipeline snapshot."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from finance_query.research.proof_policy.claim_requirements import load_jsonl, sha256_file
try:
    from scripts.research.proof_policy.verify_claim_requirement_coverage_v13 import verify_v13_artifact
except ModuleNotFoundError:  # Direct `python scripts/build_public_...py` execution.
    from verify_claim_requirement_coverage_v13 import verify_v13_artifact


PROTOCOL = "vifinqa_public_pipeline_snapshot_v13_v2"
LABELS = {
    "source.integrity": "Tính toàn vẹn nguồn",
    "source.truth_tier": "Cấp độ sự thật nguồn",
    "entity.identity": "Định danh pháp nhân",
    "entity.role": "Vai trò pháp nhân",
    "reporting.scope": "Phạm vi báo cáo",
    "statement.role": "Loại báo cáo",
    "variable.metric": "Chỉ tiêu tài chính",
    "temporal.period": "Kỳ báo cáo",
    "unit.scale": "Đơn vị và hệ số",
    "metric.tax_treatment": "Trước/sau thuế",
    "metric.gross_net_basis": "Gộp/thuần",
    "accounting.measurement_basis": "Cơ sở đo lường kế toán",
    "formula.definition": "Định nghĩa công thức",
    "operand.set": "Tập toán hạng",
    "operand.compatibility": "Tính tương thích toán hạng",
}


def _row(path: Path, question_id: int) -> dict[str, Any]:
    matches = [row for row in load_jsonl(path) if row.get("question_id") == question_id]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one Q{question_id} row in {path}")
    return matches[0]


def _expected_summary(dimension: str, expected: object) -> str:
    if expected is None:
        if dimension == "source.integrity":
            return "Policy: exact-source lineage, typed anchor và binding hash đều bắt buộc"
        if dimension == "operand.compatibility":
            return "Dependency: cần compatibility receipt độc lập cho toàn bộ toán hạng"
        if dimension == "accounting.measurement_basis":
            return "Claim dimension chưa có parser độc lập; không được coi là NOT_APPLICABLE"
        return "Claim chưa xác lập proposition cho dimension này"
    if dimension == "entity.identity":
        return " · ".join(str(value) for value in expected if isinstance(expected, list))
    if dimension == "temporal.period" and isinstance(expected, Mapping):
        years = ", ".join(str(value) for value in expected.get("requested_years") or [])
        return f"{expected.get('kind')} · năm {years} · {expected.get('role')}"
    if dimension == "unit.scale" and isinstance(expected, Mapping):
        return f"{expected.get('kind')} · {expected.get('unit')}"
    if dimension == "formula.definition" and isinstance(expected, Mapping):
        return "operations: " + ", ".join(str(value) for value in expected.get("required_operations") or [])
    if dimension == "operand.set":
        return "Typed operand obligations chưa materialize"
    if dimension == "variable.metric":
        return "Canonical metric proposition chưa được parser độc lập xác lập"
    if isinstance(expected, list):
        return " · ".join(str(value) for value in expected)
    return str(expected)


def _interpretation_summary(dimension: str, actual: object) -> str:
    if dimension == "temporal.period" and isinstance(actual, Mapping):
        kinds = ", ".join(str(value) for value in actual.get("kinds") or [])
        years = ", ".join(str(value) for value in actual.get("years") or [])
        return f"source observation: {kinds} · năm {years}"
    if isinstance(actual, list) and actual:
        return "source observation: " + " · ".join(str(value) for value in actual)
    return "Chưa có typed source observation được V13 chấp nhận"


def _effect(status: str) -> str:
    return {
        "PASS": "Dimension-local PASS; không tự cấp answer authorization",
        "FAIL": "Mâu thuẫn đã được chứng minh; chặn authorization",
        "UNRESOLVED": "Thiếu proof; chặn authorization",
        "NOT_CHECKED": "Chưa kiểm tra; chặn internal completeness",
        "NOT_APPLICABLE": "Không áp dụng cho claim này",
    }[status]


def _obligation_origin(dimension: str) -> str:
    if dimension in {"source.integrity", "source.truth_tier"}:
        return "policy"
    if dimension in {"formula.definition", "unit.scale"}:
        return "route_diagnostic"
    if dimension in {"operand.set", "operand.compatibility"}:
        return "dependency"
    return "claim"


def _public_counts(counts: Mapping[str, Any]) -> dict[str, int]:
    partitions = counts["first_blocker_partition_counts"]
    composed = counts["composed_primary_blocker_counts"]
    route = counts["route_primary_blocker_counts"]
    temporal = counts["temporal_claim_kind_counts"]
    result = {
        "questions": int(counts["question_count"]),
        "v12Candidates": int(partitions["v12_complete_shadowed"]),
        "v13InternallyComplete": int(counts["internal_coverage_status_counts"].get("INTERNALLY_COMPLETE", 0)),
        "claimCompleteEstablished": int(counts["claim_completeness_status_counts"].get("CLAIM_COMPLETE", 0)),
        "composed": int(partitions["composed"]),
        "composedFormulaDefinition": int(composed["FORMULA_DEFINITION_INCOMPLETE"]),
        "composedOperandSet": int(composed["OPERAND_SET_INCOMPLETE"]),
        "route": int(partitions["route"]),
        "routeTableOrMetric": int(route["TABLE_OR_METRIC_BINDING_UNRESOLVED"]),
        "routeFormulaOrOperator": int(route["FORMULA_OR_OPERATOR_DEFINITION_UNRESOLVED"]),
        "routeCauseUnestablished": int(route["ROUTE_CAUSE_UNESTABLISHED"]),
        "temporal": int(partitions["temporal"]),
        "temporalInstant": int(temporal["instant"]),
        "temporalDuration": int(temporal["duration"]),
    }
    if result["composedFormulaDefinition"] + result["composedOperandSet"] != result["composed"]:
        raise ValueError("public composed subtype counts do not reconcile")
    if result["routeTableOrMetric"] + result["routeFormulaOrOperator"] + result["routeCauseUnestablished"] != result["route"]:
        raise ValueError("public route subtype counts do not reconcile")
    if result["temporalInstant"] + result["temporalDuration"] != result["temporal"]:
        raise ValueError("public temporal subtype counts do not reconcile")
    if result["composed"] + result["route"] + result["temporal"] + result["v12Candidates"] != result["questions"]:
        raise ValueError("public first-blocker partition does not reconcile")
    return result


def build_public_snapshot(manifest_path: Path) -> dict[str, Any]:
    verification = verify_v13_artifact(manifest_path)
    if not verification.get("trust_root_verified"):
        raise ValueError("public snapshot requires an authoritative V13 trust root")
    artifact_dir = manifest_path.resolve().parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    requirements = _row(artifact_dir / "claim_requirement_sets_v1.jsonl", 211)
    coverage = _row(artifact_dir / "semantic_coverage_certificates_v2.jsonl", 211)
    requirement_by_dimension = {item["dimension"]: item for item in requirements["requirements"]}
    checks = coverage["proof_obligations"]
    if len(checks) != 15 or set(requirement_by_dimension) != {item["dimension"] for item in checks}:
        raise ValueError("Q211 public trace must cover all 15 frozen obligations")
    truth = coverage["source_truth_tier"]
    obligations = []
    for check in checks:
        dimension = str(check["dimension"])
        requirement = requirement_by_dimension[dimension]
        evidence_refs = [str(value) for value in check.get("evidence_refs") or []]
        obligations.append({
            "dimension": dimension,
            "label": LABELS[dimension],
            "origin": _obligation_origin(dimension),
            "requirementId": requirement["obligation_id"],
            "applicability": requirement["applicability"],
            "requirementBasis": requirement["requirement_basis"],
            "claimRequirement": _expected_summary(dimension, requirement.get("expected")),
            "sourceFact": "Không có hash-bound evidence ref" if not evidence_refs else f"{len(evidence_refs)} refs · " + " · ".join(value[:20] + "…" for value in evidence_refs[:3]),
            "systemInterpretation": _interpretation_summary(dimension, check.get("actual")),
            "decision": check["status"],
            "reasonCodes": [str(value) for value in check.get("reason_codes") or []],
            "authorizationEffect": _effect(str(check["status"])),
        })
    counts = manifest["counts"]
    return {
        "protocol": PROTOCOL,
        "snapshotId": manifest_path.parent.name,
        "sourceSummarySha256": manifest["outputs"]["summary"]["sha256"],
        "sourceManifestSha256": sha256_file(manifest_path),
        "verificationStatus": verification["status"],
        "releaseStatus": verification["release_status"],
        "counts": _public_counts(counts),
        "q211": {
            "questionId": 211,
            "requirementSetId": requirements["claim_requirement_set_id"],
            "coverageCertificateId": coverage["semantic_coverage_certificate_id"],
            "traceCompleteness": "15/15",
            "sourceTruthTier": {
                "tier": truth["tier"],
                "status": truth["status"],
                "reasonCodes": [str(value) for value in truth.get("reason_codes") or []],
            },
            "obligations": obligations,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    snapshot = build_public_snapshot(args.manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PUBLIC_SNAPSHOT_WRITTEN", "output": str(args.output), "q211_obligations": len(snapshot["q211"]["obligations"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
