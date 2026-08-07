import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "bundle_v3",
    Path(__file__).parents[1] / "scripts" / "build_review_bundle_v3.py",
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_direct_lookup_effective_metric_strips_entity():
    q = "Giá trị còn lại của bất động sản đầu tư của công ty mẹ IJC đến ngày 31 tháng 12 năm 2021 là bao nhiêu tỷ đồng?"
    plan = {
        "family": "direct_lookup",
        "tickers": ["IJC"],
        "operands": [
            {"metric": "Giá trị còn lại của bất động sản đầu tư IJC 31 tháng 12 năm 2021"}
        ],
    }
    assert mod.effective_metric(q, plan, "direct_lookup") == "Giá trị còn lại của bất động sản đầu tư"


def test_period_aware_projection_pairs_table_header_and_value():
    rows = [
        ["Nguyên giá", "Hao mòn lũy kế", "Giá trị còn lại"],
        ["Số đầu năm", "385.187.149.316", "31.197.426.846", "353.989.722.470"],
        ["Kết chuyển từ hàng tồn kho sang (*)", "32.673.139.654"],
        ["Khẩu hao trong năm", "8.105.920.291"],
        ["Số cuối năm", "417.860.288.970", "39.303.347.137", "378.556.941.833"],
    ]
    q = "Giá trị còn lại của bất động sản đầu tư của công ty mẹ IJC đến ngày 31 tháng 12 năm 2021 là bao nhiêu tỷ đồng?"
    plan = {"family": "direct_lookup", "operands": [{"metric": "Giá trị còn lại của bất động sản đầu tư"}]}
    out = mod.projection(
        rows,
        "</table> 10. Bất động sản đầu tư",
        q,
        plan,
        "Giá trị còn lại của bất động sản đầu tư",
    )
    assert out["value_row_index"] == 4
    assert out["best_row_index"] == 4
    assert "Bất động sản đầu tư" in out["direct_evidence"]
    assert "Giá trị còn lại" in out["direct_evidence"]
    assert "378.556.941.833" in out["direct_evidence"]
    assert out["evidence_features"]["numeric"] is True


def test_table_heading_metric_uses_total_row():
    rows = [
        ["", "Số cuối năm", "Số đầu năm"],
        ["Tiền mặt tại quỹ", "437.903.500", "58.081.504"],
        ["Tiền gửi ngân hàng", "180.174.387.729", "82.021.502.584"],
        ["Các khoản tương đương tiền (*)", "1.700.000.000.000", "6.324.000.000.000"],
        ["TỔNG CỘNG", "1.880.612.291.229", "6.406.079.584.088"],
    ]
    q = "Tiền và các khoản tương đương tiền của công ty mẹ SAB vào cuối năm 2016 là bao nhiêu tỷ đồng?"
    plan = {"family": "direct_lookup", "operands": [{"metric": "Tiền và các khoản tương đương tiền"}]}
    out = mod.projection(
        rows,
        "</table> 4. TIỀN VÀ CÁC KHOẢN TƯƠNG ĐƯƠNG TIỀN",
        q,
        plan,
        "Tiền và các khoản tương đương tiền",
    )
    assert out["value_row_index"] == 4
    assert "TỔNG CỘNG" in out["direct_evidence"]
    assert "1.880.612.291.229" in out["direct_evidence"]
