"""What an assistant brings *into* the workspace: an image, and a proposed post.

Two boundaries hold everything here.

**The fetch is guarded, because the URL is the caller's.** An MCP caller hands
this machine a URL and asks it to fetch it - which is a server-side request a
remote model chose the target of. The guard refuses everything but a direct
HTTPS fetch of a public address: no redirects (a public URL that redirects to a
private one is the classic escape), no loopback or private or link-local
destination (this machine runs services on loopback that no outside caller may
reach through us), a size cap, and only the image types the Library accepts.
ChatGPT's own upload URLs - the `openai/fileParams` convention, where a chat
attachment arrives as ``{"file_id": ..., "download_url": ...}`` - pass these
guards, because they are direct HTTPS links to public storage.

**A post an assistant creates is a draft, never rotation-ready.** The operator's
own additions arrive approved because adding them *is* the operator's decision;
an assistant's arrive in ``draft`` - the queue's parking brake - and enter the
rotation only when a person promotes them in the app. Without this, a model
could compose a post into a campaign whose authority is autonomous and thereby
publish it, which crosses the line the MCP policy holds: a model may write, a
person decides what goes out.

Everything else is reuse. The saved file goes through ``create_ingest_job`` -
the same dedupe-by-digest, the same immutable original, the same audit trail as
a file the operator imports - and the post through ``create_queue_item``, the
same helper the interface's own route calls.
"""

from __future__ import annotations

import hashlib
import ipaddress
import socket
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

#: The image types the Library accepts, keyed by the content type the server
#: reports. The reported type decides the suffix - never the URL's own, which
#: is the caller's to write.
_IMAGE_TYPES: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

#: Large enough for any real post image, small enough that a caller cannot fill
#: the disk through us. The engines this workspace publishes through cap photo
#: uploads well below this.
MAX_IMAGE_BYTES = 25 * 1024 * 1024

_FETCH_TIMEOUT_SECONDS = 30

#: Where an assistant's uploads land: inside the first approved media root, in
#: a directory named for how they arrived, so provenance is visible in the path
#: and `approved_source_path` accepts it without widening any root.
_UPLOAD_DIRNAME = "mcp-uploads"


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect is refused, not followed.

    Following one would fetch an address the guard below never saw - a public
    URL that answers 302 to ``http://127.0.0.1:...`` is the standard way a
    server-side fetch gets turned against its own machine.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise ValueError(
            f"The image URL redirected ({code}). Pass a direct link to the "
            "image itself."
        )


def _require_public_https(url: str) -> None:
    """Refuse any URL that is not a direct HTTPS fetch of a public address."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("Only https:// image URLs are fetched.")
    host = parsed.hostname
    if not host:
        raise ValueError("The image URL names no host.")
    try:
        found = socket.getaddrinfo(host, None)
    except OSError as error:
        raise ValueError(f"The image host could not be resolved: {host}") from error
    for entry in found:
        address = ipaddress.ip_address(entry[4][0])
        if not address.is_global:
            # Loopback, private, link-local, reserved - all the addresses a
            # remote caller must not reach through this machine.
            raise ValueError(
                "The image URL resolves to a private or local address, which "
                "this server will not fetch on a caller's behalf."
            )


def _download(url: str) -> tuple[bytes, str]:
    """The image bytes and the content type the server reported.

    Separated so a test can stand in for the network. The caps live here so no
    caller can forget them: the read stops at one byte over the limit rather
    than buffering whatever the server sends.
    """
    _require_public_https(url)
    opener = urllib.request.build_opener(_RefuseRedirects)
    request = urllib.request.Request(url, headers={"User-Agent": "TrendRelay-MCP/1.0"})
    with opener.open(request, timeout=_FETCH_TIMEOUT_SECONDS) as response:
        content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip()
        data = response.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(
            f"The image is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB, "
            "which is the most this server will fetch."
        )
    return data, content_type


def _upload_root() -> Path:
    from trendrelay_api.config import get_settings
    from trendrelay_api.tool_registry import PROJECT_ROOT

    roots = get_settings().publishing_media_root_list
    if not roots:
        raise RuntimeError("No approved media root is configured.")
    first = Path(roots[0])
    if not first.is_absolute():
        first = PROJECT_ROOT / first
    return first / _UPLOAD_DIRNAME


def upload_image(
    workspace_id: str,
    image: dict[str, Any] | None = None,
    image_url: str | None = None,
    title: str = "",
    caption: str | None = None,
    creator: str | None = None,
    source_url: str | None = None,
    platform: str | None = None,
    fetch=_download,
) -> dict[str, Any]:
    """Bring one image into the media library, from a URL or a chat attachment.

    ``image`` is the ChatGPT file object - ``{"file_id", "download_url"}`` -
    that the ``openai/fileParams`` declaration routes an attachment into;
    ``image_url`` is the same thing said plainly by any other client. Exactly
    one of them supplies the fetch. The signed ``download_url`` is used once
    and never stored: it expires, and it carries an access token in its query
    string that has no business in a database. Provenance worth keeping goes in
    ``source_url``, which the caller states on purpose.
    """
    from trendrelay_api.auth import LOCAL_ADMIN_ID
    from trendrelay_api.media_library import create_ingest_job

    fetched_from = (image or {}).get("download_url") or image_url
    if not fetched_from or not str(fetched_from).strip():
        raise ValueError(
            "Provide the image: attach one (it arrives as the `image` file "
            "parameter) or pass `image_url`."
        )
    data, content_type = fetch(str(fetched_from).strip())
    suffix = _IMAGE_TYPES.get(content_type)
    if not suffix:
        accepted = ", ".join(sorted(_IMAGE_TYPES))
        raise ValueError(
            f"The URL served {content_type or 'no content type'}; the library "
            f"accepts {accepted}."
        )
    digest = hashlib.sha256(data).hexdigest()
    root = _upload_root()
    root.mkdir(parents=True, exist_ok=True)
    # Named by digest: the same bytes land on the same path, so a retried
    # upload rewrites an identical file instead of minting siblings.
    saved = root / f"{digest[:20]}{suffix}"
    saved.write_bytes(data)

    job = create_ingest_job(
        workspace_id=workspace_id,
        actor_user_id=LOCAL_ADMIN_ID,
        path=str(saved),
        title=title.strip() or "Assistant upload",
        # The Library's own provenance: `source_type` is how these arrived and
        # is what the interface shows for them, so an assistant's uploads read
        # as their own source beside downloads and local imports. `platform` is
        # only what the caller states - the network the image genuinely came
        # from - never invented here.
        source_type="mcp-upload",
        source_url=(source_url or "").strip() or None,
        platform=(platform or "").strip() or None,
        creator=(creator or "").strip() or None,
        caption=(caption or "").strip() or None,
        source_sha256=digest,
    )
    duplicate = bool(job.get("duplicate"))
    return {
        "duplicate": duplicate,
        "asset_id": job.get("asset_id"),
        "job_id": job.get("id"),
        "status": job.get("status"),
        "note": (
            "This image is already in the library; use the asset_id as it is."
            if duplicate else
            "Import queued. Poll get_import_status with the job_id until it "
            "succeeds and reports the asset_id, then create the post with it."
        ),
    }


def get_import_status(job_id: str) -> dict[str, Any]:
    """How one upload's import is going, and the asset id once it has one."""
    from trendrelay_api.jobs import get_job_record

    try:
        record = get_job_record(job_id)
    except FileNotFoundError as error:
        raise LookupError(f"No import job {job_id!r}.") from error
    result = record.get("result") or {}
    return {
        "job_id": record.get("id"),
        "status": record.get("status"),
        "error": record.get("error"),
        "asset_id": result.get("asset_id"),
    }


