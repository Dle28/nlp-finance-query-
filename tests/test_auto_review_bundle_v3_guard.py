import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "auto_v3",
    Path(__file__).parents[1] / "scripts" / "auto_review_bundle_v3.py",
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def _item(metric):
    return {
        "question": metric,
        "effective_metric": metric,
        "question_plan": {"family": "direct_lookup", "operands": [{"metric": metric}]},
    }


def test_q13_adjacent_recovery_passes_grounding_guard():
    item = _item("Tiền và các khoản tương đương tiền")
    candidate = {
        "candidate_source": "adjacent_previous_due_context",
        "effective_metric": "Tiền và các khoản tương đương tiền",
        "direct_evidence": (
            "TABLE: 4. TIỀN VÀ CÁC KHOẢN TƯƠNG ĐƯƠNG TIỀN || "
            "COLUMNS: Số cuối năm | Số đầu năm || "
            "VALUE: TỔNG CỘNG | 1.880.612.291.229 | 6.406.079.584.088"
        ),
    }
    guard = mod.grounding(item, candidate, 0.85, 0.45)
    assert guard["guard_pass"] is True
    assert guard["token_coverage"] == 1.0


def test_q53_false_adjacent_recovery_is_rejected():
    item = _item("Giá trị còn lại của bất động sản đầu tư")
    candidate = {
        "candidate_source": "adjacent_previous_due_context",
        "effective_metric": "Giá trị còn lại của bất động sản đầu tư",
        "direct_evidence": (
            "TABLE: 2. Các khoản giảm trừ doanh thu Giá trị bất động sản bị trả lại trong năm. "
            "3. Giá vốn hàng bán || COLUMNS: Năm nay | Năm trước || "
            "VALUE: Cộng | 1.458.610.413.310 | 1.327.256.343.886"
        ),
    }
    guard = mod.grounding(item, candidate, 0.85, 0.45)
    assert guard["guard_pass"] is False
    assert guard["token_coverage"] < 0.85


def test_q53_direct_candidate_passes_and_contains_value():
    item = _item("Giá trị còn lại của bất động sản đầu tư")
    candidate = {
        "candidate_source": "retrieved",
        "effective_metric": "Giá trị còn lại của bất động sản đầu tư",
        "direct_evidence": (
            "TABLE: 10. Bất động sản đầu tư || "
            "COLUMNS: Nguyên giá | Hao mòn lũy kế | Giá trị còn lại || "
            "VALUE: Số cuối năm | 417.860.288.970 | 39.303.347.137 | 378.556.941.833"
        ),
    }
    guard = mod.grounding(item, candidate, 0.85, 0.45)
    assert guard["guard_pass"] is True
    assert guard["token_coverage"] == 1.0
    assert "378.556.941.833" in candidate["direct_evidence"]
