"""Identifier parsing and the matching keys that decide what counts as one book."""

from __future__ import annotations

import pytest

from trendrelay_api.catalog_identifiers import (
    core_title,
    display_title,
    identifier_from_url,
    infer_product_form,
    isbn10_check_digit,
    isbn10_to_isbn13,
    isbn13_check_digit,
    loose_work_key,
    normalise_author,
    normalise_title,
    parse_identifier,
    work_key,
)


def test_isbn13_check_digit_matches_a_known_number() -> None:
    assert isbn13_check_digit("9780306406157") == "7"


def test_979_prefixed_isbn_is_recognised() -> None:
    # The 979 range is what Amazon's own print imprint issues, so it turns up in
    # any KDP catalog and must not be mistaken for a malformed number.
    identifier = parse_identifier("9798187897681")
    assert identifier.scheme == "isbn13"
    assert identifier.valid is True
    assert identifier.canonical == "9798187897681"


def test_isbn13_with_hyphens_and_prefix_is_read() -> None:
    identifier = parse_identifier("ISBN 978-0-306-40615-7")
    assert identifier.scheme == "isbn13"
    assert identifier.valid is True


def test_isbn13_with_a_wrong_check_digit_is_reported_invalid() -> None:
    identifier = parse_identifier("9780306406158")
    assert identifier.scheme == "isbn13"
    assert identifier.valid is False


def test_isbn10_check_digit_can_be_x() -> None:
    assert isbn10_check_digit("043942089") == "X"


def test_isbn10_widens_to_the_same_manifestation_as_its_isbn13() -> None:
    # An ISBN-10 and its ISBN-13 name one edition, not two, so both have to land
    # on one canonical value or a catalog holding both would double-count it.
    assert isbn10_to_isbn13("0306406152") == "9780306406157"
    assert parse_identifier("0306406152").canonical == "9780306406157"


def test_b_prefixed_key_is_an_asin_not_an_isbn() -> None:
    identifier = parse_identifier("B0H9CLBXDP")
    assert identifier.scheme == "asin"
    assert identifier.is_amazon_only is True
    assert identifier.valid is True


def test_a_ten_digit_amazon_key_is_still_read_as_an_isbn10() -> None:
    # Amazon uses a print book's ISBN-10 as its ASIN, so only the B prefix marks
    # a key Amazon assigned itself.
    identifier = parse_identifier("0306406152")
    assert identifier.scheme == "isbn10"
    assert identifier.is_amazon_only is False


def test_empty_and_junk_identifiers_are_not_valid() -> None:
    assert parse_identifier(None).valid is False
    assert parse_identifier("   ").valid is False
    assert parse_identifier("not-a-book").scheme == "unknown"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("The Silent Patient", "silent patient"),
        ("Dune (Kindle Edition)", "dune"),
        ("Dune [Audiobook, Unabridged]", "dune"),
        ("Atomic Habits, Revised Edition", "atomic habits"),
        ("Sapiens: A Brief History — 2nd Edition", "sapiens a brief history"),
        ("Crime & Punishment", "crime and punishment"),
        ("Les Misérables", "les miserables"),
        ("Educated  —  Paperback", "educated"),
    ],
)
def test_titles_reduce_to_the_work_they_name(title: str, expected: str) -> None:
    assert normalise_title(title) == expected


def test_stacked_edition_wording_is_fully_removed() -> None:
    assert normalise_title("Atomic Habits, Revised Edition, Hardcover") == "atomic habits"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("The Quiet Ledger (Kindle Edition)", "The Quiet Ledger"),
        ("Sapiens: A Brief History — 2nd Edition", "Sapiens: A Brief History"),
        ("Atomic Habits, Revised Edition, Hardcover", "Atomic Habits"),
        ("Les Misérables", "Les Misérables"),
    ],
)
def test_display_title_removes_edition_wording_but_keeps_the_spelling(
    title: str, expected: str
) -> None:
    # A work grouped from three editions must not be named after whichever one
    # carried the longest format suffix, and accents belong to the title.
    assert display_title(title) == expected


