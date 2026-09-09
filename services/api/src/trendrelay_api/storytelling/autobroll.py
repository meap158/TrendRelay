"""Filling a narration's pictures from stock, when nobody chose any.

Storytelling already lets somebody search Pexels, import a clip, and see it
suggested against a sentence. This does that whole loop without them: read each
sentence, search stock for the few words it is about, bring one clip in, and
leave the existing matcher to place it. Nothing here is a second path - an
auto-imported clip is an ordinary Library asset, matched on the words it was
`searched_for` like any other, so a hand-built story and an auto-built one meet
at the same shot list and the same renderer.

Two things make that safe to run unattended. The query is drawn from the same
tokeniser the matcher scores on, so a clip is searched for exactly the words it
will later be matched on - the sentence that asked for it is the sentence it
lands on. And every step is best-effort: no provider key, a search that finds
nothing, a download that fails - each leaves that one sentence absent, and the
caller falls back to whatever the Library already holds. It can only add
pictures a narration did not have, never take one away.
"""

from __future__ import annotations

import re

from trendrelay_api.autocut.jobs import canonical_aspect
from trendrelay_api.campaign_offer_matcher import tokens

#: How many words of a sentence become its stock search. Every word finds
#: nothing; the two or three most specific ones find the shot it is about.
QUERY_TERMS = 3

#: Latin word runs, to recover a sentence's own order among the words the
#: tokeniser kept (which come back as an unordered set).
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)

#: The Pexels orientation for each canvas shape, so a portrait narration is
#: filled with upright clips rather than letterboxed wide ones.
_ORIENTATION = {"portrait": "portrait", "square": "square", "landscape": "landscape"}


def orientation_for(aspect: str) -> str:
    """The stock-search orientation for a video shape named either way."""
    return _ORIENTATION.get(canonical_aspect(aspect), "")


def sentence_query(line: str, *, max_terms: int = QUERY_TERMS) -> str:
    """The stock search for one sentence: its few most specific words.

    The words come from the matcher's own tokeniser, so what a clip is searched
    for is exactly what it is later scored on. Longer words go first as a plain
    proxy for specificity - "photosynthesis" is a better search than "the" - and
    the sentence's own order is kept among those chosen so the phrase still
    reads. Empty when a sentence is all stopwords and has nothing to find.
    """
    allow = tokens(line)
    if not allow:
        return ""
    ordered: list[str] = []
    lowered: set[str] = set()
    for raw in _WORD.findall(line):
        folded = raw.casefold()
        if folded in allow and folded not in lowered:
            ordered.append(raw)
            lowered.add(folded)
    if not ordered:
        # A sentence with no Latin words (the tokeniser returns Han characters
        # and bigrams); take the longest few as they are.
        ordered = sorted(allow, key=len, reverse=True)
    chosen = sorted(range(len(ordered)), key=lambda i: len(ordered[i]), reverse=True)
    keep = sorted(chosen[:max_terms])
    return " ".join(ordered[i] for i in keep)


def _ingest_now(record: dict, *, factory) -> str | None:
    """The asset id for a just-queued stock import, running the ingest here.

    The worker runs one kind of job at a time, so a job that queued its own
    ingest and waited for the Library lane to pick it up would wait for a turn
    that only comes after this job ends. Running it inline is what lets the
    imported clip exist by the time the arranger looks for it. A duplicate is
    resolved on the spot with no job to run.
    """
    if record.get("asset_id"):  # a duplicate, resolved without a job
        return str(record["asset_id"])
    job_id = record.get("id")
    if not job_id:
        return None
    from trendrelay_api.media_library import run_ingest_job

    try:
        done = run_ingest_job(job_id, worker_id="storytelling-autocreate")
    except Exception:  # noqa: BLE001 - a clip that will not ingest is one sentence lost
        return None
    asset_id = (done.get("result") or {}).get("asset_id")
    return str(asset_id) if asset_id else None


def fill_from_stock(
    queries: list[str],
    *,
    workspace_id: str,
    actor_user_id: str,
    kind: str = "video",
    orientation: str = "",
    factory,
) -> dict[int, str]:
    """Import one stock clip for each sentence's query; return line -> asset id.

    A repeated query reuses the clip it already brought in rather than importing
    it twice - a narration that says "the ocean" three times opens on one ocean,
    not three downloads of it. Best-effort throughout: an unconfigured provider
    returns nothing at all, and any single search or download that fails simply
    leaves that sentence out of the result.
    """
    from trendrelay_api.integrations import pexels

    if not pexels.provider_status().get("configured"):
        return {}
    search_kind = "video" if kind == "video" else "image"
    by_query: dict[str, str] = {}
    placed: dict[int, str] = {}
    for index, query in enumerate(queries):
        query = query.strip()
        if not query:
            continue
        if query in by_query:
            placed[index] = by_query[query]
            continue
        try:
            found = pexels.search(
                query, kind=search_kind, per_page=3, orientation=orientation,
            )
            candidate = next(iter(found.get("results") or []), None)
            if candidate is None:
                continue
            record = pexels.import_candidate(
                candidate,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                query=query,
                factory=factory,
            )
        except pexels.PexelsUnavailable:
            continue
        asset_id = _ingest_now(record, factory=factory)
        if asset_id:
            by_query[query] = asset_id
            placed[index] = asset_id
    return placed
