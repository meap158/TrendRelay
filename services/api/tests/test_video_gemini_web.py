"""Gemini Web: a Veo provider that uses a signed-in session through a local bridge.

The cookie is typed on the Tools card, written for the bridge, and never sent
anywhere else. Check asks the bridge for its models and starts no video. A clip
is one request to the bridge, and a resumed one is polled, not sent again.
"""

from __future__ import annotations

import dataclasses
import json
import re

import pytest

from trendrelay_api.integrations import video_generation as video

COOKIE = "SID=abc; __Secure-1PSID=g.a000xyz; __Secure-1PSIDTS=sidts-123"
REFUSED = b'{"status":"failed","error":{"code":"refused","message":"No."}}'
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
    assert cookie["label"] == "Veo (Gemini Web) cookie"
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
    assert {"id": "gemini_web", "label": "Veo (Gemini Web)"} in video.ready_providers()


def _parts(body: bytes) -> list[tuple[str, bytes]]:
    """(disposition header, content) for each part of a multipart body."""
    boundary = body.split(b"\r\n", 1)[0]
    parts = []
    for chunk in body.split(boundary)[1:-1]:
        head, _, content = chunk.partition(b"\r\n\r\n")
        parts.append((head.decode("utf-8", "replace"), content.removesuffix(b"\r\n")))
    return parts


def test_one_request_one_clip_with_the_product_picture_attached(values) -> None:
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
    parts = _parts(body or b"")
    fields = {
        re.search(r'name="(\w+)"', head).group(1): content
        for head, content in parts if "filename=" not in head
    }
    assert fields == {
        "model": b"veo-3", "prompt": b"A silent hallway.", "aspect_ratio": b"9:16",
    }
    pictures = [(head, content) for head, content in parts if "filename=" in head]
    assert len(pictures) == 1
    assert 'name="input_reference"' in pictures[0][0]
    assert "Content-Type: image/jpeg" in pictures[0][0]
    assert pictures[0][1] == b"\xff\xd8\xff\x00"
    assert [call[0] for call in seen].count("POST") == 1
    assert seen[-1][1].endswith("/videos/video_1/content")


def test_a_characters_pictures_follow_the_product_and_stop_at_three(values) -> None:
    transport, seen = _bridge([(200, b'{"id":"video_1","status":"queued"}'), (200, REFUSED)])
    references = tuple((bytes([n]) * 4, "image/png") for n in range(1, 5))
    with pytest.raises(video.VideoError):
        video.generate_gemini_web(
            _provider(), "p", b"\xff\xd8\xff\x00", "image/jpeg", references=references,
            transport=transport, sleep=lambda _s: None, now=lambda: 0,
        )
    sent = [c for head, c in _parts(seen[0][2] or b"") if "filename=" in head]
    assert sent == [b"\xff\xd8\xff\x00", b"\x01" * 4, b"\x02" * 4]


def test_a_draft_with_no_picture_is_sent_as_plain_json(values) -> None:
    transport, seen = _bridge([(200, b'{"id":"video_1","status":"queued"}'), (200, REFUSED)])
    with pytest.raises(video.VideoError):
        video.generate_gemini_web(
            _provider(), "p", b"", "image/jpeg", transport=transport,
            sleep=lambda _s: None, now=lambda: 0,
        )
    assert json.loads(seen[0][2] or b"{}")["prompt"] == "p"


def test_an_unpatched_bridge_is_named_before_anything_is_made(values) -> None:
    refusal = b'{"error":{"message":"input_reference is not supported; only text-to-video"}}'
    transport, seen = _bridge([(400, refusal)])
    with pytest.raises(video.VideoError, match="unpatched"):
        _run(transport)
    assert len(seen) == 1


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


def test_a_clip_is_tagged_by_provider_model_family_and_recipe() -> None:
    base = ["ai-generated", "ai-video"]
    assert video.clip_tags("gemini_web", "hanger_rail_reveal") == [
        *base, "veo", "gemini-web", "hanger_rail_reveal",
    ]
    assert video.clip_tags("xai") == [*base, "grok"]
    assert video.clip_tags("gemini", "HANGER") == [*base, "veo", "gemini", "HANGER"]
    # A provider added later still gets its own id as a tag.
    assert video.clip_tags("newcomer") == [*base, "newcomer"]
    # No tag twice, whatever its case.
    assert video.clip_tags("gemini", "Veo") == [*base, "veo", "gemini"]


