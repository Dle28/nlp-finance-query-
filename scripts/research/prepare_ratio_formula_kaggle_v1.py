#!/usr/bin/env python3
"""Stage same-snapshot Kaggle CPU treatment/control runs for exact ratios."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tarfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BASE_RUNTIME = (
    ROOT
    / "artifacts/kaggle_upload/dungle2810_vifinqa_arg_extreme_period_cpu_v1_20260830/runtime"
)
RATIO_RUNNER = ROOT / "scripts/research/run_ratio_formula_variant_v1.py"

RUNTIME_SLUG = "dungle2810/vifinqa-primary-exact-ratio-formula-v1-20260830"
TREATMENT_KERNEL_SLUG = "dungle2810/vifinqa-exact-ratio-formula-cpu-v1-20260830"
CONTROL_KERNEL_SLUG = "dungle2810/vifinqa-exact-ratio-formula-cpu-ctrl-v1-20260830"
FULL_ASSET_SLUG = "dungle2810/vifinqa-full-dense-assets-v1-20260826"
COORDINATE_SLUG = "dungle2810/vifinqa-full-corpus-coordinate-map-v1-20260829"


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    if "__pycache__" in Path(info.name).parts or info.name.endswith((".pyc", ".pyo")):
        return None
    return info


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _copy_runtime_with_ratio_runner(runtime_dir: Path) -> None:
    shutil.copytree(BASE_RUNTIME, runtime_dir)
    old_archive = runtime_dir / "runtime_code.tar"
    new_archive = runtime_dir / "runtime_code.with_ratio.tar"
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
        target.add(
            RATIO_RUNNER,
            arcname="scripts/research/run_ratio_formula_variant_v1.py",
            filter=_tar_filter,
        )
    os.replace(new_archive, old_archive)


def _notebook(
    *, runtime_slug: str, kernel_slug: str, treatment: bool
) -> dict[str, Any]:
    if treatment:
        title = "# ViFinQA exact ratio formula treatment (CPU)"
        description = (
            "This treatment enables exact typed two-operand ratio replay.\n"
        )
        work_name = "vifinqa_exact_ratio_formula_cpu_v1"
        runner_line = "runner = RUNTIME_ROOT / 'scripts/research/run_ratio_formula_variant_v1.py'"
        runner_args = [
            "    '--typed-plans', str(typed_plans),",
        ]
        marker = """
report['exact_ratio_formula_control'] = {
    'protocol': 'vifinqa_exact_ratio_formula_treatment_v1',
    'control_protocol': 'vifinqa_exact_ratio_formula_control_v1',
    'exact_ratio_adapter_loaded': True,
    'promotion_allowed': False,
}
"""
        protocol = "vifinqa_exact_ratio_formula_treatment_v1"
    else:
        title = "# ViFinQA exact ratio formula same-snapshot control (CPU)"
        description = (
            "This control calls the canonical builder without the ratio adapter.\n"
        )
        work_name = "vifinqa_exact_ratio_formula_control_v1"
        runner_line = "runner = RUNTIME_ROOT / 'scripts/e2e/build_competition_submission_v1.py'"
        runner_args = []
        marker = """
