"""Authenticated media acquisition API."""

from __future__ import annotations

import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from trendrelay_api.auth import CurrentUser, current_user, require_governed_assurance
from trendrelay_api.database import get_session
from trendrelay_api.foundation import audit, membership, require_role
from trendrelay_api.integrations.douyin import (
    CapturedLinksRequest,
    DownloadRequest,
    cancel_download_job,
    clear_download_history,
    create_download_job,
    download_job,
    import_captured_links,
    list_download_jobs,
    provider_status,
    reconcile_downloads_to_library,
    resume_download_job,
    start_connection,
)
from trendrelay_api.integrations.douyin_topic import (
    TopicDownloadRequest,
    TopicUnavailable,
    download_topic,
)

router = APIRouter(prefix="/api/workspaces/{workspace_id}/media", tags=["media"])
AuthenticatedUser = Annotated[CurrentUser, Depends(current_user)]
DatabaseSession = Annotated[Session, Depends(get_session)]


class ConnectionRequest(BaseModel):
    confirm_external_action: bool = False
    force_refresh: bool = False
    require_login: bool = False


class LibrarySyncRequest(BaseModel):
    confirm_external_action: bool = False


class ClearDownloadsRequest(BaseModel):
    confirm_external_action: bool = False


class ResumeDownloadRequest(BaseModel):
    confirm_external_action: bool = False
    from_saved_files: bool = False


