"""Publish local media to object storage so fetch-only engines can reach it.

Buffer has no upload endpoint: it downloads the file when the post goes out,
which may be days after the post was created. That rules out anything expiring.
Cloudflare R2 is used because its egress is free - the engine re-downloads the
whole video on every publish - and because it is S3-compatible, so the request
is signed with SigV4 here rather than pulling in an SDK for one PUT.

Uploading makes an object readable by anyone holding the URL, for as long as it
exists. Callers must therefore hand over the cut that is safe to publish; see
``publishable_source`` in the publishing adapter, which resolves an asset to its
blurred version when one exists.
"""

from __future__ import annotations

import hashlib
import hmac
import mimetypes
import re
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from trendrelay_api.env_store import (
    configured_keys,
    effective_value,
    masked_value,
    write_env_values,
)

CREDENTIAL_FIELDS: tuple[dict[str, Any], ...] = (
    {
        "id": "account_id",
        "key": "R2_ACCOUNT_ID",
        "label": "Account ID",
        "secret": False,
        "required": True,
        "help": (
            "R2 → your bucket → Settings → S3 API. Paste the whole endpoint URL; "
            "the account ID is taken out of it."
        ),
    },
    {
        "id": "access_key_id",
        "key": "R2_ACCESS_KEY_ID",
        "label": "Access key ID",
        "secret": False,
        "required": True,
        "help": (
            "R2 → API → Manage API tokens → Create token, with Object Read & Write "
            "on this bucket. Then take the Access Key ID from under \"Use the "
            "following credentials for S3 clients\"."
        ),
    },
    {
        "id": "secret_access_key",
        "key": "R2_SECRET_ACCESS_KEY",
        "label": "Secret access key",
        "secret": True,
        "required": True,
        # The page shows a Token value above these two, and it is also shown
        # once - so "shown once" on its own points at the wrong credential.
        "help": (
            "The Secret Access Key under \"Use the following credentials for S3 "
            "clients\", shown once. Not the Token value above it: that is for "
            "Cloudflare's own API and is never used here."
        ),
    },
    {
        "id": "bucket",
        "key": "R2_BUCKET",
        "label": "Bucket",
        "secret": False,
        "required": True,
        "help": "The bucket name, or paste the same S3 API endpoint again.",
    },
    {
        "id": "public_base_url",
        "key": "R2_PUBLIC_BASE_URL",
        "label": "Public base URL",
        "secret": False,
        "required": True,
        "help": (
            "R2 → your bucket → Settings → Public development URL, or your custom "
            "domain. Anyone with a link can fetch these files, so use a bucket kept "
            "for publishing."
        ),
    },
)
CREDENTIAL_KEYS: tuple[str, ...] = tuple(field["key"] for field in CREDENTIAL_FIELDS)

#: The account id inside anything Cloudflare shows it in - the S3 API endpoint
#: on the bucket page, or the dashboard URL itself. It is a 32-character hex
#: string in both.
_ACCOUNT_ID = re.compile(r"\b([0-9a-f]{32})\b", re.IGNORECASE)
_S3_ENDPOINT = re.compile(
    r"https?://([0-9a-f]{32})\.r2\.cloudflarestorage\.com/?([^/?#]*)", re.IGNORECASE
)


def normalise(field_id: str, value: str) -> str:
    """Accept what Cloudflare puts on screen, not what this file happens to store.

    Nothing on the R2 dashboard is labelled "Account ID" on its own. It appears
    inside the S3 API endpoint and inside the dashboard's own URL, so the value
    that actually gets copied is a whole URL - and pasting it produced a
    configuration that failed later, at upload time, with a DNS error.

    Same for the public base URL: the address is copied with its trailing slash
    and, from some screens, without a scheme.
    """
    value = value.strip()
    if not value:
        return value
    if field_id == "account_id":
        found = _ACCOUNT_ID.search(value)
        return found.group(1).lower() if found else value
    if field_id == "bucket":
        # A pasted S3 endpoint carries the bucket after the host.
        endpoint = _S3_ENDPOINT.match(value)
        if endpoint and endpoint.group(2):
            return endpoint.group(2)
        return value.rstrip("/").rsplit("/", 1)[-1] if "/" in value else value
    if field_id == "public_base_url":
        if not value.startswith(("http://", "https://")):
            value = f"https://{value}"
        return value.rstrip("/")
    return value


