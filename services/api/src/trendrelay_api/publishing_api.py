"""Authenticated, role-gated social publishing API."""

from __future__ import annotations

import mimetypes
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from trendrelay_api import publishing_connections
from trendrelay_api.auth import CurrentUser, current_user, require_governed_assurance
from trendrelay_api.database import get_session
from trendrelay_api.env_store import EnvWriteError
from trendrelay_api.foundation import membership, require_role
from trendrelay_api.integrations import media_hosting, posting_slots
from trendrelay_api.integrations.publishing import (
    PROVIDERS,
    PublishRequest,
    approved_media_path,
    board_options,
    clear_provider_credentials,
    connection_status,
    create_publish_job,
    discover_all_integrations,
    discover_integrations,
    list_publish_jobs,
    preview_publish,
    publish_job,
    reveal_credential,
    run_publish_job,
    save_provider_credentials,
    set_active_provider,
    test_provider,
)
from trendrelay_api.integrations.publishing_matrix import capability_matrix
from trendrelay_api.models import Workspace

router = APIRouter(prefix="/api/workspaces/{workspace_id}/publishing", tags=["publishing"])
AuthenticatedUser = Annotated[CurrentUser, Depends(current_user)]
DatabaseSession = Annotated[Session, Depends(get_session)]


class ExternalConfirmation(BaseModel):
    confirm_external_action: bool = False
    provider: str | None = None


class ProviderSelection(BaseModel):
    provider: str = Field(min_length=1, max_length=40)


class ProviderCredentials(ProviderSelection):
    values: dict[str, str] = Field(default_factory=dict)
    confirm_external_action: bool = False
    activate: bool = False


class HostingCredentials(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)
    confirm_external_action: bool = False


def validate_workspace(body: PublishRequest, workspace_id: str) -> None:
    if body.workspace_id != workspace_id:
        raise HTTPException(status_code=422, detail="Workspace path and body must match.")


def require_local_request(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="Credential changes are local-machine only.")


