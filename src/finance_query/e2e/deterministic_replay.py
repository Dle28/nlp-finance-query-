"""Public facade for the deterministic end-to-end verification workflow.

The canonical engine is colocated in :mod:`finance_query.e2e.pipeline`.  The
receipt protocol and filename remain stable so previously emitted artifacts
stay readable, without keeping a second runtime entrypoint.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .pipeline import (
    GROUNDED_E2E_PROTOCOL,
    GroundedE2EError,
    GroundedE2EInputs,
    load_inputs,
    run_grounded_e2e,
)


DETERMINISTIC_E2E_PROTOCOL = GROUNDED_E2E_PROTOCOL
DeterministicReplayError = GroundedE2EError
DeterministicReplayInputs = GroundedE2EInputs


def load_deterministic_replay_inputs(config_path: Path) -> DeterministicReplayInputs:
    """Load the explicit, hash-bound input manifest for an E2E replay."""
    return load_inputs(config_path)


def run_deterministic_replay(
    inputs: DeterministicReplayInputs, *, output_dir: Path
) -> dict[str, Any]:
    """Run exact binding, Decimal replay, authorization, and receipt emission."""
    return run_grounded_e2e(inputs, output_dir=output_dir)


def summarize_deterministic_replay(
    receipt: dict[str, Any], *, output_dir: Path
) -> dict[str, Any]:
    """Return the stable operator-facing summary without changing the receipt."""
    return {
        "system": "vifinqa-deterministic-e2e",
        "run_name": receipt["run_name"],
        "run_id": receipt["run_id"],
        "run_status": receipt["run_status"],
        "receipt": str(output_dir / "grounded_e2e_run_v1.json"),
        "binding_status_counts": receipt["outputs"]["bindings"]["counts"].get(
            "binding_packet_status_counts", {}
        ),
        "execution_status_counts": receipt["outputs"]["execution"]["counts"].get(
            "execution_status_counts", {}
        ),
        "authorization_counts": receipt["outputs"]["authorization"]["counts"],
        "technical_readiness": receipt.get(
            "technical_readiness",
            {
                "execution_replay_ready_count": receipt["outputs"]["execution"]
                ["counts"]
                .get("execution_status_counts", {})
                .get("execution_replay_ready", 0),
                "answer_authority": False,
                "legacy_receipt": True,
            },
        ),
        "answer_output_allowed": receipt.get("technical_readiness", {}).get(
            "answer_output_allowed", False
        ),
        "answer_count": receipt.get("technical_readiness", {}).get("answer_count", 0),
        "abstain_count": receipt.get("technical_readiness", {}).get("abstain_count", 0),
        "reproducibility": receipt["reproducibility"],
        "release_authorized": False,
    }
