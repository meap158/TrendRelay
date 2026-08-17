from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.integrations import publishing
from trendrelay_api.models import Base


@pytest.fixture
def job_factory(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(publishing, "JOB_SESSION_FACTORY", factory)
    return factory


@pytest.fixture
def media_file(monkeypatch, tmp_path: Path) -> Path:
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"test-video")
    monkeypatch.setattr(
        publishing,
        "get_settings",
        lambda: SimpleNamespace(
            publishing_media_root_list=[str(tmp_path)],
            publishing_provider="bundle_social",
            attribution_public_url="https://go.example.test",
        ),
    )
    credentials = {
        "BUNDLE_SOCIAL_API_KEY": "pk_test",
        "BUNDLE_SOCIAL_TEAM_ID": "team_test",
        "ZERNIO_API_KEY": "sk_test",
        "BUFFER_API_KEY": "buffer_test",
        "BUFFER_ORGANIZATION_ID": "org_test",
        "WOOPSOCIAL_API_KEY": "woop_test",
    }
    monkeypatch.setattr(publishing, "effective_value", lambda key: credentials.get(key, ""))
    # `masked_value` reads the real .env, so without this the preview in a status
    # payload - and any assertion about it - depends on whose machine is running
    # the suite.
    monkeypatch.setattr(
        publishing,
        "masked_value",
        lambda key: ("•" * 8 + credentials[key][-4:]) if credentials.get(key) else None,
    )
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


def use_provider(monkeypatch, tmp_path: Path, provider: str) -> None:
    monkeypatch.setattr(
        publishing,
        "get_settings",
        lambda: SimpleNamespace(
            publishing_media_root_list=[str(tmp_path)],
            publishing_provider=provider,
            # The preview reads this to recognise our own tracking links, so a
            # double without it stands in for settings that cannot exist.
            attribution_public_url="https://go.example.test",
        ),
    )