# R2 ignores the region but SigV4 requires one, and this is the value it expects.
REGION = "auto"
SERVICE = "s3"
UPLOAD_TIMEOUT_SECONDS = 900
PROBE_TIMEOUT_SECONDS = 20

#: Where the access check writes. Outside ``media/`` so it can never be mistaken
#: for a clip, and a fixed key so repeated checks overwrite one object rather
#: than littering the bucket.
PROBE_KEY = "trendrelay/access-check.txt"

#: The empty payload's SHA-256, which SigV4 requires for a body-less request.
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

#: Sent on the unauthenticated public fetch, because Cloudflare answers 403 to
#: urllib's default ``Python-urllib/3.x`` regardless of whether the bucket is
#: public. Without this the probe blames the bucket for a bot filter and sends
#: an operator to turn on a setting that was already on - which it did, and
#: which cost real time before the identical URL was tried with curl and
#: answered 200. Any string that is not the urllib default is accepted; naming
#: ourselves is simply the honest one.
PUBLIC_FETCH_USER_AGENT = "TrendRelay/1.0 (+media-hosting-check)"


def public_fetch(url: str) -> urllib.request.Request:
    """A GET that Cloudflare will answer, carrying no credentials.

    Credential-free is the point: it is the same request an engine makes when
    it collects the media, so anything this cannot fetch, an engine cannot
    either.
    """
    return urllib.request.Request(url, headers={"User-Agent": PUBLIC_FETCH_USER_AGENT})


class MediaHostingUnavailable(RuntimeError):
    """Raised when object storage is not configured or refuses an upload."""


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


def signing_key(secret: str, stamp: str) -> bytes:
    """Derive the SigV4 date/region/service key chain."""
    date_key = _sign(f"AWS4{secret}".encode(), stamp)
    region_key = _sign(date_key, REGION)
    service_key = _sign(region_key, SERVICE)
    return _sign(service_key, "aws4_request")


def canonical_request(
    method: str, path: str, host: str, payload_hash: str, timestamp: str
) -> str:
    """The exact string SigV4 hashes. Kept pure so a signature can be tested."""
    canonical_headers = (
        f"host:{host}\n"
        f"x-amz-content-sha256:{payload_hash}\n"
        f"x-amz-date:{timestamp}\n"
    )
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    return "\n".join(
        [method, path, "", canonical_headers, signed_headers, payload_hash]
    )


def authorization_header(
    access_key: str, secret: str, timestamp: str, request_hash: str
) -> str:
    stamp = timestamp[:8]
    scope = f"{stamp}/{REGION}/{SERVICE}/aws4_request"
    to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", timestamp, scope, hashlib.sha256(request_hash.encode()).hexdigest()]
    )
    signature = hmac.new(
        signing_key(secret, stamp), to_sign.encode(), hashlib.sha256
    ).hexdigest()
    return (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        "SignedHeaders=host;x-amz-content-sha256;x-amz-date, "
        f"Signature={signature}"
    )


def _settings(draft: dict[str, str] | None = None) -> dict[str, str]:
    """The settings to use, with anything typed but not yet saved on top.

    A draft is normalised exactly as saving would normalise it, so testing
    answers the question saving would ask rather than a slightly different one -
    a pasted S3 endpoint becomes an account ID here too.

    Partial drafts are the point: replacing one wrong value means typing one
    field, and the other four should come from what is already stored rather
    than having to be retyped to be tested.
    """
    values = {key: effective_value(key).strip() for key in CREDENTIAL_KEYS}
    fields = {field["id"]: field for field in CREDENTIAL_FIELDS}
    for field_id, raw in (draft or {}).items():
        field = fields.get(field_id)
        if not field or not (raw or "").strip():
            continue
        values[str(field["key"])] = normalise(field_id, raw)
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise MediaHostingUnavailable(
            "Media hosting is not configured. Add "
            + ", ".join(missing)
            + " on the Publish screen so engines that only fetch can reach your media."
        )
    return values


