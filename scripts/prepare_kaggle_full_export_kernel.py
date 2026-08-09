#!/usr/bin/env python3
"""Prepare a non-interactive Kaggle notebook for a full ViFinQA review export.

The generated notebook deliberately retains the existing integrity-gated rebuild
cells, disables its obsolete in-notebook dataset upload, and stops before the
old manual annotation UI.  It then exports the full question set and retains
only the review-bundle archive as the kernel output.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _source(cell: dict) -> str:
    value = cell.get("source", "")
    return "".join(value) if isinstance(value, list) else str(value)


def _set_source(cell: dict, value: str) -> None:
    cell["source"] = value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    notebook = json.loads(args.template.read_text(encoding="utf-8"))
    selected: list[dict] = []
    found_checkpoint = False

    for cell in notebook.get("cells", []):
        source = _source(cell)
        if "## 15. Train weak question-family router" in source:
            break
        if "## 2. Install package, set working directory, tests" in source:
            selected.append(
                {
                    "cell_type": "code",
                    "metadata": {},
                    "execution_count": None,
                    "outputs": [],
                    "source": """# Apply the bounded outer batching fix before installing the package.
# SentenceTransformer still uses a 32-item micro-batch internally; this only
# avoids re-entering model.encode once for every micro-batch.
retrieval_source = REPO_DIR / 'src/finance_query/retrieval.py'
retrieval_text = retrieval_source.read_text(encoding='utf-8')
old_signature = 'def build(self, assets_path: Path, batch_size: int = 32) -> int:'
new_signature = '''def build(
        self,
        assets_path: Path,
        batch_size: int = 32,
        encode_chunk_size: int = 4096,
    ) -> int:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if encode_chunk_size < batch_size:
            raise ValueError("encode_chunk_size must be at least batch_size")'''
if old_signature in retrieval_text:
    retrieval_text = retrieval_text.replace(old_signature, new_signature, 1)
    old_flush_gate = 'if len(batch_texts) >= batch_size:\\n                    flush()'
    new_flush_gate = 'if len(batch_texts) >= encode_chunk_size:\\n                    flush()'
    if old_flush_gate not in retrieval_text:
        raise RuntimeError('Expected DenseIndex flush gate is missing.')
    retrieval_source.write_text(
        retrieval_text.replace(old_flush_gate, new_flush_gate, 1),
        encoding='utf-8',
    )
    print('Applied dense outer chunk size: 4096; model micro-batch remains 32.')
elif 'encode_chunk_size' in retrieval_text and 'def build(' in retrieval_text:
    print('Repository already provides bounded outer batching; no patch needed.')
else:
    raise RuntimeError('DenseIndex.build is neither the supported legacy nor current form.')
""",
                }
            )
        if "UPLOAD_PRE_DENSE_TO_KAGGLE = True" in source:
            source = source.replace("UPLOAD_PRE_DENSE_TO_KAGGLE = True", "UPLOAD_PRE_DENSE_TO_KAGGLE = False")
            source = source.replace("UPLOAD_FULL_TO_KAGGLE = True", "UPLOAD_FULL_TO_KAGGLE = False")
            source = source.replace("RUN_BGE_BENCHMARK = True", "RUN_BGE_BENCHMARK = False")
            _set_source(cell, source)
        if "from kaggle_secrets import UserSecretsClient" in source and "def github_env()" in source:
            # A private Kaggle input is more reliable than a per-run secret:
            # CLI-pushed kernel versions cannot attach a user's Secrets
            # selection, while the snapshot is immutable and contains the
            # exact Git revision that was validated locally.  Keep the
            # authenticated clone fallback for an operator who intentionally
            # omits the source input.
            _set_source(
                cell,
                """from kaggle_secrets import UserSecretsClient
import os
import shutil
import subprocess

SOURCE_ROOT = Path('/kaggle/input/vifinqa-source-snapshot')

if (SOURCE_ROOT / 'pyproject.toml').is_file():
    if REPO_DIR.exists():
        shutil.rmtree(REPO_DIR)
    shutil.copytree(SOURCE_ROOT, REPO_DIR)
    assert (REPO_DIR / 'pyproject.toml').is_file(), 'Source snapshot is incomplete.'
    print('Repo hydrated from private Kaggle source snapshot:', SOURCE_ROOT)
else:
    print('Source snapshot unavailable; falling back to configured GitHub secret.')

    def github_env():
        token = UserSecretsClient().get_secret(GITHUB_SECRET_NAME)
        if not token:
            raise RuntimeError(f'Không đọc được Kaggle Secret: {GITHUB_SECRET_NAME}')

        askpass = Path('/tmp/github_askpass.sh')
        askpass.write_text(
            \"\"\"#!/bin/sh
case \"$1\" in
    *Username*) echo \"$GIT_USERNAME\" ;;
    *Password*) echo \"$GIT_TOKEN\" ;;
esac
\"\"\",
            encoding='utf-8',
        )
        askpass.chmod(0o700)

        env = os.environ.copy()
        env['GIT_TERMINAL_PROMPT'] = '0'
        env['GIT_ASKPASS'] = str(askpass)
        env['GIT_USERNAME'] = 'Dle28'
        env['GIT_TOKEN'] = token
        return env

    env = github_env()
    if (REPO_DIR / '.git').is_dir():
        subprocess.run(['git', '-C', str(REPO_DIR), 'pull', '--ff-only', 'origin', 'main'], env=env, check=True)
    else:
        if REPO_DIR.exists():
            shutil.rmtree(REPO_DIR)
        subprocess.run(['git', 'clone', '--depth', '1', REPO_URL, str(REPO_DIR)], env=env, check=True)
    print('Repo ready from authenticated GitHub clone:', REPO_DIR)
