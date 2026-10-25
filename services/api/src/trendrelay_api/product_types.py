"""What kind of thing a product is, as far as a recipe's promises go.

A recipe promises different things for different products: a worn garment
stays opaque and keeps its cut, a bottle keeps its label, a voucher has
nothing to photograph. The type is read from the category path the product's
own listing carries (`integrations.shopee_listing`), at read time, so it
follows the listing whenever that is refreshed and needs no stored column.

The types are few on purpose. They are chosen by what changes in a prompt,
not by the marketplace's tree: everything a person wears on the body is
`apparel`, whatever aisle it sits in; anything else worn is `accessory`; what
goes on skin, hair or teeth is `beauty`; food and drink is `food`; vouchers
and services are `service`; and every other physical thing is `goods`.
Children's clothing and accessories count as `goods`, so no recipe puts a
child in the picture. A product with no category path is `unknown`.
"""

from __future__ import annotations

from typing import Any

PRODUCT_TYPES = ("apparel", "accessory", "beauty", "goods", "food", "service")
UNKNOWN = "unknown"

#: Category path prefixes, most specific first; the first match wins. The
#: names are Shopee Vietnam's, as the listing carries them - it gives names,
#: not ids. A top level not listed here is `goods`, the neutral wording.
_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("Thể Thao & Dã Ngoại", "Thời Trang Thể Thao & Dã Ngoại"), "apparel"),
    (("Thể Thao & Dã Ngoại", "Giày Thể Thao"), "accessory"),
    (("Thể Thao & Dã Ngoại", "Phụ Kiện Thể Thao & Dã Ngoại", "Đồ Bảo Hộ Gym"), "accessory"),
    (("Thể Thao & Dã Ngoại",), "goods"),
    (("Thời Trang Nữ",), "apparel"),
    (("Thời Trang Nam",), "apparel"),
    (("Phụ Kiện Thời Trang",), "accessory"),
    (("Giày Dép Nữ", "Phụ kiện giày dép"), "goods"),
    (("Giày Dép Nam", "Phụ kiện giày dép"), "goods"),
    (("Giày Dép Nữ",), "accessory"),
    (("Giày Dép Nam",), "accessory"),
    (("Du lịch & Hành lý",), "accessory"),
    (("Túi Ví Nữ",), "accessory"),
    (("Túi Ví Nam",), "accessory"),
    (("Đồng Hồ",), "accessory"),
    (("Sắc Đẹp",), "beauty"),
    (("Sức Khỏe", "Chăm sóc cá nhân"), "beauty"),
    (("Thực phẩm và đồ uống",), "food"),
    (("Voucher & Dịch vụ",), "service"),
)


def _path(listing: dict[str, Any] | None) -> tuple[str, ...]:
    categories = (listing or {}).get("categories") or []
    return tuple(str(name).strip() for name in categories if str(name).strip())


def product_type(listing: dict[str, Any] | None) -> str:
    """The product's type, from its listing's category path."""
    path = _path(listing)
    if not path:
        return UNKNOWN
    for prefix, kind in _RULES:
        if path[: len(prefix)] == prefix:
            return kind
    return "goods"


#: How an error message names each type.
LABELS = {
    "apparel": "clothing",
    "accessory": "accessories",
    "beauty": "beauty and personal care",
    "goods": "everyday goods",
    "food": "food and drink",
    "service": "vouchers and services",
    UNKNOWN: "an unknown type",
}
