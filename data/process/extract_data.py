from __future__ import annotations

import json
from pathlib import Path

from huggingface_hub import hf_hub_download, snapshot_download

from project_paths import FINANCIAL_STATEMENTS_DIR, QUESTIONS_PATH, VIFINQA_DIR


REPO_ID = "AIGuruTinix/ViFinQA"
QUESTION_REPO_PATH = "questions/questions.jsonl"
STOCK_REPO_PATH = "code_stock.csv"


def download_required_file(repo_path: str) -> Path:
    """Download one required dataset file and return its local path."""
    downloaded = hf_hub_download(
        repo_id=REPO_ID,
        repo_type="dataset",
        filename=repo_path,
        local_dir=str(VIFINQA_DIR),
    )
    path = Path(downloaded)
    if not path.is_file():
        raise FileNotFoundError(
            f"Required dataset file was not downloaded: {repo_path}"
        )
    return path


def download_financial_reports() -> Path:
    """Download all OCR financial reports into the configured dataset root."""
    downloaded = snapshot_download(
        repo_id=REPO_ID,
        repo_type="dataset",
        local_dir=str(VIFINQA_DIR),
        allow_patterns=["financial_statements/**"],
    )
    return Path(downloaded)


def validate_questions(path: Path) -> int:
    """Validate that questions.jsonl exists and contains usable records."""
    if not path.is_file():
        raise FileNotFoundError(f"Questions file not found: {path}")

    question_count = 0
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON in questions file at line {line_number}: {exc}"
                ) from exc

            if not isinstance(record, dict):
                raise ValueError(
                    f"Question line {line_number} is not a JSON object."
                )
            if "id" not in record or "question" not in record:
                raise ValueError(
                    f"Question line {line_number} must contain 'id' and 'question'."
                )
            question_count += 1

    if question_count == 0:
        raise ValueError(f"Questions file is empty: {path}")
    return question_count


def discover_reports() -> list[Path]:
    report_paths = sorted(FINANCIAL_STATEMENTS_DIR.rglob("*_extracted.txt"))
    if not report_paths:
        report_paths = sorted(FINANCIAL_STATEMENTS_DIR.rglob("*.txt"))
    return [path for path in report_paths if path.is_file()]


def download_vifinqa() -> None:
    """Download metadata first, then the OCR financial-report corpus."""
    VIFINQA_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Dataset: {REPO_ID}")
    print(f"Output directory: {VIFINQA_DIR}")

    print("\n[1/3] Downloading questions...")
    question_path = download_required_file(QUESTION_REPO_PATH)

    print("[2/3] Downloading stock metadata...")
    stock_path = download_required_file(STOCK_REPO_PATH)

    print("[3/3] Downloading financial reports...")
    download_financial_reports()

    question_count = validate_questions(question_path)
    report_paths = discover_reports()

    if question_path.resolve() != QUESTIONS_PATH.resolve():
        raise RuntimeError(
            "Questions were downloaded to an unexpected location. "
            f"Expected {QUESTIONS_PATH}, received {question_path}."
        )
    if not report_paths:
        raise FileNotFoundError(
            f"No financial-report text files found under {FINANCIAL_STATEMENTS_DIR}"
        )

    print("\nDownload verified")
    print(f"  questions:         {question_path}")
    print(f"  question records:  {question_count:,}")
    print(f"  stock metadata:    {stock_path}")
    print(f"  reports directory: {FINANCIAL_STATEMENTS_DIR}")
    print(f"  report files:      {len(report_paths):,}")


def main() -> None:
    download_vifinqa()


if __name__ == "__main__":
    main()