def test_a_clip_is_named_for_what_it_shows_and_what_made_it() -> None:
    web, grok = _provider(), video.PROVIDERS["xai"]
    assert video.clip_title(web, "Silk cami set", "hanger_rail_reveal") == (
        "Silk cami set - Hanger rail reveal - Veo (Gemini Web)"
    )
    assert video.clip_title(grok, "  Silk   cami set ", "mirror_selfie") == (
        "Silk cami set - Mirror selfie - Grok (xAI)"
    )
    # Nothing known about the draft still names the maker, never an empty or bare title.
    assert video.clip_title(web) == "Veo (Gemini Web)"
    # The maker survives a very long product name.
    long = video.clip_title(web, "x" * 500, "mirror_selfie")
    assert long.endswith("Veo (Gemini Web)") and len(long) <= 300


def test_provenance_says_plainly_that_the_clip_is_generated() -> None:
    record = video.provenance(_provider(), "A title")
    assert record["ai_generated"] == "true"
    assert "AI-generated video" in record["comment"]
    assert "Veo (Gemini Web)" in record["comment"] and "veo-3" in record["comment"]
    assert record["title"] == "A title"


def test_provenance_is_written_into_the_file_and_the_streams_are_untouched(tmp_path) -> None:
    import shutil
    import subprocess

    from trendrelay_api.integrations.effects import FFMPEG

    if not shutil.which(str(FFMPEG)) and not FFMPEG.is_file():
        pytest.skip("ffmpeg is not available")
    source = tmp_path / "in.mp4"
    subprocess.run(
        [str(FFMPEG), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x64:rate=10",
         "-f", "lavfi", "-i", "sine=frequency=440", "-t", "1", "-shortest", str(source)],
        check=True, capture_output=True,
    )
    fields = video.provenance(_provider(), "Silk cami set - Veo (Gemini Web)")
    marked = video.embed_provenance(source.read_bytes(), fields)
    assert marked != source.read_bytes() and marked[4:8] == b"ftyp"
    out = tmp_path / "out.mp4"
    out.write_bytes(marked)
    info = subprocess.run(
        [str(FFMPEG), "-i", str(out)], capture_output=True, text=True, check=False,
    ).stderr
    assert "ai_generated" in info and "AI-generated video" in info
    assert "Video:" in info and "Audio:" in info
    # Garbage in, the same garbage out: a failed remux never loses the clip.
    assert video.embed_provenance(b"not a video", fields) == b"not a video"


VIEW = {
    "prompt": "A hand lifts the set from the rail, the exact set in the attached product image.",
    "products": [
        {
            "name": "Silk cami set",
            "listing_fields": {"description": "Satin  cami and shorts\nin cream."},
        },
        {"name": "Lace robe"},
    ],
    "character": {"name": "Mai", "archived": False},
}


def _blind() -> video.VideoProvider:
    """A provider that cannot be sent the picture, such as one added later."""
    return dataclasses.replace(_provider(), takes_image=False)


def test_a_provider_with_no_picture_is_told_about_the_product_in_words() -> None:
    sent = video.prompt_for(_blind(), VIEW)
    assert sent.endswith(VIEW["prompt"])
    assert "Product: Silk cami set." in sent
    assert "together with: Lace robe" in sent
    assert "Listing description: Satin cami and shorts in cream." in sent
    assert "The person in the video is Mai." in sent
    assert len(sent.encode("utf-8")) < video.GEMINI_WEB_PROMPT_BYTES


def test_a_provider_that_takes_the_picture_gets_the_prompt_untouched() -> None:
    for provider_id in ("xai", "gemini", "gemini_web"):
        assert video.PROVIDERS[provider_id].takes_image is True
        assert video.prompt_for(video.PROVIDERS[provider_id], VIEW) == VIEW["prompt"]


def test_context_is_left_out_when_the_draft_names_nothing() -> None:
    bare = {"prompt": "Only the prompt.", "products": []}
    assert video.prompt_for(_blind(), bare) == "Only the prompt."
    long = {**VIEW, "products": [{"name": "Set", "listing_fields": {"description": "word " * 900}}]}
    assert len(video.prompt_context(long)) <= video._CONTEXT_BUDGET
    # An archived character is not named as being in the video.
    archived = {**VIEW, "character": {"name": "Mai", "archived": True}}
    assert "Mai" not in video.prompt_context(archived)
