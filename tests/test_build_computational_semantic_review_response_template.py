from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SEMANTIC = ROOT / "artifacts" / "research" / "computational_semantics_v1"
SPEC = importlib.util.spec_from_file_location(
    "build_computational_semantic_review_response_template",
    ROOT / "scripts" / "build_computational_semantic_review_response_template_v1.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


QUEUE_INFO = {
    "source_coordinate": (
        SEMANTIC / "source_review_v2" / "computational_semantic_source_review_queue_v1.jsonl",
        SEMANTIC / "source_review_v2" / "computational_semantic_source_review_queue_v1.manifest.json",
        8,
    ),
    "scope": (
        SEMANTIC / "scope_review_v2" / "computational_semantic_scope_review_queue_v1.jsonl",
        SEMANTIC / "scope_review_v2" / "computational_semantic_scope_review_queue_v1.manifest.json",
        7,
    ),
    "dimension": (
        SEMANTIC / "dimension_review_v1" / "computational_semantic_dimension_review_queue_v1.jsonl",
        SEMANTIC / "dimension_review_v1" / "computational_semantic_dimension_review_queue_v1.manifest.json",
        31,
    ),
}


@pytest.mark.parametrize("review_kind", ["source_coordinate", "scope", "dimension"])
def test_builds_blank_separate_response_templates(tmp_path: Path, review_kind: str) -> None:
    queue, queue_manifest, expected_count = QUEUE_INFO[review_kind]
    output = tmp_path / f"{review_kind}.jsonl"
    result = MODULE.build(
        queue=queue,
        queue_manifest=queue_manifest,
        review_kind=review_kind,
        reviewer_id="human-1",
        output=output,
        manifest_output=tmp_path / f"{review_kind}.manifest.json",
    )
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert result["response_count"] == expected_count
    assert hashlib.sha256(output.read_bytes()).hexdigest() == result["outputs"]["template"]["sha256"]
    assert all(row["decision"] is None for row in rows)
    assert all(row["is_blank_template"] is True for row in rows)
    assert all(row["materialization_allowed"] is False for row in rows)
    assert all(row["reviewer_id"] == "human-1" for row in rows)


def test_template_builder_rejects_overwrite_and_tampered_queue(tmp_path: Path) -> None:
    queue, queue_manifest, _ = QUEUE_INFO["source_coordinate"]
    output = tmp_path / "source.jsonl"
    output.write_text("exists", encoding="utf-8")
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        MODULE.build(
            queue=queue,
            queue_manifest=queue_manifest,
            review_kind="source_coordinate",
            reviewer_id="human-1",
            output=output,
            manifest_output=tmp_path / "source.manifest.json",
        )
    tampered = tmp_path / "tampered.jsonl"
    tampered.write_text(queue.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        MODULE.build(
            queue=tampered,
            queue_manifest=queue_manifest,
            review_kind="source_coordinate",
            reviewer_id="human-1",
            output=tmp_path / "tampered-template.jsonl",
            manifest_output=tmp_path / "tampered-template.manifest.json",
        )


def test_template_builder_accepts_component_source_queue_protocol(tmp_path: Path) -> None:
    queue = SEMANTIC.parent / "computational_semantics_v2" / "component_source_review_v1" / "computational_semantic_component_source_review_queue_v1.jsonl"
    queue_manifest = SEMANTIC.parent / "computational_semantics_v2" / "component_source_review_v1" / "computational_semantic_component_source_review_queue_v1.manifest.json"
    result = MODULE.build(
        queue=queue,
        queue_manifest=queue_manifest,
        review_kind="source_coordinate",
        reviewer_id="human-1",
        output=tmp_path / "component-source.jsonl",
        manifest_output=tmp_path / "component-source.manifest.json",
    )
    assert result["response_count"] == 3
    assert result["source_contract"]["materialization_allowed"] is False
