from __future__ import annotations

from pathlib import Path

from huggingface_hub import snapshot_download

from project_paths import FINANCIAL_STATEMENTS_DIR, QUESTIONS_PATH, VIFINQA_DIR


REPO_ID = "AIGuruTinix/ViFinQA"


def download_vifinqa() -> Path:
    """Download the public ViFinQA corpus to the configured source path."""
    VIFINQA_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Dataset: {REPO_ID}")
    print(f"Output directory: {VIFINQA_DIR}")

    downloaded_path = snapshot_download(
        repo_id=REPO_ID,
        repo_type="dataset",
        local_dir=str(VIFINQA_DIR),
        allow_patterns=[
            "questions/questions.jsonl",
            "code_stock.csv",
            "financial_statements/**",
        ],
    )
    return Path(downloaded_path)


def inspect_downloaded_data() -> None:
    """Print a compact integrity summary after download."""
    report_paths = sorted(FINANCIAL_STATEMENTS_DIR.rglob("*_extracted.txt"))
    if not report_paths:
        report_paths = sorted(FINANCIAL_STATEMENTS_DIR.rglob("*.txt"))

    print("\nDownload summary")
    print(f"Questions path: {QUESTIONS_PATH}")
    print(f"Questions file exists: {QUESTIONS_PATH.is_file()}")
    print(f"Financial statements path: {FINANCIAL_STATEMENTS_DIR}")
    print(f"Financial reports found: {len(report_paths):,}")

    if report_paths:
        print(f"First report: {report_paths[0]}")
    else:
        print("Warning: no financial-report text files were found.")


def main() -> None:
    download_vifinqa()
    inspect_downloaded_data()


if __name__ == "__main__":
    main()