@router.get("/status")
def media_status(
    workspace_id: str, user: AuthenticatedUser, session: DatabaseSession
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    return {
        "douyin": provider_status(),
        "tiktok": {
            "installed": False,
            "active": False,
            "reason": "A reviewed TikTok acquisition provider is not installed.",
        },
    }


@router.get("/douyin/trending")
def douyin_trending_board(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict[str, Any]:
    """Douyin's hot-search board, for the Discover panel.

    Read on request rather than on a schedule: the board is only fetched when
    an operator asks to see it, which keeps this a look rather than a sweep.
    """
    membership(session, workspace_id, user.id)
    from trendrelay_api.integrations.douyin_trending import TrendingUnavailable, fetch

    try:
        return fetch(limit=limit)
    except TrendingUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get("/douyin/topic")
def douyin_topic_search(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
    term: Annotated[str, Query(min_length=1, max_length=120)],
    limit: Annotated[int, Query(ge=1, le=30)] = 10,
) -> dict[str, Any]:
    """The videos posted under a term, so a trending topic becomes downloadable.

    A look, not an acquisition: nothing is fetched and nothing is queued. It
    exists so an operator can see what a topic actually contains before
    committing disk to it.
    """
    membership(session, workspace_id, user.id)
    from trendrelay_api.integrations.douyin_topic import TopicUnavailable, search

    try:
        return search(term, limit=limit)
    except TopicUnavailable as error:
        raise HTTPException(
            status_code=503,
            # Carried through as a shape rather than a sentence, because
            # "sign in" is an action the panel can offer and a 503 body is not.
            detail={"message": str(error), "login_required": error.login_required},
        ) from error


@router.post("/douyin/topic/downloads", status_code=202)
def download_douyin_topic(
    workspace_id: str,
    body: TopicDownloadRequest,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Search a term and queue its top videos as one ordinary download job."""
    if body.workspace_id != workspace_id:
        raise HTTPException(status_code=422, detail="Workspace path and body must match.")
    require_role(membership(session, workspace_id, user.id), {"owner", "editor", "approver"})
    require_governed_assurance(user)
    try:
        result = download_topic(body, actor_user_id=user.id)
    except PermissionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except TopicUnavailable as error:
        raise HTTPException(
            status_code=503,
            detail={"message": str(error), "login_required": error.login_required},
        ) from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "media.topic_download_submitted",
        "download",
        result["job"]["id"],
        {
            "provider": "douyin-downloader",
            "term": result["term"],
            "source_count": len(result["queued"]),
        },
    )
    return result


@router.post("/douyin/connection", status_code=202)
def connect_douyin(
    workspace_id: str,
    body: ConnectionRequest,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="Douyin login is local-machine only.")
    require_role(membership(session, workspace_id, user.id), {"owner"})
    require_governed_assurance(user)
    if not body.confirm_external_action:
        raise HTTPException(
            status_code=400,
            detail="Opening the Douyin login browser requires explicit confirmation.",
        )
    connection = start_connection(
        force_refresh=body.force_refresh, require_login=body.require_login
    )
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "media.douyin_connection_started",
        "provider_connection",
        "douyin-downloader",
        {"state": connection["state"]},
    )
    return {"connection": connection}


#: How long a downloads ETag stays honest, in seconds. Job rows are in the
#: fingerprint, so any queued, finished or edited job refreshes at once; what
#: the fingerprint cannot see is the disk itself - files deleted by hand
#: between jobs - so the tag expires on a clock and the listing rescans the
#: folders at most every half minute instead of on every poll.
DOWNLOADS_ETAG_SECONDS = 30


def _downloads_etag(session: Session, workspace_id: str) -> str | None:
    """A cheap fingerprint of everything the listing is built from, or None.

    One aggregate over the workspace's durable jobs - downloads and the
    library ingests their progress bars read - plus a time bucket. None while
    any job is running: live progress comes from the disk, not from rows, and
    a fingerprint that froze a running download's counters would be a lie.
    Running only, not queued - nothing lands on disk while a job waits, a
    stale queued row can sit for days, and the moment it starts its own row
    changes and breaks the tag anyway.
    """
    from sqlalchemy import case, func, select

    from trendrelay_api.models import DurableJob

    running, total, latest = session.execute(
        select(
            func.sum(case((DurableJob.status == "running", 1), else_=0)),
            func.count(DurableJob.id),
            func.max(DurableJob.updated_at),
        ).where(DurableJob.workspace_key == workspace_id)
    ).one()
    if running:
        return None
    bucket = int(time.time() // DOWNLOADS_ETAG_SECONDS)
    return f'W/"downloads-{total}-{latest}-{bucket}"'


def _listed_download_view(job: dict[str, Any]) -> dict[str, Any]:
    """The job as the listing serves it.

    Identical to the stored record except `library_jobs`: hundreds of ingest
    rows per batch that only the server reads - `library_progress` is their
    summary, computed before this - and that made a routine poll a quarter
    megabyte heavier for nothing. The single-job read keeps the full record.
    """
    result = job.get("result")
    if not result or "library_jobs" not in result:
        return job
    trimmed = dict(result)
    del trimmed["library_jobs"]
    return {**job, "result": trimmed}


@router.get("/downloads")
def downloads(
    workspace_id: str,
    request: Request,
    response: Response,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> Any:
    """The downloads listing, answered with 304 when nothing changed.

    Every open tab polls this for the notification bell, and the payload
    carries every batch's artifact list, so the routine answer used to be
    megabytes of JSON and a disk scan per poll. The fingerprint makes the
    routine answer empty; the full build runs when something actually
    happened, or when the tag's half-minute honesty window lapses.
    """
    membership(session, workspace_id, user.id)
    etag = _downloads_etag(session, workspace_id)
    if etag and request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    if etag:
        response.headers["ETag"] = etag
    return {"jobs": [_listed_download_view(job) for job in list_download_jobs(workspace_id)]}


@router.post("/downloads/clear")
def clear_downloads(
    workspace_id: str,
    body: ClearDownloadsRequest,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(
        membership(session, workspace_id, user.id),
        {"owner", "editor", "approver"},
    )
    if not body.confirm_external_action:
        raise HTTPException(
            status_code=400,
            detail="Clearing download history requires confirmation.",
        )
    result = clear_download_history(workspace_id)
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "media.download_history_cleared",
        "workspace",
        workspace_id,
        {
            "removed_count": len(result["removed_job_ids"]),
            "preserved_active_count": len(result["preserved_active_job_ids"]),
            "preserved_on_disk_count": len(result["preserved_on_disk_job_ids"]),
        },
    )
    return {"cleanup": result}


@router.post("/douyin/downloads", status_code=202)
def submit_download(
    workspace_id: str,
    body: DownloadRequest,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    if body.workspace_id != workspace_id:
        raise HTTPException(status_code=422, detail="Workspace path and body must match.")
    require_role(membership(session, workspace_id, user.id), {"owner", "editor", "approver"})
    require_governed_assurance(user)
    try:
        job = create_download_job(body, actor_user_id=user.id)
    except PermissionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "media.download_submitted",
        "download",
        job["id"],
        {"provider": "douyin-downloader", "source_count": len(body.urls)},
    )
    return {"job": job}


@router.get("/download-providers")
def download_providers(
    workspace_id: str, user: AuthenticatedUser, session: DatabaseSession
) -> dict[str, Any]:
    """Which services can be downloaded from, and whether each one can run now.

    The interface reads its host patterns, modes and labels from here rather
    than keeping a second copy: two copies of "which link belongs to whom" is
    how the box and the server come to disagree about what somebody pasted.
    """
    membership(session, workspace_id, user.id)
    from trendrelay_api.integrations import download_providers as registry
    from trendrelay_api.integrations import tiktok

    douyin_status = provider_status()
    rows = []
    for row in registry.catalogue():
        if row["id"] == "tiktok":
            live = tiktok.provider_status()
            row = {**row, "ready": live["ready"], "reason": live["reason"],
                   "revision": live["revision"]}
        else:
            row = {
                **row,
                "ready": bool(
                    douyin_status["installed"]
                    and douyin_status["active"]
                    and douyin_status["cookies_ready"]
                ),
                "reason": "" if douyin_status.get("cookies_ready") else (
                    "Connect Douyin before downloading."
                ),
                "revision": douyin_status.get("revision", ""),
            }
        rows.append(row)
    return {"providers": rows}


@router.post("/downloads", status_code=202)
def submit_provider_download(
    workspace_id: str,
    body: DownloadRequest,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Start a download, with the service worked out from the links themselves.

    One submission is one service. A batch spanning two is refused here, named,
    and counted - it cannot be run as one job, and running it as two behind a
    single status would leave "failed" unable to say which half.
    """
    if body.workspace_id != workspace_id:
        raise HTTPException(status_code=422, detail="Workspace path and body must match.")
    require_role(membership(session, workspace_id, user.id), {"owner", "editor", "approver"})
    require_governed_assurance(user)
    from trendrelay_api.integrations import download_providers as registry

    try:
        provider, matched, _ignored = registry.detect(list(body.urls))
    except registry.MixedProviders as error:
        # 409, not 422: nothing about the request is malformed. The links are
        # each perfectly valid and simply cannot travel together.
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if body.mode not in provider.modes:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{provider.label} cannot fetch {body.mode!r}. It offers: "
                + ", ".join(provider.modes)
                + "."
            ),
        )
    try:
        job = create_download_job(
            body.model_copy(update={"urls": matched}),
            actor_user_id=user.id,
            service=provider.id,
        )
    except PermissionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "media.download_submitted",
        "download",
        job["id"],
        {"provider": provider.id, "source_count": len(matched)},
    )
    return {"job": job}


