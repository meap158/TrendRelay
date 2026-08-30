"""What an assistant brings *into* the workspace: an image, and a proposed post.

Two boundaries hold everything here.

**The fetch is guarded, because the URL is the caller's.** An MCP caller hands
this machine a URL and asks it to fetch it - which is a server-side request a
remote model chose the target of. The guard refuses everything but a direct
HTTPS fetch of a public address: no redirects (a public URL that redirects to a
private one is the classic escape), no loopback or private or link-local
destination (this machine runs services on loopback that no outside caller may
reach through us), a size cap, and only the media types the Library accepts.
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

import base64
import binascii
import hashlib
import ipaddress
import socket
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

#: The image types the Library accepts, keyed by their verified MIME type. The
#: bytes decide the suffix - never the URL, header, or caller's own label.
_IMAGE_TYPES: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

#: Large enough for any real post image, small enough that a caller cannot fill
#: the disk through us. The engines this workspace publishes through cap photo
#: uploads well below this.
MAX_IMAGE_BYTES = 25 * 1024 * 1024

#: The video types the Library accepts over MCP, by verified content type - the
#: same rule as images: the bytes decide the suffix, never the URL's spelling.
_VIDEO_TYPES: dict[str, str] = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    # The Library's own import list has taken .mkv from the start; the MCP
    # door matching it is what keeps "the Library accepts it" one fact.
    "video/x-matroska": ".mkv",
}

#: ISO base-media brands that are actually video. Merely finding ``ftyp`` is
#: not enough: HEIC and AVIF pictures use the same container and would
#: otherwise be filed as MP4 before the media worker eventually rejected them.
_MP4_VIDEO_BRANDS = {
    b"3g2a", b"3g2b", b"3gp4", b"3gp5", b"F4V ", b"M4A ", b"M4V ",
    b"MSNV", b"avc1", b"cmfc", b"cmfs", b"dash", b"hev1", b"hvc1",
    b"iso2", b"iso3", b"iso4", b"iso5", b"iso6", b"isom", b"mp41",
    b"mp42",
}

#: A short-form clip with room to spare. Well under what the disk minds, well
#: over what any network here will accept, so the refusal a caller meets is
#: the network's real one rather than an arbitrary one of ours.
MAX_VIDEO_BYTES = 512 * 1024 * 1024

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


def _fetch_bytes(url: str, *, limit: int, what: str) -> tuple[bytes, str]:
    """The media bytes and the content type the server reported.

    Separated so a test can stand in for the network. The caps live here so no
    caller can forget them: the read stops at one byte over the limit rather
    than buffering whatever the server sends.
    """
    _require_public_https(url)
    opener = urllib.request.build_opener(_RefuseRedirects)
    request = urllib.request.Request(url, headers={"User-Agent": "TrendRelay-MCP/1.0"})
    with opener.open(request, timeout=_FETCH_TIMEOUT_SECONDS) as response:
        content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip()
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError(
            f"The {what} is larger than {limit // (1024 * 1024)} MB, "
            "which is the most this server will fetch."
        )
    return data, content_type


def _download(url: str) -> tuple[bytes, str]:
    return _fetch_bytes(url, limit=MAX_IMAGE_BYTES, what="image")


def _download_media(url: str) -> tuple[bytes, str]:
    return _fetch_bytes(url, limit=MAX_VIDEO_BYTES, what="file")


def _attachment_url(value: dict[str, Any] | str | None, direct_url: str | None) -> str:
    """Resolve a client-materialized attachment, with a useful refusal otherwise.

    A file-param attachment is an object because the client must turn its own
    private file reference into a temporary URL the server can fetch. A bare
    file id or a path mounted in the model's runtime names bytes this machine
    cannot access; accepting either as though it were a URL only produces a
    misleading format or scheme error later.
    """
    if isinstance(value, dict):
        candidate = next(
            (
                value.get(key)
                for key in ("download_url", "file_url", "image_url", "url")
                if value.get(key)
            ),
            None,
        )
    else:
        candidate = value
    candidate = candidate or direct_url
    source = str(candidate or "").strip()
    if not source:
        raise ValueError(
            "Provide a source: attach one file directly to this upload tool, "
            "or pass image_url/media_url as a direct public https URL."
        )
    if not source.startswith("https://"):
        raise ValueError(
            "This is a file id or filesystem path, not a transferable file. "
            "TrendRelay cannot read another tool's private file registry or "
            "mounted runtime. Either attach the file directly so the client "
            "supplies a temporary download URL, pass a direct public https "
            "URL, or - for a file you generated and cannot give an address - "
            "send the bytes themselves as image_base64/media_base64, or as a "
            "data:<type>;base64,<data> URL in this field."
        )
    return source


#: What the first bytes of an accepted file look like. Inline uploads are
#: identified from these rather than from anything the caller says, because
#: there is no server on the other end whose Content-Type header could be
#: believed - and a declared type is only a claim in any case.
def _sniff_media_type(data: bytes) -> str | None:
    """The media type these bytes actually are, or None if it is not one we take.

    Signatures, not extensions and not the caller's word. A URL fetch can lean
    on the serving host's Content-Type; inline bytes have no such witness, so
    this is the whole of the check and it is deliberately strict: anything not
    positively recognised is refused rather than guessed at.
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp":
        # ISO base media. The brand separates QuickTime from the MP4 family;
        # everything else in that family is filed as MP4, which is what it is.
        brand = data[8:12]
        if brand[:2] == b"qt":
            return "video/quicktime"
        if brand in _MP4_VIDEO_BRANDS:
            return "video/mp4"
        return None
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        # Matroska and WebM share the EBML header and are told apart by the
        # DocType string, which sits early in the header.
        return "video/webm" if b"webm" in data[:_EBML_DOCTYPE_WINDOW] else "video/x-matroska"
    return None