def test_preview_is_dry_run_and_confirmed_bundle_job_executes_once(
    monkeypatch, job_factory, media_file: Path
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_request(method, path, **kwargs):
        calls.append((method, path))
        if path == "/upload/":
            return {"id": "upl_123"}
        if path == "/post/":
            return {"id": "post_123", "status": "SCHEDULED"}
        return {}

    monkeypatch.setattr(publishing, "_bundle_request", fake_request)

    preview = publishing.preview_publish(request(media_file))
    assert preview["status"] == "dry_run"
    assert preview["provider"] == "bundle_social"
    assert preview["delivery"] == "draft"
    assert calls == []

    with pytest.raises(PermissionError, match="external-action"):
        publishing.create_publish_job(request(media_file))

    job = publishing.create_publish_job(request(media_file, confirm_external_action=True))
    assert job["payload"]["request"]["provider"] == "bundle_social"

    publishing.run_publish_job(job["id"])
    completed = publishing.publish_job(job["id"])
    assert completed["status"] == "succeeded"
    assert completed["result"]["provider"] == "bundle_social"
    assert completed["result"]["post_ids"] == ["post_123"]
    assert completed["result"]["upload_id"] == "upl_123"
    assert calls == [("POST", "/upload/"), ("POST", "/post/")]

    publishing.run_publish_job(job["id"])
    assert len(calls) == 2


def test_validation_requires_approved_media_root(monkeypatch, media_file: Path) -> None:
    with pytest.raises(ValueError, match="existing MP4 file"):
        publishing.preview_publish(request(Path("C:/Windows/System32/clip.mp4")))
    with pytest.raises(ValueError, match="existing MP4 file"):
        publishing.preview_publish(request(media_file.parent / "missing.mp4"))


def test_bundle_accounts_are_normalized(monkeypatch, media_file: Path) -> None:
    monkeypatch.setattr(
        publishing,
        "_bundle_request",
        lambda *args, **kwargs: {
            "items": [
                {
                    "socialAccounts": [
                        {"id": "a1", "type": "TIKTOK", "displayName": "TrendRelay"},
                        {"id": "a2", "type": "UNKNOWN_APP", "username": "bad"},
                        {"id": "a3", "type": "YOUTUBE", "name": "Video Channel"},
                    ]
                }
            ]
        },
    )
    result = publishing.discover_integrations("bundle_social")
    assert result["provider"] == "bundle_social"
    # Each account names the engine that reaches it, which is what lets one post
    # address destinations on more than one engine at a time.
    # `handle` rides alongside the label because only the handle identifies a page
    # when two engines both report it; a display name repeats and changes.
    assert result["accounts"] == [
        {"id": "a1", "platform": "tiktok", "label": "TrendRelay", "handle": None,
         "provider": "bundle_social", "provider_label": "Bundle.social"},
        {"id": "a3", "platform": "youtube", "label": "Video Channel", "handle": None,
         "provider": "bundle_social", "provider_label": "Bundle.social"},
    ]


def test_zernio_uploads_then_creates_a_post(monkeypatch, media_file: Path, tmp_path: Path) -> None:
    use_provider(monkeypatch, tmp_path, "zernio")
    sent: dict[str, object] = {}

    def fake_request(method, path, **kwargs):
        if path == "/media/presign":
            return {
                "uploadUrl": "https://upload.example.com/put",
                "publicUrl": "https://cdn.example.com/clip.mp4",
            }
        if path == "/posts":
            sent["body"] = kwargs["body"]
            return {"post": {"_id": "zer_1", "status": "scheduled"}}
        return {}

    monkeypatch.setattr(publishing, "_zernio_request", fake_request)
    monkeypatch.setattr(publishing, "_http", lambda *args, **kwargs: None)

    result = publishing._execute_publish(
        request(media_file, schedule=True, made_with_ai=True, confirm_external_action=True)
    )
    body = sent["body"]
    assert result["provider"] == "zernio"
    assert result["post_ids"] == ["zer_1"]
    assert body["mediaItems"] == [{"type": "video", "url": "https://cdn.example.com/clip.mp4"}]
    assert body["platforms"] == [{"platform": "tiktok", "accountId": "account-1"}]
    assert body["tiktokSettings"]["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert body["tiktokSettings"]["content_preview_confirmed"] is True
    assert body["tiktokSettings"]["video_made_with_ai"] is True
    assert "isDraft" not in body


def test_zernio_drafts_when_scheduling_is_off(
    monkeypatch, media_file: Path, tmp_path: Path
) -> None:
    use_provider(monkeypatch, tmp_path, "zernio")
    sent: dict[str, object] = {}

    def fake_request(method, path, **kwargs):
        if path == "/posts":
            sent["body"] = kwargs["body"]
            return {"post": {"_id": "zer_2"}}
        return {"uploadUrl": "https://upload.example.com", "publicUrl": "https://cdn/clip.mp4"}

    monkeypatch.setattr(publishing, "_zernio_request", fake_request)
    monkeypatch.setattr(publishing, "_http", lambda *args, **kwargs: None)

    publishing._execute_publish(request(media_file, visibility="private"))
    assert sent["body"]["isDraft"] is True
    assert sent["body"]["tiktokSettings"]["privacy_level"] == "SELF_ONLY"


def test_buffer_requires_a_public_media_url(monkeypatch, media_file: Path, tmp_path: Path) -> None:
    use_provider(monkeypatch, tmp_path, "buffer")
    # Without this the test reads the developer's own .env: on a machine with R2
    # configured TrendRelay hosts the file itself and nothing is refused, so the
    # test passed or failed depending on who ran it.
    monkeypatch.setattr(
        publishing.media_hosting, "status", lambda: {"configured": False}
    )
    with pytest.raises(ValueError, match="public media URL"):
        publishing.preview_publish(request(media_file))

    preview = publishing.preview_publish(
        request(media_file, media_url="https://cdn.example.com/clip.mp4")
    )
    assert preview["provider"] == "buffer"
    assert preview["media_url"] == "https://cdn.example.com/clip.mp4"


def test_buffer_creates_one_post_per_channel(monkeypatch, media_file: Path, tmp_path: Path) -> None:
    use_provider(monkeypatch, tmp_path, "buffer")
    queries: list[str] = []

    def fake_graphql(query, **kwargs):
        queries.append(query)
        return {"createPost": {"post": {"id": f"buf_{len(queries)}", "status": "draft"}}}

    monkeypatch.setattr(publishing, "_buffer_graphql", fake_graphql)

    result = publishing._execute_publish(
        request(
            media_file,
            media_url="https://cdn.example.com/clip.mp4",
            targets=[
                publishing.PublishTarget(platform="tiktok", integration_id="chan-1"),
                publishing.PublishTarget(platform="instagram", integration_id="chan-2"),
            ],
        )
    )
    assert result["post_ids"] == ["buf_1", "buf_2"]
    assert len(queries) == 2
    assert "saveToDraft: true" in queries[0]
    assert '"chan-1"' in queries[0]
    assert '"https://cdn.example.com/clip.mp4"' in queries[0]


def test_buffer_surfaces_mutation_errors(monkeypatch, media_file: Path, tmp_path: Path) -> None:
    use_provider(monkeypatch, tmp_path, "buffer")
    monkeypatch.setattr(
        publishing,
        "_buffer_graphql",
        lambda *args, **kwargs: {"createPost": {"message": "Channel is locked"}},
    )
    with pytest.raises(RuntimeError, match="Channel is locked"):
        publishing._execute_publish(
            request(media_file, media_url="https://cdn.example.com/clip.mp4")
        )


def test_unsupported_platform_for_the_selected_engine_is_rejected(
    monkeypatch, media_file: Path, tmp_path: Path
) -> None:
    use_provider(monkeypatch, tmp_path, "bundle_social")
    with pytest.raises(ValueError, match="does not publish to Bluesky"):
        publishing.preview_publish(
            request(
                media_file,
                targets=[publishing.PublishTarget(platform="bluesky", integration_id="a1")],
            )
        )


def test_connection_status_reports_every_engine_without_exposing_values(
    monkeypatch, media_file: Path
) -> None:
    monkeypatch.setattr(publishing, "_bundle_request", lambda *args, **kwargs: {"items": []})
    status = publishing.connection_status()

    assert status["active_provider"] == "bundle_social"
    assert status["authenticated"] is True
    assert [provider["id"] for provider in status["providers"]] == [
        "bundle_social",
        "zernio",
        "buffer",
        "woopsocial",
    ]
    serialized = repr(status)
    assert "pk_test" not in serialized
    assert "sk_test" not in serialized
    for provider in status["providers"]:
        for field in provider["credential_fields"]:
            assert set(field) == {
                "id", "key", "label", "secret", "required", "help", "configured", "preview"
            }
            # The preview exists so a saved key is recognisable, so it must
            # carry the tail and nothing before it. A mask that leaked the front
            # of a key would defeat the point of masking at all.
            preview = field["preview"]
            if preview:
                visible = preview.lstrip("•")
                # At most a tail, and everything before it hidden. Written this
                # way so a value too short to mask - which keeps none of itself -
                # passes rather than looking like a leak.
                assert len(visible) <= 4, preview
                assert set(preview[: len(preview) - len(visible)]) <= {"•"}


def test_saving_credentials_writes_only_known_keys(monkeypatch, media_file: Path) -> None:
    written: dict[str, str] = {}
    monkeypatch.setattr(
        publishing, "write_env_values", lambda values: (written.update(values), sorted(values))[1]
    )

    result = publishing.save_provider_credentials("zernio", {"api_key": " sk_live "})
    assert written == {"ZERNIO_API_KEY": "sk_live"}
    assert result == {"provider": "zernio", "written_keys": ["ZERNIO_API_KEY"]}

    with pytest.raises(ValueError, match="Unknown Zernio settings"):
        publishing.save_provider_credentials("zernio", {"team_id": "nope"})

    with pytest.raises(ValueError, match="cannot be empty"):
        publishing.save_provider_credentials("buffer", {"api_key": "  "})


def test_scheduled_delivery_must_be_in_the_future(monkeypatch, media_file: Path) -> None:
    past = datetime.now(UTC) - timedelta(minutes=5)
    with pytest.raises(ValueError, match="in the future"):
        publishing.preview_publish(request(media_file, schedule=True, date=past))
    # A draft keeps its reference time even when that time has passed.
    assert publishing.preview_publish(request(media_file, date=past))["delivery"] == "draft"


def test_buffer_rejects_plain_http_media(monkeypatch, media_file: Path, tmp_path: Path) -> None:
    use_provider(monkeypatch, tmp_path, "buffer")
    with pytest.raises(ValueError, match="must be https"):
        publishing.preview_publish(request(media_file, media_url="http://cdn.example.com/clip.mp4"))


def test_preview_explains_each_destination(monkeypatch, media_file: Path, tmp_path: Path) -> None:
    use_provider(monkeypatch, tmp_path, "zernio")
    preview = publishing.preview_publish(
        request(
            media_file,
            made_with_ai=True,
            targets=[
                publishing.PublishTarget(platform="tiktok", integration_id="a1"),
                publishing.PublishTarget(platform="youtube", integration_id="a2"),
            ],
        )
    )
    plan = {item["platform"]: item["notes"] for item in preview["destinations"]}
    assert preview["media_source"] == "approved local file"
    assert "Privacy: everyone" in plan["tiktok"]
    assert "AI-generated disclosure on" in plan["tiktok"]
    assert "Delivered as a Short" in plan["youtube"]
    assert "Declared as synthetic media" in plan["youtube"]


def test_zernio_sends_an_idempotency_key_and_reports_deduplication(
    monkeypatch, media_file: Path, tmp_path: Path
) -> None:
    use_provider(monkeypatch, tmp_path, "zernio")
    seen: dict[str, object] = {}

    def fake_request(method, path, **kwargs):
        if path == "/posts":
            seen["request_id"] = kwargs.get("request_id")
            return {"existingPost": {"_id": "zer_original", "status": "scheduled"}}
        return {"uploadUrl": "https://up.example.com", "publicUrl": "https://cdn/clip.mp4"}

    monkeypatch.setattr(publishing, "_zernio_request", fake_request)
    monkeypatch.setattr(publishing, "_http", lambda *args, **kwargs: None)

    result = publishing._execute_publish(request(media_file), request_id="publish_abc123")
    assert seen["request_id"] == "publish_abc123"
    assert result["post_ids"] == ["zer_original"]
    assert result["deduplicated"] is True


def test_http_errors_carry_an_actionable_hint(monkeypatch, media_file: Path) -> None:
    import io
    import urllib.error

    def fail(*args, **kwargs):
        raise urllib.error.HTTPError(
            "https://api.bundle.social/api/v1/post/",
            401,
            "Unauthorized",
            {},  # type: ignore[arg-type]
            io.BytesIO(b'{"message": "Invalid API key"}'),
        )

    monkeypatch.setattr(publishing.urllib.request, "urlopen", fail)
    with pytest.raises(RuntimeError) as raised:
        publishing._bundle_request("GET", "/team/")
    assert "Invalid API key" in str(raised.value)
    assert "Save a current key" in str(raised.value)


def test_test_provider_probes_without_switching_engines(monkeypatch, media_file: Path) -> None:
    monkeypatch.setattr(
        publishing,
        "_zernio_request",
        lambda *args, **kwargs: {
            "accounts": [
                {"_id": "z1", "platform": "tiktok", "displayName": "Brand", "isActive": True}
            ]
        },
    )
    result = publishing.test_provider("zernio")
    assert result["authenticated"] is True
    assert result["account_count"] == 1
    assert result["connected_platforms"] == ["tiktok"]
    assert publishing.active_provider_id() == "bundle_social"


def test_bundle_payload_matches_the_documented_contract(
    monkeypatch, media_file: Path
) -> None:
    sent: dict[str, object] = {}

    def fake_request(method, path, **kwargs):
        if path == "/upload/":
            return {"id": "upl_1"}
        sent["body"] = kwargs["body"]
        return {"id": "post_1", "status": "DRAFT"}

    monkeypatch.setattr(publishing, "_bundle_request", fake_request)
    publishing._execute_publish(
        request(
            media_file,
            title="Launch day",
            made_with_ai=True,
            targets=[
                publishing.PublishTarget(platform="tiktok", integration_id="a1"),
                publishing.PublishTarget(platform="youtube", integration_id="a2"),
            ],
        )
    )
    body = sent["body"]
    # Required top-level fields the API rejects the post without.
    assert body["title"] == "Launch day"
    assert body["socialAccountTypes"] == ["TIKTOK", "YOUTUBE"]
    assert "socialAccountIds" not in body
    assert body["status"] == "DRAFT"
    assert body["data"]["TIKTOK"] == {
        "type": "VIDEO",
        "text": "Launch clip",
        "uploadIds": ["upl_1"],
        "privacy": "PUBLIC_TO_EVERYONE",
        "isAiGenerated": True,
    }
    youtube = body["data"]["YOUTUBE"]
    assert youtube["privacy"] == "PUBLIC"  # uppercase enum, not "public"
    assert youtube["type"] == "SHORT"
    assert youtube["text"] == "Launch day"
    assert youtube["description"] == "Launch clip"
    assert youtube["containsSyntheticMedia"] is True


def test_bundle_title_falls_back_to_the_first_caption_line(
    monkeypatch, media_file: Path
) -> None:
    sent: dict[str, object] = {}

    def fake_request(method, path, **kwargs):
        if path == "/upload/":
            return {"id": "upl_1"}
        sent["body"] = kwargs["body"]
        return {"id": "post_1"}

    monkeypatch.setattr(publishing, "_bundle_request", fake_request)
    publishing._execute_publish(request(media_file, caption="First line\nSecond line"))
    assert sent["body"]["title"] == "First line"


def test_bundle_uses_from_url_when_media_is_already_hosted(
    monkeypatch, media_file: Path
) -> None:
    calls: list[str] = []

    def fake_request(method, path, **kwargs):
        calls.append(path)
        if path == "/upload/from-url":
            return {"id": "upl_remote"}
        return {"id": "post_1"}

    monkeypatch.setattr(publishing, "_bundle_request", fake_request)
    result = publishing._execute_publish(
        request(media_file, media_url="https://cdn.example.com/clip.mp4")
    )
    assert "/upload/from-url" in calls
    assert "/upload/" not in calls
    assert result["upload_id"] == "upl_remote"


def test_reddit_and_pinterest_require_their_extra_field(
    monkeypatch, media_file: Path
) -> None:
    with pytest.raises(ValueError, match="subreddit"):
        publishing.preview_publish(
            request(
                media_file,
                targets=[publishing.PublishTarget(platform="reddit", integration_id="a1")],
            )
        )
    with pytest.raises(ValueError, match="board ID"):
        publishing.preview_publish(
            request(
                media_file,
                targets=[publishing.PublishTarget(platform="pinterest", integration_id="a1")],
            )
        )
    ok = publishing.preview_publish(
        request(
            media_file,
            subreddit="r/videos",
            targets=[publishing.PublishTarget(platform="reddit", integration_id="a1")],
        )
    )
    assert ok["destinations"][0]["platform"] == "reddit"


def test_subreddit_input_is_normalised_to_a_bare_name() -> None:
    for raw in ("r/videos", "/r/videos/", "https://www.reddit.com/r/videos"):
        assert publishing.PublishRequest.model_validate(
            {
                "workspace_id": "w",
                "video_path": "clip.mp4",
                "caption": "c",
                "date": datetime.now(UTC),
                "targets": [{"platform": "reddit", "integration_id": "a1"}],
                "subreddit": raw,
            }
        ).subreddit == "videos"


def test_bundle_probe_uses_the_documented_entry_point(monkeypatch, media_file: Path) -> None:
    seen: list[str] = []
    monkeypatch.setattr(
        publishing,
        "_bundle_request",
        lambda method, path, **kwargs: seen.append(path) or {"id": "org_1"},
    )
    assert publishing.provider_status("bundle_social")["authenticated"] is True
    assert seen == ["/organization/"]


def test_buffer_sends_the_metadata_each_network_requires(
    monkeypatch, media_file: Path, tmp_path: Path
) -> None:
    """Instagram and Facebook declare a non-null type; omitting it is rejected."""
    use_provider(monkeypatch, tmp_path, "buffer")
    queries: list[str] = []

    def fake_graphql(query, **kwargs):
        queries.append(query)
        return {"createPost": {"post": {"id": f"buf_{len(queries)}"}}}

    monkeypatch.setattr(publishing, "_buffer_graphql", fake_graphql)
    publishing._execute_publish(
        request(
            media_file,
            media_url="https://cdn.example.com/clip.mp4",
            made_with_ai=True,
            title="Launch title",
            targets=[
                publishing.PublishTarget(platform="instagram", integration_id="c1"),
                publishing.PublishTarget(platform="facebook", integration_id="c2"),
                publishing.PublishTarget(platform="youtube", integration_id="c3"),
                publishing.PublishTarget(platform="tiktok", integration_id="c4"),
            ],
        )
    )

    assert "metadata: { instagram: { type: reel" in queries[0]
    assert "shouldShareToFeed: true" in queries[0]
    assert "isAiGenerated: true" in queries[0]
    assert "metadata: { facebook: { type: reel } }" in queries[1]
    # categoryId is required on create, so the payload carries it.
    assert 'youtube: { title: "Launch title"' in queries[2]
    assert 'categoryId: "22"' in queries[2]
    assert "metadata: { tiktok: { isAiGenerated: true } }" in queries[3]


def test_publish_now_reaches_every_engine(monkeypatch, media_file: Path, tmp_path: Path) -> None:
    """Immediate delivery is a third mode, not a variation of scheduling."""
    sent: dict[str, object] = {}

    def bundle(method, path, **kwargs):
        if path == "/post/":
            sent["bundle"] = kwargs["body"]["status"]
            return {"id": "p1"}
        return {"id": "upl"}

    monkeypatch.setattr(publishing, "_bundle_request", bundle)
    publishing._execute_publish(request(media_file, delivery="now"))
    assert sent["bundle"] == "PUBLISHED"

    use_provider(monkeypatch, tmp_path, "zernio")

    def zernio(method, path, **kwargs):
        if path == "/posts":
            sent["zernio"] = kwargs["body"]
            return {"post": {"_id": "z1"}}
        return {"uploadUrl": "https://u", "publicUrl": "https://cdn/clip.mp4"}

    monkeypatch.setattr(publishing, "_zernio_request", zernio)
    monkeypatch.setattr(publishing, "_http", lambda *a, **k: None)
    publishing._execute_publish(request(media_file, delivery="now"))
    assert sent["zernio"]["publishNow"] is True
    assert "scheduledFor" not in sent["zernio"]
    assert "isDraft" not in sent["zernio"]

    use_provider(monkeypatch, tmp_path, "buffer")
    monkeypatch.setattr(
        publishing,
        "_buffer_graphql",
        lambda query, **k: sent.__setitem__("buffer", query)
        or {"createPost": {"post": {"id": "b1"}}},
    )
    publishing._execute_publish(
        request(media_file, delivery="now", media_url="https://cdn.example.com/clip.mp4")
    )
    assert "mode: shareNow" in sent["buffer"]


def test_stored_jobs_without_a_delivery_field_still_resolve(media_file: Path) -> None:
    """Jobs written before `delivery` existed must keep their meaning."""
    assert request(media_file).mode == "draft"
    assert request(media_file, schedule=True).mode == "schedule"
    assert request(media_file, delivery="now", schedule=False).mode == "now"


# --- post types -------------------------------------------------------------- #


def test_a_network_only_offers_the_types_it_can_actually_publish() -> None:
    assert [kind.id for kind in publishing.post_types_for("instagram")] == [
        "reel", "story", "post", "photo",
    ]
    # TikTok has no Story surface in any of these APIs, but it does have photo
    # carousels - offered by the platform here and refused per engine, since
    # only some of them have a contract for one.
    assert [kind.id for kind in publishing.post_types_for("tiktok")] == ["video", "photo"]
    assert publishing.resolve_post_type("tiktok", None).id == "video"
    assert [kind.id for kind in publishing.post_types_for("linkedin")] == ["post"]


def test_an_unset_post_type_falls_back_to_the_network_default() -> None:
    assert publishing.resolve_post_type("instagram", None).id == "reel"
    assert publishing.resolve_post_type("youtube", None).id == "short"


def test_a_type_the_network_cannot_publish_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match="TikTok does not accept 'story' posts"):
        publishing.resolve_post_type("tiktok", "story")


def test_a_bad_post_type_is_caught_before_a_job_is_created(media_file: Path) -> None:
    body = request(
        media_file,
        targets=[
            publishing.PublishTarget(
                platform="tiktok", integration_id="a1", post_type="story"
            )
        ],
    )
    with pytest.raises(ValueError, match="does not accept 'story'"):
        publishing._validate_request(publishing.PROVIDERS["bundle_social"], body)


def test_bundle_sends_the_chosen_type_rather_than_always_a_reel(
    monkeypatch, media_file: Path, tmp_path: Path
) -> None:
    use_provider(monkeypatch, tmp_path, "bundle_social")
    sent: dict[str, object] = {}

    def fake_request(method, path, **kwargs):
        if path == "/upload/":
            return {"id": "upload-1"}
        sent["body"] = kwargs.get("body")
        return {"id": "post-1", "status": "DRAFT"}

    monkeypatch.setattr(publishing, "_bundle_request", fake_request)
    body = request(
        media_file,
        targets=[
            publishing.PublishTarget(
                platform="instagram", integration_id="a1", post_type="story"
            )
        ],
    )

    publishing._bundle_publish(body, media_file)

    assert sent["body"]["data"]["INSTAGRAM"]["type"] == "STORY"


def test_buffer_does_not_cross_post_a_story_to_the_feed(media_file: Path) -> None:
    """A Story is not added to the grid, so shouldShareToFeed would be a lie."""
    story = publishing._buffer_metadata(
        "instagram", request(media_file), publishing.resolve_post_type("instagram", "story")
    )
    reel = publishing._buffer_metadata(
        "instagram", request(media_file), publishing.resolve_post_type("instagram", "reel")
    )

    assert "type: story" in story
    assert "shouldShareToFeed: false" in story
    assert "type: reel" in reel
    assert "shouldShareToFeed: true" in reel


def test_zernio_forwards_the_type_as_its_content_type(
    monkeypatch, media_file: Path, tmp_path: Path
) -> None:
    use_provider(monkeypatch, tmp_path, "zernio")
    sent: dict[str, object] = {}

    def fake_request(method, path, **kwargs):
        sent["body"] = kwargs.get("body")
        return {"post": {"_id": "p1", "status": "DRAFT"}}

    monkeypatch.setattr(publishing, "_zernio_request", fake_request)
    monkeypatch.setattr(publishing, "_zernio_upload", lambda video: "https://cdn/x.mp4")
    body = request(
        media_file,
        targets=[
            publishing.PublishTarget(
                platform="facebook", integration_id="a1", post_type="post"
            )
        ],
    )

    publishing._zernio_publish(body, media_file)

    assert sent["body"]["platforms"][0]["platformSpecificData"]["contentType"] == "post"


def test_the_dry_run_names_the_post_type_for_every_destination(media_file: Path) -> None:
    body = request(
        media_file,
        targets=[
            publishing.PublishTarget(
                platform="instagram", integration_id="a1", post_type="story"
            )
        ],
    )

    plan = publishing._delivery_plan(publishing.PROVIDERS["bundle_social"], body)

    assert plan[0]["post_type"] == "story"
    assert "Delivered as a Story" in plan[0]["notes"]


def test_hosting_being_configured_does_not_skip_the_other_checks(
    monkeypatch, media_file: Path, tmp_path: Path
) -> None:
    """Buffer's media branch used to return early, hiding these failures."""
    use_provider(monkeypatch, tmp_path, "buffer")
    monkeypatch.setattr(
        publishing.media_hosting, "status", lambda: {"configured": True}
    )
    buffer = publishing.PROVIDERS["buffer"]

    past = request(
        media_file,
        delivery="schedule",
        date=datetime.now(UTC) - timedelta(hours=1),
        targets=[publishing.PublishTarget(platform="tiktok", integration_id="a1")],
    )
    with pytest.raises(ValueError, match="in the future"):
        publishing._validate_request(buffer, past)

    no_board = request(
        media_file,
        targets=[publishing.PublishTarget(platform="pinterest", integration_id="a1")],
    )
    with pytest.raises(ValueError, match="board ID"):
        publishing._validate_request(buffer, no_board)


# --- length limits ----------------------------------------------------------- #


def test_the_tightest_caption_limit_governs_and_names_its_network() -> None:
    """One caption goes to every destination, so the shortest limit is the real one."""
    binding = publishing.binding_limits(["instagram", "twitter", "linkedin"])

    assert binding["caption"] == 280
    assert binding["caption_platform"] == "twitter"


def test_the_title_limit_ignores_networks_that_have_no_title() -> None:
    binding = publishing.binding_limits(["tiktok", "youtube", "reddit"])

    assert binding["title"] == 100
    assert binding["title_platform"] == "youtube"


def test_no_destinations_means_no_binding_limit() -> None:
    assert publishing.binding_limits([])["caption"] is None


def test_a_caption_too_long_for_one_network_is_refused_before_upload(
    media_file: Path,
) -> None:
    body = request(
        media_file,
        caption="x" * 300,
        targets=[publishing.PublishTarget(platform="twitter", integration_id="a1")],
    )

    with pytest.raises(ValueError, match="X / Twitter allows 280 characters"):
        publishing._validate_request(publishing.PROVIDERS["bundle_social"], body)


def test_the_same_caption_is_fine_for_a_network_that_allows_it(media_file: Path) -> None:
    body = request(
        media_file,
        caption="x" * 300,
        targets=[publishing.PublishTarget(platform="instagram", integration_id="a1")],
    )

    publishing._validate_request(publishing.PROVIDERS["bundle_social"], body)


def test_an_over_long_title_is_refused_rather_than_trimmed(media_file: Path) -> None:
    """Truncating published words the operator never wrote, and said nothing."""
    body = request(
        media_file,
        title="t" * 150,
        targets=[publishing.PublishTarget(platform="youtube", integration_id="a1")],
    )

    with pytest.raises(ValueError, match="YouTube allows 100 characters in a title"):
        publishing._validate_request(publishing.PROVIDERS["bundle_social"], body)


def test_a_title_that_fits_reaches_the_engine_whole(
    monkeypatch, media_file: Path, tmp_path: Path
) -> None:
    use_provider(monkeypatch, tmp_path, "bundle_social")
    title = "t" * 100
    sent: dict[str, object] = {}

    def fake_request(method, path, **kwargs):
        if path == "/upload/":
            return {"id": "upload-1"}
        sent["body"] = kwargs.get("body")
        return {"id": "post-1", "status": "DRAFT"}

    monkeypatch.setattr(publishing, "_bundle_request", fake_request)
    body = request(
        media_file,
        title=title,
        targets=[publishing.PublishTarget(platform="youtube", integration_id="a1")],
    )

    publishing._bundle_publish(body, media_file)

    assert sent["body"]["data"]["YOUTUBE"]["text"] == title


def test_the_dry_run_reports_how_much_headroom_is_left(
    monkeypatch, media_file: Path, tmp_path: Path
) -> None:
    use_provider(monkeypatch, tmp_path, "zernio")

    preview = publishing.preview_publish(
        request(
            media_file,
            caption="hello",
            targets=[publishing.PublishTarget(platform="twitter", integration_id="a1")],
        )
    )

    assert preview["caption_length"] == 5
    assert preview["limits"]["caption"] == 280
    assert preview["limits"]["caption_platform"] == "twitter"


# --- what Buffer's schema actually declares ---------------------------------- #


def _buffer_meta(media_file: Path, platform: str, **overrides) -> str:
    body = request(media_file, **overrides)
    return publishing._buffer_metadata(
        platform, body, publishing.resolve_post_type(platform, None)
    )


def test_pinterest_carries_the_board_buffer_requires(media_file: Path) -> None:
    """boardServiceId is required on create; the board was collected and dropped."""
    meta = _buffer_meta(media_file, "pinterest", board="board-123")

    assert 'boardServiceId: "board-123"' in meta


def test_youtube_carries_the_category_buffer_requires(media_file: Path) -> None:
    """categoryId is required on create and has no default at the API."""
    meta = _buffer_meta(media_file, "youtube")

    assert f'categoryId: "{publishing.DEFAULT_YOUTUBE_CATEGORY}"' in meta


def test_a_youtube_category_outside_the_documented_list_is_refused(
    media_file: Path,
) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="YouTube category must be one of"):
        request(media_file, youtube_category_id="999")


