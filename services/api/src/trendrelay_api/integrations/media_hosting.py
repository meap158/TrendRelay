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
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from trendrelay_api.env_store import configured_keys, effective_value, write_env_values

CREDENTIAL_FIELDS: tuple[dict[str, Any], ...] = (
    {
        "id": "account_id",
        "key": "R2_ACCOUNT_ID",
        "label": "Account ID",
        "secret": False,
        "required": True,
        "help": "Cloudflare dashboard → R2 → Overview, in the S3 API endpoint.",
    },
    {
        "id": "access_key_id",
        "key": "R2_ACCESS_KEY_ID",
        "label": "Access key ID",
        "secret": False,
        "required": True,
        "help": "From an R2 API token with Object Read & Write on this bucket.",
    },
    {
        "id": "secret_access_key",
        "key": "R2_SECRET_ACCESS_KEY",
        "label": "Secret access key",
        "secret": True,
        "required": True,
        "help": "Shown once when the R2 API token is created.",
    },
    {
        "id": "bucket",
        "key": "R2_BUCKET",
        "label": "Bucket",
        "secret": False,
        "required": True,
        "help": "The bucket uploads are written to.",
    },
    {
        "id": "public_base_url",
        "key": "R2_PUBLIC_BASE_URL",
        "label": "Public base URL",
        "secret": False,
        "required": True,
        "help": (
            "The bucket's public r2.dev address or your custom domain. "
            "Anyone with a link can fetch these files, so use a bucket kept for publishing."
        ),
    },
)
CREDENTIAL_KEYS: tuple[str, ...] = tuple(field["key"] for field in CREDENTIAL_FIELDS)
# R2 ignores the region but SigV4 requires one, and this is the value it expects.
REGION = "auto"
SERVICE = "s3"
UPLOAD_TIMEOUT_SECONDS = 900


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


def _settings() -> dict[str, str]:
    values = {key: effective_value(key).strip() for key in CREDENTIAL_KEYS}
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
        "configured": not missing,
        "missing_keys": missing,
        "credential_keys": list(CREDENTIAL_KEYS),
        "credential_fields": [
            {**field, "configured": configured[field["key"]]} for field in CREDENTIAL_FIELDS
        ],
        "reason": None
        if not missing
        else "Add " + ", ".join(missing) + " to publish media that an engine must fetch.",
    }


def save_credentials(values: dict[str, str]) -> dict[str, Any]:
    """Write hosting settings to the local .env, rejecting anything unrecognised."""
    fields = {field["id"]: field for field in CREDENTIAL_FIELDS}
    unknown = sorted(set(values) - set(fields))
    if unknown:
        raise ValueError(f"Unknown media hosting settings: {', '.join(unknown)}")
    updates: dict[str, str] = {}
    for field_id, raw in values.items():
        value = (raw or "").strip()
        field = fields[field_id]
        if not value and field["required"]:
            raise ValueError(f"Media hosting {field['label']} cannot be empty.")
        if field["id"] == "public_base_url" and not value.startswith(("http://", "https://")):
            raise ValueError("Public base URL must start with http:// or https://.")
        updates[str(field["key"])] = value
    if not updates:
        raise ValueError("Provide at least one setting to save.")
    return {"written_keys": write_env_values(updates)}


def upload(path: Path, digest: str) -> dict[str, Any]:
    """Put a file in the bucket and return the stable URL an engine can fetch.

    The object is written under its content hash, so uploading the same cut
    twice is idempotent rather than duplicating it.
    """
    values = _settings()
    if not path.is_file():
        raise MediaHostingUnavailable(f"No such media file: {path}")

    key = object_key(digest, path.suffix)
    host = f"{values['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com"
    encoded = quote(key, safe="/")
    request_path = f"/{values['R2_BUCKET']}/{encoded}"
    body = path.read_bytes()
    payload_hash = hashlib.sha256(body).hexdigest()
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    request = urllib.request.Request(
        f"https://{host}{request_path}", data=body, method="PUT"
    )
    request.add_header("Host", host)
    request.add_header("x-amz-content-sha256", payload_hash)
    request.add_header("x-amz-date", timestamp)
    request.add_header("Content-Type", content_type)
    request.add_header(
        "Authorization",
        authorization_header(
            values["R2_ACCESS_KEY_ID"],
            values["R2_SECRET_ACCESS_KEY"],
            timestamp,
            canonical_request("PUT", request_path, host, payload_hash, timestamp),
        ),
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