@router.get("/connection")
def publishing_connection(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    return {"connection": connection_status()}


@router.get("/capabilities")
def publishing_capabilities(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Which engine can do what, on which network.

    Static: it describes the engines themselves, not this workspace's keys,
    so it answers the same for everybody and needs no probe. Membership is
    still required because every route on this router is a workspace's.

    Served rather than written into the interface so there is one source for
    it. A table kept by hand in the frontend would be correct on the day it
    was typed and quietly wrong from the next engine onwards.
    """
    membership(session, workspace_id, user.id)
    return capability_matrix()


@router.post("/providers/credentials")
def save_credentials(
    workspace_id: str,
    body: ProviderCredentials,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_local_request(request)
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    require_governed_assurance(user)
    if not body.confirm_external_action:
        raise HTTPException(status_code=400, detail="Saving credentials requires confirmation.")
    try:
        result = save_provider_credentials(body.provider, body.values)
        if body.activate:
            result |= set_active_provider(body.provider)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except EnvWriteError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"result": result, "connection": connection_status()}


class CredentialClear(BaseModel):
    """Which login's keys to forget."""

    provider: str = Field(min_length=1, max_length=40)
    confirm_external_action: bool = False


@router.post("/providers/credentials/clear")
def clear_credentials(
    workspace_id: str,
    body: CredentialClear,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Forget a login's keys, so an engine stops reporting a refused one.

    A revoked key reports an authorization failure on every probe, and until now
    the only answer offered was to replace it - which is no answer for somebody
    who has stopped using the engine and has no new key to type. Clearing
    returns the card to unconfigured, which is a state it already knows how to
    show, and the error goes with the key that caused it.

    Confirmed like the save it undoes, and POST for the same reason the other
    destructive calls here are: the browser sends the app's own method list.
    """
    require_local_request(request)
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    require_governed_assurance(user)
    if not body.confirm_external_action:
        raise HTTPException(
            status_code=400, detail="Clearing a key requires confirmation."
        )
    try:
        result = clear_provider_credentials(body.provider)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except EnvWriteError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"result": result, "connection": connection_status()}


class ConnectionRequest(BaseModel):
    """Another login for an engine that already has one."""

    provider: str = Field(min_length=1, max_length=40)
    label: str = Field(default="", max_length=80)


class ConnectionRename(BaseModel):
    label: str = Field(min_length=1, max_length=80)


def _connections_payload() -> list[dict[str, Any]]:
    return [
        publishing_connections.payload(
            row, provider_label=PROVIDERS[row.provider].label
        )
        for row in publishing_connections.connections(PROVIDERS)
    ]


@router.get("/connections")
def list_publishing_connections(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Every login, including the one each engine starts with."""
    membership(session, workspace_id, user.id)
    return {"connections": _connections_payload()}


@router.post("/connections")
def add_publishing_connection(
    workspace_id: str,
    body: ConnectionRequest,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Make room for a second set of keys on an engine.

    Local-machine only and owner-level, because it writes to the same file the
    keys live in. No credentials are accepted here: adding the login and
    filling it in are two steps, the second needing somewhere to put the key,
    which is what this creates.
    """
    require_local_request(request)
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    require_governed_assurance(user)
    try:
        added = publishing_connections.add(PROVIDERS, body.provider, body.label)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except EnvWriteError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {
        "connection": publishing_connections.payload(
            added, provider_label=PROVIDERS[added.provider].label
        ),
        "connections": _connections_payload(),
    }


@router.post("/connections/{connection_id}/rename")
def rename_publishing_connection(
    workspace_id: str,
    connection_id: str,
    body: ConnectionRename,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_local_request(request)
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    try:
        renamed = publishing_connections.rename(PROVIDERS, connection_id, body.label)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except EnvWriteError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {
        "connection": publishing_connections.payload(
            renamed, provider_label=PROVIDERS[renamed.provider].label
        ),
        "connections": _connections_payload(),
    }


@router.post("/connections/{connection_id}/remove")
def remove_publishing_connection(
    workspace_id: str,
    connection_id: str,
    body: ExternalConfirmation,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Forget a login and the keys that were only for it.

    Confirmed explicitly: destinations already pointing at this login stop
    resolving, and that is not something to do because a button was near the
    cursor. POST rather than DELETE so the browser sends it - the CORS method
    list is the app's, and a verb it does not carry fails before it arrives.
    """
    require_local_request(request)
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    require_governed_assurance(user)
    if not body.confirm_external_action:
        raise HTTPException(
            status_code=400, detail="Removing a connection requires confirmation."
        )
    found = publishing_connections.find(PROVIDERS, connection_id)
    if not found:
        raise HTTPException(status_code=404, detail="No such connection.")
    keys = tuple(field.key for field in PROVIDERS[found.provider].credentials)
    try:
        publishing_connections.remove(PROVIDERS, connection_id, keys)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except EnvWriteError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"removed": connection_id, "connections": _connections_payload()}


@router.post("/media-hosting/credentials")
def save_media_hosting_credentials(
    workspace_id: str,
    body: HostingCredentials,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Store object-storage settings so fetch-only engines can reach local media."""
    require_local_request(request)
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    require_governed_assurance(user)
    if not body.confirm_external_action:
        raise HTTPException(status_code=400, detail="Saving credentials requires confirmation.")
    try:
        result = media_hosting.save_credentials(body.values)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except EnvWriteError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"result": result, "connection": connection_status(probe=False)}


class HostingProbe(BaseModel):
    """A probe, optionally of values that are only on screen."""

    #: Field ids to test in place of what is stored. Partial: the fields left
    #: out come from the saved settings, so replacing one wrong value means
    #: typing one field rather than retyping all five to test them.
    values: dict[str, str] = Field(default_factory=dict, max_length=20)
    confirm_external_action: bool = False


@router.post("/media-hosting/probe")
def probe_media_hosting(
    workspace_id: str,
    body: HostingProbe,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Try the whole path a published clip takes through object storage.

    Confirmed like the other outward calls, and for a stronger reason than most:
    this one writes a small object into the bucket, because fetching it back
    through the public URL is the only way to prove an engine could.
    """
    require_local_request(request)
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    require_governed_assurance(user)
    if not body.confirm_external_action:
        raise HTTPException(status_code=400, detail="Testing storage requires confirmation.")
    return {"probe": media_hosting.probe(body.values or None)}


class SlotEntry(BaseModel):
    weekday: int = Field(default=-1, ge=-1, le=6)
    time: str = Field(min_length=3, max_length=5)


class SlotUpdate(BaseModel):
    slots: list[SlotEntry] = Field(default_factory=list, max_length=40)
    timezone: str = Field(default="UTC", min_length=1, max_length=80)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("Use an IANA timezone such as Asia/Bangkok.") from error
        return value


@router.get("/slots")
def publishing_slots(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """This workspace's posting times, plus the presets it can start from."""
    membership(session, workspace_id, user.id)
    workspace = session.get(Workspace, workspace_id)
    return {
        "slots": posting_slots.list_slots(workspace_id, session=session),
        "presets": posting_slots.preset_payload(),
        "timezone": workspace.timezone if workspace else "UTC",
    }


# POST rather than PUT: the API is exposed to the local browser under a CORS
# policy that allows GET and POST only, and every other route follows that.
@router.post("/slots")
def save_publishing_slots(
    workspace_id: str,
    body: SlotUpdate,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    try:
        slots = posting_slots.replace_slots(
            workspace_id, [entry.model_dump() for entry in body.slots], session=session
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    workspace = session.get(Workspace, workspace_id)
    if workspace:
        workspace.timezone = body.timezone
        session.flush()
    return {
        "slots": slots,
        "presets": posting_slots.preset_payload(),
        "timezone": body.timezone,
    }


@router.post("/providers/test")
def test_provider_credentials(
    workspace_id: str,
    body: ProviderSelection,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    try:
        return {"provider": test_provider(body.provider)}
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/providers/activate")
def activate_provider(
    workspace_id: str,
    body: ProviderSelection,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_local_request(request)
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    try:
        result = set_active_provider(body.provider)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except EnvWriteError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"result": result, "connection": connection_status()}


@router.post("/integrations")
def publishing_integrations(
    workspace_id: str,
    body: ExternalConfirmation,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    require_governed_assurance(user)
    if not body.confirm_external_action:
        raise HTTPException(status_code=400, detail="Discovery requires explicit confirmation.")
    try:
        return discover_integrations(body.provider)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


#: A type no download manager has a rule for.
#:
#: Grabber extensions decide what to capture from the response's content type,
#: and they watch `video/*` - which is why serving a clip honestly meant IDM
#: took it the moment a timeline row was expanded, cancelling the browser's own
#: request in the process so the player got "Failed to fetch" and showed
#: nothing. Reading the bytes with `fetch` did not help: the response on the
#: wire is what is watched, not what the page does with it afterwards.
#:
#: The caller asks for this deliberately and puts the real type back on the blob
#: it builds, so the player still gets a `video/mp4` to play - it just never
#: travels as one.
OPAQUE_MEDIA_TYPE = "application/x-trendrelay-preview"


@router.get("/media/preview")
def preview_publishing_media(
    workspace_id: str,
    path: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
    opaque: bool = False,
) -> FileResponse:
    """Stream a file this workspace could publish, so it can be seen first.

    Bounded by the same approved media roots publishing itself resolves against,
    which is the honest boundary: anything publishable is previewable, and
    nothing else is readable. The existing player borrowed the face-blur route
    for this, and that one is confined to blurred renders - so an ordinary clip
    answered 403 and the preview showed a black frame.

    `opaque` hands back the same bytes under a type nothing recognises, for
    callers that read the response themselves rather than pointing an element at
    it. See `OPAQUE_MEDIA_TYPE`.
    """
    membership(session, workspace_id, user.id)
    try:
        resolved = approved_media_path(path)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    # Looked up rather than assembled from the suffix. Spelling the type by
    # hand turned `.jpg` into `image/jpg`, which is not a registered type - so
    # a browser handed one stops trying to display it and downloads the file
    # instead, which is what a preview must never do.
    kind, _encoding = mimetypes.guess_type(resolved.name)
    if opaque:
        kind = OPAQUE_MEDIA_TYPE
    return FileResponse(resolved, media_type=kind or "application/octet-stream")


@router.post("/credentials/{key}/reveal")
def reveal_publishing_credential(
    workspace_id: str,
    key: str,
    body: ExternalConfirmation,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Show one saved credential in full, to the operator who saved it.

    These sit in a `.env` on this machine, openable in any editor, so the value
    is not being withheld from the person asking - the gate is here because a
    browser is a wider door than the file. Loopback only, the same roles that
    may write a key, and confirmed like any other outward-facing action.

    Only keys the credential screens themselves offer to write can be asked for.
    Without that restriction this is "read any environment variable", which is
    every secret on the machine behind a button meant for an engine's API key.
    """
    require_local_request(request)
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    require_governed_assurance(user)
    if not body.confirm_external_action:
        raise HTTPException(status_code=400, detail="Revealing a credential requires confirmation.")
    try:
        return {"key": key, "value": reveal_credential(key)}
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/accounts/{provider}/{integration_id}/boards")
def publishing_account_boards(
    workspace_id: str,
    provider: str,
    integration_id: str,
    body: ExternalConfirmation,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """An account's Pinterest boards, where its engine will list them.

    Typed by hand until now, which was correct on exactly one engine:
    bundle.social matches a board by name while Zernio, Buffer and WoopSocial
    each want its id, so a name entered for one silently failed on the others.

    An empty list means the engine does not offer them, not that the account has
    no boards - the field falls back to being typed rather than claiming the
    account owns none.
    """
    require_role(membership(session, workspace_id, user.id), {"owner", "editor", "approver"})
    require_governed_assurance(user)
    if not body.confirm_external_action:
        raise HTTPException(status_code=400, detail="Reading boards requires confirmation.")
    try:
        return {"boards": board_options(provider, integration_id)}
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/integrations/all")
def publishing_integrations_all(
    workspace_id: str,
    body: ExternalConfirmation,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Connected accounts across every configured engine.

    One post can address destinations on several engines, so the page needs all
    of them together rather than whichever engine happens to be active.
    """
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    require_governed_assurance(user)
    if not body.confirm_external_action:
        raise HTTPException(status_code=400, detail="Discovery requires explicit confirmation.")
    return discover_all_integrations()


@router.post("/preview")
def preview_publishing(
    workspace_id: str,
    body: PublishRequest,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    validate_workspace(body, workspace_id)
    require_role(membership(session, workspace_id, user.id), {"owner", "editor", "approver"})
    try:
        return {"preview": preview_publish(body)}
    except (PermissionError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/jobs", status_code=202)
def submit_publishing(
    workspace_id: str,
    body: PublishRequest,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    validate_workspace(body, workspace_id)
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    require_governed_assurance(user)
    try:
        job = create_publish_job(body)
    except PermissionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    background_tasks.add_task(run_publish_job, job["id"])
    return {"job": job}


@router.get("/jobs")
def publishing_jobs(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    return {"jobs": list_publish_jobs(workspace_id)}


@router.get("/jobs/{job_id}")
def get_publishing_job(
    workspace_id: str,
    job_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    try:
        job = publish_job(job_id)
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Publishing job not found.") from error
    if job["workspace_id"] != workspace_id:
        raise HTTPException(status_code=404, detail="Publishing job not found.")
    return {"job": job}