#: Far enough into an EBML header to have passed the DocType, and no further:
#: reading more only raises the chance of matching the word inside content.
_EBML_DOCTYPE_WINDOW = 256


def _decode_inline(
    value: str,
    *,
    allowed: dict[str, str],
    what: str,
) -> tuple[bytes, str]:
    """Bytes a caller sent directly, rather than a URL for us to go and get.

    This exists because a generated file often has no address. An assistant
    that has just produced an image holds it in its own private file registry;
    if the client cannot mint a temporary public URL from that, there is
    nothing to fetch and the upload is impossible however well the fetch path
    works. The bytes themselves are the one thing the caller always has.

    Nothing is fetched here, so none of the SSRF guarding above applies or is
    needed - there is no address for a caller to point this machine at. What
    replaces it is a stricter identification: the type comes from the file's
    own signature, never from a claim.
    """
    payload = value.strip()
    if payload.startswith("data:"):
        header, _, encoded = payload.partition(",")
        parameters = [part.strip().lower() for part in header.split(";")[1:]]
        if not encoded or "base64" not in parameters:
            raise ValueError(
                "A data: URL must be base64-encoded, as data:<type>;base64,<data>."
            )
        payload = encoded

    # Decode only enough to identify the container before allocating the whole
    # payload. This is what keeps a 200 MB blob labelled as a video from using
    # the video allowance when its own signature says it is a picture.
    sample_text = payload if len(payload) <= 1024 else payload[:1024]
    try:
        sample = base64.b64decode(sample_text, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError(
            "The base64 payload could not be decoded. Send standard base64 "
            "with no line breaks, or a data:<type>;base64,<data> URL."
        ) from error
    if not sample:
        raise ValueError("The base64 payload decoded to no bytes.")
    sniffed = _sniff_media_type(sample)
    if not sniffed or sniffed not in allowed:
        accepted = ", ".join(sorted(allowed))
        raise ValueError(
            "These bytes are not a file type this upload accepts. The "
            "signature matches no allowed image or video format; accepted "
            f"types are {accepted}. Check the payload is the file itself and "
            "not text, a wrapper, or base64 that has been encoded twice."
        )

    limit = MAX_IMAGE_BYTES if sniffed.startswith("image/") else MAX_VIDEO_BYTES
    # Base64 runs at exactly four characters per three source bytes (rounded
    # up). Refuse above the type's real limit before building the full bytes.
    if len(payload) > 4 * ((limit + 2) // 3):
        raise ValueError(
            f"The {what} is larger than {limit // (1024 * 1024)} MB, "
            "which is the most this server accepts."
        )
    try:
        data = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError(
            "The base64 payload could not be decoded. Send standard base64 "
            "with no line breaks, or a data:<type>;base64,<data> URL."
        ) from error
    if len(data) > limit:
        raise ValueError(
            f"The {what} is larger than {limit // (1024 * 1024)} MB, "
            "which is the most this server accepts."
        )
    return data, sniffed


def _inline_source(
    value: dict[str, Any] | str | None,
    direct_url: str | None,
    explicit: str | None,
) -> str | None:
    """The inline bytes among the arguments, if a caller supplied any.

    A `data:` URL is accepted wherever a URL is, because that is where a client
    that has one will naturally put it, and refusing it there for being the
    wrong shape of URL would be pedantry.
    """
    if explicit and explicit.strip():
        return explicit
    candidates = [direct_url]
    if isinstance(value, str):
        candidates.append(value)
    elif isinstance(value, dict):
        candidates.extend(str(value.get(key) or "") for key in ("data", "base64", "url"))
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text.startswith("data:"):
            return text
    return None


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
    image: dict[str, Any] | str | None = None,
    image_url: str | None = None,
    title: str = "",
    caption: str | None = None,
    creator: str | None = None,
    source_url: str | None = None,
    platform: str | None = None,
    image_base64: str | None = None,
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
    inline = _inline_source(image, image_url, image_base64)
    if inline:
        data, content_type = _decode_inline(
            inline, allowed=_IMAGE_TYPES, what="image"
        )
    else:
        data, content_type = fetch(_attachment_url(image, image_url))
    return _ingest_fetched(
        workspace_id, data, content_type, _IMAGE_TYPES,
        title=title, caption=caption, creator=creator,
        source_url=source_url, platform=platform,
    )


def upload_media(
    workspace_id: str,
    media: dict[str, Any] | str | None = None,
    media_url: str | None = None,
    title: str = "",
    caption: str | None = None,
    creator: str | None = None,
    source_url: str | None = None,
    platform: str | None = None,
    media_base64: str | None = None,
    fetch=_download_media,
) -> dict[str, Any]:
    """Bring one video or image into the media library.

    The same door as `upload_image` with the video types allowed through it,
    kept as its own tool so existing callers of the image tool keep the
    tighter cap they were promised. Everything else is identical: the file
    signature decides what the file is, the digest names it, and it lands
    in the Library through the ordinary ingest pipeline under the
    'mcp-upload' source.
    """
    inline = _inline_source(media, media_url, media_base64)
    if inline:
        data, content_type = _decode_inline(
            inline, allowed={**_IMAGE_TYPES, **_VIDEO_TYPES}, what="file"
        )
    else:
        data, content_type = fetch(_attachment_url(media, media_url))
    return _ingest_fetched(
        workspace_id, data, content_type, {**_IMAGE_TYPES, **_VIDEO_TYPES},
        title=title, caption=caption, creator=creator,
        source_url=source_url, platform=platform,
    )


def _ingest_fetched(
    workspace_id: str,
    data: bytes,
    content_type: str,
    allowed: dict[str, str],
    *,
    title: str,
    caption: str | None,
    creator: str | None,
    source_url: str | None,
    platform: str | None,
) -> dict[str, Any]:
    from trendrelay_api.auth import LOCAL_ADMIN_ID
    from trendrelay_api.media_library import create_ingest_job, run_ingest_job

    # The bytes decide the type on every route. HTTP Content-Type is useful
    # metadata but not proof, and accepting it alone lets arbitrary content be
    # written with a media suffix and queued for a worker. It also rejects a
    # legitimate file served as application/octet-stream for no good reason.
    sniffed = _sniff_media_type(data)
    suffix = allowed.get(sniffed or "")
    if not suffix:
        accepted = ", ".join(sorted(allowed))
        raise ValueError(
            f"The file was labelled {content_type or 'with no content type'}, "
            "but its signature matches no media type this upload accepts. "
            f"Accepted types are {accepted}."
        )
    content_type = sniffed or content_type
    limit = MAX_IMAGE_BYTES if content_type.startswith("image/") else MAX_VIDEO_BYTES
    if len(data) > limit:
        kind = "image" if content_type.startswith("image/") else "video"
        raise ValueError(
            f"The {kind} is larger than {limit // (1024 * 1024)} MB, "
            "which is the most this server accepts."
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
        # only what the caller states - the network the media genuinely came
        # from - never invented here.
        source_type="mcp-upload",
        source_url=(source_url or "").strip() or None,
        platform=(platform or "").strip() or None,
        creator=(creator or "").strip() or None,
        caption=(caption or "").strip() or None,
        source_sha256=digest,
    )
    # An MCP image is normally the missing half of a draft the caller is
    # already assembling. Sending it through the general durable worker put a
    # two-second thumbnail behind hours of video proxies and effect renders;
    # one real upload in this workspace waited 53 minutes without ever being
    # claimed. Process a newly queued image in this request so the caller gets
    # its asset id immediately. The ordinary durable record remains the audit
    # trail and retry boundary, and video stays asynchronous because proxying a
    # long clip does not belong inside a tool-call timeout.
    if (
        content_type.startswith("image/")
        and job.get("id")
        and job.get("status") == "queued"
    ):
        try:
            job = run_ingest_job(
                str(job["id"]), worker_id=f"mcp-image-{digest[:12]}"
            )
        except PermissionError:
            # A live worker won the narrow claim race. It now owns the job, so
            # preserve the normal status response rather than processing the
            # same immutable bytes twice.
            from trendrelay_api.jobs import get_job_record

            job = get_job_record(str(job["id"]))
    duplicate = bool(job.get("duplicate"))
    result = job.get("result") or {}
    asset_id = job.get("asset_id") or result.get("asset_id")
    ready = bool(asset_id) and job.get("status") == "succeeded"
    if duplicate:
        note = "This file is already in the library; use the asset_id as it is."
    elif ready and content_type.startswith("image/"):
        note = "Image imported at its original resolution; use the asset_id now."
    else:
        note = (
            "Import queued. Poll get_import_status with the job_id until it "
            "succeeds and reports the asset_id, then create the post with it."
        )
    return {
        "duplicate": duplicate,
        "asset_id": asset_id,
        "job_id": job.get("id"),
        "status": job.get("status"),
        "note": note,
    }


#: How many imports one status call will report on. A carousel is at most
#: `MAX_CAROUSEL_IMAGES` pictures, so a caller never needs more than that in
#: flight for one post, and a bound stops a stray call reading the whole queue.
MAX_TRACKED_IMPORTS = 40


def _import_record(job_id: str) -> dict[str, Any]:
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


def get_import_status(
    job_id: str | None = None, job_ids: list[str] | None = None
) -> dict[str, Any]:
    """How the uploads for one post are going, and their asset ids once ready.

    Several at once, because a carousel is several uploads and polling is a
    loop: six pictures polled one at a time is six calls per round, and a
    caller waiting on the slowest of them makes that round several times. The
    whole set is the unit a caller actually waits on.

    `all_done` and `ready` are stated rather than left to be derived. A caller
    that has to compare statuses itself to decide whether to go on is a caller
    that will sometimes decide wrong, and creating the post one picture short
    is a failure nothing downstream can see.
    """
    wanted = list(dict.fromkeys([*(job_ids or []), *([job_id] if job_id else [])]))
    if not wanted:
        raise ValueError("Name the import job to check: `job_id`, or `job_ids`.")
    if len(wanted) > MAX_TRACKED_IMPORTS:
        raise ValueError(
            f"{len(wanted)} import jobs at once; {MAX_TRACKED_IMPORTS} is the "
            "most this reports on. A post holds fewer pictures than that."
        )
    imports = [_import_record(one) for one in wanted]
    return {
        "imports": imports,
        # In the order asked for, which for a carousel is the order the
        # pictures swipe through - so the list can be handed straight to
        # `create_campaign_post` once `all_done` is true.
        "ready": [
            entry["asset_id"] for entry in imports
            if entry["status"] == "succeeded" and entry["asset_id"]
        ],
        "pending": [
            entry["job_id"] for entry in imports
            if entry["status"] not in {"succeeded", "failed"}
        ],
        "failed": [entry for entry in imports if entry["status"] == "failed"],
        "all_done": all(
            entry["status"] in {"succeeded", "failed"} for entry in imports
        ),
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


def _media_package(assets: list[Any]) -> dict[str, Any]:
    """The queue's media fields for a set of Library assets.

    One video standing alone, or pictures gathering into one carousel - the
    queue's own package rule, answered from what the assets are. Empty when
    no assets were named, which a caller allows on purpose or not at all.
    """
    if not assets:
        return {}
    kinds = {asset.media_kind for asset in assets}
    if kinds == {"image"}:
        return {"image_paths": [asset.original_path for asset in assets]}
    if kinds == {"video"} and len(assets) == 1:
        return {"video_path": assets[0].original_path}
    if "video" in kinds:
        raise ValueError("A package is one video or a set of pictures, never both.")
    raise ValueError(
        "Only images and video can be posted; "
        f"this selection includes {', '.join(sorted(kinds - {'image', 'video'}))}."
    )


def resolve_post_assets(
    session: Session, workspace_id: str, asset_ids: list[str]
) -> list[Any]:
    """The named Library assets, in order, or a LookupError naming the gap."""
    from trendrelay_api.media_models import MediaAsset

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
    return assets


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
    topic: str | None = None,
    post_types: dict[str, str] | None = None,
    text_only: bool = False,
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

    assets = resolve_post_assets(session, workspace_id, asset_ids)
    media = _media_package(assets)
    # A post may arrive as words without media, or media without words - both
    # halves can follow later - but never as neither. Text is the one thing
    # only a caller can supply, so an empty create is a mistake to name, not
    # a draft to keep.
    if not assets and not (caption or "").strip():
        raise ValueError(
            "A post needs at least its words or its media. Send a caption - "
            "media can follow with set_post_media once uploaded - or name "
            "Library assets to start from the media instead."
        )
    if text_only and assets:
        raise ValueError(
            "A copy-only post carries no media. Drop the asset_ids, or drop "
            "text_only to post the media with these words."
        )

    if caption is not None:
        _refuse_links("caption", caption)
    if first_comment is not None:
        _refuse_links("first comment", first_comment)
    for index, reply in enumerate(thread or [], start=1):
        _refuse_links(f"reply {index}", reply)

    from trendrelay_api.integrations.publishing import MAX_CAROUSEL_IMAGES

    pictures = media.get("image_paths") or []
    if len(pictures) > MAX_CAROUSEL_IMAGES:
        # In words, before the queue's schema says the same thing as a
        # validation error naming a field and linking to pydantic's website.
        raise ValueError(
            f"A post carries at most {MAX_CAROUSEL_IMAGES} pictures, and this "
            f"names {len(pictures)}. Send fewer, or split them across posts."
        )

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
        # Deliberate: an assistant may draft the words first and attach the
        # clip with set_post_media once it is uploaded. The scheduler skips a
        # media-less post with a note until then, exactly as it skips one
        # whose copy is still the placeholder. A copy-only post is neither:
        # it has no media because it wants none, and posts as it is.
        media_later=not assets and not text_only,
        text_only=text_only,
        asset_id=assets[0].id if assets else None,
        body=caption or "",
        title=title,
        hashtags=hashtags or [],
        first_comment=first_comment,
        thread=thread or [],
        topic=topic,
        post_type_overrides=post_types or {},
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