def object_key(digest: str, suffix: str) -> str:
    """Content-addressed, so re-publishing the same cut reuses one object.

    The digest is the file's own hash, which means a blurred render and its
    original can never collide on a key and quietly serve the wrong one.
    """
    return f"media/{digest[:2]}/{digest}{suffix.lower()}"


def public_url(base: str, key: str) -> str:
    return f"{base.rstrip('/')}/{key}"


def status() -> dict[str, Any]:
    """Report configuration without raising, for a status surface."""
    configured = configured_keys(CREDENTIAL_KEYS)
    missing = sorted(key for key, present in configured.items() if not present)
    return {
        "id": "media-hosting",
        "provider": "cloudflare-r2",
        "label": "Cloudflare R2",
        # Straight to R2 in the Cloudflare dashboard. Every field's help text
        # names a path inside this page, so linking to it is the difference
        # between following instructions and hunting for where they start.
        "dashboard_url": "https://dash.cloudflare.com/?to=/:account/r2",
        "configured": not missing,
        "missing_keys": missing,
        "credential_keys": list(CREDENTIAL_KEYS),
        "credential_fields": [
            {
                **field,
                "configured": configured[field["key"]],
                # Same reason as the engine keys: an empty box reads as nothing
                # saved, and these five are exactly the fields somebody re-pastes
                # by mistake because they cannot see which one is wrong.
                "preview": masked_value(field["key"]),
            }
            for field in CREDENTIAL_FIELDS
        ],
        "reason": None
        if not missing
        else "Add " + ", ".join(missing) + " to publish media that an engine must fetch.",
    }


def _reject_obvious_mix_ups(updates: dict[str, str]) -> None:
    """Catch the two paste errors this form invites, at the point of saving.

    Everything on the R2 bucket page is a 32-character hex string or a URL, and
    the fields do not say which is which, so the S3 endpoint lands in the secret
    and the account ID lands in the access key. Both save without complaint and
    fail much later, at publish.

    Only mistakes that cannot be anything else are refused here. A wrong-but-
    plausible key is what `probe` is for; guessing at those would block someone
    from saving a credential that is in fact correct.
    """
    for key in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
        value = updates.get(key, "")
        if value.startswith(("http://", "https://")):
            raise ValueError(
                f"{key} looks like a URL. That is the S3 API endpoint, which "
                "belongs in Account ID. The access key ID and secret come from "
                "R2 → API → Manage API tokens."
            )
    access_key = updates.get("R2_ACCESS_KEY_ID", "")
    account = updates.get("R2_ACCOUNT_ID") or effective_value("R2_ACCOUNT_ID").strip()
    if access_key and access_key == account:
        raise ValueError(
            "The access key ID is the same as the account ID. They are both "
            "32-character hex strings but different values - create an API "
            "token under R2 → API and paste its access key ID."
        )


def save_credentials(values: dict[str, str]) -> dict[str, Any]:
    """Write hosting settings to the local .env, rejecting anything unrecognised."""
    fields = {field["id"]: field for field in CREDENTIAL_FIELDS}
    unknown = sorted(set(values) - set(fields))
    if unknown:
        raise ValueError(f"Unknown media hosting settings: {', '.join(unknown)}")
    updates: dict[str, str] = {}
    for field_id, raw in values.items():
        field = fields[field_id]
        value = normalise(field_id, raw or "")
        if not value and field["required"]:
            raise ValueError(f"Media hosting {field['label']} cannot be empty.")
        updates[str(field["key"])] = value
    if not updates:
        raise ValueError("Provide at least one setting to save.")
    _reject_obvious_mix_ups(updates)
    return {"written_keys": write_env_values(updates)}