def test_facebook_is_not_sent_an_ai_flag_it_does_not_declare(media_file: Path) -> None:
    """Buffer rejects a field the network's input type does not define."""
    meta = _buffer_meta(media_file, "facebook", made_with_ai=True)

    assert "isAiGenerated" not in meta


def test_tiktok_is_not_sent_a_post_type_it_does_not_declare(media_file: Path) -> None:
    meta = _buffer_meta(media_file, "tiktok")

    assert "type:" not in meta


def test_a_first_comment_reaches_the_three_networks_that_take_one(
    media_file: Path,
) -> None:
    for platform in ("instagram", "facebook", "linkedin"):
        meta = _buffer_meta(media_file, platform, first_comment="#winter #layering")
        assert 'firstComment: "#winter #layering"' in meta, platform


def test_a_first_comment_is_dropped_where_buffer_has_no_field_for_it(
    media_file: Path,
) -> None:
    """Sending it anyway would be rejected; silently keeping it would mislead."""
    for platform in ("tiktok", "youtube", "threads", "pinterest"):
        meta = _buffer_meta(media_file, platform, first_comment="#winter")
        assert "firstComment" not in meta, platform


def test_no_first_comment_means_no_empty_field(media_file: Path) -> None:
    meta = _buffer_meta(media_file, "linkedin")

    assert meta == ""


