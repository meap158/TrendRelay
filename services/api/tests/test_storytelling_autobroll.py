"""Filling a narration's pictures from stock, one sentence at a time.

The promise is the same as auto-reframe's: it can only add a picture a script
did not have, never remove one. So the tests pin the two things that keep it
safe to run unattended - a query drawn from the words the matcher will score
on, and a best-effort loop where an unconfigured provider or a failed download
costs one sentence, not the whole build.
"""

from __future__ import annotations

from trendrelay_api.integrations import pexels
from trendrelay_api.storytelling import autobroll


class _Result:
    """The one field fill_from_stock reads off a search result - it takes the
    first candidate and hands it straight to import_candidate."""

    def __init__(self, ident: str) -> None:
        self.id = ident


def _install_provider(monkeypatch, *, configured, on_search=None, on_import=None) -> None:
    monkeypatch.setattr(pexels, "provider_status", lambda: {"configured": configured})
    if on_search is not None:
        monkeypatch.setattr(pexels, "search", on_search)
    if on_import is not None:
        monkeypatch.setattr(pexels, "import_candidate", on_import)


def test_a_query_is_a_sentence_few_most_specific_words() -> None:
    # The specific words survive; the filler does not.
    query = autobroll.sentence_query("The photosynthesis of a leaf converts sunlight.")
    assert "photosynthesis" in query and "sunlight" in query
    assert "the" not in query.split()
    # A sentence with nothing to search for asks for nothing.
    assert autobroll.sentence_query("!!!") == ""


def test_orientation_follows_the_chosen_shape() -> None:
    assert autobroll.orientation_for("9:16") == "portrait"
    assert autobroll.orientation_for("16:9") == "landscape"
    assert autobroll.orientation_for("portrait") == "portrait"


def test_an_unconfigured_provider_fills_nothing(monkeypatch) -> None:
    # No key, no imports - and crucially no crash, so the build carries on with
    # whatever the Library already holds.
    calls: list[str] = []
    _install_provider(
        monkeypatch, configured=False,
        on_search=lambda q, **_: calls.append(q) or {"results": []},
    )
    assert autobroll.fill_from_stock(
        ["a mountain", "an ocean"],
        workspace_id="w", actor_user_id="u", factory=object(),
    ) == {}
    assert calls == []


def test_one_clip_per_sentence_and_a_repeat_reuses_it(monkeypatch) -> None:
    """A clip is imported once per distinct query and placed on the sentence
    that asked for it; a sentence that repeats a query opens on the same clip
    rather than downloading it twice."""
    imported: list[str] = []

    def fake_search(query, **_):
        return {"results": [_Result(f"id-{query}")]}

    def fake_import(candidate, *, workspace_id, actor_user_id, query="", factory=None):
        imported.append(query)
        # A resolved import hands back an asset id directly (the de-duplicated
        # path), so no ingest job has to run in the test.
        return {"asset_id": f"asset-{query}"}

    _install_provider(monkeypatch, configured=True, on_search=fake_search, on_import=fake_import)

    placed = autobroll.fill_from_stock(
        ["the ocean", "a mountain", "the ocean"],
        workspace_id="w", actor_user_id="u", factory=object(),
    )
    # "the ocean" appears twice as the same query -> one import, and the repeat
    # reuses the first clip.
    assert placed[0] == placed[2] == "asset-the ocean"
    assert placed[1] == "asset-a mountain"
    assert imported.count("the ocean") == 1


def test_a_failed_download_costs_one_sentence_not_the_build(monkeypatch) -> None:
    def fake_search(query, **_):
        return {"results": [_Result(f"id-{query}")]}

    def fake_import(candidate, *, workspace_id, actor_user_id, query="", factory=None):
        if "broken" in query:
            raise pexels.PexelsUnavailable("nope")
        return {"asset_id": f"asset-{query}"}

    _install_provider(monkeypatch, configured=True, on_search=fake_search, on_import=fake_import)

    placed = autobroll.fill_from_stock(
        ["a broken clip", "a good clip"],
        workspace_id="w", actor_user_id="u", factory=object(),
    )
    assert 0 not in placed          # the failed one is simply absent
    assert placed[1] == "asset-a good clip"


def test_a_search_that_finds_nothing_leaves_that_sentence_alone(monkeypatch) -> None:
    def fake_search(query, **_):
        return {"results": [] if "empty" in query else [_Result("id")]}

    _install_provider(
        monkeypatch, configured=True, on_search=fake_search,
        on_import=lambda *a, **k: {"asset_id": "asset-found"},
    )
    placed = autobroll.fill_from_stock(
        ["empty query", "a real query"],
        workspace_id="w", actor_user_id="u", factory=object(),
    )
    assert placed == {1: "asset-found"}
