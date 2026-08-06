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


def use_provider(monkeypatch, tmp_path: Path, provider: str) -> None:
    monkeypatch.setattr(
        publishing,
        "get_settings",
        lambda: SimpleNamespace(
            publishing_media_root_list=[str(tmp_path)],
            publishing_provider=provider,
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
    assert result["accounts"] == [
        {"id": "a1", "platform": "tiktok", "label": "TrendRelay"},
        {"id": "a3", "platform": "youtube", "label": "Video Channel"},
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
    ]
    serialized = repr(status)
    assert "pk_test" not in serialized
    assert "sk_test" not in serialized
    for provider in status["providers"]:
        for field in provider["credential_fields"]:
            assert set(field) == {
                "id", "key", "label", "secret", "required", "help", "configured"
            }


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
    with pytest.raises(ValueError, match="board name"):
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
        "reel", "story", "post",
    ]
    # TikTok has no Story surface in any of these APIs, so there is no choice.
    assert [kind.id for kind in publishing.post_types_for("tiktok")] == ["video"]
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
    with pytest.raises(ValueError, match="board name"):
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
