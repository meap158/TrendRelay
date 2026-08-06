"""Book identifiers and the matching keys that group editions of one work.

The vocabulary here is the publishing industry's own rather than something
invented for TrendRelay. FRBR — the model behind ONIX for Books, and the same
separation Amazon, Goodreads and OpenLibrary present — distinguishes the
abstract *work* from each *manifestation* of it. An ISBN or ASIN identifies a
manifestation, never a work: the paperback, the hardback and the Kindle edition
of one book each carry a different number by design.

That distinction is the whole point of this module. Ad spend and royalties
recorded against separate identifiers are measuring the same book, so anything
that divides revenue by spend has to roll up to the work first or it divides
numbers that do not belong to each other.

Everything here is pure: no database, no network. Grouping decisions are made
from these functions and then *stored*, because a key computed at read time
would re-guess on every import and move reporting history underneath the user.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

IdentifierScheme = Literal["isbn13", "isbn10", "asin", "unknown"]
ProductForm = Literal["hardcover", "paperback", "ebook", "audiobook", "unknown"]

#: ONIX for Books List 150 (ProductForm) codes, so an export lines up with what
#: distributors already expect. "00" is the list's own "undefined".
ONIX_PRODUCT_FORM = {
    "hardcover": "BB",
    "paperback": "BC",
    "ebook": "EA",
    "audiobook": "AJ",
    "unknown": "00",
}

PRODUCT_FORM_LABEL = {
    "hardcover": "Hardcover",
    "paperback": "Paperback",
    "ebook": "eBook",
    "audiobook": "Audiobook",
    "unknown": "Unknown format",
}

_ASIN = re.compile(r"^B[0-9A-Z]{9}$")
_LEADING_ARTICLE = re.compile(r"^(?:the|a|an)\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Qualifiers publishers append to a title to name the *manifestation*. They are
# exactly what must come off before two manifestations can be recognised as one
# work, since "Dune (Kindle Edition)" and "Dune (Mass Market Paperback)" differ
# only by the part that names the format.
_FORM_QUALIFIER = re.compile(
    r"[\(\[\{][^\)\]\}]*\b(?:"
    r"kindle|paperback|hardcover|hardback|audiobook|audio|audible|ebook|e-book|"
    r"unabridged|abridged|large\s*print|mass\s*market|box(?:ed)?\s*set|"
    r"illustrated|annotated|reprint|edition|ed\.?"
    r")\b[^\)\]\}]*[\)\]\}]",
    re.IGNORECASE,
)
_EDITION_SUFFIX = re.compile(
    r"[,:;\-–—\s]+(?:"
    r"\d+(?:st|nd|rd|th)\s+edition|"
    r"(?:revised|expanded|updated|anniversary|deluxe|collector'?s|special|"
    r"international|student|teacher'?s|new)\s+edition|"
    r"edition\s+\d+|"
    r"kindle\s+edition|"
    r"(?:paperback|hardcover|hardback|audiobook|ebook|e-book)"
    r")\s*$",
    re.IGNORECASE,
)

_FORM_HINTS: tuple[tuple[ProductForm, re.Pattern[str]], ...] = (
    ("audiobook", re.compile(r"\b(?:audiobook|audible|audio\s*cd|narrat)", re.I)),
    ("ebook", re.compile(r"\b(?:kindle|ebook|e-book|epub|digital)\b", re.I)),
    ("hardcover", re.compile(r"\b(?:hardcover|hardback|hardbound)\b", re.I)),
    ("paperback", re.compile(r"\b(?:paperback|softcover|mass\s*market|trade\s*paper)\b", re.I)),
)


@dataclass(frozen=True)
class BookIdentifier:
    """A catalog key read as the industry identifier it actually is."""

    value: str
    scheme: IdentifierScheme
    #: ISBN-13 equivalent where one exists, so an ISBN-10 and its ISBN-13 are
    #: recognised as the same manifestation rather than two.
    canonical: str
    valid: bool

    @property
    def is_amazon_only(self) -> bool:
        """True for a B-prefixed ASIN, which Amazon assigns and no registry does."""
        return self.scheme == "asin"


def _strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def isbn13_check_digit(body: str) -> str:
    """The final digit of an ISBN-13, weighting alternate digits 1 and 3."""
    total = sum(int(digit) * (3 if index % 2 else 1) for index, digit in enumerate(body[:12]))
    return str((10 - total % 10) % 10)


def isbn10_check_digit(body: str) -> str:
    """The final character of an ISBN-10, which is mod 11 and so may be 'X'."""
    total = sum(int(digit) * (10 - index) for index, digit in enumerate(body[:9]))
    remainder = (11 - total % 11) % 11
    return "X" if remainder == 10 else str(remainder)


def isbn10_to_isbn13(isbn10: str) -> str:
    """Widen an ISBN-10 to its ISBN-13 form by prefixing the 978 Bookland code."""
    body = "978" + isbn10[:9]
    return body + isbn13_check_digit(body)


def parse_identifier(raw: str | None) -> BookIdentifier:
    """Read a catalog key as an ISBN-13, ISBN-10 or ASIN.

    Ambiguity is real and worth naming: a print book's Amazon ASIN *is* its
    ISBN-10, so a ten-character key is only an ASIN when it starts with the "B"
    that Amazon reserves for keys it assigns itself.
    """
    text = (raw or "").strip().upper()
    if not text:
        return BookIdentifier(value="", scheme="unknown", canonical="", valid=False)

    compact = re.sub(r"[\s\-‐-―]", "", text)
    if compact.startswith("ISBN"):
        compact = compact[4:].lstrip(":")

    if _ASIN.match(compact):
        return BookIdentifier(value=compact, scheme="asin", canonical=compact, valid=True)

    if len(compact) == 13 and compact.isdigit():
        valid = compact[12] == isbn13_check_digit(compact)
        return BookIdentifier(value=compact, scheme="isbn13", canonical=compact, valid=valid)

    if len(compact) == 10 and compact[:9].isdigit():
        valid = compact[9] == isbn10_check_digit(compact)
        canonical = isbn10_to_isbn13(compact) if valid else compact
        return BookIdentifier(value=compact, scheme="isbn10", canonical=canonical, valid=valid)

    return BookIdentifier(value=text, scheme="unknown", canonical=text, valid=False)


_URL_IDENTIFIER = re.compile(
    r"/(?:dp|gp/product|gp/aw/d|product|ebook/dp)/([A-Z0-9]{10})(?:[/?#]|$)",
    re.IGNORECASE,
)
_URL_QUERY_IDENTIFIER = re.compile(r"[?&](?:asin|isbn)=([A-Z0-9]{10,13})", re.IGNORECASE)


def identifier_from_url(url: str | None) -> str:
    """Pull the identifier out of a product link.

    Affiliate imports frequently carry no identifier column while every row's
    URL contains one, because Amazon puts the ASIN in the path. Reading it there
    is the difference between a catalog that can be grouped and one that cannot.
    """
    if not url:
        return ""
    match = _URL_IDENTIFIER.search(url) or _URL_QUERY_IDENTIFIER.search(url)
    if not match:
        return ""
    candidate = parse_identifier(match.group(1))
    return candidate.value if candidate.valid else ""


def infer_product_form(*hints: str | None) -> ProductForm:
    """Guess the manifestation's format from whatever text names it.

    Order matters: "Kindle audiobook" is an audiobook, and a title carrying both
    words should not be filed as an ebook, so the more specific form wins.
    """
    haystack = " ".join(hint for hint in hints if hint)
    if not haystack.strip():
        return "unknown"
    for form, pattern in _FORM_HINTS:
        if pattern.search(haystack):
            return form
    return "unknown"


def normalise_title(title: str | None) -> str:
    """Reduce a title to the part that names the work rather than the edition."""
    text = _strip_accents(title or "").lower()
    text = text.replace("&", " and ")
    text = _FORM_QUALIFIER.sub(" ", text)
    # Applied repeatedly because publishers stack them: "..., Revised Edition,
    # Paperback" needs both suffixes removed, not just the outermost one.
    for _ in range(3):
        stripped = _EDITION_SUFFIX.sub("", text)
        if stripped == text:
            break
        text = stripped
    text = _NON_ALNUM.sub(" ", text).strip()
    text = _LEADING_ARTICLE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def display_title(title: str | None) -> str:
    """The title with edition wording removed but its own spelling left intact.

    Distinct from `normalise_title`, which flattens case and punctuation to build
    a key nobody reads. This is what gets shown as the book's name, so a work
    grouped from three editions is not labelled after whichever of them happened
    to have the longest format suffix.
    """
    text = (title or "").strip()
    text = _FORM_QUALIFIER.sub(" ", text)
    for _ in range(3):
        stripped = _EDITION_SUFFIX.sub("", text)
        if stripped == text:
            break
        text = stripped
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip(" ,:;-–—")


def core_title(title: str | None) -> str:
    """The main title with any subtitle dropped.

    Editions of one book frequently disagree about the subtitle — the ebook
    keeps it, the paperback cover drops it — so this is the looser key. It is
    only ever used to *suggest* a grouping, never to apply one automatically.
    """
    text = _strip_accents(title or "")
    head = re.split(r"[:–—]|\s-\s", text, maxsplit=1)[0]
    return normalise_title(head)


def normalise_author(author: str | None) -> str:
    """Reduce a byline to a surname and first initial.

    Libraries match on the main entry, and so does this: the first name listed
    is the one editions agree on, where the trailing contributors ("with…",
    "illustrated by…") vary between formats. Surname plus initial absorbs the
    other common disagreement, "J. R. R. Tolkien" against "John Tolkien".
    """
    text = _strip_accents(author or "").strip()
    if not text:
        return ""

    primary = re.split(r"\s*(?:;|&|,\s*and\s+|\sand\s+|\swith\s+)\s*", text, maxsplit=1)[0]
    primary = primary.strip()

    if "," in primary:
        # "Smith, Jane Q." — the surname already leads.
        surname, _, given = primary.partition(",")
        parts = [surname.strip()] + given.split()
    else:
        parts = primary.split()

    words = [_NON_ALNUM.sub("", part.lower()) for part in parts]
    words = [word for word in words if word]
    if not words:
        return ""
    if len(words) == 1:
        return words[0]

    surname = words[0] if "," in primary else words[-1]
    given = words[1:] if "," in primary else words[:-1]
    initial = next((word[0] for word in given if word), "")
    return f"{surname} {initial}".strip()


def work_key(title: str | None, author: str | None) -> str:
    """The stable key two manifestations of one work share.

    Author is part of the key and not optional decoration. Distinct books share
    titles constantly — there are many books called "Blink" — so a title-only
    key would merge unrelated products and quietly pool their revenue.
    """
    normalised_title = normalise_title(title)
    normalised_author = normalise_author(author)
    if not normalised_title:
        return ""
    return f"{normalised_title}|{normalised_author}"


def loose_work_key(title: str | None, author: str | None) -> str:
    """A subtitle-insensitive key, for suggestions a person still confirms."""
    head = core_title(title)
    if not head:
        return ""
    return f"{head}|{normalise_author(author)}"
