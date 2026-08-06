from __future__ import annotations

import bisect
import hashlib
import json
import re
from pathlib import Path
from typing import Iterable

from bs4 import BeautifulSoup
from tqdm import tqdm

from .schemas import TableAsset


TABLE_TEXT_RE = re.compile(r"<table\b.*?</table\s*>", re.IGNORECASE | re.DOTALL)
TABLE_BYTES_RE = re.compile(rb"<table\b.*?</table\s*>", re.IGNORECASE | re.DOTALL)
PAGE_RE = re.compile(r"===== PAGE\s+(\d+)\s+=====", re.IGNORECASE)
SCOPE_RE = re.compile(r"_(consolidated|separate|aggregated)(?:_|$)", re.IGNORECASE)
UNIT_RE = re.compile(
    r"(?:đơn vị|đvt)\s*[:：]?\s*(nghìn đồng|triệu đồng|tỷ đồng|%|phần trăm)",
    re.IGNORECASE,
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def infer_document_id(path: Path) -> str:
    suffix = "_extracted.txt"
    if path.name.endswith(suffix):
        return path.name[: -len(suffix)]
    return path.parent.name


def infer_scope(document_id: str) -> str:
    match = SCOPE_RE.search(document_id)
    return match.group(1).casefold() if match else "unknown"


def infer_path_metadata(path: Path, reports_root: Path) -> tuple[str, int | None]:
    relative = path.relative_to(reports_root)
    ticker = relative.parts[0] if relative.parts else ""
    try:
        year = int(relative.parts[1])
    except (IndexError, ValueError):
        year = None
    return ticker, year


def page_for_position(page_starts: list[int], page_numbers: list[int], position: int) -> int | None:
    index = bisect.bisect_right(page_starts, position) - 1
    return page_numbers[index] if index >= 0 else None


def parse_table_structure(
    table_html: str,
) -> tuple[list[str], list[list[str]], list[str], str]:
    """Return header cells, structured rows, row paths, and retrieval text.

    This baseline preserves each parsed row and cell. It does not claim perfect
    rowspan/colspan recovery; later hierarchy reconstruction can replace this
    parser without changing source identity.
    """
    soup = BeautifulSoup(table_html, "lxml")
    rows: list[list[str]] = []
    for row in soup.find_all("tr"):
        cells = [
            normalize_space(cell.get_text(" ", strip=True))
            for cell in row.find_all(["th", "td"])
        ]
        cells = [cell for cell in cells if cell]
        if cells:
            rows.append(cells)

    headers: list[str] = []
    for row in rows[:3]:
        headers.extend(row)

    row_paths = [" > ".join(row) for row in rows]
    flattened = "\n".join(row_paths)
    return headers, rows, row_paths, flattened


def infer_unit(context: str, table_text: str) -> str | None:
    search_area = f"{context}\n{table_text[:1500]}"
    match = UNIT_RE.search(search_area)
    if not match:
        return None
    value = match.group(1).casefold()
    return {
        "nghìn đồng": "thousand_vnd",
        "triệu đồng": "million_vnd",
        "tỷ đồng": "billion_vnd",
        "%": "percent",
        "phần trăm": "percent",
    }.get(value, value)


def iter_report_paths(reports_root: Path) -> Iterable[Path]:
    paths = sorted(reports_root.rglob("*_extracted.txt"))
    if not paths:
        paths = sorted(reports_root.rglob("*.txt"))
    return (path for path in paths if path.is_file())


def extract_assets_from_report(path: Path, reports_root: Path) -> list[TableAsset]:
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="strict")
    source_sha256 = sha256_bytes(raw)
    document_id = infer_document_id(path)
    ticker, year = infer_path_metadata(path, reports_root)
    scope = infer_scope(document_id)

    char_matches = list(TABLE_TEXT_RE.finditer(text))
    byte_matches = list(TABLE_BYTES_RE.finditer(raw))
    if len(char_matches) != len(byte_matches):
        raise ValueError(
            f"Character/byte table count mismatch for {path}: "
            f"{len(char_matches)} != {len(byte_matches)}"
        )

    page_matches = list(PAGE_RE.finditer(text))
    page_starts = [match.start() for match in page_matches]
    page_numbers = [int(match.group(1)) for match in page_matches]

    assets: list[TableAsset] = []
    for ordinal, (char_match, byte_match) in enumerate(
        zip(char_matches, byte_matches, strict=True),
        start=1,
    ):
        table_html = char_match.group(0)
        table_sha256 = sha256_bytes(table_html.encode("utf-8"))
        context_start = max(0, char_match.start() - 1000)
        context = normalize_space(text[context_start : char_match.start()])[-800:]
        headers, rows, row_paths, flattened = parse_table_structure(table_html)
        unit = infer_unit(context, flattened)

        uid_payload = (
            f"{document_id}\x1f{ordinal}\x1f{char_match.start()}\x1f{table_sha256}"
        ).encode("utf-8")
        internal_uid = hashlib.sha256(uid_payload).hexdigest()

        search_text = normalize_space(
            "\n".join(
                [
                    document_id,
                    ticker,
                    str(year or ""),
                    scope,
                    context,
                    " | ".join(headers),
                    flattened,
                    unit or "",
                ]
            )
        )

        assets.append(
            TableAsset(
                internal_table_uid=internal_uid,
                document_id=document_id,
                ticker=ticker,
                report_year=year,
                scope=scope,
                source_path=str(path),
                page_no=page_for_position(page_starts, page_numbers, char_match.start()),
                local_ordinal=ordinal,
                char_start=char_match.start(),
                char_end=char_match.end(),
                byte_start=byte_match.start(),
                byte_end=byte_match.end(),
                source_sha256=source_sha256,
                table_sha256=table_sha256,
                unit_hint=unit,
                context_before=context,
                headers=headers,
                rows=rows,
                row_paths=row_paths,
                search_text=search_text,
            )
        )
    return assets


def build_table_assets(reports_root: Path, output_path: Path) -> dict[str, int]:
    reports_root = reports_root.resolve()
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    report_count = 0
    table_count = 0
    report_paths = list(iter_report_paths(reports_root))
    with output_path.open("w", encoding="utf-8") as output:
        for path in tqdm(report_paths, desc="Building table assets"):
            report_count += 1
            for asset in extract_assets_from_report(path, reports_root):
                output.write(json.dumps(asset.to_dict(), ensure_ascii=False) + "\n")
                table_count += 1

    return {"reports": report_count, "tables": table_count}


def iter_assets(path: Path) -> Iterable[dict]:
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid asset JSONL at line {line_number}: {exc}") from exc
