#!/usr/bin/env python3
"""Create a private, hash-bound Kaggle kernel package for CCL Phase 3."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil


KERNEL_ID = "dungle2810/vifinqa-ccl-phase-3-gpu-bake-off-v1"
KERNEL_TITLE = "ViFinQA CCL Phase 3 GPU Bake-off V1"
NOTEBOOK_NAME = "vifinqa_ccl_phase3_bakeoff_v1.ipynb"
DATASET_SOURCES = (
    "dungle2810/vifinqa-ccl-phase3-source-v1",
    "dungle2810/vifinqa-ccl-phase3-bakeoff-v1",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_slug(value: str, *, label: str) -> str:
    normalized = value.strip()
    user, separator, slug = normalized.partition("/")
    if not separator or not user or not slug or "/" in slug or any(character.isspace() for character in normalized):
        raise ValueError(f"{label} must be exactly <username>/<slug>")
    return normalized


def _require_notebook_name(value: str) -> str:
    path = Path(value)
    if path.name != value or path.suffix != ".ipynb":
        raise ValueError("notebook-name must be one .ipynb filename, without a directory")
    return value


def build_kernel_package(
    *,
    repo_root: Path,
    output_dir: Path,
    kernel_id: str = KERNEL_ID,
    kernel_title: str = KERNEL_TITLE,
    notebook_name: str = NOTEBOOK_NAME,
    dataset_sources: tuple[str, str] = DATASET_SOURCES,
    package_protocol: str = "kaggle_ccl_phase3_kernel_package_v1",
) -> dict[str, object]:
    repo_root = repo_root.resolve()
    kernel_id = _require_slug(kernel_id, label="kernel-id")
    notebook_name = _require_notebook_name(notebook_name)
    if not kernel_title.strip():
        raise ValueError("kernel-title must not be blank")
    if not package_protocol.strip() or any(character.isspace() for character in package_protocol):
        raise ValueError("package-protocol must be a non-empty whitespace-free identifier")
    if len(dataset_sources) != 2:
        raise ValueError("exactly two private Kaggle datasets are required: source and prepared job")
    normalized_sources = tuple(_require_slug(value, label="dataset-source") for value in dataset_sources)
    if len(set(normalized_sources)) != 2:
        raise ValueError("source and prepared-job datasets must be distinct")
    notebook = repo_root / "notebooks" / notebook_name
    if not notebook.is_file():
        raise FileNotFoundError(notebook)
    parsed = json.loads(notebook.read_text(encoding="utf-8"))
    if parsed.get("nbformat") != 4 or not isinstance(parsed.get("cells"), list):
        raise ValueError("CCL Phase 3 notebook is not a valid nbformat-4 notebook")
    if output_dir.exists():
        raise FileExistsError("Kaggle kernel package directory must be new")
    output_dir.mkdir(parents=True)
    destination = output_dir / notebook_name
    shutil.copyfile(notebook, destination)
    metadata = {
        "id": kernel_id,
        "title": kernel_title.strip(),
        "code_file": notebook_name,
        "language": "python",
        "kernel_type": "notebook",
        "is_private": "true",
        "enable_gpu": "true",
        "enable_tpu": "false",
        "enable_internet": "true",
        "machine_shape": "",
        "dataset_sources": list(normalized_sources),
        "competition_sources": [],
        "kernel_sources": [],
        "model_sources": [],
    }
    metadata_path = output_dir / "kernel-metadata.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    package = {
        "schema_version": 1,
        "protocol": package_protocol,
        "kernel_id": kernel_id,
        "notebook": {"name": notebook_name, "sha256": sha256_file(destination)},
        "kernel_metadata": {"sha256": sha256_file(metadata_path)},
        "dataset_sources": list(normalized_sources),
        "source_contract": {
            "training_eligible": False,
            "certification_allowed": False,
            "contains_credentials": False,
        },
    }
    (output_dir / "KERNEL_PACKAGE.json").write_text(
        json.dumps(package, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return package


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--kernel-id", default=KERNEL_ID)
    parser.add_argument("--kernel-title", default=KERNEL_TITLE)
    parser.add_argument("--notebook-name", default=NOTEBOOK_NAME)
    parser.add_argument("--dataset-source", action="append", dest="dataset_sources")
    parser.add_argument("--package-protocol", default="kaggle_ccl_phase3_kernel_package_v1")
    args = parser.parse_args()
    dataset_sources = tuple(args.dataset_sources) if args.dataset_sources is not None else DATASET_SOURCES
    print(
        json.dumps(
            build_kernel_package(
                repo_root=args.repo_root,
                output_dir=args.output_dir,
                kernel_id=args.kernel_id,
                kernel_title=args.kernel_title,
                notebook_name=args.notebook_name,
                dataset_sources=dataset_sources,
                package_protocol=args.package_protocol,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
