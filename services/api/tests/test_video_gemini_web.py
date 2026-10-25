"""Gemini Web: a Veo provider that uses a signed-in session through a local bridge.

The cookie is typed on the Tools card, written for the bridge, and never sent
anywhere else. Check asks the bridge for its models and starts no video. A clip
is one request to the bridge, and a resumed one is polled, not sent again.
"""

from __future__ import annotations

import json

import pytest

from trendrelay_api.integrations import video_generation as video

COOKIE = "SID=abc; __Secure-1PSID=g.a000xyz; __Secure-1PSIDTS=sidts-123"
MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 16


@pytest.fixture
def values(monkeypatch, tmp_path):
    store: dict[str, str] = {}
    monkeypatch.setattr(video, "effective_value", lambda key: store.get(key, ""))
    monkeypatch.setattr(video, "CHECKS_PATH", tmp_path / "checks.json")
    monkeypatch.setattr(video, "BRIDGE_ENV_PATH", tmp_path / "gemini-web" / ".env")

    def write(values: dict[str, str]) -> list[str]:
        store.update(values)
        return list(values)

    monkeypatch.setattr(video, "write_env_values", write)
    return store


def _provider() -> video.VideoProvider:
    return video.PROVIDERS["gemini_web"]


def _bridge(answers):
    """A transport that records calls and answers from a list of (status, body) or errors."""
    seen: list[tuple[str, str, bytes | None]] = []

    def transport(method, url, headers, body, timeout):
        seen.append((method, url, body))
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return transport, seen


def _run(transport, progress=None, prompt="A silent hallway."):
    return video.generate_gemini_web(
        _provider(), prompt, b"\xff\xd8\xff\x00", "image/jpeg",
        transport=transport, sleep=lambda _seconds: None, now=lambda: 0, progress=progress,
    )


def test_it_is_offered_on_the_card_as_a_cookie_not_an_api_key(values) -> None:
    fields = {field["key"]: field for field in video.describe_fields()}
    cookie = fields["GEMINI_WEB_COOKIES"]
    assert cookie["secret"] is True
    assert cookie["label"] == "Gemini Web cookie"
    assert fields["VIDEO_PROVIDER_GEMINI_WEB_ENABLED"]["options"] == ["on", "off"]
    assert fields["GEMINI_WEB_BRIDGE_URL"]["value"] == video.GEMINI_WEB_BRIDGE
    assert fields["GEMINI_WEB_AUTH_USER"]["kind"] == "text"


def test_a_pasted_cookie_header_is_cleaned_and_a_wrong_paste_is_refused(values) -> None:
    video.save_settings({"GEMINI_WEB_COOKIES": f"Cookie: {COOKIE}"})
    assert values["GEMINI_WEB_COOKIES"] == COOKIE
    with pytest.raises(video.SettingsError, match="__Secure-1PSIDTS"):
        video.save_settings({"GEMINI_WEB_COOKIES": "just some words that are not a cookie"})
    # PSIDTS alone is not enough: the bridge exits without PSID as well.
    with pytest.raises(video.SettingsError, match="__Secure-1PSID"):
        video.save_settings({"GEMINI_WEB_COOKIES": "a=b; __Secure-1PSIDTS=x"})
    with pytest.raises(video.SettingsError, match="one line"):
        video.save_settings({"GEMINI_WEB_COOKIES": COOKIE + "\nsecond"})


def test_the_bridge_address_and_account_slot_are_validated(values) -> None:
    video.save_settings({
        "GEMINI_WEB_BRIDGE_URL": "http://127.0.0.1:5000/", "GEMINI_WEB_AUTH_USER": "2",
    })
    assert values["GEMINI_WEB_BRIDGE_URL"] == "http://127.0.0.1:5000"
    for bad in ("ftp://host", "http://user:pw@host", "not a url", "http://host/?q=1"):
        with pytest.raises(video.SettingsError):
            video.save_settings({"GEMINI_WEB_BRIDGE_URL": bad})
    with pytest.raises(video.SettingsError):
        video.save_settings({"GEMINI_WEB_AUTH_USER": "x"})


def test_saving_writes_the_env_file_the_bridge_starts_with(values) -> None:
    assert video.write_bridge_env() is None
    video.save_settings({
        "GEMINI_WEB_COOKIES": COOKIE, "GEMINI_WEB_BRIDGE_URL": "http://127.0.0.1:5000",
        "GEMINI_WEB_AUTH_USER": "2",
    })
    lines = video.BRIDGE_ENV_PATH.read_text(encoding="utf-8").splitlines()
    assert lines == ["PORT=5000", f"GEMINI_COOKIES='{COOKIE}'", "GEMINI_AUTH_USER=2"]


