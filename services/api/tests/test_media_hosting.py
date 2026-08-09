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


# --- the access check ---------------------------------------------------------


CONFIGURED = {
    "R2_ACCOUNT_ID": "acct",
    "R2_ACCESS_KEY_ID": "AKID",
    "R2_SECRET_ACCESS_KEY": "secret",
    "R2_BUCKET": "media",
    "R2_PUBLIC_BASE_URL": "https://cdn.example.com",
}


class _Reply:
    """Enough of an HTTP response for the probe to read."""

    def __init__(self, body: bytes = b"") -> None:
        self.status = 200
        self._body = body

    def read(self, _size: int | None = None) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _probe_with(monkeypatch, handler) -> dict:
    """Run the probe against a fake bucket, `handler` deciding each step."""
    monkeypatch.setattr(media_hosting, "effective_value", lambda key: CONFIGURED[key])
    written: dict[str, bytes] = {}

    def fake_urlopen(request, timeout=None):
        url = request if isinstance(request, str) else request.full_url
        method = "GET" if isinstance(request, str) else request.get_method()
        if method == "PUT":
            written["body"] = request.data
        return handler(method, url, written)

    monkeypatch.setattr(media_hosting.urllib.request, "urlopen", fake_urlopen)
    return media_hosting.probe()


def by_check(result: dict) -> dict[str, dict]:
    return {check["id"]: check for check in result["checks"]}


def test_the_probe_walks_the_path_a_published_clip_takes(monkeypatch) -> None:
    def handler(method, url, written):
        if method == "PUT":
            return _Reply()
        if url.startswith("https://cdn.example.com/"):
            return _Reply(written["body"])
        return _Reply()

    result = _probe_with(monkeypatch, handler)

    assert result["ok"] is True
    found = by_check(result)
    # The last one is the point: everything else can pass while an engine, which
    # fetches with no credentials from outside, still gets nothing.
    assert found["public"]["ok"] is True
    assert [check["id"] for check in result["checks"]] == [
        "settings", "bucket", "write", "public",
    ]


def test_a_missing_setting_is_reported_before_anything_is_called(monkeypatch) -> None:
    monkeypatch.setattr(media_hosting, "effective_value", lambda key: "")

    def explode(*_args, **_kwargs):  # pragma: no cover - must never run
        raise AssertionError("the probe called out with nothing configured")

    monkeypatch.setattr(media_hosting.urllib.request, "urlopen", explode)
    result = media_hosting.probe()

    assert result["ok"] is False
    assert [check["id"] for check in result["checks"]] == ["settings"]


def test_each_refusal_names_the_setting_to_look_at(monkeypatch) -> None:
    """"Access denied" with no field to check is a dead end.

    Five settings have to agree, and the failure that arrives is the same shape
    whichever one is wrong, so the code is what separates them.
    """
    def refuse(code):
        def handler(method, url, written):
            raise media_hosting.urllib.error.HTTPError(url, code, "no", {}, None)
        return handler

    assert "access key" in by_check(
        _probe_with(monkeypatch, refuse(403)))["bucket"]["detail"]
    assert "bucket" in by_check(
        _probe_with(monkeypatch, refuse(404)))["bucket"]["detail"]

    def unreachable(method, url, written):
        raise media_hosting.urllib.error.URLError("getaddrinfo failed")

    assert "account ID" in by_check(
        _probe_with(monkeypatch, unreachable))["bucket"]["detail"]


def test_a_public_url_serving_something_else_fails_rather_than_passes(monkeypatch) -> None:
    """A 200 from the wrong bucket is the worst outcome available.

    It looks like success and publishes someone else's file, so matching bytes
    are required rather than a status code.
    """
    def handler(method, url, written):
        if url.startswith("https://cdn.example.com/"):
            return _Reply(b"a different object entirely\n")
        return _Reply()

    result = _probe_with(monkeypatch, handler)

    assert result["ok"] is False
    assert "another bucket" in by_check(result)["public"]["detail"]


def test_the_check_writes_outside_the_media_prefix(monkeypatch) -> None:
    # A fixed key so repeated checks overwrite one object, and outside `media/`
    # so it can never be picked up as a clip.
    assert not media_hosting.PROBE_KEY.startswith("media/")
    seen: list[str] = []

    def handler(method, url, written):
        seen.append(url)
        return _Reply(written.get("body", b""))

    _probe_with(monkeypatch, handler)
    assert any(media_hosting.PROBE_KEY in url for url in seen)


