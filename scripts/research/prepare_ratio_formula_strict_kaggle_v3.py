#!/usr/bin/env python3
"""Stage corrected strict exact-ratio v3 for Kaggle CPU."""

from __future__ import annotations

import json
import os
import shutil
import tarfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BASE_STAGING = ROOT / (
    "artifacts/kaggle_upload/"
    "dungle2810_vifinqa_exact_ratio_formula_cpu_v1_20260830"
)
BASE_RUNTIME = BASE_STAGING / "runtime"
BASE_TREATMENT_NOTEBOOK = BASE_STAGING / "treatment_kernel/main.ipynb"
V2_RUNNER = ROOT / "scripts/research/run_ratio_formula_variant_v2.py"
V3_RUNNER = ROOT / "scripts/research/run_ratio_formula_variant_v3.py"

RUNTIME_SLUG = "dungle2810/vifinqa-exact-ratio-strict-v3-20260830"
KERNEL_SLUG = "dungle2810/vifinqa-exact-ratio-strict-cpu-v3-20260830"
PROTOCOL = "vifinqa_exact_ratio_formula_v3"
WORK_NAME = "vifinqa_exact_ratio_strict_cpu_v3"
FULL_ASSET_SLUG = "dungle2810/vifinqa-full-dense-assets-v1-20260826"
COORDINATE_SLUG = "dungle2810/vifinqa-full-corpus-coordinate-map-v1-20260829"


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    if "__pycache__" in Path(info.name).parts or info.name.endswith((".pyc", ".pyo")):
        return None
    return info


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _copy_runtime(runtime_dir: Path) -> None:
    shutil.copytree(BASE_RUNTIME, runtime_dir)
    old_archive = runtime_dir / "runtime_code.tar"
    new_archive = runtime_dir / "runtime_code.v3.tar"
    with tarfile.open(old_archive, mode="r") as source, tarfile.open(
        new_archive, mode="w"
    ) as target:
        for info in source:
            if not _tar_filter(info):
                continue
            if info.isfile():
                payload = source.extractfile(info)
                if payload is None:
                    raise RuntimeError(f"cannot read archived member {info.name}")
                target.addfile(info, payload)
                payload.close()
            else:
                target.addfile(info)
        target.add(V2_RUNNER, arcname="scripts/research/run_ratio_formula_variant_v2.py")
        target.add(V3_RUNNER, arcname="scripts/research/run_ratio_formula_variant_v3.py")
    os.replace(new_archive, old_archive)


def _treatment_notebook() -> dict[str, Any]:
    if not BASE_TREATMENT_NOTEBOOK.exists():
        raise FileNotFoundError(BASE_TREATMENT_NOTEBOOK)
    notebook = json.loads(BASE_TREATMENT_NOTEBOOK.read_text(encoding="utf-8"))
    code = "".join(
        "".join(cell.get("source", []))
        for cell in notebook.get("cells", [])
        if cell.get("cell_type") == "code"
    )
    replacements = {
        "run_ratio_formula_variant_v1.py": "run_ratio_formula_variant_v3.py",
        "vifinqa-primary-exact-ratio-formula-v1-20260830": RUNTIME_SLUG.split(
            "/", 1
        )[1],
        "vifinqa_exact_ratio_formula_cpu_v1": WORK_NAME,
        "dungle2810/vifinqa-exact-ratio-formula-cpu-v1-20260830": KERNEL_SLUG,
        "vifinqa_exact_ratio_formula_treatment_v1": PROTOCOL,
    }
    for old, new in replacements.items():
        code = code.replace(old, new)
    if "run_ratio_formula_variant_v3.py" not in code:
        raise AssertionError("v3 runner was not wired into notebook")
    if PROTOCOL not in code:
        raise AssertionError("v3 protocol was not wired into notebook")
    for cell in notebook.get("cells", []):
        if cell.get("cell_type") == "code":
            cell["source"] = [line + "\n" for line in code.splitlines()]
        elif cell.get("cell_type") == "markdown":
            cell["source"] = [
                "# ViFinQA exact ratio strict corrected treatment (CPU)\n",
                "\n",
                "This treatment adds strict row identity and fail-closed scope gates.\n",
            ]
    notebook.setdefault("metadata", {})["variant_protocol"] = PROTOCOL
    notebook["metadata"]["kernel_slug"] = KERNEL_SLUG
    return notebook


def stage(output_root: Path) -> dict[str, str]:
    output_root = output_root.expanduser().resolve()
    if output_root.exists():
        raise FileExistsError(f"refusing to overwrite staging directory: {output_root}")
    for path in (BASE_RUNTIME, V2_RUNNER, V3_RUNNER):
        if not path.exists():
            raise FileNotFoundError(path)
    output_root.mkdir(parents=True)
    runtime_dir = output_root / "runtime"
    _copy_runtime(runtime_dir)
    _write_json(
        runtime_dir / "dataset-metadata.json",
        {
            "title": "ViFinQA Exact Ratio Strict Runtime V3 20260830",
            "id": RUNTIME_SLUG,
            "licenses": [{"name": "other"}],
            "description": (
                "Immutable v1 canonical runtime plus corrected strict row-identity "
                "and fail-closed scope treatment adapter."
            ),
        },
    )
    treatment_dir = output_root / "treatment_kernel"
    treatment_dir.mkdir()
    _write_json(treatment_dir / "main.ipynb", _treatment_notebook())
    _write_json(
        treatment_dir / "kernel-metadata.json",
        {
            "id": KERNEL_SLUG,
            "title": "ViFinQA Exact Ratio Strict CPU V3 20260830",
            "code_file": "main.ipynb",
            "language": "python",
            "kernel_type": "notebook",
            "is_private": True,
            "enable_gpu": False,
            "enable_tpu": False,
            "enable_internet": True,
            "dataset_sources": [RUNTIME_SLUG, FULL_ASSET_SLUG, COORDINATE_SLUG],
        },
    )
    return {
        "output_root": str(output_root),
        "runtime_dir": str(runtime_dir),
        "treatment_kernel_dir": str(treatment_dir),
        "runtime_slug": RUNTIME_SLUG,
        "kernel_slug": KERNEL_SLUG,
        "protocol": PROTOCOL,
        "control_artifact": str(
            ROOT
            / "artifacts/runs/"
            "vifinqa_exact_ratio_formula_control_cpu_v1_20260830/kaggle_output/submission.zip"
        ),
    }


def main() -> None:
    output_root = ROOT / (
        "artifacts/kaggle_upload/"
        "dungle2810_vifinqa_exact_ratio_strict_cpu_v3_20260830"
    )
    print(json.dumps(stage(output_root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