@router.post("/downloads/{job_id}/import-links", status_code=202)
def import_download_links(
    workspace_id: str, job_id: str, body: CapturedLinksRequest,
    request: Request, user: AuthenticatedUser, session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), {"owner", "editor", "approver"})
    require_governed_assurance(user)
    try:
        job = import_captured_links(job_id, workspace_id, body, user.id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Download not found.") from error
    except PermissionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    audit(session, request, workspace_id, user.id, "media.download_links_imported",
          "download", job["id"], {"parent_job_id": job_id, "source_count": len(body.urls)})
    return {"job": job}


@router.post("/downloads/library-sync", status_code=202)
def sync_downloads_to_library(
    workspace_id: str,
    body: LibrarySyncRequest,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), {"owner", "editor", "approver"})
    if not body.confirm_external_action:
        raise HTTPException(
            status_code=400, detail="Adding downloads to Library requires confirmation."
        )
    result = reconcile_downloads_to_library(workspace_id, user.id)
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "media.downloads_library_sync_queued",
        "workspace",
        workspace_id,
        {
            "scanned_downloads": result["scanned_downloads"],
            "queued_count": len(result["queued"]),
            "error_count": len(result["errors"]),
            "removed_count": len(result["removed_asset_ids"]),
        },
    )
    return {"sync": result}


@router.post("/downloads/{job_id}/resume", status_code=202)
def resume_download(
    workspace_id: str,
    job_id: str,
    body: ResumeDownloadRequest,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(
        membership(session, workspace_id, user.id),
        {"owner", "editor", "approver"},
    )
    require_governed_assurance(user)
    if not body.confirm_external_action:
        raise HTTPException(
            status_code=400,
            detail="Resuming a download requires confirmation.",
        )
    try:
        job = resume_download_job(
            job_id, workspace_id, from_saved_files=body.from_saved_files
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Download not found.") from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "media.download_resumed",
        "download",
        job_id,
        {
            "files_on_disk": job["progress"]["files_downloaded"],
            "from_saved_files": body.from_saved_files,
        },
    )
    return {"job": job}


@router.post("/downloads/{job_id}/cancel", status_code=202)
def cancel_download(
    workspace_id: str,
    job_id: str,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(
        membership(session, workspace_id, user.id),
        {"owner", "editor", "approver"},
    )
    try:
        job = cancel_download_job(job_id, workspace_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Download not found.") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "media.download_cancelled",
        "download",
        job_id,
        {"status": job["status"]},
    )
    return {"job": job}


@router.get("/downloads/{job_id}")
def get_download(
    workspace_id: str,
    job_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    try:
        job = download_job(job_id)
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Download not found.") from error
    if job["workspace_id"] != workspace_id:
        raise HTTPException(status_code=404, detail="Download not found.")
    return {"job": job}
