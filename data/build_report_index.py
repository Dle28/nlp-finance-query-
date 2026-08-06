from pathlib import Path

import pandas as pd


DATA_ROOT = Path(__file__).resolve().parent / "ViFinQA"
REPORT_ROOT = DATA_ROOT / "financial_statements"
OUTPUT_DIR = Path(__file__).resolve().parent / "processed"
OUTPUT_PATH = OUTPUT_DIR / "report_index.csv"


def classify_statement_type(document_id: str) -> str:
    """Phân loại báo cáo dựa trên tên tài liệu."""

    name = document_id.lower()

    if "separate" in name:
        return "separate"

    if "consolidated" in name:
        return "consolidated"

    if "aggregated" in name:
        return "aggregated"

    return "other"


def build_report_index() -> pd.DataFrame:
    stock_path = DATA_ROOT / "code_stock.csv"

    stock_df = pd.read_csv(stock_path)
    stock_df = stock_df.rename(
        columns={
            "Mã CK": "ticker",
            "Tên công ty": "company_name",
        }
    )

    records = []

    for report_path in sorted(REPORT_ROOT.rglob("*.txt")):
        relative_path = report_path.relative_to(REPORT_ROOT)
        parts = relative_path.parts

        # Cấu trúc:
        # TICKER / YEAR / DOCUMENT / FILE.txt
        if len(parts) < 4:
            print(f"Bỏ qua đường dẫn không đúng cấu trúc: {report_path}")
            continue

        ticker = parts[0]
        year_text = parts[1]
        document_id = parts[2]

        try:
            year = int(year_text)
        except ValueError:
            print(f"Năm không hợp lệ: {report_path}")
            continue

        records.append(
            {
                "ticker": ticker,
                "year": year,
                "document_id": document_id,
                "statement_type": classify_statement_type(document_id),
                "relative_path": str(relative_path),
            }
        )

    report_df = pd.DataFrame(records)

    report_df = report_df.merge(
        stock_df[["ticker", "company_name"]],
        on="ticker",
        how="left",
    )

    return report_df[
        [
            "ticker",
            "company_name",
            "year",
            "document_id",
            "statement_type",
            "relative_path",
        ]
    ]


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    report_df = build_report_index()
    report_df.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")

    print(f"Đã tạo: {OUTPUT_PATH}")
    print(f"Tổng số báo cáo: {len(report_df)}")

    print("\nPhân bố loại báo cáo:")
    print(report_df["statement_type"].value_counts())

if __name__ == "__main__":
    main()