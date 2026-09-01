from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = (
    Path(__file__).parents[2]
    / "scripts"
    / "research"
    / "run_ratio_formula_variant_v4.py"
)
SPEC = importlib.util.spec_from_file_location("ratio_formula_variant_v4", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_v4_protocol_uses_qualifier_aware_strict_runner() -> None:
    assert MODULE.VARIANT_PROTOCOL == "vifinqa_exact_ratio_formula_v4"
    assert MODULE.RATIO_TIER == "program_exact_ratio_formula_v4"
    assert not MODULE.V2._row_identity_accepts(
        "doanh thu",
        "Doanh thu hoạt động tài chính",
        raw_metric_hint="Tổng doanh thu",
    )[0]
    assert MODULE.V2._row_identity_accepts(
        "khoan phai thu ngan han khac", "Phải thu ngắn hạn khác"
    )[0]