""",
            )
        if '"finance-query",\n            "build-dense",' in source:
            # Kaggle's current PyTorch wheel does not include kernels for the
            # P100 (compute capability 6.0).  A lexical-only bundle is much
            # more useful than failing after the costly corpus rebuild; on a
            # compatible GPU, retain the bounded dense build.
            _set_source(
                cell,
                """# Dense is optional: P100 (sm_60) is incompatible with the
# current Kaggle PyTorch wheel, while T4 and newer GPUs are supported.
DENSE_ENABLED = False
try:
    import torch
    if torch.cuda.is_available():
        gpu_capability = torch.cuda.get_device_capability(0)
        DENSE_ENABLED = gpu_capability >= (7, 0)
        print(f'GPU capability: sm_{gpu_capability[0]}{gpu_capability[1]}; '
              f'dense enabled: {DENSE_ENABLED}')
    else:
        print('CUDA unavailable; exporting lexical-only bundle.')
except Exception as exc:
    print(f'Cannot inspect CUDA capability ({exc!r}); exporting lexical-only bundle.')

local_info = inspect_artifacts_dir(ARTIFACTS_DIR)
print('Before dense:')
print(json.dumps(local_info, ensure_ascii=False, indent=2))

if DENSE_ENABLED:
    if not local_info['valid_dense']:
        print('Dense chua dong bo -> rebuild E5 dense index (max 70 minutes)')
        for name in ['dense.index', 'dense_uids.jsonl', 'dense.index.meta.json']:
            path = ARTIFACTS_DIR / name
            if path.exists():
                path.unlink()
        subprocess.run(
            [
                'finance-query', 'build-dense', '--config', 'configs/kaggle_e5.yaml',
            ],
            cwd=REPO_DIR,
            check=True,
            timeout=4200,
        )
    else:
        print('Dense checkpoint da hop le -> skip rebuild.')
else:
    print('Dense skipped: using validated lexical index only.')

local_info = inspect_artifacts_dir(ARTIFACTS_DIR)
print('After dense decision:')
print(json.dumps(local_info, ensure_ascii=False, indent=2))
assert local_info['valid_lexical'], 'Lexical integrity gate FAILED.'
if DENSE_ENABLED:
    assert local_info['valid_dense'], 'Dense integrity gate FAILED.'
    assert local_info['asset_count'] == local_info['dense_uid_count']
    assert local_info['asset_count'] == local_info['dense_ntotal']
""",
            )
        if "hybrid_pipeline = ViFinQARetrievalPipeline(" in source:
            # The Q13 gate must not touch a stale/partial FAISS file after
            # the P100 fallback selected lexical-only retrieval.
            source = source.replace("use_dense=True,", "use_dense=DENSE_ENABLED,", 1)
            _set_source(cell, source)
        if "full_archive, full_manifest, full_sha = make_checkpoint" in source:
            found_checkpoint = True
            source = source.replace("include_dense=True", "include_dense=DENSE_ENABLED")
            _set_source(cell, source)
        selected.append(cell)

    if not found_checkpoint:
        raise RuntimeError("Template does not contain the full artifact checkpoint cell.")

    export_source = """# Export all 1,012 questions from the newly validated index.
# This does not rebuild corpus/index a second time.
bundle_dir = Path('/kaggle/working/vifinqa_review_bundle_v3_all')
bundle_archive = bundle_dir.parent / f'{bundle_dir.name}.tar.gz'
bundle_dense_args = [] if DENSE_ENABLED else ['--no-dense']
subprocess.run(
    [
        sys.executable, 'scripts/build_review_bundle_v3.py',
        '--questions', 'data/ViFinQA/questions/questions.jsonl',
        '--config', 'configs/annotation_baseline.yaml',
        '--repo-root', '.',
        '--output-dir', str(bundle_dir),
        '--top-k', '50',
        '--max-review-candidates', '40',
        *bundle_dense_args,
    ],
    cwd=REPO_DIR,
    check=True,
)
assert bundle_archive.is_file(), f'Missing bundle archive: {bundle_archive}'
bundle_manifest = json.loads((bundle_dir / 'manifest.json').read_text(encoding='utf-8'))
assert bundle_manifest['question_count'] == 1012
assert bundle_manifest['artifact_health']['valid']

# Retain only the portable bundle and an execution receipt in kernel output.
receipt = {
    'schema_version': 1,
    'bundle_archive': bundle_archive.name,
    'bundle_bytes': bundle_archive.stat().st_size,
    'question_count': bundle_manifest['question_count'],
    'review_item_count': bundle_manifest['review_item_count'],
    'unique_table_count': bundle_manifest['unique_table_count'],
    'artifact_health': bundle_manifest['artifact_health'],
    'use_dense': bundle_manifest['use_dense'],
}
Path('/kaggle/working/vifinqa_full_export_receipt.json').write_text(
    json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8'
)
shutil.rmtree(bundle_dir)
shutil.rmtree(ARTIFACTS_DIR)
shutil.rmtree(EXPORT_DIR)
shutil.rmtree(REPO_DIR)
print(json.dumps(receipt, ensure_ascii=False, indent=2))
print('Persistent kernel output:', bundle_archive)
"""
    selected.append(
        {
            "cell_type": "markdown",
            "metadata": {},
            "source": "## 15. Full autonomous-review bundle export\n\n"
            "This version creates a persistent kernel output rather than opening a manual widget.",
        }
    )
    selected.append(
        {
            "cell_type": "code",
            "metadata": {},
            "execution_count": None,
            "outputs": [],
            "source": export_source,
        }
    )
    notebook["cells"] = selected
    notebook["nbformat_minor"] = max(int(notebook.get("nbformat_minor", 0)), 5)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(notebook, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Prepared {args.output} with {len(selected)} cells.")


if __name__ == "__main__":
    main()
