"""Run one library tool over many assets.

The unit of work stays a single-asset job. A batch is a grouping at the API and
in the interface, not a new job kind, because the durable queue already leases,
retries and reports per job - and partial failure is the normal case here, not
the exception. One corrupt file should not fail the forty-nine beside it.

Adding a tool means adding a ``BulkAction`` below. The endpoint, the permission
check, the applicability rules and the interface all read this registry, so a
new tool needs no new route and no new branch in the client.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

from trendrelay_api.database import SessionFactory
from trendrelay_api.media_models import MediaAsset, MediaAssetVersion


@dataclass(frozen=True)
class AssetView:
    """What an action needs to judge an asset, read once for the whole batch."""

    id: str
    title: str
    media_kind: str
    original_path: str
    version_kinds: frozenset[str]


@dataclass(frozen=True)
class BulkAction:
    id: str
    label: str
    #: Present tense, for progress text: "Blurring faces on 12 clips".
    verb: str
    description: str
    #: Roles allowed to run it, checked before anything is queued.
    roles: frozenset[str]
    #: Refuse above this many assets rather than quietly truncating the list.
    max_batch: int
    #: Do the work for one asset and return its record; a job id if it queued one.
    enqueue: Callable[[str, AssetView, Any], dict[str, Any]]
    #: Runtime readiness, e.g. whether OpenCV is installed. (ok, reason).
    availability: Callable[[], tuple[bool, str | None]] = lambda: (True, None)
    #: Why this asset is not eligible, or None when it is.
    ineligible: Callable[[AssetView], str | None] = lambda asset: None
    media_kinds: frozenset[str] = field(default_factory=lambda: frozenset({"video"}))


def _blur_enqueue(workspace_id: str, asset: AssetView, _factory: Any = None) -> dict[str, Any]:
    from trendrelay_api.integrations.face_blur import FaceBlurRequest, create_blur_job

    return create_blur_job(
        FaceBlurRequest.model_validate(
            {
                "workspace_id": workspace_id,
                "source_path": asset.original_path,
                "confirm_external_action": True,
            }
        )
    )


def _blur_available() -> tuple[bool, str | None]:
    from trendrelay_api.integrations.face_blur import runtime_status

    status = runtime_status()
    return bool(status["available"]), status.get("reason")


def _blur_ineligible(asset: AssetView) -> str | None:
    if asset.media_kind != "video":
        return "Face blurring applies to video"
    if "blurred" in asset.version_kinds:
        # Re-blurring an already blurred cut wastes a long render and produces a
        # second version nobody asked for, so it is skipped rather than queued.
        return "Already has a blurred version"
    return None


def _delete_asset(workspace_id: str, asset: AssetView, factory: Any = None) -> dict[str, Any]:
    """Remove a library entry and the copies the library itself keeps.

    The library stores each asset in its own directory under its media root; the
    downloaded source it was imported from lives elsewhere and is left alone, so
    a delete here is recoverable by re-importing rather than a loss of the only
    copy. The directory is only removed when it sits directly inside this
    workspace's root, so a stored path cannot direct a delete somewhere else.
    """
    import shutil

    from trendrelay_api.media_library import LIBRARY_ROOT

    workspace_root = (LIBRARY_ROOT / workspace_id).resolve()
    directory: Path | None = None
    try:
        candidate = Path(asset.original_path).resolve().parent
        if candidate.parent == workspace_root:
            directory = candidate
    except OSError:
        directory = None

    session_factory = factory or SessionFactory
    with session_factory.begin() as session:
        session.execute(
            delete(MediaAssetVersion).where(MediaAssetVersion.asset_id == asset.id)
        )
        session.execute(
            delete(MediaAsset).where(
                MediaAsset.workspace_id == workspace_id, MediaAsset.id == asset.id
            )
        )
    if directory is not None:
        shutil.rmtree(directory, ignore_errors=True)
    return {"removed_directory": str(directory) if directory else None}


REGISTRY: dict[str, BulkAction] = {
    "face_blur": BulkAction(
        id="face_blur",
        label="Blur faces",
        verb="Blurring faces",
        description=(
            "Detect every face and burn the blur into a new version of each clip. "
            "The original is kept; handoffs send the blurred cut."
        ),
        roles=frozenset({"owner", "editor", "approver"}),
        # A 4K render runs for over a minute, so a large batch is an hour of CPU.
        max_batch=25,
        enqueue=_blur_enqueue,
        availability=_blur_available,
        ineligible=_blur_ineligible,
    ),
    "delete": BulkAction(
        id="delete",
        label="Delete",
        verb="Deleting",
        description=(
            "Remove these items from the library, along with the copies it keeps. "
            "The downloaded source is left in place, so a delete can be undone by "
            "importing again."
        ),
        # Destructive and immediate, so it is held to the stricter pair of roles.
        roles=frozenset({"owner", "approver"}),
        max_batch=200,
        enqueue=_delete_asset,
        media_kinds=frozenset({"video", "audio", "image"}),
    ),
}


def catalogue() -> list[dict[str, Any]]:
    """The actions an interface can offer, with why one is unavailable."""
    entries = []
    for action in REGISTRY.values():
        ready, reason = action.availability()
        entries.append(
            {
                "id": action.id,
                "label": action.label,
                "verb": action.verb,
                "description": action.description,
                "media_kinds": sorted(action.media_kinds),
                "max_batch": action.max_batch,
                "roles": sorted(action.roles),
                "available": ready,
                "reason": None if ready else reason,
            }
        )
    return entries


def resolve(action_id: str) -> BulkAction:
    action = REGISTRY.get(action_id)
    if action is None:
        known = ", ".join(sorted(REGISTRY))
        raise ValueError(f"Unknown library action '{action_id}'. Available: {known}.")
    return action


def load_assets(workspace_id: str, asset_ids: Iterable[str], *, factory=None) -> list[AssetView]:
    """Read the selected assets once, with the version kinds each already has."""
    wanted = list(dict.fromkeys(asset_ids))
    if not wanted:
        return []
    session_factory = factory or SessionFactory
    with session_factory() as session:
        assets = session.scalars(
            select(MediaAsset).where(
                MediaAsset.workspace_id == workspace_id, MediaAsset.id.in_(wanted)
            )
        ).all()
        versions: dict[str, set[str]] = {}
        for asset_id, kind in session.execute(
            select(MediaAssetVersion.asset_id, MediaAssetVersion.version_kind).where(
                MediaAssetVersion.asset_id.in_([asset.id for asset in assets] or [""])
            )
        ):
            versions.setdefault(asset_id, set()).add(kind)
        by_id = {
            asset.id: AssetView(
                id=asset.id,
                title=asset.title,
                media_kind=asset.media_kind,
                original_path=asset.original_path,
                version_kinds=frozenset(versions.get(asset.id, set())),
            )
            for asset in assets
        }
    # Preserve the order the caller asked for, so results read predictably.
    return [by_id[asset_id] for asset_id in wanted if asset_id in by_id]


def run(
    workspace_id: str, action_id: str, asset_ids: list[str], *, factory=None
) -> dict[str, Any]:
    """Queue one job per eligible asset and report what happened to each.

    Every asset gets an outcome. A caller that only sees "202 accepted" cannot
    tell that twelve of its fifty selections were skipped for already being
    done, which is exactly the thing an operator needs to know.
    """
    action = resolve(action_id)
    if not asset_ids:
        raise ValueError("Select at least one item.")
    if len(asset_ids) > action.max_batch:
        raise ValueError(
            f"{action.label} runs on up to {action.max_batch} items at a time; "
            f"{len(asset_ids)} were selected. Narrow the selection and run it again."
        )
    ready, reason = action.availability()
    if not ready:
        raise RuntimeError(reason or f"{action.label} is unavailable.")

    assets = load_assets(workspace_id, asset_ids, factory=factory)
    found = {asset.id for asset in assets}
    results: list[dict[str, Any]] = [
        {"asset_id": asset_id, "status": "missing", "detail": "No such asset in this workspace"}
        for asset_id in asset_ids
        if asset_id not in found
    ]

    for asset in assets:
        skip = action.ineligible(asset)
        if skip:
            results.append(
                {"asset_id": asset.id, "title": asset.title, "status": "skipped", "detail": skip}
            )
            continue
        try:
            job = action.enqueue(workspace_id, asset, factory)
        except Exception as error:  # noqa: BLE001 - one failure must not stop the batch
            results.append(
                {
                    "asset_id": asset.id,
                    "title": asset.title,
                    "status": "failed",
                    "detail": str(error),
                }
            )
            continue
        results.append(
            {
                "asset_id": asset.id,
                "title": asset.title,
                "status": "queued",
                "job_id": job.get("id"),
            }
        )

    counts = {
        state: sum(1 for item in results if item["status"] == state)
        for state in ("queued", "skipped", "failed", "missing")
    }
    return {
        "action": action.id,
        "label": action.label,
        "verb": action.verb,
        "counts": counts,
        "results": results,
        "job_ids": [item["job_id"] for item in results if item.get("job_id")],
    }
