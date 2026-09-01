from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = (
    Path(__file__).parents[2]
    / "scripts"
    / "research"
    / "run_ratio_formula_variant_v3.py"
)
SPEC = importlib.util.spec_from_file_location("ratio_formula_variant_v3", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_v3_protocol_reuses_strict_v2_gates() -> None:
    assert MODULE.VARIANT_PROTOCOL == "vifinqa_exact_ratio_formula_v3"
    assert MODULE.RATIO_TIER == "program_exact_ratio_formula_v3"
    assert MODULE.V2._row_identity_accepts(
        "chi phi tra truoc ngan han", "Chi phí trả trước ngắn hạn"
    )[0]
    assert not MODULE.V2._row_identity_accepts(
        "tong doanh thu", "Doanh thu hoạt động tài chính"
    )[0]
