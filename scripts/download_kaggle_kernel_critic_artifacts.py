#!/usr/bin/env python3
"""Download only the hash-auditable critic artifacts from a Kaggle kernel.

The legacy ``kaggle kernels output`` command can return only the first page of
a large kernel-output listing.  This helper paginates the current kernel
session explicitly, rejects ambiguous duplicate artifact names, and never
downloads the source snapshot or other incidental output files.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import requests


EXPECTED_NAMES = frozenset(
    {
        "qwen14_grounded_critic_results_v2.jsonl",
        "qwen14_grounded_critic_results_v2.manifest.json",
        "qwen14_grounded_critic_runtime_v2.json",
    }
)


def parse_kernel_ref(value: str) -> tuple[str, str]:
    user, separator, slug = value.strip().partition("/")
    if not separator or not user or not slug or "/" in slug:
        raise argparse.ArgumentTypeError("--kernel must be exactly <username>/<kernel-slug>")
    return user, slug


def iter_session_files(*, user_name: str, kernel_slug: str) -> Iterable[Any]:
    """Yield all files from the latest session, across every API page."""
    from kaggle.api.kaggle_api_extended import ApiListKernelSessionOutputRequest, KaggleApi

    api = KaggleApi()
    api.authenticate()
    page_token = ""
    with api.build_kaggle_client() as client:
        while True:
            request = ApiListKernelSessionOutputRequest()
            request.user_name = user_name
            request.kernel_slug = kernel_slug
            request.page_size = 200
            request.page_token = page_token
            response = client.kernels.kernels_api_client.list_kernel_session_output(request)
            yield from (response.files or [])
            page_token = str(response.next_page_token or "")
            if not page_token:
                break


def select_expected_urls(files: Iterable[Any]) -> dict[str, str]:
    """Return exactly one URL for each expected basename or fail closed."""
    urls: dict[str, set[str]] = defaultdict(set)
    for item in files:
        file_name = str(getattr(item, "file_name", "") or "")
        name = Path(file_name).name
        if name not in EXPECTED_NAMES:
            continue
        url = str(getattr(item, "url", "") or "")
        if not url:
            raise ValueError(f"Kaggle output {file_name!r} has no download URL")
        urls[name].add(url)
    missing = sorted(EXPECTED_NAMES - set(urls))
    duplicate = sorted(name for name, values in urls.items() if len(values) != 1)
    if missing or duplicate:
        problems: list[str] = []
        if missing:
            problems.append(f"missing expected artifact(s): {', '.join(missing)}")
        if duplicate:
            problems.append(f"ambiguous artifact URL(s): {', '.join(duplicate)}")
        raise ValueError("; ".join(problems))
    return {name: next(iter(urls[name])) for name in sorted(EXPECTED_NAMES)}


def download(url: str, destination: Path) -> None:
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite existing artifact: {destination}")
    partial = destination.with_name(destination.name + ".part")
    if partial.exists():
        raise FileExistsError(f"Refusing to overwrite partial artifact: {partial}")
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    partial.write_bytes(response.content)
    partial.replace(destination)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", type=parse_kernel_ref, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    user_name, kernel_slug = args.kernel
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    urls = select_expected_urls(iter_session_files(user_name=user_name, kernel_slug=kernel_slug))
    for name, url in urls.items():
        destination = output_dir / name
        download(url, destination)
        print(destination)


if __name__ == "__main__":
    main()
