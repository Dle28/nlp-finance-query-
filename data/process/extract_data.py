from __future__ import annotations

import argparse
from pathlib import Path

from huggingface_hub import snapshot_download


REPO_ID = "AIGuruTinix/ViFinQA"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "ViFinQA"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download the public ViFinQA corpus from Hugging Face."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Dataset destination. Default: <repository>/data/ViFinQA",
    )
    return parser.parse_args()


def download_vifinqa(output_dir: Path) -> Path:
    """Download questions, stock metadata, and OCR financial reports."""
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Dataset: {REPO_ID}")
    print(f"Output directory: {output_dir}")

    downloaded_path = snapshot_download(
        repo_id=REPO_ID,
        repo_type="dataset",
        local_dir=str(output_dir),
        allow_patterns=[
            "questions/questions.jsonl",
            "code_stock.csv",
            "financial_statements/**",
        ],
    )
    return Path(downloaded_path)


def inspect_downloaded_data(data_root: Path) -> None:
    """Print a compact integrity summary after download."""
    question_path = data_root / "questions" / "questions.jsonl"
    report_root = data_root / "financial_statements"
    report_paths = sorted(report_root.rglob("*_extracted.txt"))
    if not report_paths:
        report_paths = sorted(report_root.rglob("*.txt"))

    print("\nDownload summary")
    print(f"Questions file exists: {question_path.is_file()}")
    print(f"Financial reports found: {len(report_paths):,}")

    if report_paths:
        print(f"First report: {report_paths[0]}")
    else:
        print(f"Warning: no report text files found under {report_root}")


def main() -> None:
    config = parse_args()
    dataset_path = download_vifinqa(config.output_dir)
    inspect_downloaded_data(dataset_path)


if __name__ == "__main__":
    main()
