from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = (
    Path(__file__).parents[2]
    / "scripts"
    / "research"
    / "run_ratio_formula_variant_v2.py"
)
SPEC = importlib.util.spec_from_file_location("ratio_formula_variant_v2", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_high_information_row_anchors_reject_semantic_lookalikes() -> None:
    assert MODULE._row_identity_accepts(
        "cam ket ngoai bang", "Các cam kết ngoại bảng"
    )[0]
    assert not MODULE._row_identity_accepts(
        "cam ket ngoai bang", "Bằng ngoại tệ"
    )[0]
    assert not MODULE._row_identity_accepts(
        "nguyen gia tscd huu hinh", "Tài sản cố định hữu hình"
    )[0]
    assert MODULE._row_identity_accepts(
        "nguyen gia tscd huu hinh", "Nguyên giá tài sản cố định hữu hình"
    )[0]


def test_row_identity_rejects_known_accounting_lookalikes() -> None:
    assert not MODULE._row_identity_accepts(
        "loi nhuan sau thue", "Lợi nhuận sau thuế chưa phân phối"
    )[0]
    assert not MODULE._row_identity_accepts(
        "tong doanh thu", "Các khoản giảm trừ doanh thu"
    )[0]
    assert not MODULE._row_identity_accepts(
        "von gop cua co dong nguyen khai hoan", "Ông Nguyễn Khải Hoàn"
    )[0]
    assert not MODULE._row_identity_accepts(
        "loi nhuan sau thue", "Lợi nhuận kế toán trước thuế"
    )[0]
    assert not MODULE._row_identity_accepts(
        "chi phi tra truoc ngan han", "Chi phí phải trả ngắn hạn"
    )[0]
    assert not MODULE._row_identity_accepts(
        "tong doanh thu", "Doanh thu hoạt động tài chính"
    )[0]
    assert not MODULE._row_identity_accepts(
        "doanh thu", "Doanh thu hoạt động tài chính",
        raw_metric_hint="Tổng doanh thu",
    )[0]
    assert not MODULE._row_identity_accepts(
        "khoan phai thu ngan han khac",
        "Dự phòng phải thu ngắn hạn khác",
    )[0]
    assert not MODULE._row_identity_accepts(
        "no phai tra tai chinh",
        "Vay và nợ thuê tài chính ngắn hạn phải trả các tổ chức khác",
        raw_metric_hint="Tổng nợ phải trả tài chính",
    )[0]
    assert not MODULE._row_identity_accepts(
        "tong du phong rui ro cho vay khach hang",
        "Trích lập dự phòng cụ thể cho vay khách hàng",
    )[0]


def test_row_identity_preserves_valid_derived_rows() -> None:
    assert MODULE._row_identity_accepts(
        "chi phi ban hang", "Chi phí bán hàng"
    )[0]
    assert MODULE._row_identity_accepts(
        "tong tai san", "TỔNG TÀI SẢN"
    )[0]
    assert MODULE._row_identity_accepts(
        "dong tien thuan tu hoat dong kinh doanh",
        "Lưu chuyển tiền thuần từ hoạt động kinh doanh",
    )[0]
    assert MODULE._row_identity_accepts(
        "khoan phai thu ngan han khac", "Phải thu ngắn hạn khác"
    )[0]
    assert not MODULE._row_identity_accepts(
        "tong khoan phai thu khac", "Các khoản khác phải thu Nhà nước"
    )[0]


def test_row_identity_requires_requested_horizon() -> None:
    assert not MODULE._row_identity_accepts(
        "du no vay dai han", "Vay ngắn hạn"
    )[0]
    assert MODULE._row_identity_accepts(
        "du no vay dai han", "Vay dài hạn"
    )[0]