def test_check_asks_the_bridge_for_models_and_never_for_a_video(values) -> None:
    video.save_settings({"GEMINI_WEB_COOKIES": COOKIE})
    transport, seen = _bridge([(200, b'{"object":"list","data":[{"id":"gemini-2.5-flash"}]}')])
    outcome = video.check_provider("gemini_web", transport=transport)
    assert outcome["ok"] is True
    assert seen == [("GET", "http://127.0.0.1:4981/openai/v1/models", None)]


@pytest.mark.parametrize("body", [b'{"object":"list","data":null}', b'{"data":[]}', b"not json"])
def test_a_bridge_with_a_rejected_cookie_fails_check(values, body) -> None:
    """The bridge answers 200 with no models when Gemini refuses its cookie."""
    video.save_settings({"GEMINI_WEB_COOKIES": COOKIE})
    transport, _seen = _bridge([(200, body)])
    outcome = video.check_provider("gemini_web", transport=transport)
    assert outcome["ok"] is False
    assert "cookie" in outcome["message"]


def test_check_says_where_to_start_the_bridge_when_it_is_down(values) -> None:
    video.save_settings({"GEMINI_WEB_COOKIES": COOKIE})
    transport, _seen = _bridge([ConnectionRefusedError("refused")])
    outcome = video.check_provider("gemini_web", transport=transport)
    assert outcome["ok"] is False
    assert str(video.BRIDGE_ENV_PATH.parent) in outcome["message"]
    # Nothing saved yet: the card asks for the cookie first.
    values.pop("GEMINI_WEB_COOKIES")
    assert "cookie" in video.check_provider("gemini_web", transport=transport)["message"]


def test_it_is_not_offered_until_on_and_checked(values) -> None:
    video.save_settings({"GEMINI_WEB_COOKIES": COOKIE})
    assert all(item["id"] != "gemini_web" for item in video.ready_providers())
    video.save_settings({"VIDEO_PROVIDER_GEMINI_WEB_ENABLED": "on"})
    video._store_check("gemini_web", ok=True, message="ok")
    assert {"id": "gemini_web", "label": "Gemini Web"} in video.ready_providers()


def test_one_request_one_clip_and_no_image_is_sent(values) -> None:
    transport, seen = _bridge([
        (200, b'{"id":"video_1","status":"queued"}'),
        (200, b'{"id":"video_1","status":"in_progress","progress":40}'),
        (200, b'{"id":"video_1","status":"completed"}'),
        (200, MP4),
    ])
    data = _run(transport)
    assert data == MP4
    method, url, body = seen[0]
    assert (method, url) == ("POST", "http://127.0.0.1:4981/openai/v1/videos")
    sent = json.loads(body or b"{}")
    assert sent == {"model": "veo-3", "prompt": "A silent hallway.", "aspect_ratio": "9:16"}
    assert [call[0] for call in seen].count("POST") == 1
    assert seen[-1][1].endswith("/videos/video_1/content")


def test_a_resumed_clip_is_polled_and_never_sent_again(values) -> None:
    transport, seen = _bridge([(200, b'{"status":"completed"}'), (200, MP4)])
    _run(transport, progress=video.Progress(resume="video_1"))
    assert [call[0] for call in seen] == ["GET", "GET"]


def test_a_busy_bridge_is_reported_not_retried_into_a_second_clip(values) -> None:
    transport, seen = _bridge([(429, b'{"error":"another video job is running"}')])
    with pytest.raises(video.VideoError) as caught:
        _run(transport)
    assert caught.value.code == "quota"
    assert len(seen) == 1


@pytest.mark.parametrize(("code", "expected"), [
    ("quota_exceeded", "quota"), ("refused", "moderation"), ("timeout", "unavailable"),
])
def test_a_failed_job_maps_to_the_codes_the_draft_shows(values, code, expected) -> None:
    transport, _seen = _bridge([
        (200, b'{"id":"video_1","status":"queued"}'),
        (200, json.dumps({"status": "failed", "error": {"code": code, "message": "No."}}).encode()),
    ])
    with pytest.raises(video.VideoError) as caught:
        _run(transport)
    assert caught.value.code == expected


def test_a_download_before_the_file_is_ready_is_asked_again(values) -> None:
    transport, seen = _bridge([
        (200, b'{"id":"video_1","status":"queued"}'),
        (200, b'{"status":"completed"}'),
        (409, b"not ready"),
        (200, MP4),
    ])
    assert _run(transport) == MP4
    assert [call[0] for call in seen].count("POST") == 1


def test_a_prompt_the_bridge_would_refuse_never_leaves(values) -> None:
    transport, seen = _bridge([])
    with pytest.raises(video.VideoError):
        _run(transport, prompt="x" * (video.GEMINI_WEB_PROMPT_BYTES + 1))
    assert seen == []
