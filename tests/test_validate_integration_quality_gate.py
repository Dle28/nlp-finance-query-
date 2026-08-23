from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml


spec = importlib.util.spec_from_file_location(
    "validate_integration_quality_gate",
    Path(__file__).parents[1] / "scripts" / "validate_integration_quality_gate.py",
)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


def test_repository_policy_passes_static_fail_closed_gate() -> None:
    root = Path(__file__).parents[1]
    result = module.validate(
        policy_path=root / "configs" / "integration_quality_gate_v1.yaml",
        repository_root=root,
        policy_only=True,
    )
    assert result == {"status": "policy_only_pass", "artifact_validation": False}


def test_repository_artifacts_pass_full_integration_gate() -> None:
    root = Path(__file__).parents[1]
    result = module.validate(
        policy_path=root / "configs" / "integration_quality_gate_v1.yaml",
        repository_root=root,
    )
    assert result["status"] == "full_integration_gate_pass"
    assert result["artifact_validation"] is True


def test_policy_requires_gate_scoped_reviewer_authority(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    policy = yaml.safe_load((root / "configs" / "integration_quality_gate_v1.yaml").read_text())
    del policy["reviewer_authority_policy"]
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(policy))
    with pytest.raises(ValueError, match="reviewer_authority_policy"):
        module.validate(policy_path=path, repository_root=root, policy_only=True)


def test_policy_rejects_authority_file_that_grants_release(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    authority = yaml.safe_load((root / "configs" / "reviewer_authority_policy_v1.json").read_text())
    authority["excluded_authorities"]["release_authority_included"] = True
    authority_path = tmp_path / "authority.json"
    authority_path.write_text(json.dumps(authority))
    policy = yaml.safe_load((root / "configs" / "integration_quality_gate_v1.yaml").read_text())
    policy["reviewer_authority_policy"] = {
        "path": str(authority_path),
        "sha256": module.sha256_file(authority_path),
    }
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(yaml.safe_dump(policy))
    with pytest.raises(ValueError, match="widens or mislabels"):
        module.validate(policy_path=policy_path, repository_root=root, policy_only=True)


def test_policy_requires_human_verified_v5_section(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    policy = yaml.safe_load((root / "configs" / "integration_quality_gate_v1.yaml").read_text())
    del policy["grounded_v5_human_verified"]
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(policy))
    try:
        module.validate(policy_path=path, repository_root=root, policy_only=True)
    except ValueError as exc:
        assert "grounded_v5_human_verified" in str(exc)
    else:
        raise AssertionError("quality gate must bind the human-verified V5 replay")


def test_policy_requires_v5_campaign_review_section(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    policy = yaml.safe_load((root / "configs" / "integration_quality_gate_v1.yaml").read_text())
    del policy["grounded_campaign_review_v5"]
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(policy))
    try:
        module.validate(policy_path=path, repository_root=root, policy_only=True)
    except ValueError as exc:
        assert "grounded_campaign_review_v5" in str(exc)
    else:
        raise AssertionError("quality gate must bind the pending V5 campaign review")


def test_policy_requires_v7_document_role_replay(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    policy = yaml.safe_load((root / "configs" / "integration_quality_gate_v1.yaml").read_text())
    del policy["grounded_v7_document_role"]
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(policy))
    with pytest.raises(ValueError, match="grounded_v7_document_role"):
        module.validate(policy_path=path, repository_root=root, policy_only=True)


def test_policy_requires_chatgpt_operation_graph_review(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    policy = yaml.safe_load((root / "configs" / "integration_quality_gate_v1.yaml").read_text())
    del policy["operation_graph_chatgpt_review_v1"]
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(policy))
    with pytest.raises(ValueError, match="operation_graph_chatgpt_review_v1"):
        module.validate(policy_path=path, repository_root=root, policy_only=True)


def test_policy_requires_chatgpt_route_context_review(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    policy = yaml.safe_load((root / "configs" / "integration_quality_gate_v1.yaml").read_text())
    del policy["route_context_chatgpt_review_v1"]
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(policy))
    with pytest.raises(ValueError, match="route_context_chatgpt_review_v1"):
        module.validate(policy_path=path, repository_root=root, policy_only=True)


@pytest.mark.parametrize(
    "section",
    [
        "cross_entity_operand_packets_v1",
        "cross_entity_operand_chatgpt_review_v1",
        "cross_entity_subtract_materialization_v1",
    ],
)
def test_policy_requires_cross_entity_operand_lane(tmp_path: Path, section: str) -> None:
    root = Path(__file__).parents[1]
    policy = yaml.safe_load((root / "configs" / "integration_quality_gate_v1.yaml").read_text())
    del policy[section]
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(policy))
    with pytest.raises(ValueError, match=section):
        module.validate(policy_path=path, repository_root=root, policy_only=True)