def test_whitespace_is_not_a_first_comment(media_file: Path) -> None:
    body = request(media_file, first_comment="   ")

    assert body.first_comment is None


def test_the_dry_run_says_where_a_first_comment_will_land(media_file: Path) -> None:
    body = request(
        media_file,
        first_comment="#winter",
        targets=[
            publishing.PublishTarget(platform="instagram", integration_id="a1"),
            publishing.PublishTarget(platform="tiktok", integration_id="a2"),
        ],
    )

    plan = {item["platform"]: item["notes"] for item in
            publishing._delivery_plan(publishing.PROVIDERS["buffer"], body)}

    assert "First comment posted after" in plan["instagram"]
    assert "No first comment - this network does not take one" in plan["tiktok"]


# --- threads and approval ----------------------------------------------------- #


def test_the_caption_leads_the_thread_because_buffer_wants_the_root(
    media_file: Path,
) -> None:
    """Buffer's array is the whole thread, root included, and the root must
    match the post's own text."""
    meta = _buffer_meta(media_file, "twitter", caption="One", thread=["Two", "Three"])

    assert 'thread: [{ text: "One" }, { text: "Two" }, { text: "Three" }]' in meta


def test_every_network_that_declares_a_thread_gets_one(media_file: Path) -> None:
    for platform in ("twitter", "threads", "mastodon", "bluesky"):
        meta = _buffer_meta(media_file, platform, thread=["Reply"])
        assert "thread: [" in meta, platform


def test_a_network_without_a_thread_field_is_not_sent_one(media_file: Path) -> None:
    for platform in ("instagram", "facebook", "youtube", "tiktok", "pinterest"):
        meta = _buffer_meta(media_file, platform, thread=["Reply"])
        assert "thread:" not in meta, platform


def test_blank_replies_are_dropped_rather_than_published_empty(media_file: Path) -> None:
    body = request(media_file, thread=["Real", "   ", ""])

    assert body.thread == ["Real"]


def test_each_reply_is_measured_against_the_limit_on_its_own(media_file: Path) -> None:
    """A thread is one post per part, so the limit is per part, not per thread."""
    body = request(
        media_file,
        caption="short",
        thread=["x" * 300],
        targets=[publishing.PublishTarget(platform="twitter", integration_id="a1")],
    )

    with pytest.raises(ValueError, match="per post and reply 1 is 300"):
        publishing._validate_request(publishing.PROVIDERS["buffer"], body)


def test_a_thread_within_the_limit_passes_even_though_the_total_exceeds_it(
    media_file: Path,
) -> None:
    body = request(
        media_file,
        caption="x" * 270,
        thread=["y" * 270, "z" * 270],
        media_url="https://cdn.example.com/clip.mp4",
        targets=[publishing.PublishTarget(platform="twitter", integration_id="a1")],
    )

    publishing._validate_request(publishing.PROVIDERS["buffer"], body)


def test_an_engine_that_cannot_thread_says_so(media_file: Path) -> None:
    body = request(media_file, thread=["Reply"])

    with pytest.raises(ValueError, match="Zernio does not publish threads"):
        publishing._validate_request(publishing.PROVIDERS["zernio"], body)


def test_a_thread_with_no_threadable_destination_is_refused(media_file: Path) -> None:
    body = request(
        media_file,
        thread=["Reply"],
        targets=[publishing.PublishTarget(platform="instagram", integration_id="a1")],
    )

    with pytest.raises(ValueError, match="None of the chosen destinations take a thread"):
        publishing._validate_request(publishing.PROVIDERS["buffer"], body)


def test_approval_cannot_be_asked_for_on_a_post_meant_to_go_out(
    media_file: Path,
) -> None:
    """Buffer holds an approval request as a draft, so the two contradict."""
    body = request(media_file, needs_approval=True, delivery="now")

    with pytest.raises(ValueError, match="cannot also be scheduled or published"):
        publishing._validate_request(publishing.PROVIDERS["buffer"], body)


def test_approval_rides_along_with_a_draft(monkeypatch, media_file: Path, tmp_path: Path) -> None:
    use_provider(monkeypatch, tmp_path, "buffer")
    queries: list[str] = []
    monkeypatch.setattr(
        publishing,
        "_buffer_graphql",
        lambda query, **kwargs: (
            queries.append(query),
            {"createPost": {"post": {"id": "p1"}}},
        )[1],
    )

    publishing._buffer_publish(
        request(media_file, needs_approval=True, media_url="https://cdn/x.mp4")
    )

    assert "saveToDraft: true needsApproval: true" in queries[0]


def test_the_dry_run_names_the_thread_and_the_hold(media_file: Path) -> None:
    body = request(
        media_file,
        thread=["Two", "Three"],
        needs_approval=True,
        targets=[
            publishing.PublishTarget(platform="twitter", integration_id="a1"),
            publishing.PublishTarget(platform="instagram", integration_id="a2"),
        ],
    )

    plan = {item["platform"]: item["notes"] for item in
            publishing._delivery_plan(publishing.PROVIDERS["buffer"], body)}

    assert "Thread of 3 posts" in plan["twitter"]
    assert "Caption only - this network does not take a thread" in plan["instagram"]
    assert "Held for approval" in plan["twitter"]


# --- WoopSocial ---------------------------------------------------------------


def woop_accounts_payload() -> list[dict[str, object]]:
    return [
        {"id": "w1", "platform": "TIKTOK", "username": "halcyon", "status": "CONNECTED"},
        {"id": "w2", "platform": "X", "username": "halcyonbooks", "status": "CONNECTED"},
        {"id": "w3", "platform": "LINKEDIN_PAGES", "username": "Halcyon", "status": "CONNECTED"},
        {"id": "w4", "platform": "WOOPTEST", "username": "sandbox", "status": "CONNECTED"},
        {"id": "w5", "platform": "FACEBOOK", "username": "pageish", "status": "DISCONNECTED"},
    ]


def test_woopsocial_accounts_are_normalized(monkeypatch, media_file: Path) -> None:
    monkeypatch.setattr(
        publishing, "_woopsocial_request", lambda *a, **k: woop_accounts_payload()
    )
    accounts = publishing.discover_integrations("woopsocial")["accounts"]

    by_id = {item["id"]: item for item in accounts}
    assert by_id["w2"]["platform"] == "twitter"
    # Their two LinkedIn kinds are one platform to us.
    assert by_id["w3"]["platform"] == "linkedin"
    # The sandbox destination is not a network anyone has an audience on, so it
    # is never offered as somewhere a post can go.
    assert "w4" not in by_id
    # A disconnected account is shown and marked rather than hidden: it is a
    # thing to fix, and hiding it reads as the account having been removed.
    assert by_id["w5"]["label"].endswith("(reconnect)")


