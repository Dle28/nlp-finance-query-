from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from finance_query.route_coverage_adjudication import (
    build_route_coverage_adjudication,
    sha256_file,
)


def _contract() -> dict[str, bool]:
    return {
        "navigation_metadata_only": True,
        "evidence_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
    }


def _route(question_id: int, reasons: list[str], *, stages: list[dict] | None = None) -> dict:
    return {
        "question_id": question_id,
        "question": f"Question {question_id}",
        "normalized_question": f"question {question_id}",
        "route_status": "abstain",
        "reason_codes": reasons,
        "question_context": {"entities": ["ACME"], "years": [2024], "scope": "separate"},
        "stages": stages or [],
        "source_contract": _contract(),
    }


def _write_fixture(tmp_path: Path, routes: list[dict]) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    routes_path = tmp_path / "routes.jsonl"
    routes_path.write_text("".join(json.dumps(row) + "\n" for row in routes), encoding="utf-8")
    manifest_path = tmp_path / "routes.manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "question_count": len(routes),
                "output": {"sha256": sha256_file(routes_path)},
                "source_contract": _contract(),
            }
        ),
        encoding="utf-8",
    )
    return routes_path, manifest_path


def test_queue_classifies_contract_gaps_and_keeps_blank_templates(tmp_path: Path) -> None:
    routes_path, manifest_path = _write_fixture(
        tmp_path,
        [
            _route(1, ["NO_LITERAL_METRIC_OR_CONCEPT_MATCH"]),
            _route(
                2,
                ["MISSING_SCOPE_CONTEXT", "DIRECT_CONCEPT_OPERATION_UNSUPPORTED"],
                stages=[
                    {
                        "stage_id": "s1",
                        "route_kind": "reported_concept",
                        "concept_id": "assets",
                        "required_operands": [
                            {
                                "role": "assets",
                                "concept_id": "assets",
                                "period_type": "instant",
                                "statement_types": ["balance_sheet"],
                            }
                        ],
                    }
                ],
            ),
            _route(3, ["COMPOSED_EXECUTION_REQUIRED", "MISSING_ENTITY_CONTEXT"]),
        ],
    )
    output = tmp_path / "out"
    result = build_route_coverage_adjudication(
        routes=routes_path, routes_manifest=manifest_path, output_dir=output
    )
    queue = [json.loads(line) for line in (output / "route_coverage_adjudication_queue_v1.jsonl").read_text().splitlines()]
    templates = [json.loads(line) for line in (output / "route_coverage_adjudication_label_template_v1.jsonl").read_text().splitlines()]

    assert [row["question_id"] for row in queue] == [1, 2, 3]
    assert queue[0]["review_tracks"] == ["literal_concept_taxonomy"]
    assert queue[1]["review_tracks"] == ["question_plan_context", "typed_direct_operation"]
    assert queue[1]["missing_context"] == ["scope"]
    assert queue[1]["route_stages"][0]["required_operands"][0]["concept_id"] == "assets"
    assert queue[2]["review_tracks"] == ["question_plan_context", "typed_composed_operation"]
    assert result["counts"]["abstain_count"] == 3
    assert result["counts"]["prepopulated_label_count"] == 0
    assert all(row["is_blank_template"] and row["decision"] is None for row in templates)
    assert all(row["source_contract"]["may_infer_missing_context"] is False for row in queue)


def test_hash_duplicate_and_promotable_input_fail_closed(tmp_path: Path) -> None:
    routes = [_route(1, ["NO_LITERAL_METRIC_OR_CONCEPT_MATCH"])]
    routes_path, manifest_path = _write_fixture(tmp_path / "hash", routes)
    manifest = json.loads(manifest_path.read_text())
    manifest["output"]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        build_route_coverage_adjudication(
            routes=routes_path, routes_manifest=manifest_path, output_dir=tmp_path / "bad-hash"
        )

    duplicate_routes_path, duplicate_manifest_path = _write_fixture(
        tmp_path / "duplicate", routes + routes
    )
    with pytest.raises(ValueError, match="duplicate"):
        build_route_coverage_adjudication(
            routes=duplicate_routes_path,
            routes_manifest=duplicate_manifest_path,
            output_dir=tmp_path / "bad-duplicate",
        )

    promotable = _route(1, ["NO_LITERAL_METRIC_OR_CONCEPT_MATCH"])
    promotable["source_contract"] = {**_contract(), "submission_eligible": True}
    bad_routes_path, bad_manifest_path = _write_fixture(tmp_path / "promotable", [promotable])
    with pytest.raises(ValueError, match="submission_eligible"):
        build_route_coverage_adjudication(
            routes=bad_routes_path,
            routes_manifest=bad_manifest_path,
            output_dir=tmp_path / "bad-promotable",
        )
