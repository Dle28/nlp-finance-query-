from __future__ import annotations

from finance_query.schemas import TableAsset


def _asset(unit_hint: str) -> TableAsset:
    return TableAsset(
        internal_table_uid="table-1",
        document_id="AAA_2023",
        ticker="AAA",
        report_year=2023,
        scope="consolidated",
        source_path="reports/AAA_2023.txt",
        page_no=1,
        local_ordinal=1,
        char_start=10,
        char_end=20,
        byte_start=10,
        byte_end=20,
        source_sha256="a" * 64,
        table_sha256="b" * 64,
        unit_hint=unit_hint,
        headers=["Năm 2023"],
        rows=[["Doanh thu", "1000"]],
    )


def test_raw_asset_identity_excludes_derived_unit_assertion() -> None:
    million = _asset("million_vnd")
    billion = _asset("billion_vnd")

    assert million.raw_asset() == billion.raw_asset()
    assert million.derived_assertions()["unit_hint"] == "million_vnd"
    assert billion.derived_assertions()["unit_hint"] == "billion_vnd"
