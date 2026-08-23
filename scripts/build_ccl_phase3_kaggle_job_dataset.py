#!/usr/bin/env python3
"""Materialize the three immutable CCL Phase 3 job files as a Kaggle dataset.

This is a copy-only hand-off. It verifies every declared job output hash and
does not include a model response, raw report, label or source credential.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any


PROTOCOL = "kaggle_ccl_phase3_job_dataset_v1"
REQUIRED_FILES = (
    "bakeoff_job_manifest.json",
    "llm_bakeoff_requests_v1.jsonl",
    "llm_proposal_schema_v1.json",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_dataset(*, job_dir: Path, output_dir: Path) -> dict[str, Any]:
    job_dir = job_dir.resolve()
    manifest_path = job_dir / "bakeoff_job_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("protocol") != "vifinqa_ccl_phase3_bakeoff_v1"
        or manifest.get("run_status") != "prepared_phase_3_inference_not_executed"
        or manifest.get("training_eligible") is not False
        or manifest.get("model_execution_recorded") is not False
    ):
        raise ValueError("job manifest is not a non-promotable prepared CCL Phase 3 job")
    if output_dir.exists():
        raise FileExistsError("Kaggle dataset output directory must be new")
    output_dir.mkdir(parents=True)
    outputs = manifest.get("outputs") or {}
    copied: dict[str, dict[str, Any]] = {}
    for name in REQUIRED_FILES:
        source = job_dir / name
        if not source.is_file():
            raise FileNotFoundError(source)
        # The job manifest is intentionally non-self-referential; the other
        # two files must match the hashes it declares.
        if name != manifest_path.name:
            expected = (outputs.get(name) or {}).get("sha256")
            if expected != sha256_file(source):
                raise ValueError(f"job manifest checksum mismatch: {name}")
        destination = output_dir / name
        shutil.copyfile(source, destination)
        copied[name] = {"sha256": sha256_file(destination), "bytes": destination.stat().st_size}
    contract = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "source_job_manifest_sha256": sha256_file(manifest_path),
        "files": copied,
        "contains_model_output": False,
        "contains_raw_reports": False,
        "contains_labels": False,
        "contains_credentials": False,
        "training_eligible": False,
        "promotion_allowed": False,
    }
    (output_dir / "DATASET_CONTENTS.json").write_text(
        json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return contract


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_dataset(job_dir=args.job_dir, output_dir=args.output_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
