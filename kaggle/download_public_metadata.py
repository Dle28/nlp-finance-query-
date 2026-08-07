#!/usr/bin/env python3
"""Download only the small public ViFinQA files needed for Kaggle training.

The completed local baseline already contains derived table assets and indexes,
so Kaggle does not need to download the full OCR report corpus again.
"""

from __future__ import annotations

from pathlib import Path

from huggingface_hub import hf_hub_download


REPO_ID = "AIGuruTinix/ViFinQA"
ROOT = Path("data/ViFinQA")


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    for filename in ("questions/questions.jsonl", "code_stock.csv"):
        path = hf_hub_download(
            repo_id=REPO_ID,
            repo_type="dataset",
            filename=filename,
            local_dir=str(ROOT),
        )
        print(path)


if __name__ == "__main__":
    main()
