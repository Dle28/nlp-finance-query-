#!/usr/bin/env python3
"""Verify hashes, contracts and deterministic replay of a prepared model job."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.active_learning_models import MODEL_JOB_PROTOCOL, build_model_job  # noqa: E402
from finance_query.evidence_closure import load_json, load_jsonl, sha256_file  # noqa: E402


def verify(path: Path) -> dict[str, object]:
    manifest = load_json(path)
    if manifest.get("protocol") != MODEL_JOB_PROTOCOL or manifest.get("status") != "PREPARED_GPU_EXECUTION_NOT_RUN":
        raise ValueError("invalid model job manifest")
    if manifest.get("chatgpt_in_model_graph") is not False or manifest.get("training_eligible") is not False:
        raise ValueError("unsafe model job boundary")
    for group in ("inputs", "outputs"):
        for name, record in (manifest.get(group) or {}).items():
            if not isinstance(record, Mapping):
                raise ValueError(f"invalid {group} record {name}")
            item = Path(str(record.get("path") or ""))
            if not item.is_file() or sha256_file(item) != record.get("sha256"):
                raise ValueError(f"{group} hash mismatch: {name}")
    for name in ("packets", "proposer_requests", "critic_requests"):
        rows = load_jsonl(Path(manifest["outputs"][name]["path"]))
        if len(rows) != manifest["packet_count"]:
            raise ValueError(f"row count mismatch: {name}")
    with tempfile.TemporaryDirectory(prefix="verify-active-model-job-") as temporary:
        rebuilt = Path(temporary) / "rebuilt"
        build_model_job(active_cycle_manifest_path=Path(manifest["inputs"]["active_cycle_manifest"]["path"]), model_policy_path=Path(manifest["inputs"]["model_policy"]["path"]), output_dir=rebuilt)
        for name, record in manifest["outputs"].items():
            candidate = rebuilt / Path(record["path"]).name
            if sha256_file(candidate) != record["sha256"]:
                raise ValueError(f"deterministic replay mismatch: {name}")
    return {"status": "VERIFIED_ACTIVE_LEARNING_MODEL_JOB_V1", "packet_count": manifest["packet_count"], "manifest_sha256": sha256_file(path), "gpu_execution_status": "not_run", "release_status": "blocked"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.manifest.resolve()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
