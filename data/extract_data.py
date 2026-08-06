from pathlib import Path

from huggingface_hub import snapshot_download


REPO_ID = "AIGuruTinix/ViFinQA"

# File hiện tại:
# AI_guru/data/extract_data_report.py
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Dữ liệu sẽ nằm tại:
# AI_guru/data/ViFinQA/
OUTPUT_DIR = PROJECT_ROOT / "data" / "ViFinQA"


def download_vifinqa() -> Path:
    """Tải câu hỏi, danh sách công ty và toàn bộ báo cáo ViFinQA."""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Đang tải dataset: {REPO_ID}")
    print(f"Thư mục đầu ra: {OUTPUT_DIR}")

    downloaded_path = snapshot_download(
        repo_id=REPO_ID,
        repo_type="dataset",
        local_dir=str(OUTPUT_DIR),
        allow_patterns=[
            "questions/questions.jsonl",
            "code_stock.csv",
            "financial_statements/**",
        ],
    )

    return Path(downloaded_path)


def inspect_downloaded_data(data_root: Path) -> None:
    """Kiểm tra số lượng câu hỏi và báo cáo đã tải."""

    question_path = data_root / "questions" / "questions.jsonl"
    report_paths = sorted(
        (data_root / "financial_statements").glob("*/*/*/*.txt")
    )

    print("\nHoàn tất tải dữ liệu.")
    print(f"File câu hỏi: {question_path}")
    print(f"Số báo cáo tìm thấy: {len(report_paths)}")

    if not question_path.exists():
        print("Cảnh báo: Không tìm thấy questions.jsonl")

    if report_paths:
        print(f"Báo cáo đầu tiên: {report_paths[0]}")
    else:
        print("Cảnh báo: Không tìm thấy file báo cáo .txt")


if __name__ == "__main__":
    dataset_path = download_vifinqa()
    inspect_downloaded_data(dataset_path)