def test_core_title_drops_the_subtitle() -> None:
    assert core_title("Sapiens: A Brief History of Humankind") == "sapiens"
    assert core_title("Educated - A Memoir") == "educated"


@pytest.mark.parametrize(
    ("author", "expected"),
    [
        ("James Clear", "clear j"),
        ("Clear, James", "clear j"),
        ("J. R. R. Tolkien", "tolkien j"),
        ("Yuval Noah Harari", "harari y"),
        ("Harari, Yuval Noah", "harari y"),
        ("Homer", "homer"),
        ("Neil Gaiman & Terry Pratchett", "gaiman n"),
        ("Michelle Obama; read by the author", "obama m"),
        (None, ""),
    ],
)
def test_bylines_reduce_to_surname_and_initial(author: str | None, expected: str) -> None:
    assert normalise_author(author) == expected


def test_editions_of_one_book_share_a_work_key() -> None:
    paperback = work_key("Atomic Habits", "James Clear")
    kindle = work_key("Atomic Habits (Kindle Edition)", "Clear, James")
    audio = work_key("Atomic Habits [Audiobook]", "James Clear")
    assert paperback == kindle == audio


def test_different_books_sharing_a_title_do_not_share_a_key() -> None:
    # The reason author is part of the key: many unrelated books are called
    # "Blink", and merging them would pool their revenue with nothing to show it.
    assert work_key("Blink", "Malcolm Gladwell") != work_key("Blink", "Ted Dekker")


def test_a_title_without_an_author_still_produces_a_key() -> None:
    assert work_key("Atomic Habits", None) == "atomic habits|"


def test_an_empty_title_produces_no_key_at_all() -> None:
    assert work_key("", "James Clear") == ""
    assert work_key(None, "James Clear") == ""


def test_the_loose_key_bridges_a_missing_subtitle() -> None:
    with_subtitle = loose_work_key("Sapiens: A Brief History of Humankind", "Yuval Noah Harari")
    without = loose_work_key("Sapiens", "Harari, Yuval Noah")
    assert with_subtitle == without
    # ...but the strict key does not, which is why that match only ever gets
    # suggested rather than applied.
    assert work_key("Sapiens: A Brief History of Humankind", "Yuval Noah Harari") != work_key(
        "Sapiens", "Harari, Yuval Noah"
    )


@pytest.mark.parametrize(
    ("hints", "expected"),
    [
        (("Atomic Habits (Kindle Edition)",), "ebook"),
        (("Atomic Habits", "Audible Audiobook"), "audiobook"),
        (("Atomic Habits Hardcover",), "hardcover"),
        (("Atomic Habits, Mass Market Paperback",), "paperback"),
        (("Atomic Habits",), "unknown"),
        ((None, ""), "unknown"),
    ],
)
def test_product_form_is_read_from_whatever_names_it(
    hints: tuple[str | None, ...], expected: str
) -> None:
    assert infer_product_form(*hints) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.amazon.com/Tidewrack/dp/B0H9CLBXDP/ref=sr_1_1", "B0H9CLBXDP"),
        ("https://www.amazon.co.uk/gp/product/B0H9CLBXDP?tag=aff-21", "B0H9CLBXDP"),
        ("https://www.amazon.com/dp/0306406152", "0306406152"),
        ("https://merchant.example/books/tidewrack", ""),
        # A ten-character path segment that fails its checksum is not an
        # identifier, and guessing one would attach spend to the wrong book.
        ("https://www.amazon.com/dp/0306406153", ""),
        (None, ""),
    ],
)
def test_identifiers_are_read_out_of_product_links(url: str | None, expected: str) -> None:
    assert identifier_from_url(url) == expected


def test_the_more_specific_format_wins() -> None:
    # A Kindle-branded audiobook is an audiobook; filing it as an ebook would put
    # two different products in the same format bucket.
    assert infer_product_form("Kindle Audiobook Edition") == "audiobook"
