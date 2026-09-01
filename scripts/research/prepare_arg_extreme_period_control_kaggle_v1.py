#!/usr/bin/env python3
"""Stage a same-snapshot Kaggle CPU control for the arg-extreme lane.

The treatment kernel runs ``run_arg_extreme_period_variant_v1.py`` from the
runtime dataset.  This control unpacks the exact same immutable runtime tar
but calls the canonical builder directly, so the only intended difference is
the arg-extreme monkeypatch.  The control is a comparison artifact, not an
answer-authority or promotion path.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_SLUG = "dungle2810/vifinqa-primary-arg-extreme-period-v1-20260830"
KERNEL_SLUG = "dungle2810/vifinqa-arg-extreme-period-control-cpu-v1-20260830"
FULL_ASSET_SLUG = "dungle2810/vifinqa-full-dense-assets-v1-20260826"
COORDINATE_SLUG = "dungle2810/vifinqa-full-corpus-coordinate-map-v1-20260829"
VARIANT_PROTOCOL = "vifinqa_arg_extreme_period_control_v1"


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _notebook(*, runtime_slug: str, kernel_slug: str) -> dict[str, Any]:
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
WORK = Path('/kaggle/working/vifinqa_arg_extreme_period_control_v1')
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

builder = RUNTIME_ROOT / 'scripts/e2e/build_competition_submission_v1.py'
structured_tables = FULL_ASSET_INPUT / 'full_table_assets_v1.jsonl'
source_line_map = COORDINATE_INPUT / 'source_line_map_full_v1.json'
output_dir = WORK / 'submission'
for required in (builder, structured_tables, source_line_map):
    assert required.exists(), required

# Keep these flags byte-for-byte aligned with the treatment notebook.  The
# control deliberately does not pass --typed-plans and does not import the
# arg-extreme adapter.
cmd = [
    sys.executable, str(builder),
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
    raise RuntimeError(f'control builder failed with exit code {run.returncode}')
if run.stderr:
    print(run.stderr[-12000:])

report_path = output_dir / 'build_report.json'
report = json.loads(report_path.read_text())
report['arg_extreme_period_control'] = {
    'protocol': 'vifinqa_arg_extreme_period_control_v1',
    'treatment_protocol': 'vifinqa_arg_extreme_period_v1',
    'arg_extreme_adapter_loaded': False,
    'same_snapshot_runtime_dataset': '__RUNTIME_DATASET_SLUG__',
    'promotion_allowed': False,
}
report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({
    'protocol': 'vifinqa_arg_extreme_period_control_v1',
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
    source = source.replace("__RUNTIME_DATASET_SLUG__", runtime_slug.split("/", 1)[-1])
    source = source.replace("__KERNEL_SLUG__", kernel_slug)
    return {
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    "# ViFinQA arg-extreme-period same-snapshot control (CPU)\n",
                    "\n",
                    "This control calls the canonical builder without the arg-extreme adapter.\n",
                ],
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
            "variant_protocol": VARIANT_PROTOCOL,
            "kernel_slug": kernel_slug,
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def stage(
    output_root: Path,
    *,
    runtime_slug: str = RUNTIME_SLUG,
    kernel_slug: str = KERNEL_SLUG,
) -> dict[str, str]:
    output_root = output_root.expanduser().resolve()
    if output_root.exists():
        raise FileExistsError(f"refusing to overwrite staging directory: {output_root}")
    kernel_dir = output_root / "kernel"
    kernel_dir.mkdir(parents=True)
    (kernel_dir / "main.ipynb").write_text(
        json.dumps(
            _notebook(runtime_slug=runtime_slug, kernel_slug=kernel_slug),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_json(
        kernel_dir / "kernel-metadata.json",
        {
            "id": kernel_slug,
            "title": "ViFinQA Arg Extreme Period Control CPU V1 20260830",
            "code_file": "main.ipynb",
            "language": "python",
            "kernel_type": "notebook",
            "is_private": True,
            "enable_gpu": False,
            "enable_tpu": False,
            "enable_internet": True,
            "dataset_sources": [runtime_slug, FULL_ASSET_SLUG, COORDINATE_SLUG],
        },
    )
    return {
        "output_root": str(output_root),
        "kernel_dir": str(kernel_dir),
        "runtime_slug": runtime_slug,
        "kernel_slug": kernel_slug,
        "full_asset_slug": FULL_ASSET_SLUG,
        "coordinate_slug": COORDINATE_SLUG,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT
        / "artifacts/kaggle_upload/dungle2810_vifinqa_arg_extreme_period_control_cpu_v1_20260830",
    )
    parser.add_argument("--runtime-slug", default=RUNTIME_SLUG)
    parser.add_argument("--kernel-slug", default=KERNEL_SLUG)
    args = parser.parse_args()
    print(
        json.dumps(
            stage(
                args.output_root,
                runtime_slug=args.runtime_slug,
                kernel_slug=args.kernel_slug,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