def test_woopsocial_uploads_then_creates_a_post(
    monkeypatch, media_file: Path, tmp_path: Path
) -> None:
    use_provider(monkeypatch, tmp_path, "woopsocial")
    sent: dict[str, object] = {}

    def fake_request(method, path, **kwargs):
        if path == "/projects":
            return [{"id": "proj_1", "name": "Default"}]
        if path.startswith("/media"):
            sent["media_path"] = path
            return {"mediaId": "med_1"}
        if path == "/social-accounts":
            return woop_accounts_payload()
        if path == "/posts":
            sent["body"] = kwargs["body"]
            return {
                "id": "post_1",
                "socialAccountPosts": [
                    {"socialAccountId": "w1", "platform": "TIKTOK",
                     "deliveryStatus": "SENDING", "externalPostUrl": None},
                ],
            }
        return {}

    monkeypatch.setattr(publishing, "_woopsocial_request", fake_request)

    result = publishing._execute_publish(request(
        media_file,
        targets=[publishing.PublishTarget(platform="tiktok", integration_id="w1")],
        schedule=True,
        confirm_external_action=True,
    ))
    body = sent["body"]

    assert result["provider"] == "woopsocial"
    assert result["post_ids"] == ["post_1"]
    # Media is uploaded into a project, so the id has to be on the request.
    assert "projectId=proj_1" in str(sent["media_path"])
    assert body["content"] == [{
        "text": "Launch clip",
        "media": [{"type": "MEDIA_LIBRARY", "mediaId": "med_1"}],
    }]
    assert body["schedule"]["type"] == "SCHEDULE_FOR_LATER"
    assert body["socialAccounts"][0]["platform"] == "TIKTOK"
    assert body["socialAccounts"][0]["privacyLevel"] == "PUBLIC_TO_EVERYONE"
    # Reported per destination: three of four networks reached is not the same
    # outcome as all four, and only this engine says so.
    assert result["delivery"][0]["platform"] == "tiktok"


def test_woopsocial_draft_and_now_are_first_class(
    monkeypatch, media_file: Path, tmp_path: Path
) -> None:
    """All three deliveries are the engine's own, not something we simulate."""
    use_provider(monkeypatch, tmp_path, "woopsocial")
    seen: list[str] = []

    def fake_request(method, path, **kwargs):
        if path == "/projects":
            return [{"id": "proj_1"}]
        if path.startswith("/media"):
            return {"mediaId": "med_1"}
        if path == "/social-accounts":
            return woop_accounts_payload()
        if path == "/posts":
            seen.append(kwargs["body"]["schedule"]["type"])
            return {"id": "p"}
        return {}

    monkeypatch.setattr(publishing, "_woopsocial_request", fake_request)
    target = [publishing.PublishTarget(platform="tiktok", integration_id="w1")]
    publishing._execute_publish(
        request(media_file, targets=target, confirm_external_action=True)
    )
    publishing._execute_publish(request(
        media_file, targets=target, delivery="now", confirm_external_action=True
    ))
    assert seen == ["DRAFT", "PUBLISH_NOW"]


def test_woopsocial_reads_the_platform_off_the_account(
    monkeypatch, media_file: Path, tmp_path: Path
) -> None:
    """LINKEDIN and LINKEDIN_PAGES are both `linkedin` to us.

    The post body is a discriminated union that rejects the wrong one, so the
    name has to come back from the account rather than be mapped from ours.
    """
    use_provider(monkeypatch, tmp_path, "woopsocial")
    sent: dict[str, object] = {}

    def fake_request(method, path, **kwargs):
        if path == "/projects":
            return [{"id": "proj_1"}]
        if path.startswith("/media"):
            return {"mediaId": "med_1"}
        if path == "/social-accounts":
            return woop_accounts_payload()
        if path == "/posts":
            sent["body"] = kwargs["body"]
            return {"id": "p"}
        return {}

    monkeypatch.setattr(publishing, "_woopsocial_request", fake_request)
    publishing._execute_publish(request(
        media_file,
        targets=[publishing.PublishTarget(platform="linkedin", integration_id="w3")],
        confirm_external_action=True,
    ))
    assert sent["body"]["socialAccounts"][0]["platform"] == "LINKEDIN_PAGES"


def test_woopsocial_refuses_an_account_it_no_longer_lists(
    monkeypatch, media_file: Path, tmp_path: Path
) -> None:
    # Rather than sending a platform guessed from ours, which would either be
    # rejected or, worse, accepted for the wrong account.
    use_provider(monkeypatch, tmp_path, "woopsocial")

    def fake_request(method, path, **kwargs):
        if path == "/projects":
            return [{"id": "proj_1"}]
        if path.startswith("/media"):
            return {"mediaId": "med_1"}
        if path == "/social-accounts":
            return []
        return {}

    monkeypatch.setattr(publishing, "_woopsocial_request", fake_request)
    with pytest.raises(RuntimeError, match="no longer lists"):
        publishing._execute_publish(request(media_file, confirm_external_action=True))


def test_a_public_url_for_buffer_does_not_starve_woopsocial(
    monkeypatch, media_file: Path, tmp_path: Path
) -> None:
    """One post, two engines, and only one of them can fetch a URL.

    Supplying a public media URL is how Buffer is made to work at all, and it
    used to excuse every engine from reading the local file - leaving WoopSocial,
    which has no endpoint that takes a URL, with nothing to upload.
    """
    woop = publishing.PROVIDERS["woopsocial"]
    buffer = publishing.PROVIDERS["buffer"]
    hosted = request(
        media_file,
        media_url="https://cdn.example.com/clip.mp4",
        confirm_external_action=True,
    )

    assert publishing._needs_local_media(woop, hosted) is True
    # Buffer never takes an upload, so it is still handed nothing.
    assert publishing._needs_local_media(buffer, hosted) is False
    # And an engine that can fetch a URL still skips the local read.
    assert publishing._needs_local_media(publishing.PROVIDERS["zernio"], hosted) is False


def test_woopsocial_names_the_file_and_the_limit_before_uploading(
    monkeypatch, media_file: Path, tmp_path: Path
) -> None:
    # Sending 300 MB to be told it was too big costs the whole upload and returns
    # an error about a request body, naming neither the file nor the cap.
    big = tmp_path / "big.mp4"
    big.write_bytes(b"x" * 16)
    monkeypatch.setattr(publishing, "WOOPSOCIAL_UPLOAD_LIMIT_BYTES", 8)
    monkeypatch.setattr(
        publishing, "_woopsocial_request",
        lambda *a, **k: pytest.fail("uploaded a file that was over the limit"),
    )

    with pytest.raises(ValueError, match="big.mp4"):
        publishing._woopsocial_upload(big)


def test_a_picked_board_reaches_each_engine_in_its_own_terms(
    monkeypatch, media_file: Path, tmp_path: Path
) -> None:
    """One string cannot serve every engine.

    bundle.social matches a board by name; Zernio, Buffer and WoopSocial each
    want its id. A board picked from an account carries both, so a post spanning
    two engines gives each the form it understands instead of failing on one.
    """
    use_provider(monkeypatch, tmp_path, "bundle_social")
    sent: dict[str, object] = {}

    def fake_request(method, path, **kwargs):
        if path == "/upload/":
            return {"id": "upl_1"}
        if path == "/post/":
            sent["body"] = kwargs["body"]
            return {"id": "post_1"}
        return {}

    monkeypatch.setattr(publishing, "_bundle_request", fake_request)
    publishing._execute_publish(request(
        media_file,
        targets=[publishing.PublishTarget(platform="pinterest", integration_id="account-1")],
        board="board-99",
        board_name="Product launches",
        confirm_external_action=True,
    ))
    posted = sent["body"]["data"]["PINTEREST"]
    assert posted["boardName"] == "Product launches"


def test_a_typed_board_still_works_where_only_one_value_is_known(
    monkeypatch, media_file: Path, tmp_path: Path
) -> None:
    # Nothing forces a picker: an engine that cannot list boards leaves the
    # field typed, and that single value has to go somewhere sensible.
    use_provider(monkeypatch, tmp_path, "bundle_social")
    sent: dict[str, object] = {}

    def fake_request(method, path, **kwargs):
        if path == "/post/":
            sent["body"] = kwargs["body"]
            return {"id": "p"}
        return {"id": "upl_1"}

    monkeypatch.setattr(publishing, "_bundle_request", fake_request)
    publishing._execute_publish(request(
        media_file,
        targets=[publishing.PublishTarget(platform="pinterest", integration_id="account-1")],
        board="Product launches",
        confirm_external_action=True,
    ))
    assert sent["body"]["data"]["PINTEREST"]["boardName"] == "Product launches"


def test_boards_are_offered_only_by_an_engine_that_lists_them(
    monkeypatch, media_file: Path
) -> None:
    monkeypatch.setattr(
        publishing, "_woopsocial_request",
        lambda *a, **k: {"boards": [{"id": "b1", "name": "Espresso"}]},
    )
    assert publishing.board_options("woopsocial", "w1") == [
        {"id": "b1", "name": "Espresso"},
    ]
    # Empty means the engine does not offer them, not that the account has none,
    # so the field falls back to being typed rather than claiming it is empty.
    assert publishing.board_options("buffer", "acct") == []


def test_the_preview_asks_woopsocial_what_it_would_refuse(
    monkeypatch, media_file: Path, tmp_path: Path
) -> None:
    """The only engine here that will answer, so the only one asked.

    Sent without media: validating the real thing would mean uploading it, and a
    dry run that uploads is not a dry run. Complaints about the missing media are
    therefore an artefact of the question and are dropped.
    """
    use_provider(monkeypatch, tmp_path, "woopsocial")

    def fake_request(method, path, **kwargs):
        if path == "/social-accounts":
            return woop_accounts_payload()
        if path == "/posts/validate":
            assert "media" not in kwargs["body"]["content"][0]
            return {"validationErrors": [
                {"field": "MEDIA", "message": "Media is required"},
                {"field": "DESCRIPTION", "message": "Caption is too long"},
            ]}
        return {}

    monkeypatch.setattr(publishing, "_woopsocial_request", fake_request)
    preview = publishing.preview_publish(request(
        media_file,
        targets=[publishing.PublishTarget(platform="tiktok", integration_id="w1")],
    ))
    assert preview["engine_problems"] == ["DESCRIPTION: Caption is too long"]