@pytest.mark.parametrize(
    "section",
    [
        "cross_entity_binding_chatgpt_promotion_q750_v1",
        "grounded_v10_cross_entity",
        "grounded_campaign_review_v10",
        "grounded_campaign_chatgpt_audit_v10",
    ],
)
def test_policy_requires_human_equivalent_v10_campaign_lane(tmp_path: Path, section: str) -> None:
    root = Path(__file__).parents[1]
    policy = yaml.safe_load((root / "configs" / "integration_quality_gate_v1.yaml").read_text())
    del policy[section]
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(policy))
    with pytest.raises(ValueError, match=section):
        module.validate(policy_path=path, repository_root=root, policy_only=True)


@pytest.mark.parametrize(
    "section",
    [
        "cross_entity_operand_packets_q746_v2",
        "cross_entity_operand_dual_chatgpt_adjudication_q746_v2",
        "cross_entity_subtract_materialization_q746_v2",
        "cross_entity_binding_chatgpt_promotion_q746_q750_v2",
        "grounded_v11_dual_review_cross_entity",
        "grounded_campaign_review_v11",
        "grounded_campaign_chatgpt_audit_v11",
    ],
)
def test_policy_requires_dual_review_v11_campaign_lane(tmp_path: Path, section: str) -> None:
    root = Path(__file__).parents[1]
    policy = yaml.safe_load((root / "configs" / "integration_quality_gate_v1.yaml").read_text())
    del policy[section]
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(policy))
    with pytest.raises(ValueError, match=section):
        module.validate(policy_path=path, repository_root=root, policy_only=True)


@pytest.mark.parametrize("section", ["navigation_review_evidence_v1", "navigation_chatgpt_review_v1"])
def test_policy_requires_navigation_review_sections(tmp_path: Path, section: str) -> None:
    root = Path(__file__).parents[1]
    policy = yaml.safe_load((root / "configs" / "integration_quality_gate_v1.yaml").read_text())
    del policy[section]
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(policy))
    with pytest.raises(ValueError, match=section):
        module.validate(policy_path=path, repository_root=root, policy_only=True)


@pytest.mark.parametrize("section", ["grounded_campaign_review_v7", "grounded_campaign_chatgpt_audit_v7"])
def test_policy_requires_v7_campaign_audit_sections(tmp_path: Path, section: str) -> None:
    root = Path(__file__).parents[1]
    policy = yaml.safe_load((root / "configs" / "integration_quality_gate_v1.yaml").read_text())
    del policy[section]
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(policy))
    with pytest.raises(ValueError, match=section):
        module.validate(policy_path=path, repository_root=root, policy_only=True)


@pytest.mark.parametrize(
    "section",
    [
        "semantic_binding_chatgpt_correction_q702_v2",
        "grounded_v8_semantic_correction",
        "grounded_campaign_review_v8",
        "grounded_campaign_chatgpt_audit_v8",
    ],
)
def test_policy_requires_v8_semantic_correction_sections(tmp_path: Path, section: str) -> None:
    root = Path(__file__).parents[1]
    policy = yaml.safe_load((root / "configs" / "integration_quality_gate_v1.yaml").read_text())
    del policy[section]
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(policy))
    with pytest.raises(ValueError, match=section):
        module.validate(policy_path=path, repository_root=root, policy_only=True)


def test_policy_rejects_hierarchy_enabled_default(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    policy = yaml.safe_load((root / "configs" / "integration_quality_gate_v1.yaml").read_text())
    policy["safety_defaults"]["hierarchy_rrf_enabled"] = True
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(policy))
    try:
        module.validate(policy_path=path, repository_root=root, policy_only=True)
    except ValueError as exc:
        assert "disabled by default" in str(exc)
    else:
        raise AssertionError("unsafe hierarchy default must fail")
