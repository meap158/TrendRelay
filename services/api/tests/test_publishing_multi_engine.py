"""One post delivered through more than one engine at a time."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from trendrelay_api.integrations import publishing


@pytest.fixture
def media_file(monkeypatch, tmp_path: Path) -> Path:
    """An approved clip and a full set of engine credentials, contacting nothing."""
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"test-video")
    monkeypatch.setattr(
        publishing,
        "get_settings",
        lambda: SimpleNamespace(
            publishing_media_root_list=[str(tmp_path)],
            publishing_provider="bundle_social",
        ),
    )
    credentials = {
        "BUNDLE_SOCIAL_API_KEY": "pk_test",
        "BUNDLE_SOCIAL_TEAM_ID": "team_test",
        "ZERNIO_API_KEY": "sk_test",
        "BUFFER_API_KEY": "buffer_test",
        "BUFFER_ORGANIZATION_ID": "org_test",
    }
    monkeypatch.setattr(publishing, "effective_value", lambda key: credentials.get(key, ""))
    monkeypatch.setattr(
        publishing,
        "configured_keys",
        lambda keys: {key: bool(credentials.get(key)) for key in keys},
    )
    return media


def request(video_path: Path, **overrides) -> publishing.PublishRequest:
    payload = {
        "workspace_id": "workspace-1",
        "video_path": str(video_path),
        "caption": "Launch clip",
        "date": datetime.now(UTC) + timedelta(hours=2),
        "targets": [publishing.PublishTarget(platform="tiktok", integration_id="account-1")],
    }
    payload.update(overrides)
    return publishing.PublishRequest(**payload)


def targets(*pairs) -> list[publishing.PublishTarget]:
    return [
        publishing.PublishTarget(platform=platform, integration_id=f"acct-{platform}",
                                 provider=provider)
        for platform, provider in pairs
    ]


# --- grouping -----------------------------------------------------------------


def test_destinations_are_grouped_by_the_engine_that_will_deliver_them(media_file) -> None:
    body = request(
        media_file,
        provider="bundle_social",
        targets=targets(("tiktok", "bundle_social"), ("youtube", "buffer"),
                        ("instagram", "bundle_social")),
    )
    groups = publishing.grouped_targets(body)
    assert list(groups) == ["bundle_social", "buffer"]
    assert [t.platform for t in groups["bundle_social"]] == ["tiktok", "instagram"]
    assert [t.platform for t in groups["buffer"]] == ["youtube"]


def test_a_target_without_an_engine_falls_to_the_requests_own(media_file) -> None:
    # Back-compat: a post that names one engine for the whole request keeps
    # meaning exactly what it did before targets could carry their own.
    body = request(
        media_file,
        provider="zernio",
        targets=targets(("tiktok", None), ("youtube", None)),
    )
    groups = publishing.grouped_targets(body)
    assert list(groups) == ["zernio"]
    assert len(groups["zernio"]) == 2


def test_engines_are_attempted_in_the_order_their_destinations_were_chosen(media_file) -> None:
    body = request(
        media_file,
        provider="buffer",
        targets=targets(("youtube", "buffer"), ("tiktok", "zernio")),
    )
    assert list(publishing.grouped_targets(body)) == ["buffer", "zernio"]


# --- dispatch -----------------------------------------------------------------


@pytest.fixture
def engines(monkeypatch):
    """Record what each engine was asked to send, without contacting any."""
    calls: dict[str, list[list[str]]] = {"bundle_social": [], "zernio": [], "buffer": []}

    def record(name):
        def run(request_object, *args, **kwargs):
            calls[name].append([t.platform for t in request_object.targets])
            return {"remote_id": f"{name}-1"}
        return run

    monkeypatch.setattr(publishing, "_bundle_publish", record("bundle_social"))
    monkeypatch.setattr(publishing, "_zernio_publish", record("zernio"))
    monkeypatch.setattr(publishing, "_buffer_publish", record("buffer"))
    return calls


def test_each_engine_receives_only_its_own_destinations(media_file, engines) -> None:
    body = request(
        media_file,
        provider="bundle_social",
        media_url="https://cdn.example.com/clip.mp4",
        targets=targets(("tiktok", "bundle_social"), ("youtube", "buffer")),
    )
    result = publishing._execute_publish(body)

    assert engines["bundle_social"] == [["tiktok"]]
    assert engines["buffer"] == [["youtube"]]
    assert engines["zernio"] == []
    assert result["status"] == "created"
    assert result["engines"] == ["bundle_social", "buffer"]


def test_a_single_engine_post_still_reports_the_way_it_always_did(media_file, engines) -> None:
    body = request(
        media_file,
        provider="zernio",
        targets=targets(("tiktok", None)),
    )
    result = publishing._execute_publish(body)
    assert result["status"] == "created"
    assert result["remote_id"] == "zernio-1"


# --- partial failure ----------------------------------------------------------


def test_one_engine_failing_does_not_hide_that_the_others_sent(media_file, monkeypatch) -> None:
    monkeypatch.setattr(publishing, "_bundle_publish", lambda *a, **k: {"remote_id": "b-1"})
    def refuse(*args, **kwargs):
        raise RuntimeError("Buffer rejected the channel")
    monkeypatch.setattr(publishing, "_buffer_publish", refuse)

    body = request(
        media_file,
        provider="bundle_social",
        media_url="https://cdn.example.com/clip.mp4",
        targets=targets(("tiktok", "bundle_social"), ("youtube", "buffer")),
    )
    result = publishing._execute_publish(body)

    # The post is live on TikTok. Reporting a blanket failure would invite a
    # retry that posts there twice.
    assert result["status"] == "partial"
    sent = [item for item in result["deliveries"] if item["status"] == "created"]
    failed = [item for item in result["deliveries"] if item["status"] == "failed"]
    assert [item["provider"] for item in sent] == ["bundle_social"]
    assert [item["provider"] for item in failed] == ["buffer"]
    assert "Buffer rejected the channel" in failed[0]["error"]
    assert "retrying would repost" in result["partial_note"]


def test_every_engine_failing_is_an_ordinary_failure(media_file, monkeypatch) -> None:
    def refuse(name):
        def run(*args, **kwargs):
            raise RuntimeError(f"{name} said no")
        return run
    monkeypatch.setattr(publishing, "_bundle_publish", refuse("bundle"))
    monkeypatch.setattr(publishing, "_buffer_publish", refuse("buffer"))

    body = request(
        media_file,
        provider="bundle_social",
        media_url="https://cdn.example.com/clip.mp4",
        targets=targets(("tiktok", "bundle_social"), ("youtube", "buffer")),
    )
    # Nothing reached anything, so there is no half-sent post to protect and
    # this can raise like any other failure.
    with pytest.raises(RuntimeError, match="said no"):
        publishing._execute_publish(body)


def test_a_destination_rejected_by_one_engine_stops_the_whole_post(media_file, engines) -> None:
    # Validation runs across every engine before any of them is called: a post
    # that the second engine will refuse must not already be live on the first,
    # because nothing sent can be taken back.
    body = request(
        media_file,
        provider="bundle_social",
        media_url="https://cdn.example.com/clip.mp4",
        caption="x" * 4000,
        targets=targets(("tiktok", "bundle_social"), ("youtube", "buffer")),
    )
    with pytest.raises(ValueError):
        publishing._execute_publish(body)
    assert engines["bundle_social"] == []
    assert engines["buffer"] == []


# --- the dry run --------------------------------------------------------------


def test_the_dry_run_describes_every_engine_as_one_post(media_file) -> None:
    body = request(
        media_file,
        provider="bundle_social",
        media_url="https://cdn.example.com/clip.mp4",
        targets=targets(("tiktok", "bundle_social"), ("youtube", "buffer")),
    )
    preview = publishing.preview_publish(body)
    assert [item["id"] for item in preview["engines"]] == ["bundle_social", "buffer"]
    # One list of destinations rather than one report per engine.
    assert [item["platform"] for item in preview["destinations"]] == ["tiktok", "youtube"]


def test_media_is_hosted_once_for_every_engine_that_needs_it(media_file, engines,
                                                             monkeypatch) -> None:
    hosted: list[int] = []

    def host(request_object):
        hosted.append(1)
        return {"url": "https://cdn.example.com/hosted.mp4", "sha256": "a" * 64, "blurred": False}

    monkeypatch.setattr(publishing, "host_media_for_engine", host)
    # Validation refuses a public-media engine before hosting is ever reached
    # unless storage is configured, which is the point of that check.
    monkeypatch.setattr(publishing.media_hosting, "status", lambda: {"configured": True})
    body = request(
        media_file,
        provider="buffer",
        targets=targets(("youtube", "buffer"), ("tiktok", "buffer")),
    )
    result = publishing._execute_publish(body)
    # The engines send the same cut; uploading it per engine would pay for the
    # same bytes twice.
    assert len(hosted) == 1
    assert result["hosted_media"]["url"] == "https://cdn.example.com/hosted.mp4"


def test_a_path_with_no_public_media_need_is_not_hosted(media_file, engines, monkeypatch) -> None:
    monkeypatch.setattr(
        publishing, "host_media_for_engine",
        lambda request_object: pytest.fail("hosting should not be reached"),
    )
    body = request(media_file, provider="zernio", targets=targets(("tiktok", "zernio")))
    publishing._execute_publish(body)
