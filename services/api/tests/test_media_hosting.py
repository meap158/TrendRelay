import hashlib
import hmac
from pathlib import Path

import pytest

from trendrelay_api.integrations import media_hosting


def test_signing_key_matches_the_documented_derivation() -> None:
    """SigV4 chains four HMACs; a wrong order signs cleanly and is rejected remotely."""
    secret, stamp = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "20260806"

    expected = hmac.new(
        hmac.new(
            hmac.new(
                hmac.new(f"AWS4{secret}".encode(), stamp.encode(), hashlib.sha256).digest(),
                media_hosting.REGION.encode(),
                hashlib.sha256,
            ).digest(),
            media_hosting.SERVICE.encode(),
            hashlib.sha256,
        ).digest(),
        b"aws4_request",
        hashlib.sha256,
    ).digest()

    assert media_hosting.signing_key(secret, stamp) == expected


def test_canonical_request_has_the_exact_shape_sigv4_hashes() -> None:
    """Whitespace and header order are part of the signature, not cosmetic."""
    canonical = media_hosting.canonical_request(
        "PUT",
        "/bucket/media/ab/abc.mp4",
        "acct.r2.cloudflarestorage.com",
        "hash",
        "20260806T101500Z",
    )
    lines = canonical.split("\n")

    assert lines[0] == "PUT"
    assert lines[1] == "/bucket/media/ab/abc.mp4"
    assert lines[2] == ""  # no query string
    assert lines[3].startswith("host:")
    assert "x-amz-content-sha256:hash" in canonical
    assert "x-amz-date:20260806T101500Z" in canonical
    # Signed headers then payload hash close the request.
    assert lines[-2] == "host;x-amz-content-sha256;x-amz-date"
    assert lines[-1] == "hash"


def test_authorization_header_names_the_scope_and_signed_headers() -> None:
    header = media_hosting.authorization_header("AKID", "secret", "20260806T101500Z", "canonical")

    assert header.startswith("AWS4-HMAC-SHA256 Credential=AKID/20260806/auto/s3/aws4_request")
    assert "SignedHeaders=host;x-amz-content-sha256;x-amz-date" in header
    assert "Signature=" in header


def test_object_key_is_content_addressed_so_a_cut_cannot_be_confused() -> None:
    """A blurred render and its original must never share a key."""
    original = media_hosting.object_key("a" * 64, ".mp4")
    blurred = media_hosting.object_key("b" * 64, ".mp4")

    assert original != blurred
    assert original.startswith("media/aa/")
    # Re-uploading the same bytes reuses one object rather than duplicating it.
    assert media_hosting.object_key("a" * 64, ".MP4") == original


def test_public_url_joins_without_doubling_the_separator() -> None:
    assert (
        media_hosting.public_url("https://cdn.example.com/", "media/ab/x.mp4")
        == "https://cdn.example.com/media/ab/x.mp4"
    )


def test_status_reports_what_is_missing_without_raising(monkeypatch) -> None:
    monkeypatch.setattr(media_hosting, "configured_keys", lambda keys: dict.fromkeys(keys, False))
    state = media_hosting.status()

    assert state["configured"] is False
    assert set(state["missing_keys"]) == set(media_hosting.CREDENTIAL_KEYS)
    assert "R2_BUCKET" in state["reason"]


def test_upload_refuses_when_storage_is_not_configured(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(media_hosting, "effective_value", lambda key: "")
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"x")

    with pytest.raises(media_hosting.MediaHostingUnavailable, match="not configured"):
        media_hosting.upload(media, "a" * 64)


def test_upload_returns_a_stable_url_that_does_not_expire(monkeypatch, tmp_path: Path) -> None:
    """A signed URL outlives createPost but not the publish, so it must not be one."""
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"video-bytes")
    values = {
        "R2_ACCOUNT_ID": "acct",
        "R2_ACCESS_KEY_ID": "AKID",
        "R2_SECRET_ACCESS_KEY": "secret",
        "R2_BUCKET": "media",
        "R2_PUBLIC_BASE_URL": "https://cdn.example.com",
    }
    monkeypatch.setattr(media_hosting, "effective_value", lambda key: values[key])
    sent: dict[str, object] = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(request, timeout=None):
        sent["url"] = request.full_url
        sent["method"] = request.get_method()
        sent["auth"] = request.get_header("Authorization")
        return Response()

    monkeypatch.setattr(media_hosting.urllib.request, "urlopen", fake_urlopen)
    digest = hashlib.sha256(b"video-bytes").hexdigest()

    result = media_hosting.upload(media, digest)

    assert result["expires"] is False
    assert result["url"] == f"https://cdn.example.com/{media_hosting.object_key(digest, '.mp4')}"
    # No query string means nothing to expire.
    assert "?" not in result["url"]
    assert sent["method"] == "PUT"
    assert "acct.r2.cloudflarestorage.com" in str(sent["url"])
    assert str(sent["auth"]).startswith("AWS4-HMAC-SHA256 Credential=AKID/")