def _signed_request(
    method: str,
    values: dict[str, str],
    request_path: str,
    *,
    body: bytes | None = None,
    content_type: str | None = None,
) -> urllib.request.Request:
    """A SigV4-signed request against the configured bucket."""
    host = f"{values['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com"
    payload_hash = hashlib.sha256(body).hexdigest() if body is not None else EMPTY_SHA256
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    request = urllib.request.Request(
        f"https://{host}{request_path}", data=body, method=method
    )
    request.add_header("Host", host)
    request.add_header("x-amz-content-sha256", payload_hash)
    request.add_header("x-amz-date", timestamp)
    if content_type:
        request.add_header("Content-Type", content_type)
    request.add_header(
        "Authorization",
        authorization_header(
            values["R2_ACCESS_KEY_ID"],
            values["R2_SECRET_ACCESS_KEY"],
            timestamp,
            canonical_request(method, request_path, host, payload_hash, timestamp),
        ),
    )
    return request


def _check(id: str, label: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"id": id, "label": label, "ok": ok, "detail": detail}


def probe(draft: dict[str, str] | None = None) -> dict[str, Any]:
    """Try the whole path a published clip takes, and say which part broke.

    A draft tests values that are only on screen. That matters most when what
    is stored is wrong: without it, the only way to find out whether a
    replacement works is to save over the thing you are replacing.

    Five settings have to agree before a fetch-only engine can collect a video,
    and until now nothing tried them. A wrong account ID, bucket or public URL
    was saved without complaint and failed at publish, which is both the latest
    and the most expensive moment to find out.

    So the check does what publishing does: sign a request against the bucket,
    write a small object, and fetch that object back through the public base URL
    without credentials - the last step being the only one that can prove a URL
    an engine will use from the outside actually serves what was written.

    Each stage names the setting it clears, because "access denied" without a
    field to look at is a dead end. The stages run in order and stop at the
    first failure, since every later one would fail for the same reason.
    """
    checks: list[dict[str, Any]] = []

    try:
        values = _settings(draft)
    except MediaHostingUnavailable as error:
        checks.append(_check("settings", "Settings present", False, str(error)))
        return {"ok": False, "checks": checks}
    try:
        # The same two paste errors saving refuses, reported here as a failed
        # stage rather than raised - the point of testing first is to be told
        # what is wrong, not to be stopped at the door.
        _reject_obvious_mix_ups(values)
    except ValueError as error:
        checks.append(_check("settings", "Settings present", False, str(error)))
        return {"ok": False, "checks": checks}
    checks.append(_check(
        "settings", "Settings present",
        True,
        "All five settings are present"
        + (", including the ones you have typed but not saved." if draft else "."),
    ))

    # The account ID and credentials, against the bucket itself. HEAD writes
    # nothing and its failures are the ones that separate the fields: a host
    # that will not resolve is the account ID, a refusal is the key, a missing
    # bucket is the bucket.
    bucket_path = f"/{values['R2_BUCKET']}"
    try:
        with urllib.request.urlopen(
            _signed_request("HEAD", values, bucket_path), timeout=PROBE_TIMEOUT_SECONDS
        ):
            pass
    except urllib.error.HTTPError as error:
        # A secret that is not 64 hex characters is usually the Token value from
        # the same page, which looks like a credential and is not this one.
        secret = values["R2_SECRET_ACCESS_KEY"]
        wrong_shape = (
            " The Secret Access Key is 64 hexadecimal characters; this one is not,"
            " so it may be the Token value shown above it on that page."
            if not re.fullmatch(r"[0-9a-fA-F]{64}", secret) else ""
        )
        detail = {
            403: "The access key was refused. Check the access key ID and secret, "
                 "and that the token has Object Read & Write on this bucket." + wrong_shape,
            401: "The access key was refused. Check the access key ID and secret."
                 + wrong_shape,
            404: f"No bucket named {values['R2_BUCKET']} on this account. Check the "
                 "bucket, and that the account ID belongs to the same account.",
        }.get(error.code, f"Cloudflare answered HTTP {error.code}.")
        checks.append(_check("bucket", "Bucket reachable", False, detail))
        return {"ok": False, "checks": checks}
    except (OSError, urllib.error.URLError) as error:
        checks.append(_check("bucket", "Bucket reachable", False, (
            f"Could not reach {values['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com "
            f"({error}). Check the account ID."
        )))
        return {"ok": False, "checks": checks}
    checks.append(_check("bucket", "Bucket reachable", True, (
        f"Signed in to {values['R2_BUCKET']} with the saved access key."
    )))

    # A real write, because read access and write access are different grants
    # and only one of them publishes anything.
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = f"TrendRelay access check {stamp}\n".encode()
    try:
        with urllib.request.urlopen(
            _signed_request(
                "PUT", values, f"/{values['R2_BUCKET']}/{quote(PROBE_KEY, safe='/')}",
                body=body, content_type="text/plain",
            ),
            timeout=PROBE_TIMEOUT_SECONDS,
        ):
            pass
    except urllib.error.HTTPError as error:
        checks.append(_check("write", "Upload accepted", False, (
            f"The bucket refused the upload (HTTP {error.code}). The API token "
            "needs Object Read & Write, not read-only."
        )))
        return {"ok": False, "checks": checks}
    except (OSError, urllib.error.URLError) as error:
        checks.append(_check("write", "Upload accepted", False, f"Upload failed: {error}"))
        return {"ok": False, "checks": checks}
    checks.append(_check("write", "Upload accepted", True, (
        f"Wrote {len(body)} bytes to {PROBE_KEY}."
    )))

    # The step nothing else covers. Everything above can pass while an engine
    # still gets nothing, because the engine fetches this URL from the outside
    # with no credentials at all.
    url = public_url(values["R2_PUBLIC_BASE_URL"], PROBE_KEY)
    try:
        with urllib.request.urlopen(public_fetch(url), timeout=PROBE_TIMEOUT_SECONDS) as response:
            served = response.read(len(body) + 64)
    except urllib.error.HTTPError as error:
        checks.append(_check("public", "Public URL serves it", False, (
            f"{url} answered HTTP {error.code}. Turn on the bucket's public "
            "development URL or attach a custom domain, then check the public "
            "base URL matches it. If it is already on, check that no WAF or "
            "bot rule is filtering the request."
        )))
        return {"ok": False, "checks": checks}
    except (OSError, urllib.error.URLError) as error:
        checks.append(_check("public", "Public URL serves it", False, (
            f"Could not fetch {url} ({error}). Check the public base URL."
        )))
        return {"ok": False, "checks": checks}

    if served.strip() != body.strip():
        # A 200 from the wrong place is the worst outcome here: it looks correct
        # and publishes the wrong file, so it is failed rather than passed.
        checks.append(_check("public", "Public URL serves it", False, (
            f"{url} answered, but with different content. The public base URL "
            "points at another bucket or a cache."
        )))
        return {"ok": False, "checks": checks}
    checks.append(_check("public", "Public URL serves it", True, (
        "Fetched the same bytes back with no credentials, which is how an "
        "engine will collect your media."
    )))
    return {"ok": True, "checks": checks}


