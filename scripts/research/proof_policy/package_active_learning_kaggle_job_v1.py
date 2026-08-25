#!/usr/bin/env python3
"""Create a portable, hash-verified Kaggle input bundle from a prepared job."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Mapping

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.proof_policy.active_learning_models import MODEL_JOB_PROTOCOL  # noqa: E402
from finance_query.research.proof_policy.evidence_closure import load_json, sha256_file  # noqa: E402
from finance_query.research.llm.diagnostic_lane import (  # noqa: E402
    LLM_DIAGNOSTIC_BATCH_PROTOCOL,
)


def _validate_source_job(source: Mapping[str, object]) -> None:
    identity = (source.get("protocol"), source.get("status"))
    allowed = {
        (MODEL_JOB_PROTOCOL, "PREPARED_GPU_EXECUTION_NOT_RUN"),
        (LLM_DIAGNOSTIC_BATCH_PROTOCOL, "PREPARED_PROPOSER_DIAGNOSTIC_NOT_RUN"),
    }
    if identity not in allowed:
        raise ValueError("invalid prepared active-learning or proposer diagnostic job")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    source = load_json(args.job_manifest.resolve())
    _validate_source_job(source)
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{args.output_dir.name}.tmp-", dir=args.output_dir.parent))
    try:
        portable_outputs = {}
        source_base = args.job_manifest.resolve().parent
        for name, record in source["outputs"].items():
            if not isinstance(record, Mapping):
                raise ValueError(f"invalid output record {name}")
            path = Path(str(record.get("path") or ""))
            if not path.is_absolute():
                path = source_base / path
            if not path.is_file() or sha256_file(path) != record.get("sha256"):
                raise ValueError(f"source output hash mismatch: {name}")
            destination = staging / path.name
            shutil.copyfile(path, destination)
            if sha256_file(destination) != record["sha256"]:
                raise ValueError(f"copied output hash mismatch: {name}")
            portable_outputs[name] = {"path": destination.name, "sha256": record["sha256"]}
        portable = {
            **{key: value for key, value in source.items() if key not in {"inputs", "outputs", "definition"}},
            "source_manifest_sha256": sha256_file(args.job_manifest),
            "outputs": portable_outputs,
            "portable_bundle": True,
            "training_eligible": False,
            "certification_allowed": False,
            "release_status": "blocked",
        }
        manifest_path = staging / "kaggle_job.manifest.json"
        manifest_path.write_text(json.dumps(portable, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with manifest_path.open("rb") as handle:
            os.fsync(handle.fileno())
        bundle_manifest = {
            "protocol": "vifinqa_active_learning_kaggle_bundle_v1",
            "files": {
                path.name: {"sha256": sha256_file(path), "bytes": path.stat().st_size}
                for path in sorted(staging.iterdir()) if path.is_file()
            },
            "gpu_execution_status": "not_run",
            "submission_eligible": False,
        }
        bundle_path = staging / "bundle.manifest.json"
        bundle_path.write_text(json.dumps(bundle_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with bundle_path.open("rb") as handle:
            os.fsync(handle.fileno())
        staging.rename(args.output_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    print(args.output_dir / "bundle.manifest.json")


if __name__ == "__main__":
    main()
