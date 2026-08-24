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

from sqlalchemy import func, select
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


def list_library_assets(
    session: Session,
    workspace_id: str,
    *,
    query: str | None = None,
    kind: str | None = None,
    collected_within_days: int | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict[str, Any]:
    """Find media already in the Library, so a post can be made from it.

    Without this the only asset ids a caller could name were the ones its own
    uploads had just returned. Everything the operator collected - the whole
    library, which is what a campaign is normally built from - was unreachable,
    and re-uploading a file to learn its id is both wasteful and a lie about
    where the media came from. (The ingest deduplicates by content, so it would
    have returned the existing id and the wrong provenance with it.)

    The filter is the Library's own `asset_conditions`, not a second query that
    reads "the same" - a browse that disagreed with the screen the operator is
    looking at would have them talking past each other about which clips exist.

    Paths are not returned. A caller names media by asset id, here as
    everywhere else on this boundary: a filesystem path is not a remote
    caller's to know, and `create_campaign_post` would not take one.
    """
    from trendrelay_api.media_library_api import AssetFilter, asset_conditions
    from trendrelay_api.media_models import MediaAsset

    if kind is not None and kind not in {"video", "image", "audio"}:
        raise ValueError(
            f"Unknown media kind {kind!r}. It is 'video', 'image' or 'audio'."
        )
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    filters = AssetFilter(
        q=query,
        media_kind=kind,
        collected_within_days=collected_within_days,
    )
    where = asset_conditions(workspace_id, filters)
    total = session.scalar(select(func.count(MediaAsset.id)).where(*where)) or 0
    # Newest first, and every sort ends on the id: `collected_at` is not unique,
    # and two rows sharing one let the database order them differently between
    # requests - which across a page boundary silently drops assets.
    rows = session.scalars(
        select(MediaAsset)
        .where(*where)
        .order_by(MediaAsset.collected_at.desc(), MediaAsset.id.asc())
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "assets": [_library_row(asset) for asset in rows],
        "total": total,
        "offset": offset,
        "returned": len(rows),
        # Said rather than left to arithmetic. A caller that stops at the first
        # page because it did not compare three numbers reports "these are your
        # clips" about the newest twenty-five of two thousand.
        "more": offset + len(rows) < total,
    }


def _library_row(asset: Any) -> dict[str, Any]:
    """One asset, as much as is needed to choose it and no more.

    Enough to recognise the media and to judge whether it suits a post: what it
    is, how long, where it came from and when it arrived. Not the whole
    Library view - a caller choosing between clips does not need codecs, and a
    long payload of them crowds out the titles it is actually reading.
    """
    return {
        "asset_id": asset.id,
        "title": asset.title,
        "kind": asset.media_kind,
        # How it got here, which is how a caller tells its own uploads
        # ("mcp-upload") from what the operator collected.
        "source": asset.source_type,
        "platform": asset.platform,
        "creator": asset.creator,
        "caption": asset.caption,
        # Seconds, because a caller writing to length thinks in seconds and
        # milliseconds invite an order-of-magnitude mistake.
        "duration_seconds": (
            round(asset.duration_ms / 1000, 1) if asset.duration_ms else None
        ),
        "dimensions": (
            f"{asset.width}x{asset.height}" if asset.width and asset.height else None
        ),
        # When it arrived in the Library, which is what "the ones from
        # yesterday" means to the operator asking.
        "collected_at": (
            asset.collected_at.isoformat() if asset.collected_at else None
        ),
    }


def _and_list(names: list[str]) -> str:
    """"a", "a and b", "a, b and c" - a sentence rather than a list dump."""
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _carousel_reach(
    session: Session, workspace_id: str, campaign_id: str, image_count: int
) -> tuple[list[str], list[str]]:
    """Which of a campaign's accounts would carry this gallery, and why not.

    Whether a login can post several pictures somewhere is a property of the
    engine as much as the network - Buffer sends no gallery at all, Zernio
    sends one everywhere except Instagram - so the campaign's own destinations
    are what decides it, not the platform names.

    Asked here because this is where the pictures are chosen. `campaign_runner`
    asks the same question of the same helper before it posts, which is the
    right last line but the wrong first one: by then the assistant is gone and
    the operator is looking at a post they were told was ready.

    An empty warning list means nothing is known to refuse it - including a
    campaign with no accounts yet, which is how most campaigns start.
    """
    from trendrelay_api.autopilot_models import CampaignDestination
    from trendrelay_api.integrations.publishing import (  # noqa: PLC0415
        PLATFORM_LABELS,
        carousel_fits_destination,
    )

    destinations = session.scalars(
        select(CampaignDestination).where(
            CampaignDestination.workspace_id == workspace_id,
            CampaignDestination.campaign_id == campaign_id,
            # A switched-off account posts nothing, so it neither blocks the
            # carousel nor excuses one the live accounts cannot take.
            CampaignDestination.enabled.is_(True),
        )
    ).all()
    reaches: list[str] = []
    warnings: list[str] = []
    for destination in destinations:
        fits, why = carousel_fits_destination(
            destination.provider, destination.platform, image_count
        )
        if fits:
            reaches.append(
                PLATFORM_LABELS.get(destination.platform, destination.platform)
            )
        elif why:
            warnings.append(why)
    return reaches, warnings


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

    # Said, not enforced. The app's own queue route accepts the same package
    # without asking, and a rule that exists only for assistants would mean the
    # operator adding this post by hand sails through where their assistant is
    # refused. It is also a legitimate thing to do: campaigns are filled before
    # the account that will carry them is connected.
    reaches, carousel_warnings = (
        _carousel_reach(session, workspace_id, campaign_id, len(assets))
        if "image_paths" in media
        else ([], [])
    )

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
    view["carousel_warnings"] = carousel_warnings
    view["note"] = (
        # Where the pictures land leads, when it is not everywhere. A post the
        # operator is told is waiting, and which then reaches one of their three
        # accounts, is worse news arriving later than it needed to.
        (
            f"The pictures reach {_and_list(reaches)}; the rest of this "
            "campaign's accounts cannot post a gallery. "
            if carousel_warnings and reaches
            else "No account in this campaign can post a picture carousel, so "
            "this post has nowhere to go until one is added. "
            if carousel_warnings
            else ""
        )
        + "Created as a draft. It enters the campaign's rotation only when the "
        "operator approves it in the app - tell them it is waiting."
    )
    return view