def create_campaign_post(
    session: Session,
    workspace_id: str,
    campaign_id: str,
    asset_ids: list[str],
    *,
    caption: str | None = None,
    title: str | None = None,
    hashtags: list[str] | None = None,
    first_comment: str | None = None,
    thread: list[str] | None = None,
) -> dict[str, Any]:
    """Propose one post into a campaign, as a draft the operator promotes.

    The media is named by Library asset id - never by filesystem path, which is
    not a caller's to know - and the copy passes the same no-links rule every
    MCP copy write passes: the campaign carries the link itself.
    """
    from trendrelay_api.campaign_autopilot_api import (  # noqa: PLC0415
        QueueItemCreate,
        _queue_view,
        create_queue_item,
    )
    from trendrelay_api.integrations.mcp.writes import _refuse_links
    from trendrelay_api.media_models import MediaAsset

    if not asset_ids:
        raise ValueError("Name at least one Library asset to post.")
    assets = []
    for asset_id in dict.fromkeys(asset_ids):
        asset = session.scalar(
            select(MediaAsset).where(
                MediaAsset.id == asset_id,
                MediaAsset.workspace_id == workspace_id,
            )
        )
        if not asset:
            raise LookupError(f"No Library asset {asset_id!r} in this workspace.")
        assets.append(asset)

    kinds = {asset.media_kind for asset in assets}
    if kinds == {"image"}:
        media: dict[str, Any] = {
            "image_paths": [asset.original_path for asset in assets]
        }
    elif kinds == {"video"} and len(assets) == 1:
        media = {"video_path": assets[0].original_path}
    elif "video" in kinds:
        raise ValueError("A package is one video or a set of pictures, never both.")
    else:
        raise ValueError(
            "Only images and video can be posted; "
            f"this selection includes {', '.join(sorted(kinds - {'image', 'video'}))}."
        )

    if caption is not None:
        _refuse_links("caption", caption)
    if first_comment is not None:
        _refuse_links("first comment", first_comment)
    for index, reply in enumerate(thread or [], start=1):
        _refuse_links(f"reply {index}", reply)

    from trendrelay_api.auth import LOCAL_ADMIN_ID

    body = QueueItemCreate(
        **media,
        asset_id=assets[0].id,
        body=caption or "",
        title=title,
        hashtags=hashtags or [],
        first_comment=first_comment,
        thread=thread or [],
    )
    item = create_queue_item(
        session, workspace_id, campaign_id, body,
        created_by=LOCAL_ADMIN_ID,
        # The parking brake, on purpose - see the module docstring. Promotion
        # to the rotation is the operator's act, in the app.
        state="draft",
    )
    session.commit()
    view = _queue_view(item)
    view["note"] = (
        "Created as a draft. It enters the campaign's rotation only when the "
        "operator approves it in the app - tell them it is waiting."
    )
    return view