def test_a_dry_run_survives_the_engine_refusing_to_answer(
    monkeypatch, media_file: Path, tmp_path: Path
) -> None:
    # Checking a little less beats a preview that fails because a validation
    # call timed out.
    use_provider(monkeypatch, tmp_path, "woopsocial")

    def fake_request(method, path, **kwargs):
        if path == "/social-accounts":
            return woop_accounts_payload()
        raise RuntimeError("api.woopsocial.com: HTTP 503")

    monkeypatch.setattr(publishing, "_woopsocial_request", fake_request)
    preview = publishing.preview_publish(request(
        media_file,
        targets=[publishing.PublishTarget(platform="tiktok", integration_id="w1")],
    ))
    assert preview["status"] == "dry_run"
    assert preview["engine_problems"] == []


# --- seeing what is saved without exposing it ---------------------------------


def test_a_masked_preview_shows_the_tail_and_nothing_else(monkeypatch) -> None:
    from trendrelay_api import env_store

    monkeypatch.setattr(env_store, "effective_value", lambda key: "sk_live_abcdef123456")
    preview = env_store.masked_value("ANY")
    assert preview is not None
    assert preview.endswith("3456")
    assert "sk_live" not in preview
    assert set(preview[:-4]) == {"\u2022"}


def test_a_short_secret_keeps_none_of_itself(monkeypatch) -> None:
    """The tail is for recognition, not verification.

    Four characters of a six-character secret gives away most of it, so a value
    too short to mask is masked entirely.
    """
    from trendrelay_api import env_store

    monkeypatch.setattr(env_store, "effective_value", lambda key: "abc123")
    assert env_store.masked_value("ANY") == "\u2022" * 6


def test_nothing_saved_previews_as_nothing(monkeypatch) -> None:
    from trendrelay_api import env_store

    monkeypatch.setattr(env_store, "effective_value", lambda key: "")
    # None rather than an empty string, so a screen says "not set" instead of
    # rendering an empty secret.
    assert env_store.masked_value("ANY") is None


def test_reveal_reaches_no_further_than_save(monkeypatch, media_file: Path) -> None:
    """Otherwise the button is "read any environment variable".

    Every secret on the machine - the database URL, a service key - would sit
    behind a control meant for an engine's API key.
    """
    assert "BUFFER_API_KEY" in publishing.revealable_keys()
    assert "R2_SECRET_ACCESS_KEY" in publishing.revealable_keys()
    assert "SUPABASE_SERVICE_ROLE_KEY" not in publishing.revealable_keys()

    with pytest.raises(ValueError, match="not a credential"):
        publishing.reveal_credential("SUPABASE_SERVICE_ROLE_KEY")
    with pytest.raises(ValueError, match="not a credential"):
        publishing.reveal_credential("PATH")


def test_revealing_a_saved_credential_returns_it(monkeypatch, media_file: Path) -> None:
    assert publishing.reveal_credential("BUFFER_API_KEY") == "buffer_test"


def test_revealing_an_unset_credential_says_so(monkeypatch, media_file: Path) -> None:
    with pytest.raises(ValueError, match="no saved value"):
        publishing.reveal_credential("WOOPSOCIAL_PROJECT_ID")


# --- TikTok photo carousels ---------------------------------------------------


@pytest.fixture
def carousel_images(media_file: Path, tmp_path: Path) -> list[str]:
    paths = []
    for index in range(3):
        image = tmp_path / f"frame{index}.jpg"
        image.write_bytes(b"jpeg-bytes")
        paths.append(str(image))
    return paths


def carousel(images: list[str], **overrides):
    """A carousel post: images, and no video at all.

    Built directly rather than through `request`, whose first parameter is the
    video path and so cannot be overridden to nothing.
    """
    payload = {
        "workspace_id": "workspace-1",
        "video_path": "",
        "image_paths": images,
        "caption": "Launch clip",
        "date": datetime.now(UTC) + timedelta(hours=2),
        "targets": [publishing.PublishTarget(
            platform="tiktok", integration_id="account-1", post_type="photo",
        )],
    }
    payload.update(overrides)
    return publishing.PublishRequest(**payload)


def test_zernio_posts_a_carousel_as_images_not_a_video(
    monkeypatch, media_file: Path, tmp_path: Path, carousel_images: list[str]
) -> None:
    """A carousel is a different post, not a video with a flag set.

    Its media is images, its settings say `media_type: photo`, and the caption
    moves to `description` because `content` becomes a 90-character title.
    """
    use_provider(monkeypatch, tmp_path, "zernio")
    sent: dict[str, object] = {}
    uploaded: list[str] = []

    def fake_request(method, path, **kwargs):
        if path == "/media/presign":
            name = kwargs["body"]["filename"]
            uploaded.append(name)
            assert kwargs["body"]["contentType"] == "image/jpeg"
            return {
                "uploadUrl": f"https://upload.example.com/{name}",
                "publicUrl": f"https://cdn.example.com/{name}",
            }
        if path == "/posts":
            sent["body"] = kwargs["body"]
            return {"post": {"_id": "zer_photo"}}
        return {}

    monkeypatch.setattr(publishing, "_zernio_request", fake_request)
    monkeypatch.setattr(publishing, "_http", lambda *args, **kwargs: None)

    publishing._execute_publish(
        carousel(carousel_images, confirm_external_action=True)
    )
    body = sent["body"]

    assert [item["type"] for item in body["mediaItems"]] == ["image"] * 3
    # Swipe order is the post: a carousel opens on its first image.
    assert uploaded == ["frame0.jpg", "frame1.jpg", "frame2.jpg"]
    assert body["tiktokSettings"]["media_type"] == "photo"
    assert body["description"] == "Launch clip"
    assert len(body["content"]) <= 90
    # Duet and stitch are video settings and have no meaning on a carousel.
    assert "allow_duet" not in body["tiktokSettings"]
    assert "allow_stitch" not in body["tiktokSettings"]
    assert body["tiktokSettings"]["express_consent_given"] is True


def test_woopsocial_posts_a_carousel_as_one_post_of_many_media(
    monkeypatch, media_file: Path, tmp_path: Path, carousel_images: list[str]
) -> None:
    use_provider(monkeypatch, tmp_path, "woopsocial")
    sent: dict[str, object] = {}

    def fake_request(method, path, **kwargs):
        if path == "/projects":
            return [{"id": "proj_1"}]
        if path.startswith("/media"):
            return {"mediaId": f"med_{len(sent)}"}
        if path == "/social-accounts":
            return [{"id": "account-1", "platform": "TIKTOK",
                     "username": "brand", "status": "CONNECTED"}]
        if path == "/posts":
            sent["body"] = kwargs["body"]
            return {"id": "p"}
        return {}

    monkeypatch.setattr(publishing, "_woopsocial_request", fake_request)
    publishing._execute_publish(
        carousel(carousel_images, confirm_external_action=True)
    )
    body = sent["body"]

    assert len(body["content"][0]["media"]) == 3
    assert body["socialAccounts"][0]["postType"] == "PHOTO"


def test_an_engine_without_a_carousel_contract_refuses_by_name(
    monkeypatch, media_file: Path, tmp_path: Path, carousel_images: list[str]
) -> None:
    """Refused before anything is uploaded, rather than discovered mid-publish.

    Buffer has no documented contract for one here, and handing it a carousel
    would either be rejected by the engine or quietly posted as something else.
    """
    use_provider(monkeypatch, tmp_path, "buffer")
    with pytest.raises(ValueError, match="cannot post a TikTok photo carousel"):
        publishing.preview_publish(carousel(carousel_images))


def test_a_carousel_with_no_images_is_refused(
    monkeypatch, media_file: Path, tmp_path: Path
) -> None:
    use_provider(monkeypatch, tmp_path, "zernio")
    with pytest.raises(ValueError, match="at least one image"):
        publishing.preview_publish(carousel([]))


def test_carousel_images_obey_the_media_root(
    monkeypatch, media_file: Path, tmp_path: Path
) -> None:
    # The same boundary the video path has: without it an authenticated LAN
    # client could name any file on the server and have it published. A real
    # file outside the root, because a missing one fails on existence first and
    # would pass this test without the boundary existing at all.
    outside = tmp_path.parent / "outside-the-root.png"
    outside.write_bytes(b"png-bytes")
    with pytest.raises(PermissionError, match="approved media root"):
        publishing.approved_image_paths([str(outside)])


def test_a_carousel_refuses_a_file_that_is_not_an_image(
    monkeypatch, media_file: Path, tmp_path: Path
) -> None:
    stray = tmp_path / "notes.txt"
    stray.write_bytes(b"x")
    with pytest.raises(ValueError, match="existing image"):
        publishing.approved_image_paths([str(stray)])


def test_a_carousel_is_only_offered_by_an_engine_that_can_post_one(media_file: Path) -> None:
    """Filtered where the choice is made, not refused after it.

    Buffer has no contract for a carousel here, so offering the option and then
    rejecting it would be a control that exists to say no.
    """
    status = publishing.connection_status(probe=False)
    by_id = {provider["id"]: provider for provider in status["providers"]}

    assert [kind["id"] for kind in by_id["zernio"]["post_types"]["tiktok"]] == [
        "video", "photo",
    ]
    assert [kind["id"] for kind in by_id["woopsocial"]["post_types"]["tiktok"]] == [
        "video", "photo",
    ]
    assert [kind["id"] for kind in by_id["buffer"]["post_types"]["tiktok"]] == ["video"]
    assert [kind["id"] for kind in by_id["bundle_social"]["post_types"]["tiktok"]] == ["video"]


def test_a_carousel_needs_no_video_path(carousel_images: list[str]) -> None:
    """It posts images, so demanding an MP4 as well made no sense.

    The field was required for every post because every post used to be a
    video, which meant a carousel could not be submitted without also naming a
    clip it would never publish.
    """
    body = carousel(carousel_images)
    assert body.video_path == ""
    assert body.image_paths == carousel_images


