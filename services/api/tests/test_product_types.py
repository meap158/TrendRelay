"""A product's type, read from the category path its listing carries."""

from __future__ import annotations

import pytest

from trendrelay_api.product_types import LABELS, PRODUCT_TYPES, UNKNOWN, product_type

# Category paths as Shopee Vietnam listings carry them, one or more per type,
# including every aisle where the first level alone would mislead.
PATHS = [
    (["Thời Trang Nữ", "Đồ ngủ", "Pyjama"], "apparel"),
    (["Thời Trang Nữ", "Đồ lót", "Áo ngực"], "apparel"),
    (["Thời Trang Nữ", "Vớ/ Tất", "Tất"], "apparel"),
    (["Thời Trang Nam", "Áo", "Áo thun"], "apparel"),
    (["Thể Thao & Dã Ngoại", "Thời Trang Thể Thao & Dã Ngoại", "Áo Lót Thể Thao"], "apparel"),
    (["Thể Thao & Dã Ngoại", "Giày Thể Thao", "Giày Tập Luyện"], "accessory"),
    (["Thể Thao & Dã Ngoại", "Phụ Kiện Thể Thao & Dã Ngoại", "Đồ Bảo Hộ Gym"], "accessory"),
    (["Thể Thao & Dã Ngoại", "Dụng Cụ Thể Thao & Dã Ngoại", "Yoga & Pilates"], "goods"),
    (["Phụ Kiện Thời Trang", "Kính mắt", "Kính mát"], "accessory"),
    (["Phụ Kiện Thời Trang", "Phụ kiện tóc", "Kẹp tóc"], "accessory"),
    (["Giày Dép Nữ", "Xăng-đan và dép", "Xăng-đan đế bằng"], "accessory"),
    (["Giày Dép Nam", "Phụ kiện giày dép", "Khử mùi giày dép"], "goods"),
    (["Du lịch & Hành lý", "Túi du lịch", "Túi trống"], "accessory"),
    (["Sắc Đẹp", "Trang điểm", "Trang điểm môi"], "beauty"),
    (["Sắc Đẹp", "Chăm sóc da mặt", "Tinh chất dưỡng"], "beauty"),
    (["Sức Khỏe", "Chăm sóc cá nhân", "Vệ sinh răng miệng"], "beauty"),
    (["Sức Khỏe", "Vật tư y tế", "Bao tay và khẩu trang y tế"], "goods"),
    (["Nhà cửa & Đời sống", "Dụng cụ chăm sóc nhà cửa", "Khăn giấy, giấy ướt"], "goods"),
    (["Điện Thoại & Phụ Kiện", "Phụ kiện", "Kẹp điện thoại"], "goods"),
    (["Văn Phòng Phẩm", "Bút Các Loại", "Bút & Mực"], "goods"),
    (["Mẹ & Bé", "Đồ chơi", "Đồ chơi giáo dục"], "goods"),
    # Children's clothing is shown as a product, so no recipe casts a child.
    (["Thời trang trẻ em & trẻ sơ sinh", "Quần áo trẻ em", "Bộ quần áo"], "goods"),
    (["Thực phẩm và đồ uống", "Đồ ăn vặt", "Khác"], "food"),
    (["Voucher & Dịch vụ", "Sức khỏe & Làm đẹp", "Gym & Yoga"], "service"),
    # An aisle this list has never seen is a plain product, not clothing.
    (["Ô tô", "Phụ kiện"], "goods"),
]


@pytest.mark.parametrize(("path", "expected"), PATHS)
def test_the_category_path_decides_the_type(path: list[str], expected: str) -> None:
    assert product_type({"categories": path}) == expected


def test_a_listing_without_categories_is_unknown() -> None:
    assert product_type(None) == UNKNOWN
    assert product_type({}) == UNKNOWN
    assert product_type({"categories": ["", " "]}) == UNKNOWN


def test_every_type_has_a_label() -> None:
    assert set(LABELS) == {*PRODUCT_TYPES, UNKNOWN}