report['exact_ratio_formula_control'] = {
    'protocol': 'vifinqa_exact_ratio_formula_control_v1',
    'treatment_protocol': 'vifinqa_exact_ratio_formula_treatment_v1',
    'exact_ratio_adapter_loaded': False,
    'promotion_allowed': False,
}
"""
        protocol = "vifinqa_exact_ratio_formula_control_v1"

    source = r'''from pathlib import Path
import json
import os
import shutil
import subprocess
import sys

INPUT = Path('/kaggle/input')

def find_dataset(slug):
    direct = INPUT / slug
    if direct.exists():
        return direct
    matches = [path for path in INPUT.rglob(slug) if path.is_dir()]
    if len(matches) == 1:
        return matches[0]
    raise FileNotFoundError(f'cannot locate dataset {slug}; matches={matches}')

RUNTIME_INPUT = find_dataset('__RUNTIME_DATASET_SLUG__')
FULL_ASSET_INPUT = find_dataset('vifinqa-full-dense-assets-v1-20260826')
COORDINATE_INPUT = find_dataset('vifinqa-full-corpus-coordinate-map-v1-20260829')
WORK = Path('/kaggle/working/__WORK_NAME__')
WORK.mkdir(parents=True, exist_ok=True)
RUNTIME_ROOT = WORK / 'runtime'
RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
runtime_archive = RUNTIME_INPUT / 'runtime_code.tar'
runtime_tree = RUNTIME_INPUT / 'runtime_code'
if runtime_archive.exists():
    shutil.unpack_archive(str(runtime_archive), RUNTIME_ROOT)
elif runtime_tree.is_dir():
    shutil.copytree(runtime_tree, RUNTIME_ROOT, dirs_exist_ok=True)
else:
    raise FileNotFoundError(
        f'cannot locate runtime_code.tar or runtime_code/ under {RUNTIME_INPUT}'
    )

print('runtime input:', RUNTIME_INPUT)
print('full asset input:', FULL_ASSET_INPUT)
print('coordinate input:', COORDINATE_INPUT)
print('cuda visible:', os.environ.get('CUDA_VISIBLE_DEVICES', '<unset>'))
subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'pandas', 'pyyaml'], check=True)

__RUNNER_LINE__
typed_plans = RUNTIME_INPUT / 'typed_operand_plans_v1.jsonl'
structured_tables = FULL_ASSET_INPUT / 'full_table_assets_v1.jsonl'
source_line_map = COORDINATE_INPUT / 'source_line_map_full_v1.json'
output_dir = WORK / 'submission'
for required in (runner, structured_tables, source_line_map):
    assert required.exists(), required

cmd = [
    sys.executable, str(runner),
__RUNNER_ARGS__
    '--questions', str(RUNTIME_INPUT / 'questions.jsonl'),
    '--bundle', str(RUNTIME_INPUT),
    '--structured-tables', str(structured_tables),
    '--replay', str(RUNTIME_INPUT / 'replay.jsonl'),
    '--source-line-map', str(source_line_map),
    '--require-source-line-map',
    '--research-candidate', str(RUNTIME_INPUT / 'period_candidates.jsonl'),
    '--research-candidate', str(RUNTIME_INPUT / 'multicol_candidates.jsonl'),
    '--route-overlay', str(RUNTIME_INPUT / 'route_overlay.jsonl'),
    '--model-answer-candidate', str(RUNTIME_INPUT / 'model_answers.jsonl'),
    '--candidate-validity-model', str(RUNTIME_INPUT / 'review_calibrator.joblib'),
    '--direct-evidence-replay', str(RUNTIME_INPUT / 'direct_evidence_replay_all_ready_v1.jsonl'),
    '--direct-replay-include-provisional',
    '--disable-source-first-report-year-neighbor',
    '--disable-source-first-cross-entity',
    '--verification-candidate-limit', '8',
    '--output', str(output_dir),
]

env = os.environ.copy()
env['PYTHONPATH'] = str(RUNTIME_ROOT / 'src') + os.pathsep + env.get('PYTHONPATH', '')
run = subprocess.run(cmd, env=env, text=True, capture_output=True)
print(run.stdout[-30000:])
if run.returncode:
    print(run.stderr[-30000:])
    raise RuntimeError(f'ratio formula builder failed with exit code {run.returncode}')
if run.stderr:
    print(run.stderr[-12000:])

report_path = output_dir / 'build_report.json'
report = json.loads(report_path.read_text())
__MARKER__
report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({
    'protocol': '__PROTOCOL__',
    'kernel': '__KERNEL_SLUG__',
    'question_count': report.get('question_count'),
    'predicted_questions': report.get('predicted_questions'),
    'nonzero_answers': report.get('nonzero_answers'),
    'confidence_tiers': report.get('confidence_tiers'),
    'validation': report.get('validation'),
    'verification': report.get('verification'),
    'implementation': report.get('implementation'),
}, ensure_ascii=False, indent=2))

assert report.get('question_count') == 1012
assert report.get('validation', {}).get('valid') is True
assert report.get('validation', {}).get('records') == 1012
assert report.get('validation', {}).get('queries_replayed') == 1012
assert report.get('validation', {}).get('errors') == []
assert (output_dir / 'submission.json').exists()
zip_path = output_dir.with_suffix('.zip')
assert zip_path.exists(), zip_path
shutil.copy2(zip_path, Path('/kaggle/working/submission.zip'))
print(json.dumps({'submission_zip': str(zip_path), 'zip_bytes': zip_path.stat().st_size}, indent=2))
'''
    runner_args_text = "\n".join(runner_args)
    source = source.replace("__RUNTIME_DATASET_SLUG__", runtime_slug.split("/", 1)[-1])
    source = source.replace("__WORK_NAME__", work_name)
    source = source.replace("__RUNNER_LINE__", runner_line)
    source = source.replace("__RUNNER_ARGS__", runner_args_text)
    source = source.replace("__MARKER__", marker.strip())
    source = source.replace("__PROTOCOL__", protocol)
    source = source.replace("__KERNEL_SLUG__", kernel_slug)
    return {
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [title + "\n", "\n", description],
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": [line + "\n" for line in source.splitlines()],
            },
        ],
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python", "version": "3.11"},
            "variant_protocol": protocol,
            "kernel_slug": kernel_slug,
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def _kernel_dir(
    root: Path,
    *,
    kernel_slug: str,
    title: str,
    treatment: bool,
) -> Path:
    directory = root / ("treatment_kernel" if treatment else "control_kernel")
    directory.mkdir(parents=True)
    notebook = _notebook(
        runtime_slug=RUNTIME_SLUG,
        kernel_slug=kernel_slug,
        treatment=treatment,
    )
    (directory / "main.ipynb").write_text(
        json.dumps(notebook, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_json(
        directory / "kernel-metadata.json",
        {
            "id": kernel_slug,
            "title": title,
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
    return directory


def stage(output_root: Path) -> dict[str, str]:
    output_root = output_root.expanduser().resolve()
    if output_root.exists():
        raise FileExistsError(f"refusing to overwrite staging directory: {output_root}")
    if not BASE_RUNTIME.exists():
        raise FileNotFoundError(BASE_RUNTIME)
    if not RATIO_RUNNER.exists():
        raise FileNotFoundError(RATIO_RUNNER)
    runtime_dir = output_root / "runtime"
    _copy_runtime_with_ratio_runner(runtime_dir)
    _write_json(
        runtime_dir / "dataset-metadata.json",
        {
            "title": "ViFinQA Exact Ratio Formula Runtime V1 20260830",
            "id": RUNTIME_SLUG,
            "licenses": [{"name": "other"}],
            "description": (
                "Immutable runtime snapshot for an exact typed two-operand ratio treatment/control."
            ),
        },
    )
    treatment_dir = _kernel_dir(
        output_root,
        kernel_slug=TREATMENT_KERNEL_SLUG,
        title="ViFinQA Exact Ratio Formula CPU V1 20260830",
        treatment=True,
    )
    control_dir = _kernel_dir(
        output_root,
        kernel_slug=CONTROL_KERNEL_SLUG,
        title="ViFinQA Exact Ratio Formula CPU Ctrl V1 20260830",
        treatment=False,
    )
    return {
        "output_root": str(output_root),
        "runtime_dir": str(runtime_dir),
        "treatment_kernel_dir": str(treatment_dir),
        "control_kernel_dir": str(control_dir),
        "runtime_slug": RUNTIME_SLUG,
        "treatment_kernel_slug": TREATMENT_KERNEL_SLUG,
        "control_kernel_slug": CONTROL_KERNEL_SLUG,
        "full_asset_slug": FULL_ASSET_SLUG,
        "coordinate_slug": COORDINATE_SLUG,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT
        / "artifacts/kaggle_upload/dungle2810_vifinqa_exact_ratio_formula_cpu_v1_20260830",
    )
    args = parser.parse_args()
    print(json.dumps(stage(args.output_root), indent=2))


if __name__ == "__main__":
    main()