def test_a_carousel_cannot_also_carry_a_video(
    media_file: Path, carousel_images: list[str]
) -> None:
    # Ambiguous about which one publishes, so it is not a post we accept.
    with pytest.raises(ValueError, match="cannot also carry a video"):
        carousel(carousel_images, video_path=str(media_file))


def test_a_video_post_still_needs_its_media() -> None:
    # Built directly: `request` takes the video path positionally, so it cannot
    # be overridden to nothing through it.
    payload = {
        "workspace_id": "workspace-1",
        "video_path": "",
        "caption": "Launch clip",
        "date": datetime.now(UTC) + timedelta(hours=2),
        "targets": [publishing.PublishTarget(platform="tiktok", integration_id="a1")],
    }
    with pytest.raises(ValueError, match="approved MP4 or a public media URL"):
        publishing.PublishRequest(**payload)
    # A public URL is the other way to have media, and is enough on its own.
    assert publishing.PublishRequest(**payload, media_url="https://cdn.example.com/c.mp4")


def test_images_without_a_carousel_destination_are_refused(
    media_file: Path, carousel_images: list[str]
) -> None:
    """Attached, then the destination switched back to a video.

    Silently ignoring them would publish a video while the composer still shows
    a list of images that were supposedly going out.
    """
    with pytest.raises(ValueError, match="no destination is posting a carousel"):
        request(media_file, image_paths=carousel_images)


def test_every_credential_key_is_documented_in_the_env_template() -> None:
    """A new engine drifts out of the template silently otherwise.

    WoopSocial was added with none of its keys listed, and R2 - without which
    Buffer cannot publish a local clip at all - had never been listed. Both were
    invisible to anyone setting the project up from the template rather than
    from the Publish screen.
    """
    from trendrelay_api.tool_registry import PROJECT_ROOT

    template = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    documented = {
        line.split("=", 1)[0].strip()
        for line in template.splitlines()
        if "=" in line and not line.strip().startswith("#")
    }
    missing = sorted(publishing.revealable_keys() - documented)
    assert not missing, f"absent from .env.example: {', '.join(missing)}"


def test_a_carousel_cannot_share_a_post_with_a_video_destination(
    carousel_images: list[str]
) -> None:
    """One post carries one set of media.

    The video destination would be handed the images as a video, or nothing at
    all - which is what happened before this: an Instagram reel went out with
    no media while the carousel published normally.
    """
    with pytest.raises(ValueError, match="cannot go out with a video destination"):
        carousel(carousel_images, targets=[
            publishing.PublishTarget(
                platform="tiktok", integration_id="a1", post_type="photo"),
            publishing.PublishTarget(
                platform="instagram", integration_id="a2", post_type="reel"),
        ])


def test_two_carousel_destinations_are_one_post(carousel_images: list[str]) -> None:
    # Two TikTok accounts both getting the same carousel is one post, not a
    # mixture - the rule is about media, not about how many destinations there
    # are.
    body = carousel(carousel_images, targets=[
        publishing.PublishTarget(platform="tiktok", integration_id="a1", post_type="photo"),
        publishing.PublishTarget(platform="tiktok", integration_id="a2", post_type="photo"),
    ])
    assert len(body.targets) == 2


# --- attribution, said while it can still be changed ---------------------------


def test_a_preview_says_when_a_post_cannot_be_attributed(
    monkeypatch, media_file: Path, tmp_path: Path
) -> None:
    """The one thing this app exists to optimise, and it was silent about it.

    A post without an affiliate link earns whatever it earns with no report
    anywhere - not the network's, not ours - that can trace it. Said in the
    preview, while the caption can still be changed.
    """
    use_provider(monkeypatch, tmp_path, "zernio")

    plain = publishing.preview_publish(request(media_file))
    assert plain["attribution"]["tracked"] is False
    assert "nothing it earns can be traced" in plain["attribution"]["note"]

    tracked = publishing.preview_publish(request(
        media_file,
        caption="Great espresso https://go.example.test/c/abc123",
    ))
    assert tracked["attribution"]["tracked"] is True


def test_a_link_in_the_first_comment_counts(
    monkeypatch, media_file: Path, tmp_path: Path
) -> None:
    # Instagram and TikTok captions are not clickable, so the link often lives
    # in the comment instead - and that post is just as attributable.
    use_provider(monkeypatch, tmp_path, "zernio")
    preview = publishing.preview_publish(request(
        media_file, first_comment="Link: https://go.example.test/c/xyz789",
    ))
    assert preview["attribution"]["tracked"] is True


def test_a_bare_product_url_is_not_vouched_for(
    monkeypatch, media_file: Path, tmp_path: Path
) -> None:
    # Recognition is deliberately conservative: only the network's own short
    # hosts count, because a full product URL may or may not carry affiliate
    # credit and calling it tracked would vouch for something unseeable.
    use_provider(monkeypatch, tmp_path, "zernio")
    preview = publishing.preview_publish(request(
        media_file, caption="Buy it https://shopee.vn/thing-i.1.2?af=me",
    ))
    assert preview["attribution"]["tracked"] is False


# --- media the network will refuse, said before delivery -----------------------


def test_a_too_wide_threads_video_is_refused_at_validation(
    monkeypatch, media_file: Path
) -> None:
    """Buffer relayed Meta's "no more than 1920px" refusal only after the job
    had already run; the same fact is now stated before anything uploads."""
    monkeypatch.setattr(publishing, "_video_dimensions", lambda _path: (2560, 1440))
    body = request(media_file, targets=[
        publishing.PublishTarget(platform="threads", integration_id="account-1"),
    ])

    with pytest.raises(ValueError) as refusal:
        publishing._validate_request(publishing.PROVIDERS["zernio"], body)

    assert "1920px" in str(refusal.value)
    assert "2560×1440" in str(refusal.value)


def test_the_same_wide_video_passes_where_no_network_limit_is_known(
    monkeypatch, media_file: Path
) -> None:
    # Only limits engines have actually enforced are encoded; nothing is
    # refused on an invented constraint.
    monkeypatch.setattr(publishing, "_video_dimensions", lambda _path: (2560, 1440))
    body = request(media_file, targets=[
        publishing.PublishTarget(platform="facebook", integration_id="account-1"),
    ])

    publishing._validate_request(publishing.PROVIDERS["zernio"], body)


def test_unknown_dimensions_are_not_treated_as_wrong_ones(
    monkeypatch, media_file: Path
) -> None:
    # A broken probe is not evidence the media is wrong; an unreadable file
    # already fails by name at delivery time.
    monkeypatch.setattr(publishing, "_video_dimensions", lambda _path: None)
    body = request(media_file, targets=[
        publishing.PublishTarget(platform="threads", integration_id="account-1"),
    ])

    publishing._validate_request(publishing.PROVIDERS["zernio"], body)


# --- threads topics -----------------------------------------------------------


def threads_request(**overrides) -> publishing.PublishRequest:
    payload = {
        "workspace_id": "workspace-1",
        "video_path": "",
        "media_url": "https://cdn.example.test/clip.mp4",
        "caption": "Launch clip",
        "date": datetime.now(UTC) + timedelta(hours=2),
        "targets": [publishing.PublishTarget(platform="threads", integration_id="channel-1")],
    }
    payload.update(overrides)
    return publishing.PublishRequest(**payload)


def test_a_topic_loses_the_hash_somebody_typed() -> None:
    """Threads shows the hash itself.

    Typing one is the natural thing to do, and sending it would tag "#coffee"
    rather than "coffee".
    """
    assert threads_request(topic="#coldbrew").topic == "coldbrew"
    assert threads_request(topic="  cold brew  ").topic == "cold brew"


def test_a_topic_of_nothing_is_no_topic() -> None:
    assert threads_request(topic="   ").topic is None
    assert threads_request(topic="#").topic is None


@pytest.mark.parametrize("bad", ["cold.brew", "tea & coffee"])
def test_a_topic_meta_will_not_take_is_refused_here(bad: str) -> None:
    # Finding this out from Buffer means the post did not go out.
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        threads_request(topic=bad)


def test_a_topic_longer_than_meta_allows_is_refused() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        threads_request(topic="x" * 51)


def test_buffer_sends_the_topic_it_declares_on_threads(monkeypatch, tmp_path: Path) -> None:
    use_provider(monkeypatch, tmp_path, "buffer")
    metadata = publishing._buffer_metadata(
        "threads", threads_request(topic="coldbrew"), publishing.PostType("post", "Post", "")
    )

    assert 'topic: "coldbrew"' in metadata


def test_no_topic_means_no_field_at_all(monkeypatch, tmp_path: Path) -> None:
    # An empty topic is not an empty string to Buffer; it is a field that
    # should not be there.
    use_provider(monkeypatch, tmp_path, "buffer")
    metadata = publishing._buffer_metadata(
        "threads", threads_request(), publishing.PostType("post", "Post", "")
    )

    assert "topic:" not in metadata


def test_a_network_without_topics_never_receives_one(monkeypatch, tmp_path: Path) -> None:
    """Buffer rejects a field a network does not declare, outright.

    Only ThreadsPostMetadataInput carries `topic`, so sending it anywhere else
    would fail the post rather than be ignored.
    """
    use_provider(monkeypatch, tmp_path, "buffer")
    request = threads_request(
        topic="coldbrew",
        targets=[publishing.PublishTarget(platform="instagram", integration_id="channel-2")],
    )

    metadata = publishing._buffer_metadata(
        "instagram", request, publishing.PostType("reel", "Reel", "")
    )

    assert "topic:" not in metadata


def test_only_engines_with_a_topic_contract_advertise_one() -> None:
    assert publishing.provider_status("buffer", probe=False)["topic_platforms"] == ["threads"]
    assert publishing.provider_status("zernio", probe=False)["topic_platforms"] == []


# --- story and feed post ------------------------------------------------------


@pytest.mark.parametrize("kind_id", ["reel", "story", "post"])
def test_buffer_sends_facebook_a_type_its_own_enum_accepts(media_file: Path, kind_id: str) -> None:
    """Facebook uses PostTypeFacebook, not PostType.

    Introspected against Buffer's live schema: PostTypeFacebook accepts exactly
    post, reel and story, so these three ids go through as they are. Only
    `reel` was covered before, which left both of the modes somebody actually
    asks about untested.
    """
    meta = publishing._buffer_metadata(
        "facebook", request(media_file), publishing.resolve_post_type("facebook", kind_id)
    )

    assert f"type: {kind_id}" in meta


