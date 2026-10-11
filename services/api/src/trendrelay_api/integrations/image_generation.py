"""Still images from xAI's Imagine API, for drawing a new character.

The only caller is `characters`: one generation for a portrait, one edit of
that portrait for the consistency sheet. Both are paid, so nothing here runs
without the route's `confirm_external_action`, and the provider has to be
ready the same way it is for video - the xAI key saved in Tools, its switch
on, and Check passed. Images come back in the response body, so unlike video
there is no bucket to write to.
"""

from __future__ import annotations

import base64
import json
import urllib.request
from typing import Any

from trendrelay_api.integrations.video_generation import (
    PROVIDERS,
    XAI_BASE,
    Transport,
    VideoError,
    _classify,
    _json_body,
    _key,
    _row,
    _sniff_image,
    _transport,
)

#: The model the current xAI docs name for generation and editing.
MODEL = "grok-imagine-image-2.0"
PROVIDER_ID = "xai"
#: A generated still is a few hundred kilobytes; this is a ceiling, not a size.
MAX_IMAGE_BYTES = 20_000_000


def readiness() -> dict[str, Any]:
    """Whether a character can be drawn now, and what to fix if not."""
    row = _row(PROVIDERS[PROVIDER_ID])
    ready = bool(row["enabled"] and row["configured"] and row["check_ok"])
    if ready:
        message = "Characters are drawn with xAI."
    elif not row["configured"]:
        message = "Save an xAI API key in Tools to draw new characters."
    elif not row["enabled"]:
        message = "Switch xAI on in Tools to draw new characters."
    else:
        message = "Run Check for xAI in Tools to draw new characters."
    return {"ready": ready, "provider_id": PROVIDER_ID, "label": "xAI", "message": message}


def require_ready() -> None:
    state = readiness()
    if not state["ready"]:
        raise ValueError(state["message"])


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_key(PROVIDERS[PROVIDER_ID])}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _download(url: str) -> bytes:
    if not url.startswith("https://"):
        raise VideoError("unavailable", "The image service returned an address that is not https.")
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - https checked above
        return response.read(MAX_IMAGE_BYTES + 1)


def _image_from(body: bytes, download: Any) -> bytes:
    data = _json_body(body).get("data") or []
    first = data[0] if data and isinstance(data[0], dict) else {}
    if first.get("b64_json"):
        image = base64.b64decode(str(first["b64_json"]))
    elif first.get("url"):
        image = download(str(first["url"]))
    else:
        raise VideoError("unavailable", "The image service answered without an image.")
    if len(image) > MAX_IMAGE_BYTES:
        raise VideoError("unavailable", "The image is larger than this server accepts.")
    _sniff_image(image)
    return image


def _post(path: str, payload: dict[str, Any], transport: Transport | None, download: Any) -> bytes:
    call = transport or _transport
    status, body = call(
        "POST", f"{XAI_BASE}{path}", _headers(), json.dumps(payload).encode("utf-8"), 180,
    )
    failed = _classify(status, body)
    if failed is not None:
        raise failed
    return _image_from(body, download or _download)


def generate(
    prompt: str,
    *,
    aspect_ratio: str = "2:3",
    transport: Transport | None = None,
    download: Any = None,
) -> bytes:
    """One new image from text."""
    return _post(
        "/images/generations",
        {
            "model": MODEL,
            "prompt": prompt,
            "n": 1,
            "aspect_ratio": aspect_ratio,
            "response_format": "b64_json",
        },
        transport,
        download,
    )


def edit(
    prompt: str,
    image: bytes,
    *,
    transport: Transport | None = None,
    download: Any = None,
) -> bytes:
    """One image drawn from a reference picture and a prompt."""
    mime = _sniff_image(image)
    data_uri = f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}"
    return _post(
        "/images/edits",
        {
            "model": MODEL,
            "prompt": prompt,
            "image": {"url": data_uri, "type": "image_url"},
            "response_format": "b64_json",
        },
        transport,
        download,
    )


__all__ = ["MODEL", "VideoError", "edit", "generate", "readiness", "require_ready"]
