from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from finance_query.route_coverage_adjudication import canonical_sha256, sha256_file, source_contract
from finance_query.route_coverage_source_validation import validate_consensus_handoff_sources


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    raw = bundle / "tables.jsonl"
    raw.write_text("{}\n", encoding="utf-8")
    source_path = tmp_path / "reports" / "ABC.txt"
    source_path.parent.mkdir()
    source_path.write_text("original source", encoding="utf-8")
    provenance = {"source_path": str(source_path), "source_sha256": sha256_file(source_path)}
    v2 = bundle / "tables_structured_v2.jsonl"
    v3 = bundle / "tables_evidence_context_v3.jsonl"
    row = {"internal_table_uid": "u1", "document_id": "ABC_2023", "page_no": 12, "source_provenance": provenance}
    _write_jsonl(v2, [row])
    _write_jsonl(v3, [row])
    (bundle / "table_structure_v2.manifest.json").write_text(
        json.dumps({"structure_version": 2, "error_count": 0, "input_bundle_tables_sha256": sha256_file(raw), "sidecar_sha256": sha256_file(v2), "repaired_table_count": 1, "table_count": 1}),
        encoding="utf-8",
    )
    (bundle / "table_evidence_context_v3.manifest.json").write_text(
        json.dumps({"evidence_context_version": 3, "numeric_binding_policy": "one_reliable_raw_v2_number_per_cell", "error_count": 0, "input_structure_sha256": sha256_file(v2), "input_bundle_tables_sha256": sha256_file(raw), "sidecar_sha256": sha256_file(v3)}),
        encoding="utf-8",
    )
    return bundle


def _handoff(tmp_path: Path, *, locator: str, document_id: str = "ABC_2023") -> tuple[Path, Path]:
    coordinates = [{"source_locator": locator, "document_id": document_id, "page_no": 12}]
    proposal = {"proposed_question_plan": {"scope": "consolidated"}, "proposed_taxonomy_alias": None, "proposed_operation_contract": None}
    row = {
        "schema_version": 1,
        "protocol": "route_coverage_consensus_handoff_v1",
        "question_id": 7,
        "immutable_queue_payload_sha256": "a" * 64,
        "review_tracks": ["question_plan_context"],
        "consensus_proposal": proposal,
        "consensus_proposal_sha256": canonical_sha256(proposal),
        "consensus_source_coordinates_checked": coordinates,
        "consensus_source_coordinates_sha256": canonical_sha256({"source_coordinates_checked": coordinates}),
        "handoff_state": "requires_source_bound_validation",
        "route_status": "abstain",
        "materialization_allowed": False,
        "source_contract": source_contract(),
    }
    handoff = tmp_path / "handoff.jsonl"
    _write_jsonl(handoff, [row])
    manifest = tmp_path / "handoff.manifest.json"
    manifest.write_text(json.dumps({"protocol": "route_coverage_consensus_handoff_v1", "materialization_allowed": False, "source_contract": source_contract(), "outputs": {"handoff": {"sha256": sha256_file(handoff)}}, "counts": {"consensus_candidate_count": 1}}), encoding="utf-8")
    return handoff, manifest


def test_source_validator_reopens_handoff_locator_without_materializing_route(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    source_path = tmp_path / "reports" / "ABC.txt"
    handoff, manifest = _handoff(tmp_path, locator=f"{source_path}#page=12")
    result = validate_consensus_handoff_sources(bundle_dir=bundle, handoff=handoff, handoff_manifest=manifest, output_dir=tmp_path / "out")
    assert result["counts"] == {"handoff_count": 1, "source_locatable_count": 1, "review_track_counts": {"question_plan_context": 1}}
    row = json.loads((tmp_path / "out" / "route_coverage_source_validation_v1.jsonl").read_text())
    assert row["validation_state"] == "source_locatable_non_materializable"
    assert row["route_status"] == "abstain"
    assert row["materialization_allowed"] is False
    assert row["source_coordinate_validation"][0]["v2_v3_coordinate_exists"] is True


def test_source_validator_rejects_unreopenable_locator_or_unsafe_handoff(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    handoff, manifest = _handoff(tmp_path, locator=f"{tmp_path}/reports/missing.txt#page=12")
    with pytest.raises(ValueError, match="absent from V2/V3"):
        validate_consensus_handoff_sources(bundle_dir=bundle, handoff=handoff, handoff_manifest=manifest, output_dir=tmp_path / "missing")
    source_path = tmp_path / "reports" / "ABC.txt"
    handoff, manifest = _handoff(tmp_path / "unsafe", locator=f"{source_path}#page=12")
    rows = [json.loads(line) for line in handoff.read_text().splitlines()]
    rows[0]["consensus_proposal"]["proposed_question_plan"] = {"internal_table_uid": "u1"}
    _write_jsonl(handoff, rows)
    payload = json.loads(manifest.read_text())
    payload["outputs"]["handoff"]["sha256"] = sha256_file(handoff)
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="violates its immutable contract|forbidden key"):
        validate_consensus_handoff_sources(bundle_dir=bundle, handoff=handoff, handoff_manifest=manifest, output_dir=tmp_path / "unsafe-out")
