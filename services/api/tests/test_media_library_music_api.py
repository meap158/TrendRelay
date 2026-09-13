"""Adding music from the Library: the licence comes from Openverse, never the request.

The Openverse module is covered on its own; these pin what the routes promise
on top of it - confirmation before anything is fetched, the licence and credit
recorded on the asset, and a refused licence answered in plain words.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from test_media_library_api import create_workspace, request, setup_function, teardown_function

from trendrelay_api import media_library, media_library_api
from trendrelay_api.integrations import openverse_music as music

__all__ = ["setup_function", "teardown_function"]

TRACK_ID = "3b4580e1-1454-4b4c-a03a-e03bf4af0849"


def found(**overrides) -> music.Track:
    base = {
        "id": TRACK_ID,
        "title": "Upbeat Corporate",
        "creator": "Soundrider",
        "licence": "CC-BY-3.0",
        "licence_url": "https://creativecommons.org/licenses/by/3.0/",
        "landing_url": "https://www.jamendo.com/track/1670486",
        "file_url": "https://mp3d.jamendo.com/download/track/1670486/mp32",
        "suffix": ".mp3",
        "duration_ms": 151000,
        "source": "jamendo",
        "genres": ("electronic",),
    }
    return music.Track(**{**base, **overrides})


def stand_in_for_openverse(monkeypatch, tmp_path: Path, track: music.Track) -> list[str]:
    """Openverse answers with `track`, and its file lands under tmp_path."""
    fetched: list[str] = []

    def fake_download(item: music.Track, destination: Path) -> Path:
        fetched.append(item.id)
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / f"{item.id}{item.suffix}"
        target.write_bytes(b"ID3-music-" + item.id.encode())
        return target

    monkeypatch.setattr(music, "track", lambda _id: track)
    monkeypatch.setattr(music, "download", fake_download)
    monkeypatch.setattr(media_library_api, "MUSIC_DOWNLOAD_ROOT", tmp_path / "music")
    monkeypatch.setattr(
        media_library,
        "get_settings",
        lambda: SimpleNamespace(publishing_media_root_list=[str(tmp_path)]),
    )
    return fetched


def import_path(workspace_id: str) -> str:
    return f"/api/workspaces/{workspace_id}/media/library/music/imports"


def test_adding_music_needs_confirmation_before_anything_is_fetched(
    tmp_path: Path, monkeypatch
) -> None:
    fetched = stand_in_for_openverse(monkeypatch, tmp_path, found())
    workspace_id = create_workspace()

    response = asyncio.run(
        request("POST", import_path(workspace_id), json={"track_id": TRACK_ID})
    )

    assert response.status_code == 400
    assert fetched == []


def test_an_added_track_records_its_licence_and_credit(tmp_path: Path, monkeypatch) -> None:
    stand_in_for_openverse(monkeypatch, tmp_path, found())
    workspace_id = create_workspace()

    response = asyncio.run(
        request(
            "POST",
            import_path(workspace_id),
            json={"track_id": TRACK_ID, "confirm_external_action": True},
        )
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["track"]["credit"] == 'Music: "Upbeat Corporate" by Soundrider (CC BY 3.0)'
    job = media_library.get_job_record(body["job"]["id"], factory=media_library.JOB_SESSION_FACTORY)
    payload = job["payload"]
    assert payload["license"] == "CC-BY-3.0"
    assert payload["license_url"] == "https://creativecommons.org/licenses/by/3.0/"
    assert payload["attribution"] == 'Music: "Upbeat Corporate" by Soundrider (CC BY 3.0)'
    assert payload["source_type"] == "openverse-music"
    assert payload["source_url"] == "https://www.jamendo.com/track/1670486"
    assert Path(payload["source_path"]).parent == (tmp_path / "music" / workspace_id).resolve()


def test_a_cc0_track_is_recorded_as_owing_no_credit(tmp_path: Path, monkeypatch) -> None:
    stand_in_for_openverse(
        monkeypatch, tmp_path, found(licence="CC0-1.0", licence_url=None)
    )
    workspace_id = create_workspace()

    response = asyncio.run(
        request(
            "POST",
            import_path(workspace_id),
            json={"track_id": TRACK_ID, "confirm_external_action": True},
        )
    )

    assert response.status_code == 202, response.text
    job = media_library.get_job_record(
        response.json()["job"]["id"], factory=media_library.JOB_SESSION_FACTORY
    )
    assert job["payload"]["license"] == "CC0-1.0"
    assert job["payload"]["attribution"] is None


def test_a_licence_claimed_in_the_request_is_ignored(tmp_path: Path, monkeypatch) -> None:
    """A browser saying CC0 does not make a CC BY track owe nothing."""
    stand_in_for_openverse(monkeypatch, tmp_path, found())
    workspace_id = create_workspace()

    response = asyncio.run(
        request(
            "POST",
            import_path(workspace_id),
            json={
                "track_id": TRACK_ID,
                "license": "CC0-1.0",
                "attribution": None,
                "confirm_external_action": True,
            },
        )
    )

    assert response.status_code == 202, response.text
    job = media_library.get_job_record(
        response.json()["job"]["id"], factory=media_library.JOB_SESSION_FACTORY
    )
    assert job["payload"]["license"] == "CC-BY-3.0"
    assert job["payload"]["attribution"]


def test_a_refused_licence_is_answered_in_words_and_nothing_is_saved(
    tmp_path: Path, monkeypatch
) -> None:
    fetched = stand_in_for_openverse(monkeypatch, tmp_path, found())

    def refuse(_id: str) -> music.Track:
        raise music.LicenceRefused("This track is licensed BY-NC, and only CC0 and CC BY ...")

    monkeypatch.setattr(music, "track", refuse)
    workspace_id = create_workspace()

    response = asyncio.run(
        request(
            "POST",
            import_path(workspace_id),
            json={"track_id": TRACK_ID, "confirm_external_action": True},
        )
    )

    assert response.status_code == 422
    assert "CC0 and CC BY" in response.json()["detail"]
    assert fetched == []


def test_openverse_being_down_is_a_bad_gateway_not_a_bad_request(
    tmp_path: Path, monkeypatch
) -> None:
    stand_in_for_openverse(monkeypatch, tmp_path, found())

    def unavailable(*_args, **_kwargs):
        raise music.MusicUnavailable("Openverse could not be reached.")

    monkeypatch.setattr(music, "search", unavailable)
    workspace_id = create_workspace()

    response = asyncio.run(
        request("GET", f"/api/workspaces/{workspace_id}/media/library/music/search?q=upbeat")
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "Openverse could not be reached."


def test_search_passes_the_query_and_page_through(tmp_path: Path, monkeypatch) -> None:
    asked: list[tuple[str, int]] = []

    def fake_search(query: str, *, page: int = 1, page_size: int = 20) -> dict:
        asked.append((query, page))
        return {"tracks": [found().payload()], "total": 1, "page": page, "page_count": 1}

    monkeypatch.setattr(music, "search", fake_search)
    workspace_id = create_workspace()

    response = asyncio.run(
        request(
            "GET",
            f"/api/workspaces/{workspace_id}/media/library/music/search?q=lofi&page=2",
        )
    )

    assert response.status_code == 200, response.text
    assert asked == [("lofi", 2)]
    assert response.json()["tracks"][0]["license"] == "CC-BY-3.0"
