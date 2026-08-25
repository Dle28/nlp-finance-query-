"""Public contract for the deterministic E2E operator interface."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from finance_query import cli
from finance_query.e2e import DETERMINISTIC_E2E_PROTOCOL, summarize_deterministic_replay
from finance_query.e2e.pipeline import GROUNDED_E2E_PROTOCOL


pytestmark = pytest.mark.e2e


def test_public_e2e_protocol_preserves_the_hash_bound_implementation() -> None:
    assert DETERMINISTIC_E2E_PROTOCOL == GROUNDED_E2E_PROTOCOL
    summary = summarize_deterministic_replay(
        {
            "run_name": "deterministic-e2e",
            "run_id": "a" * 64,
            "run_status": "complete_research_only",
            "reproducibility": {"bindings_match": True},
            "outputs": {
                "bindings": {"counts": {"binding_packet_status_counts": {"BLOCKED": 1}}},
                "execution": {"counts": {"execution_status_counts": {"ABSTAIN": 1}}},
                "authorization": {"counts": {"answer_certificate_status_counts": {"ABSTAIN": 1}}},
            },
        },
        output_dir=Path("artifacts/runs/example"),
    )
    assert summary["system"] == "vifinqa-deterministic-e2e"
    assert summary["release_authorized"] is False


def test_cli_exposes_the_short_e2e_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "finance-query",
            "run-e2e",
            "--config",
            "configs/e2e/deterministic_replay_v1_locked.yaml",
            "--output-dir",
            "artifacts/runs/example",
        ],
    )
    args = cli.parse_args()
    assert args.command == "run-e2e"


def test_cli_does_not_keep_the_removed_compatibility_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "finance-query",
            "run-grounded-e2e",
            "--config",
            "configs/e2e/deterministic_replay_v1_locked.yaml",
            "--output-dir",
            "artifacts/runs/example",
        ],
    )
    with pytest.raises(SystemExit):
        cli.parse_args()
