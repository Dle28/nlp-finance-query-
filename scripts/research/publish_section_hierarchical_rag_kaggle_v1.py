#!/usr/bin/env python3
"""Publish the private, non-submittable section-RAG evaluation to Kaggle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _response_error(response: object) -> str | None:
    error = getattr(response, "error", None)
    return str(error) if error else None


def _current_dataset_source(api: object, *, username: str, dataset_slug: str, dataset_ref: str) -> str:
    datasets = api.dataset_list(search=dataset_slug, user=username)
    matches = [row for row in datasets or [] if str(getattr(row, "ref", "")) == dataset_ref]
    if len(matches) != 1:
        raise RuntimeError("could not resolve the current Kaggle Dataset version")
    version = getattr(matches[0], "current_version_number", None)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise RuntimeError("Kaggle Dataset does not expose a valid current version")
    return f"{dataset_ref}/{version}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--notebook", type=Path, required=True)
    parser.add_argument("--dataset-slug", default="vifinqa-section-rag-input-20260827")
    parser.add_argument("--kernel-slug", default="vifinqa-section-rag-20260827")
    parser.add_argument("--kernel-dir", type=Path, required=True)
    parser.add_argument("--update-existing-dataset", action="store_true")
    args = parser.parse_args()
    if not (args.input_dir / "input_manifest.json").is_file() or not args.notebook.is_file():
        raise SystemExit("input staging manifest or notebook is missing")
    input_manifest = json.loads((args.input_dir / "input_manifest.json").read_text(encoding="utf-8"))
    if input_manifest.get("submission_eligible") is not False or input_manifest.get("contains_human_verified") is not False:
        raise SystemExit("refusing to publish a non-sanitized or submission-eligible input")
    from kaggle import KaggleApi

    api = KaggleApi()
    api.authenticate()
    username = str(api.config_values[api.CONFIG_NAME_USER])
    dataset_ref = f"{username}/{args.dataset_slug}"
    kernel_ref = f"{username}/{args.kernel_slug}"
    dataset_metadata = {
        "title": "ViFinQA Section RAG Input 20260827",
        "id": dataset_ref,
        "licenses": [{"name": "CC0-1.0"}],
        "description": "Private, value-blind navigation-only input for ViFinQA section-to-table RAG evaluation.",
    }
    _write_json(args.input_dir / "dataset-metadata.json", dataset_metadata)
    dataset_created = False
    dataset_updated = False
    try:
        api.dataset_status(dataset_ref)
        if args.update_existing_dataset:
            response = api.dataset_create_version(
                str(args.input_dir),
                version_notes="Refresh reproducible section-RAG code bundle and GPU compatibility guard.",
                quiet=False,
                convert_to_csv=False,
                dir_mode="skip",
            )
            if error := _response_error(response):
                raise SystemExit(f"Kaggle Dataset was not updated: {error}")
            dataset_updated = True
    except Exception:
        response = api.dataset_create_new(
            str(args.input_dir), public=False, quiet=False, convert_to_csv=False, dir_mode="skip"
        )
        if error := _response_error(response):
            raise SystemExit(f"Kaggle Dataset was not created: {error}")
        dataset_created = True
    dataset_source_ref = _current_dataset_source(
        api, username=username, dataset_slug=args.dataset_slug, dataset_ref=dataset_ref
    )
    if args.kernel_dir.exists():
        raise SystemExit(f"refusing to overwrite kernel staging directory: {args.kernel_dir}")
    args.kernel_dir.mkdir(parents=True)
    try:
        code_name = args.notebook.name
        shutil.copy2(args.notebook, args.kernel_dir / code_name)
        kernel_metadata = {
            "id": kernel_ref,
            "title": "ViFinQA Section RAG 20260827",
            "code_file": code_name,
            "language": "python",
            "kernel_type": "notebook",
            "is_private": True,
            "enable_gpu": True,
            "enable_internet": True,
            "dataset_sources": [dataset_source_ref],
        }
        _write_json(args.kernel_dir / "kernel-metadata.json", kernel_metadata)
        kernel_response = api.kernels_push(str(args.kernel_dir), timeout="3600", acc="NvidiaTeslaT4")
        if error := _response_error(kernel_response):
            raise SystemExit(f"Kaggle Notebook was not pushed: {error}")
    except Exception:
        # The successfully-created Dataset remains intentionally immutable; do
        # not delete an external artifact after an unrelated notebook failure.
        raise
    print(
        json.dumps(
            {
                "status": "PUBLISHED",
                "dataset_ref": dataset_ref,
                "dataset_source_ref": dataset_source_ref,
                "kernel_ref": kernel_ref,
                "dataset_created_this_run": dataset_created,
                "dataset_updated_this_run": dataset_updated,
                "private": True,
                "enable_gpu": True,
                "machine_shape": "NvidiaTeslaT4",
                "navigation_metadata_only": True,
                "may_authorize_answer": False,
                "submission_eligible": False,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
