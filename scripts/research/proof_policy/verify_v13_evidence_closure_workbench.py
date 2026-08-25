#!/usr/bin/env python3
"""Verify and deterministically replay a V13 evidence-closure workbench."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.proof_policy.evidence_closure import PROTOCOL, build_workbench, load_jsonl, sha256_file  # noqa: E402


FORBIDDEN_VALUE_KEYS = {"answer", "answer_decimal", "raw_value", "parsed_value", "numeric_value"}


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def _assert_no_answer_values(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in FORBIDDEN_VALUE_KEYS:
                raise ValueError(f"closure workbench leaks forbidden key {key}")
            _assert_no_answer_values(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_answer_values(item)


def verify(manifest_path: Path) -> dict[str, Any]:
    manifest = _json(manifest_path)
    if manifest.get("protocol") != PROTOCOL:
        raise ValueError("unexpected closure workbench protocol")
    if (manifest.get("release_decision") or {}).get("status") != "blocked":
        raise ValueError("closure workbench must remain blocked")
    inputs = manifest.get("inputs") or {}
    for name, record in inputs.items():
        if not isinstance(record, Mapping):
            raise ValueError(f"invalid input record {name}")
        path = Path(str(record.get("path") or ""))
        if not path.is_file() or sha256_file(path) != record.get("sha256"):
            raise ValueError(f"input hash mismatch: {name}")
    definition = manifest.get("definition_bundle") or {}
    implementation = Path(str(definition.get("implementation_path") or ""))
    if not implementation.is_file() or sha256_file(implementation) != definition.get("implementation_sha256"):
        raise ValueError("closure implementation source hash mismatch")
    outputs = manifest.get("outputs") or {}
    counts = manifest.get("counts") or {}
    for name, record in outputs.items():
        if not isinstance(record, Mapping):
            raise ValueError(f"invalid output record {name}")
        path = Path(str(record.get("path") or ""))
        if not path.is_file() or sha256_file(path) != record.get("sha256"):
            raise ValueError(f"output hash mismatch: {name}")
        if name != "summary":
            rows = load_jsonl(path)
            if len(rows) != counts.get(name):
                raise ValueError(f"output count mismatch: {name}")
            for row in rows:
                _assert_no_answer_values(row)
                contract = row.get("source_contract") or {}
                if contract.get("submission_eligible") is not False or contract.get("release_authorized") is not False:
                    raise ValueError(f"output contract may promote: {name}")
    config = Path(str((inputs.get("config") or {}).get("path") or ""))
    if not config.is_file():
        raise ValueError("closure config input is missing")
    with tempfile.TemporaryDirectory(prefix="v13-closure-verify-") as temporary:
        rebuilt_dir = Path(temporary) / "rebuilt"
        rebuilt = build_workbench(config_path=config, output_dir=rebuilt_dir)
        for name, record in outputs.items():
            expected = str(record.get("sha256") or "")
            filename = Path(str(record.get("path") or "")).name
            rebuilt_path = rebuilt_dir / filename
            if name == "summary" or name in rebuilt.get("outputs", {}):
                if not rebuilt_path.is_file() or sha256_file(rebuilt_path) != expected:
                    raise ValueError(f"deterministic replay mismatch: {name}")
    return {
        "status": "VERIFIED_V13_EVIDENCE_CLOSURE_WORKBENCH",
        "manifest_sha256": sha256_file(manifest_path),
        "release_status": "blocked",
        "outputs_replayed": True,
        "queue_counts": counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.manifest.resolve()), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
