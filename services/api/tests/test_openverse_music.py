"""Music sourced on demand, and the licence rule it may never break.

TrendRelay publishes affiliate content, which is commercial use. Only CC0 and
CC BY music may come in: CC0 owes nothing, CC BY owes a credit the app adds for
the operator. Everything here pins that rule from both ends - what a search may
offer, and what an import will accept - without touching the network.
"""

from __future__ import annotations

from typing import Any

import pytest

from trendrelay_api.integrations import openverse_music as music

TRACK_ID = "3b4580e1-1454-4b4c-a03a-e03bf4af0849"


def row(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": TRACK_ID,
        "title": "Upbeat Corporate",
        "creator": "Soundrider",
        "license": "by",
        "license_version": "3.0",
        "license_url": "https://creativecommons.org/licenses/by/3.0/",
        "foreign_landing_url": "https://www.jamendo.com/track/1670486",
        "url": "https://mp3d.jamendo.com/download/track/1670486/mp32",
        "filetype": "mp32",
        "duration": 151000,
        "source": "jamendo",
        "mature": False,
        "genres": ["electronic"],
    }
    return {**base, **overrides}


@pytest.fixture
def answering(monkeypatch):
    """Stand in for Openverse, recording what was asked of it."""
    asked: list[tuple[str, dict[str, Any] | None]] = []

    def install(body: Any) -> list[tuple[str, dict[str, Any] | None]]:
        def fake_get(path: str, params: dict[str, Any] | None = None) -> Any:
            asked.append((path, params))
            return body
        monkeypatch.setattr(music, "_get", fake_get)
        return asked

    return install


# --- the licence rule --------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "version", "expected"),
    [
        ("cc0", "1.0", "CC0-1.0"),
        ("by", "4.0", "CC-BY-4.0"),
        ("by", "3.0", "CC-BY-3.0"),
        ("BY", "4.0", "CC-BY-4.0"),
        # Everything the operator did not accept has no SPDX name here at all.
        ("by-nc", "4.0", None),
        ("by-sa", "4.0", None),
        ("by-nd", "4.0", None),
        ("pdm", "1.0", None),
        ("by", "", None),
        ("", "", None),
    ],
)
def test_only_cc0_and_cc_by_have_a_name_here(code: str, version: str, expected) -> None:
    assert music.spdx(code, version) == expected


def test_a_search_asks_openverse_for_the_allowed_licences_only(answering) -> None:
    asked = answering({"results": [], "result_count": 0, "page_count": 0})

    music.search("upbeat")

    _path, params = asked[0]
    assert params["license"] == "by,cc0"
    assert params["category"] == "music"
    assert params["mature"] == "false"


def test_a_search_drops_what_the_index_let_through_by_mistake(answering) -> None:
    """The index's filter is a courtesy; this rule is a promise.

    A result Openverse returned despite the query - an indexing change, a
    filter that stopped working - is removed here rather than offered.
    """
    answering({
        "results": [
            row(),
            row(id="11111111-1111-1111-1111-111111111111", license="by-nc"),
            row(id="22222222-2222-2222-2222-222222222222", license="by-sa"),
            row(id="33333333-3333-3333-3333-333333333333", license="cc0", license_version="1.0"),
            row(id="44444444-4444-4444-4444-444444444444", mature=True),
        ],
        "result_count": 5,
        "page_count": 1,
    })

    found = music.search("upbeat")["tracks"]

    assert [track["license"] for track in found] == ["CC-BY-3.0", "CC0-1.0"]


def test_an_import_reads_the_licence_back_rather_than_trusting_the_request(answering) -> None:
    """A browser could claim CC0 for a CC BY track and skip its credit.

    So an import takes only an id, and the licence recorded is the one
    Openverse reports now.
    """
    asked = answering(row(license="by", license_version="4.0"))

    found = music.track(TRACK_ID)

    assert asked[0][0] == f"/audio/{TRACK_ID}/"
    assert found.licence == "CC-BY-4.0"


def test_an_import_of_a_refused_licence_says_why(answering) -> None:
    answering(row(license="by-nc"))

    with pytest.raises(music.LicenceRefused) as raised:
        music.track(TRACK_ID)

    assert "BY-NC" in str(raised.value)
    assert "CC0 and CC BY" in str(raised.value)


def test_a_track_whose_licence_changed_since_it_was_found_is_refused(answering) -> None:
    """Found as CC BY, relicensed NC at the source before the import ran."""
    answering(row(license="by-nc", license_version="4.0"))

    with pytest.raises(music.LicenceRefused):
        music.track(TRACK_ID)


def test_a_malformed_id_never_reaches_the_network(answering) -> None:
    asked = answering(row())

    with pytest.raises(ValueError):
        music.track("../../etc/passwd")

    assert asked == []


# --- the credit --------------------------------------------------------------


def test_cc_by_owes_a_credit_and_cc0_owes_none() -> None:
    assert music.credit_required("CC-BY-4.0") is True
    assert music.credit_required("CC-BY-3.0") is True
    assert music.credit_required("CC0-1.0") is False
    assert music.credit_required(None) is False


def test_the_credit_names_title_author_and_licence() -> None:
    line = music.credit_line("Upbeat Corporate", "Soundrider", "CC-BY-3.0")

    assert line == 'Music: "Upbeat Corporate" by Soundrider (CC BY 3.0)'


def test_the_credit_carries_no_link() -> None:
    """Several networks read a link in a caption as spam.

    The licence URL and the track's page stay on the asset; the caption credit
    names what the licence needs named.
    """
    line = music.credit_line("Track", "Someone", "CC-BY-4.0")

    assert "http" not in line


def test_a_track_with_no_named_creator_still_gets_a_credit() -> None:
    assert music.credit_line("Track", None, "CC-BY-4.0") == 'Music: "Track" (CC BY 4.0)'


def test_cc0_produces_no_credit_line() -> None:
    assert music.credit_line("Track", "Someone", "CC0-1.0") is None


# --- what can be saved -------------------------------------------------------


@pytest.mark.parametrize(
    ("filetype", "offered"),
    [("mp3", True), ("mp32", True), ("ogx", True), ("wav", True), ("midi", False), ("", False)],
)
def test_only_file_types_the_library_can_ingest_are_offered(answering, filetype, offered) -> None:
    answering({"results": [row(filetype=filetype)], "result_count": 1, "page_count": 1})

    assert bool(music.search("x")["tracks"]) is offered


def test_a_plain_http_file_url_is_not_offered(answering) -> None:
    answering({
        "results": [row(url="http://example.test/a.mp3")],
        "result_count": 1,
        "page_count": 1,
    })

    assert music.search("x")["tracks"] == []


def test_an_empty_query_is_refused_before_asking() -> None:
    with pytest.raises(ValueError):
        music.search("   ")