def test_a_feed_post_is_not_cross_posted_as_a_reel(media_file: Path) -> None:
    # shouldShareToFeed is the Reels cross-post toggle and Buffer requires it,
    # so a feed post sends false: it is already in the feed.
    meta = publishing._buffer_metadata(
        "instagram", request(media_file), publishing.resolve_post_type("instagram", "post")
    )

    assert "type: post" in meta
    assert "shouldShareToFeed: false" in meta


@pytest.mark.parametrize(
    ("platform", "kind_id", "expected"),
    [
        ("instagram", "story", "STORY"),
        ("instagram", "post", "POST"),
        ("facebook", "story", "STORY"),
        # WoopSocial calls a Facebook feed post a video.
        ("facebook", "post", "VIDEO"),
    ],
)
def test_woopsocial_maps_every_mode_it_offers(platform: str, kind_id: str, expected: str) -> None:
    assert publishing._WOOPSOCIAL_POST_TYPES[platform][kind_id] == expected


def test_every_mode_woopsocial_offers_has_a_mapping() -> None:
    """A mode with no entry would raise a KeyError mid-publish.

    The lookup is a plain subscript, so a type this engine offers and the table
    omits fails the post rather than falling back. Asked of what the engine
    offers rather than of what the platform has: Instagram has a carousel, and
    WoopSocial has no contract for one, so it never reaches this table.
    """
    provider = publishing.PROVIDERS["woopsocial"]
    for platform, table in publishing._WOOPSOCIAL_POST_TYPES.items():
        offered = {
            kind.id
            for kind in publishing.post_types_for(platform)
            if kind.id != "photo" or platform in provider.photo_carousel_platforms
        }
        assert offered <= set(table), f"{platform} is missing {offered - set(table)}"


# --- instagram carousels ------------------------------------------------------


def instagram_carousel(images: list[str], **overrides):
    payload = {
        "workspace_id": "workspace-1",
        "video_path": "",
        "image_paths": images,
        "caption": "Launch set",
        "date": datetime.now(UTC) + timedelta(hours=2),
        "targets": [publishing.PublishTarget(
            platform="instagram", integration_id="channel-1", post_type="photo",
        )],
    }
    payload.update(overrides)
    return publishing.PublishRequest(**payload)


def test_no_engine_claims_an_instagram_carousel() -> None:
    """The schema said yes and the network said no.

    Buffer's PostType enum declares `carousel`, but that enum is shared across
    every network and Buffer validates per network at publish time. Instagram
    answered: "does not support the 'carousel' post type. Valid types are post,
    story, or reel." A type existing in the schema is not a contract for the
    network being posted to, and this is the test that remembers that.
    """
    for provider in publishing.PROVIDERS.values():
        assert "instagram" not in provider.photo_carousel_platforms, provider.label


def test_a_network_refuses_more_images_than_it_swipes(
    monkeypatch, carousel_images: list[str]
) -> None:
    """Refused here rather than by the network, which rejects a built post.

    The ceilings differ by network - TikTok takes thirty-five, Instagram's API
    ten - and only the largest bounds the request itself. This lowers TikTok's
    for the length of the test rather than asserting against Instagram, which
    no engine can post a carousel to today.
    """
    monkeypatch.setattr(publishing, "CAROUSEL_LIMITS", {"tiktok": 2})
    body = carousel(carousel_images)  # three images

    with pytest.raises(ValueError, match="at most 2 images"):
        publishing._validate_request(publishing.PROVIDERS["zernio"], body)


def test_the_request_itself_is_bounded_by_the_largest_ceiling() -> None:
    # Whatever the network, a request carrying more than any of them takes is
    # refused before it reaches a provider at all.
    assert publishing.MAX_CAROUSEL_IMAGES == 35


def test_tiktok_still_takes_thirty_five() -> None:
    # The ceilings are per network and nowhere near each other.
    assert publishing.carousel_limit("tiktok") == 35
    assert publishing.carousel_limit("instagram") == 10
    assert publishing.carousel_limit("threads") == 0


# --- saying something after the post ------------------------------------------
#
# Two fields, one capability. Buffer takes `firstComment` on Instagram, Facebook
# and LinkedIn, and a `thread` array on Twitter, Threads, Mastodon and Bluesky -
# and on the second group a reply in the thread *is* the follow-up, because
# there is no comment box beside the post to put one in.


def test_a_follow_up_on_threads_rides_the_thread_array(media_file: Path) -> None:
    """It used to be dropped, and the campaign then reported that this
    destination's engine "cannot post one" - on a network Buffer had been
    posting replies to all along."""
    meta = _buffer_meta(media_file, "threads", first_comment="Link: https://example.com")

    assert "thread: [" in meta
    # The caption leads, because Buffer wants the root in the array too.
    assert '{ text: "Launch clip" }, { text: "Link: https://example.com" }' in meta
    # And never through the field Threads does not declare.
    assert "firstComment" not in meta


def test_a_follow_up_lands_after_the_thread_rather_than_inside_it(media_file: Path) -> None:
    # On a network with real threads the link belongs after the point has been
    # made; slipping it in second would cut the thread in half.
    meta = _buffer_meta(
        media_file, "threads", thread=["Second point."], first_comment="Link: https://x.com"
    )

    assert (
        '{ text: "Launch clip" }, { text: "Second point." }, { text: "Link: https://x.com" }'
    ) in meta


def test_instagram_still_uses_the_field_buffer_declares_for_it(media_file: Path) -> None:
    meta = _buffer_meta(media_file, "instagram", first_comment="#tags")

    assert 'firstComment: "#tags"' in meta
    # Instagram declares no thread array, and Buffer rejects a field a network
    # does not accept outright.
    assert "thread: [" not in meta


def test_a_thread_network_with_nothing_to_add_sends_no_array(media_file: Path) -> None:
    # An array holding only the caption would turn every ordinary post into a
    # one-part "thread".
    meta = _buffer_meta(media_file, "threads")

    assert "thread: [" not in meta


def test_the_plan_names_the_follow_up_the_way_each_network_shows_it() -> None:
    """A plan covering both kinds at once should not call them one thing."""
    body = publishing.PublishRequest(
        workspace_id="ws", video_path="clip.mp4", caption="hello",
        date=datetime(2026, 8, 18, 12, 0),
        first_comment="the link",
        targets=[
            publishing.PublishTarget(platform="threads", integration_id="a1"),
            publishing.PublishTarget(platform="instagram", integration_id="a2"),
        ],
        confirm_external_action=True,
    )

    plan = {item["platform"]: item["notes"] for item in
            publishing._delivery_plan(publishing.PROVIDERS["buffer"], body)}

    assert any("reply in the thread" in note for note in plan["threads"])
    assert any("First comment posted after" in note for note in plan["instagram"])


# --- what YouTube will actually make of the clip -------------------------------
#
# The Short/Video choice reaches no API. Buffer's YouTube input declares a
# title, a category and an AI disclosure and nothing else, because YouTube
# reads the file. So the plan says what the file will become.


def _shaped(monkeypatch, tmp_path: Path, *, width, height, duration_ms) -> str:
    """A clip whose probe answers whatever the case needs."""
    clip = tmp_path / f"{width}x{height}-{duration_ms}.mp4"
    clip.write_bytes(b"test-video")
    monkeypatch.setattr(
        publishing,
        "_video_shape",
        lambda path_text: publishing.VideoShape(width, height, duration_ms),
    )
    return str(clip)


def test_a_long_landscape_clip_is_not_going_to_be_a_short(monkeypatch, tmp_path) -> None:
    """The preview promised "Delivered as a Short" for a seven-minute landscape
    clip, which was the operator's selection read back rather than anything
    YouTube would do."""
    clip = _shaped(monkeypatch, tmp_path, width=1280, height=720, duration_ms=438_000)

    surface, why = publishing.youtube_surface(clip)

    assert surface == "video"
    assert "438s is over the 60s Shorts limit" in why
    assert "1280x720 is not vertical" in why


def test_a_short_vertical_clip_is_a_short(monkeypatch, tmp_path) -> None:
    clip = _shaped(monkeypatch, tmp_path, width=1072, height=1920, duration_ms=4_400)

    surface, why = publishing.youtube_surface(clip)

    assert surface == "short"
    assert "under a minute and vertical" in why


def test_vertical_is_not_enough_on_its_own(monkeypatch, tmp_path) -> None:
    # Both halves of YouTube's rule, not whichever one is easier to check.
    clip = _shaped(monkeypatch, tmp_path, width=720, height=1270, duration_ms=316_000)

    surface, why = publishing.youtube_surface(clip)

    assert surface == "video"
    assert "not vertical" not in why


def test_a_clip_that_cannot_be_probed_makes_no_claim() -> None:
    # An unreadable file is not evidence of anything, and delivery already
    # fails it by name. Guessing here would refuse twice for one fault.
    assert publishing.youtube_surface(None) == ("", None)
    assert publishing.youtube_surface("S:/nowhere/missing.mp4") == ("", None)


def test_the_plan_says_so_only_when_it_disagrees_with_the_choice(
    monkeypatch, tmp_path
) -> None:
    """Agreeing with the operator is not news."""
    def plan_for(post_type, *, width, height, duration_ms):
        clip = _shaped(
            monkeypatch, tmp_path, width=width, height=height, duration_ms=duration_ms
        )
        body = publishing.PublishRequest(
            workspace_id="ws", video_path=clip, caption="hello",
            date=datetime(2026, 8, 18, 12, 0),
            targets=[publishing.PublishTarget(
                platform="youtube", integration_id="a1", post_type=post_type,
            )],
            confirm_external_action=True,
        )
        return publishing._delivery_plan(publishing.PROVIDERS["buffer"], body)[0]["notes"]

    asked_short_got_video = plan_for(
        "short", width=1280, height=720, duration_ms=438_000
    )
    assert any("normal video" in note for note in asked_short_got_video)

    asked_short_got_short = plan_for(
        "short", width=1072, height=1920, duration_ms=4_400
    )
    assert not any("YouTube will publish" in note for note in asked_short_got_short)