def upload(path: Path, digest: str) -> dict[str, Any]:
    """Put a file in the bucket and return the stable URL an engine can fetch.

    The object is written under its content hash, so uploading the same cut
    twice is idempotent rather than duplicating it.
    """
    values = _settings()
    if not path.is_file():
        raise MediaHostingUnavailable(f"No such media file: {path}")

    key = object_key(digest, path.suffix)
    encoded = quote(key, safe="/")
    request_path = f"/{values['R2_BUCKET']}/{encoded}"
    body = path.read_bytes()
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    request = _signed_request(
        "PUT", values, request_path, body=body, content_type=content_type
    )
    try:
        with urllib.request.urlopen(request, timeout=UPLOAD_TIMEOUT_SECONDS) as response:
            if response.status not in (200, 201):
                raise MediaHostingUnavailable(
                    f"Object storage rejected the upload with HTTP {response.status}."
                )
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:400]
        raise MediaHostingUnavailable(
            f"Object storage rejected the upload ({error.code}): {detail}"
        ) from error
    except (OSError, urllib.error.URLError) as error:
        raise MediaHostingUnavailable(f"Could not reach object storage: {error}") from error

    return {
        "url": public_url(values["R2_PUBLIC_BASE_URL"], key),
        "key": key,
        "sha256": digest,
        "size_bytes": len(body),
        "content_type": content_type,
        # A signed URL would outlive createPost but not the publish, so the
        # bucket serves this openly and the object is retained until the post
        # it feeds has gone out.
        "expires": False,
    }