def test_the_endpoint_url_cannot_be_saved_as_the_secret(monkeypatch) -> None:
    """The paste error this form invites, refused where it happens.

    Every value on the R2 bucket page is a 32-character hex string or a URL and
    the fields do not say which is which, so the S3 endpoint lands in the secret.
    It saved without complaint and failed at publish.
    """
    monkeypatch.setattr(media_hosting, "effective_value", lambda key: "")
    with pytest.raises(ValueError, match="looks like a URL"):
        media_hosting.save_credentials({
            "secret_access_key": "https://405e37fc.r2.cloudflarestorage.com/bucket",
        })


def test_the_account_id_cannot_be_saved_as_the_access_key(monkeypatch) -> None:
    monkeypatch.setattr(media_hosting, "effective_value", lambda key: "")
    with pytest.raises(ValueError, match="same as the account ID"):
        media_hosting.save_credentials({
            "account_id": "a" * 32,
            "access_key_id": "a" * 32,
        })


def test_a_plausible_key_is_still_saved(monkeypatch) -> None:
    """Only unambiguous mistakes are refused here.

    A wrong-but-plausible key is what the access check is for. Guessing at those
    would stop someone saving a credential that is in fact correct.
    """
    written: dict[str, str] = {}
    monkeypatch.setattr(media_hosting, "effective_value", lambda key: "")
    monkeypatch.setattr(
        media_hosting, "write_env_values",
        lambda updates: (written.update(updates), sorted(updates))[1],
    )

    media_hosting.save_credentials({
        "account_id": "a" * 32,
        "access_key_id": "b" * 32,
        "secret_access_key": "c" * 64,
    })

    assert written["R2_ACCESS_KEY_ID"] == "b" * 32


# --- setup: accept what the Cloudflare dashboard actually puts on screen -------


def test_the_account_id_is_taken_out_of_a_pasted_s3_endpoint() -> None:
    """Nothing in the R2 dashboard is labelled "Account ID" on its own.

    It appears inside the S3 API endpoint, so that whole URL is what gets
    copied. Storing it verbatim produced a host of
    `https://<url>.r2.cloudflarestorage.com` and failed at upload time with a
    DNS error, a long way from the field that caused it.
    """
    account = "0123456789abcdef0123456789abcdef"
    assert media_hosting.normalise(
        "account_id", f"https://{account}.r2.cloudflarestorage.com/clips"
    ) == account
    # The dashboard's own URL carries it too.
    assert media_hosting.normalise(
        "account_id", f"https://dash.cloudflare.com/{account}/r2/overview"
    ) == account
    assert media_hosting.normalise("account_id", f"  {account.upper()}  ") == account


def test_the_bucket_can_be_pasted_as_the_same_endpoint() -> None:
    # So the two fields accept one clipboard, rather than one of them requiring
    # the URL to be edited down by hand.
    account = "0123456789abcdef0123456789abcdef"
    assert media_hosting.normalise(
        "bucket", f"https://{account}.r2.cloudflarestorage.com/trendrelay-media"
    ) == "trendrelay-media"
    assert media_hosting.normalise("bucket", "trendrelay-media") == "trendrelay-media"


def test_the_public_url_gains_a_scheme_and_loses_a_trailing_slash() -> None:
    # Copied from the dashboard it arrives with a slash, and from some screens
    # without a scheme. Both used to be rejected or stored as-is, producing
    # double-slashed media URLs.
    assert media_hosting.normalise(
        "public_base_url", "pub-abc123.r2.dev/"
    ) == "https://pub-abc123.r2.dev"
    assert media_hosting.normalise(
        "public_base_url", "https://media.example.com/"
    ) == "https://media.example.com"


def test_saving_stores_the_normalised_values(monkeypatch) -> None:
    written: dict[str, str] = {}
    monkeypatch.setattr(media_hosting, "write_env_values",
                        lambda values: written.update(values) or list(values))
    account = "0123456789abcdef0123456789abcdef"
    media_hosting.save_credentials({
        "account_id": f"https://{account}.r2.cloudflarestorage.com/clips",
        "bucket": f"https://{account}.r2.cloudflarestorage.com/clips",
        "public_base_url": "pub-abc123.r2.dev/",
    })
    assert written == {
        "R2_ACCOUNT_ID": account,
        "R2_BUCKET": "clips",
        "R2_PUBLIC_BASE_URL": "https://pub-abc123.r2.dev",
    }


def test_a_required_field_that_normalises_to_nothing_is_still_refused() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        media_hosting.save_credentials({"bucket": "   "})
