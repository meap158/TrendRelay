"""Video providers: one registry, and a job that fills a clip.

xAI and Gemini are the first entries. Another provider is another entry and
an adapter. The Tools card, the Attribution Generate dialog, and the Library
editing row all read this table rather than keeping a list of their own. A
provider is offered only when its switch is on, its key is saved, Check has
accepted that key, and any extra requirement it declares is met. Several may
be ready at once. The operator picks one. A refusal does not fall through to
another, because that would spend again.

Check calls a models endpoint. It does not start a video. Generation is a
job the worker claims, one at a time. A draft files the clip through its own
ingest and links the product. A Library image files a new video and does not.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from trendrelay_api.env_store import effective_value, masked_value, write_env_values
from trendrelay_api.jobs import (
    claim_job,
    complete_job,
    create_job_record,
    fail_job,
    get_job_record,
)
from trendrelay_api.models import DurableJob
from trendrelay_api.project_storage import DATA_ROOT
from trendrelay_api.tool_settings import SettingsError

JOB_KIND = "video_generation"
LEASE_SECONDS = 720
POLL_TIMEOUT_SECONDS = 480
POLL_INTERVAL_SECONDS = 10
#: A subject still, not a video. Large enough for a listing photo.
MAX_SUBJECT_BYTES = 8_000_000
CHECKS_PATH = DATA_ROOT / "video-generation" / "checks.json"
XAI_BASE = "https://api.x.ai/v1"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

Transport = Callable[[str, str, dict[str, str], bytes | None, int], tuple[int, bytes]]
Sleep = Callable[[float], None]
Clock = Callable[[], float]


class VideoError(Exception):
    """A generation that stopped, named so the draft can say which kind."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class VideoProvider:
    """One vendor. The card, the check, and the picker all read this."""

    id: str
    label: str
    key_env: str
    enabled_env: str
    dashboard_url: str
    key_help: str
    #: The model the current vendor docs name for an image-conditioned clip.
    model: str
    duration_seconds: int
    #: A models or account URL. Never a video endpoint: Check must not spend.
    check_url: str
    #: This deployment's xAI account keeps zero data retention, so a video
    #: with nowhere to write the file is refused. The bucket is the one
    #: Publish already configured.
    requires_bucket: bool


PROVIDERS: dict[str, VideoProvider] = {
    "xai": VideoProvider(
        id="xai",
        label="xAI",
        key_env="XAI_API_KEY",
        enabled_env="VIDEO_PROVIDER_XAI_ENABLED",
        dashboard_url="https://console.x.ai/team/default/api-keys",
        key_help=(
            "From the xAI console. The same key Last 30 Days reads, so saving "
            "it here serves both. The switch below is what offers it on a video draft."
        ),
        model="grok-imagine-video-1.5",
        duration_seconds=10,
        check_url=f"{XAI_BASE}/models",
        requires_bucket=True,
    ),
    "gemini": VideoProvider(
        id="gemini",
        label="Gemini",
        key_env="GEMINI_API_KEY",
        enabled_env="VIDEO_PROVIDER_GEMINI_ENABLED",
        dashboard_url="https://aistudio.google.com/apikey",
        key_help=(
            "From Google AI Studio. Video on this API is paid. Check only "
            "proves the key is accepted."
        ),
        model="veo-3.1-generate-preview",
        duration_seconds=8,
        check_url=f"{GEMINI_BASE}/models",
        requires_bucket=False,
    ),
}


def _enabled(provider: VideoProvider) -> bool:
    return effective_value(provider.enabled_env).strip().lower() == "on"


def _key(provider: VideoProvider) -> str:
    return effective_value(provider.key_env).strip()


def _read_checks() -> dict[str, Any]:
    if not CHECKS_PATH.is_file():
        return {}
    try:
        loaded = json.loads(CHECKS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _write_checks(data: dict[str, Any]) -> None:
    CHECKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = CHECKS_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temporary.replace(CHECKS_PATH)


def _store_check(provider_id: str, *, ok: bool, message: str) -> None:
    checks = _read_checks()
    checks[provider_id] = {
        "ok": ok,
        "message": message[:500],
        "checked_at": datetime.now(UTC).isoformat(),
    }
    _write_checks(checks)


def bucket_ready() -> tuple[bool, str]:
    """Whether Publish's R2 settings are complete. No network call."""
    from trendrelay_api.integrations.media_hosting import status

    report = status()
    missing = report.get("missing") or []
    if missing:
        return False, (
            "Media hosting is missing "
            + ", ".join(missing)
            + ". Set it on the Publish screen. This card does not ask for those secrets again."
        )
    return True, "The Cloudflare R2 bucket already set on Publish is ready."


def _row(provider: VideoProvider) -> dict[str, Any]:
    checks = _read_checks().get(provider.id) or {}
    check_ok = bool(checks.get("ok"))
    configured = bool(_key(provider))
    enabled = _enabled(provider)
    bucket_ok, bucket_detail = (True, "") if not provider.requires_bucket else bucket_ready()
    ready = enabled and configured and check_ok and bucket_ok
    return {
        "id": provider.id,
        "label": provider.label,
        "enabled": enabled,
        "configured": configured,
        "check_ok": check_ok,
        "check_message": str(checks.get("message") or ""),
        "requires_bucket": provider.requires_bucket,
        "bucket_ready": bucket_ok,
        "bucket_detail": bucket_detail,
        "ready": ready,
    }


def public_providers() -> list[dict[str, Any]]:
    """Every provider, with nothing a screen could use as a key."""
    return [_row(provider) for provider in PROVIDERS.values()]


def ready_providers() -> list[dict[str, str]]:
    """The labels the Generate dialog may offer. Nothing else."""
    return [
        {"id": row["id"], "label": row["label"]}
        for row in public_providers()
        if row["ready"]
    ]


def require_ready(provider_id: str) -> VideoProvider:
    provider = PROVIDERS.get(provider_id)
    if provider is None:
        known = ", ".join(PROVIDERS)
        raise ValueError(f"Unknown video provider {provider_id!r}. Known providers: {known}.")
    if not _row(provider)["ready"]:
        raise ValueError(
            f"{provider.label} is not ready. Switch it on in Tools and check the key."
        )
    return provider


def describe_fields() -> list[dict[str, Any]]:
    """The Tools form, one key and one switch per registry entry."""
    described: list[dict[str, Any]] = []
    for provider in PROVIDERS.values():
        key_stored = _key(provider)
        switch = effective_value(provider.enabled_env).strip().lower()
        described.append({
            "key": provider.key_env,
            "label": f"{provider.label} API key",
            "kind": "text",
            "secret": True,
            "required": False,
            "help": provider.key_help,
            "help_url": provider.dashboard_url,
            "configured": bool(key_stored),
            "value": "",
            "preview": masked_value(provider.key_env) if key_stored else None,
        })
        described.append({
            "key": provider.enabled_env,
            "label": f"Offer {provider.label}",
            "kind": "choice",
            "secret": False,
            "required": True,
            "options": ["on", "off"],
            "default": "off",
            "help": (
                "On offers this provider on a video draft after Check accepts "
                "the key. Off keeps the key and hides the provider."
            ),
            "configured": bool(switch),
            "value": switch if switch in {"on", "off"} else "off",
            "preview": None,
        })
    return described


def save_settings(values: dict[str, str]) -> list[str]:
    allowed = {field["key"] for field in describe_fields()}
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise SettingsError(f"Not a video generation setting: {unknown[0]}.")
    by_key = {field["key"]: field for field in describe_fields()}
    cleaned: dict[str, str] = {}
    for key, raw in values.items():
        value = str(raw).strip()
        field = by_key[key]
        if field["secret"]:
            # An empty secret box means "leave the saved key", which is how
            # the form submits a switch change without retyping the key.
            if not value:
                continue
            if len(value) < 10 or any(character.isspace() for character in value):
                raise SettingsError(
                    f"That does not look like an API key for {field['label']}."
                )
        elif value not in field["options"]:
            raise SettingsError(f"Choose on or off for {field['label']}.")
        cleaned[key] = value
    if not cleaned:
        return []
    return write_env_values(cleaned)


def reveal_setting(key: str) -> str:
    field = next((item for item in describe_fields() if item["key"] == key), None)
    if field is None or not field["secret"]:
        raise SettingsError("That setting is not an exposable secret.")
    value = effective_value(key).strip()
    if not value:
        raise SettingsError("No saved value is available for that secret.")
    return value


def _transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout: int,
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def check_provider(provider_id: str, *, transport: Transport | None = None) -> dict[str, Any]:
    """Ask whether the key is accepted. Does not start a video."""
    provider = PROVIDERS.get(provider_id)
    if provider is None:
        raise KeyError(provider_id)
    key = _key(provider)
    if not key:
        outcome = {"ok": False, "message": f"Save a {provider.label} API key first."}
        _store_check(provider_id, **outcome)
        return outcome
    if "/videos" in provider.check_url or "predict" in provider.check_url:
        raise RuntimeError(f"{provider.label} check is pointed at a video endpoint.")
    call = transport or _transport
    headers = {"Accept": "application/json"}
    if provider.id == "gemini":
        headers["x-goog-api-key"] = key
    else:
        headers["Authorization"] = f"Bearer {key}"
    try:
        status, body = call("GET", provider.check_url, headers, None, 20)
    except (OSError, urllib.error.URLError) as error:
        outcome = {"ok": False, "message": f"Could not reach {provider.label}: {error}"}
        _store_check(provider_id, **outcome)
        return outcome
    if 200 <= status < 300:
        outcome = {"ok": True, "message": f"The {provider.label} key was accepted."}
    else:
        detail = body.decode("utf-8", "replace").strip()[:300] or f"HTTP {status}"
        outcome = {"ok": False, "message": f"{provider.label} refused the key: {detail}"}
    _store_check(provider_id, **outcome)
    return outcome


def launch_action(action_id: str) -> dict[str, Any]:
    if not action_id.startswith("check-"):
        raise KeyError(f"video-generation:{action_id}")
    provider_id = action_id.removeprefix("check-")
    if provider_id not in PROVIDERS:
        raise KeyError(f"video-generation:{action_id}")
    outcome = check_provider(provider_id)
    return {
        "status": "ok" if outcome["ok"] else "problem",
        "message": outcome["message"],
    }


def setup_report() -> dict[str, Any]:
    """What is saved, and which providers a video draft would offer."""
    requirements: list[dict[str, str]] = []
    actions: list[dict[str, Any]] = []
    for provider in PROVIDERS.values():
        row = _row(provider)
        requirements.append({
            "id": f"{provider.id}-key",
            "label": f"{provider.label} API key",
            "status": "ready" if row["configured"] else "setup-required",
            "detail": (
                f"{provider.key_env} is set. Its value never leaves the API process."
                if row["configured"]
                else provider.key_help
            ),
        })
        requirements.append({
            "id": f"{provider.id}-switch",
            "label": f"Offer {provider.label}",
            "status": "ready" if row["enabled"] else "optional",
            "detail": (
                "On. A video draft lists it after Check has accepted the key."
                if row["enabled"]
                else "Off. The key stays saved and the provider stays off the draft."
            ),
        })
        if row["check_ok"]:
            check_status, check_detail = "ready", row["check_message"] or "The key was accepted."
        elif row["check_message"]:
            check_status, check_detail = "setup-required", row["check_message"]
        else:
            check_status, check_detail = "setup-required", (
                "Not checked yet. Check asks whether the key is accepted and does not generate a video."
            )
        requirements.append({
            "id": f"{provider.id}-check",
            "label": f"{provider.label} check",
            "status": check_status,
            "detail": check_detail,
        })
        if provider.requires_bucket:
            requirements.append({
                "id": f"{provider.id}-bucket",
                "label": "Cloudflare R2",
                "status": "ready" if row["bucket_ready"] else "setup-required",
                "detail": row["bucket_detail"],
            })
        actions.append({
            "id": f"check-{provider.id}",
            "label": f"Check {provider.label}",
            "kind": "local-launch",
            "requires_confirmation": True,
            "confirm": (
                f"Check the {provider.label} key? This asks the service whether "
                "the key is accepted. It does not generate a video."
            ),
        })
    requirements.append({
        "id": "worker",
        "label": "API worker",
        "status": "optional",
        "detail": (
            "Generation runs as a job, one at a time. The worker has to be "
            "running, and it needs a restart after this tool is added before "
            "it will claim the job. This screen does not start it."
        ),
    })
    actions.append({
        "id": "open-publish",
        "label": "Open Publish",
        "kind": "navigate",
        "href": "/publish",
    })
    return {
        "summary": (
            "Hosts that turn a stored video prompt and a Library image into a "
            "clip. Each one is a key and a switch. A video draft on Attribution "
            "lists the ones that are on and whose check passed. Image drafts "
            "stay a file from outside."
        ),
        "requirements": requirements,
        "actions": actions,
        "configured_secret_names": [
            provider.key_env for provider in PROVIDERS.values() if _key(provider)
        ],
        "supported_secret_names": [provider.key_env for provider in PROVIDERS.values()],
        "secret_previews": {
            provider.key_env: masked_value(provider.key_env)
            for provider in PROVIDERS.values()
            if _key(provider)
        },
        "settings": describe_fields(),
        "settings_title": "Video providers",
        "settings_blurb": (
            "Saved to this machine's local .env and masked here afterwards. "
            "Several providers can be on at once. The draft asks which one to use."
        ),
    }


def _classify(status: int, body: bytes) -> VideoError | None:
    if 200 <= status < 300:
        return None
    text = body.decode("utf-8", "replace").strip()[:500] or f"HTTP {status}"
    lowered = text.lower()
    if "content-moderated" in lowered or "content moderation" in lowered or "safety" in lowered:
        return VideoError("moderation", text)
    if status in {401, 403}:
        return VideoError("auth", text)
    if status == 429 or "rate limit" in lowered or "quota" in lowered:
        return VideoError("quota", text)
    return VideoError("unavailable", text)


def _json_body(body: bytes) -> dict[str, Any]:
    try:
        loaded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VideoError("unavailable", "The video service returned something that is not JSON.") from error
    if not isinstance(loaded, dict):
        raise VideoError("unavailable", "The video service returned an unexpected response.")
    return loaded


def _failure_text(payload: dict[str, Any]) -> VideoError:
    text = json.dumps(payload)[:500]
    lowered = text.lower()
    if "moderat" in lowered or "safety" in lowered:
        return VideoError("moderation", text)
    return VideoError("unavailable", text)


def _sniff_image(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    raise VideoError("unavailable", "The subject image is not a JPEG, PNG, or WebP file.")


def _poll(
    call: Transport,
    method: str,
    url: str,
    headers: dict[str, str],
    *,
    done,
    sleep: Sleep,
    now: Clock,
) -> dict[str, Any]:
    """Poll until `done` returns the payload, or the wait is over.

    A pending status is not a retry of a refusal. A refusal raises and the
    caller stops.
    """
    started = now()
    while True:
        status, body = call(method, url, headers, None, 30)
        failed = _classify(status, body)
        if failed is not None:
            raise failed
        payload = _json_body(body)
        finished = done(payload)
        if finished is not None:
            return finished
        if now() - started >= POLL_TIMEOUT_SECONDS:
            raise VideoError("unavailable", "The video service did not finish in time.")
        sleep(POLL_INTERVAL_SECONDS)


def _xai_headers(key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def generate_xai(
    provider: VideoProvider,
    prompt: str,
    image: bytes,
    mime: str,
    object_key: str,
    *,
    transport: Transport | None = None,
    presign: Callable[..., str] | None = None,
    download: Callable[[str], bytes] | None = None,
    sleep: Sleep = time.sleep,
    now: Clock = time.monotonic,
) -> bytes:
    """Reference-to-video. The still is the garment, not a locked first frame.

    The stored prompt describes a scene that uses the product image. Pinning
    that still as frame zero would open on the catalog photo. One reference
    image keeps the garment and lets the prompt play. The account keeps zero
    data retention, so the file is written to a presigned address in the
    existing bucket and read back from there.
    """
    from trendrelay_api.integrations.media_hosting import get_object, presign_put

    key = _key(provider)
    sign = presign or presign_put
    fetch = download or get_object
    upload_url = sign(object_key, expires_seconds=3600)
    data_uri = f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}"
    payload = {
        "model": provider.model,
        "prompt": prompt,
        "duration": provider.duration_seconds,
        "aspect_ratio": "9:16",
        "resolution": "720p",
        "reference_images": [{"url": data_uri}],
        "output": {"upload_url": upload_url},
    }
    call = transport or _transport
    status, body = call(
        "POST",
        f"{XAI_BASE}/videos/generations",
        _xai_headers(key),
        json.dumps(payload).encode("utf-8"),
        60,
    )
    failed = _classify(status, body)
    if failed is not None:
        raise failed
    request_id = str(_json_body(body).get("request_id") or "").strip()
    if not request_id:
        raise VideoError("unavailable", "The video service did not return a request to follow.")

    def done(polled: dict[str, Any]) -> dict[str, Any] | None:
        state = str(polled.get("status") or "")
        if state == "done":
            return polled
        if state in {"", "pending", "queued", "processing", "in_progress"}:
            return None
        raise _failure_text(polled)

    _poll(
        call,
        "GET",
        f"{XAI_BASE}/videos/{request_id}",
        _xai_headers(key),
        done=done,
        sleep=sleep,
        now=now,
    )
    try:
        found = fetch(object_key)
    except Exception as error:
        raise VideoError("unavailable", f"The video was not in the bucket: {error}") from error
    if not found:
        raise VideoError("unavailable", "The bucket object was empty.")
    return found


def generate_gemini(
    provider: VideoProvider,
    prompt: str,
    image: bytes,
    mime: str,
    object_key: str = "",
    *,
    transport: Transport | None = None,
    sleep: Sleep = time.sleep,
    now: Clock = time.monotonic,
) -> bytes:
    """Image-conditioned video. Gemini returns an address; there is no bucket."""
    # Gemini returns the file itself, so the bucket key the shared adapter
    # passes is unused here.
    del object_key
    key = _key(provider)
    headers = {
        "x-goog-api-key": key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "instances": [{
            "prompt": prompt,
            "image": {
                "bytesBase64Encoded": base64.b64encode(image).decode("ascii"),
                "mimeType": mime,
            },
        }],
        "parameters": {
            "aspectRatio": "9:16",
            "durationSeconds": provider.duration_seconds,
        },
    }
    call = transport or _transport
    status, body = call(
        "POST",
        f"{GEMINI_BASE}/models/{provider.model}:predictLongRunning",
        headers,
        json.dumps(payload).encode("utf-8"),
        60,
    )
    failed = _classify(status, body)
    if failed is not None:
        raise failed
    name = str(_json_body(body).get("name") or "").strip()
    if not name:
        raise VideoError("unavailable", "The video service did not return an operation to follow.")

    def done(polled: dict[str, Any]) -> dict[str, Any] | None:
        if polled.get("error"):
            raise _failure_text(polled)
        if polled.get("done") is True:
            return polled
        return None

    finished = _poll(
        call,
        "GET",
        f"{GEMINI_BASE}/{name}",
        headers,
        done=done,
        sleep=sleep,
        now=now,
    )
    response = finished.get("response") if isinstance(finished.get("response"), dict) else {}
    samples = (response.get("generateVideoResponse") or {}).get("generatedSamples") or []
    uri = ""
    if samples and isinstance(samples[0], dict):
        video = samples[0].get("video") or {}
        uri = str(video.get("uri") or "")
    if not uri:
        videos = response.get("videos") or []
        if videos and isinstance(videos[0], dict):
            uri = str(videos[0].get("uri") or "")
    if not uri:
        raise VideoError("unavailable", "The video service finished without a file address.")
    status, body = call("GET", uri, {"x-goog-api-key": key}, None, 120)
    failed = _classify(status, body)
    if failed is not None:
        raise failed
    if not body:
        raise VideoError("unavailable", "The video download was empty.")
    return body


ADAPTERS: dict[str, Callable[..., bytes]] = {
    "xai": generate_xai,
    "gemini": generate_gemini,
}


def _subject_bytes(session: Session, workspace_id: str, asset_id: str) -> tuple[bytes, str]:
    from trendrelay_api.media_models import MediaAsset

    asset = session.get(MediaAsset, asset_id)
    if asset is None or asset.workspace_id != workspace_id or asset.media_kind != "image":
        raise VideoError("unavailable", "The Library image is no longer available.")
    path = asset.original_path
    try:
        data = open(path, "rb").read(MAX_SUBJECT_BYTES + 1)
    except OSError as error:
        raise VideoError("unavailable", f"The subject image could not be read: {error}") from error
    if len(data) > MAX_SUBJECT_BYTES:
        raise VideoError("unavailable", "The subject image is too large to send.")
    return data, _sniff_image(data)


def _open_jobs(session: Session, workspace_id: str) -> list[DurableJob]:
    return list(session.scalars(
        select(DurableJob)
        .where(
            DurableJob.workspace_key == workspace_id,
            DurableJob.kind == JOB_KIND,
            DurableJob.status.in_(("queued", "running")),
        )
        .order_by(DurableJob.created_at.desc())
    ).all())


def _inflight(session: Session, workspace_id: str, draft_id: str) -> DurableJob | None:
    for item in _open_jobs(session, workspace_id):
        payload = item.payload if isinstance(item.payload, dict) else {}
        if payload.get("source") != "library" and payload.get("draft_id") == draft_id:
            return item
    return None


def _inflight_library(session: Session, workspace_id: str, asset_id: str) -> DurableJob | None:
    for item in _open_jobs(session, workspace_id):
        payload = item.payload if isinstance(item.payload, dict) else {}
        if payload.get("source") == "library" and payload.get("subject_asset_id") == asset_id:
            return item
    return None


def enqueue(
    session: Session,
    workspace_id: str,
    actor_user_id: str,
    draft_id: str,
    provider_id: str,
) -> dict[str, Any]:
    """Queue one generation. Refuses before any request leaves the machine.

    A draft already has at most one generation in flight. A second request
    returns that job and does not start the provider it names.
    """
    from trendrelay_api.product_creative_drafts import get_draft

    if session is not None:
        existing = _inflight(session, workspace_id, draft_id)
        if existing is not None:
            record = get_job_record(existing.id, session=session)
            record["provider_id"] = (existing.payload or {}).get("provider_id")
            return record
    provider = require_ready(provider_id)
    view = get_draft(session, workspace_id, draft_id)
    if view["kind"] != "video":
        raise ValueError("Video generation is for a video draft.")
    if view["status"] != "pending" or int(view["owed"]) <= 0:
        raise ValueError("This draft does not still need a file.")
    subjects = [item for item in view["subject_assets"] if not item.get("missing")]
    if not subjects:
        raise ValueError("Choose a Library image for this draft before generating.")
    asset_id = str(subjects[0]["asset_id"])
    # Read it now so a missing file is a refusal, not a job that fails later
    # for a reason the operator could have been told before anything was queued.
    try:
        _subject_bytes(session, workspace_id, asset_id)
    except VideoError as error:
        raise ValueError(str(error)) from error
    nonce = f"{workspace_id}:{draft_id}:{provider.id}:{datetime.now(UTC).isoformat()}"
    job_id = "vid_" + hashlib.sha256(nonce.encode()).hexdigest()[:16]
    record = create_job_record(
        job_id,
        workspace_id,
        JOB_KIND,
        {
            "draft_id": draft_id,
            "provider_id": provider.id,
            "provider_label": provider.label,
            "workspace_id": workspace_id,
            "actor_user_id": actor_user_id,
            "subject_asset_id": asset_id,
        },
        max_attempts=1,
        session=session,
    )
    record["provider_id"] = provider.id
    return record


def enqueue_library(
    session: Session,
    workspace_id: str,
    actor_user_id: str,
    asset_id: str,
    provider_id: str,
    prompt: str,
) -> dict[str, Any]:
    """Queue one clip from a Library image. The operator wrote the prompt.

    One image has at most one library generation in flight. A second request
    returns that job and does not start the provider it names, and does not
    replace the prompt already stored on it.
    """
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt.strip()) > 4000:
        raise ValueError("Write a prompt of at most 4000 characters.")
    text = prompt.strip()
    if session is not None:
        existing = _inflight_library(session, workspace_id, asset_id)
        if existing is not None:
            record = get_job_record(existing.id, session=session)
            record["provider_id"] = (existing.payload or {}).get("provider_id")
            return record
    provider = require_ready(provider_id)
    try:
        _subject_bytes(session, workspace_id, asset_id)
    except VideoError as error:
        raise ValueError(str(error)) from error
    nonce = f"{workspace_id}:{asset_id}:{provider.id}:{datetime.now(UTC).isoformat()}"
    job_id = "vid_" + hashlib.sha256(nonce.encode()).hexdigest()[:16]
    record = create_job_record(
        job_id,
        workspace_id,
        JOB_KIND,
        {
            "source": "library",
            "subject_asset_id": asset_id,
            "asset_id": asset_id,
            "prompt": text,
            "provider_id": provider.id,
            "provider_label": provider.label,
            "workspace_id": workspace_id,
            "actor_user_id": actor_user_id,
        },
        max_attempts=1,
        session=session,
    )
    record["provider_id"] = provider.id
    return record


def generation_status(session: Session, workspace_id: str, draft_id: str) -> dict[str, Any]:
    """The latest generation for this draft, or none."""
    from trendrelay_api.product_creative_drafts import get_draft

    get_draft(session, workspace_id, draft_id)
    rows = session.scalars(
        select(DurableJob)
        .where(
            DurableJob.workspace_key == workspace_id,
            DurableJob.kind == JOB_KIND,
        )
        .order_by(DurableJob.created_at.desc())
    ).all()
    for item in rows:
        payload = item.payload if isinstance(item.payload, dict) else {}
        if payload.get("source") == "library" or payload.get("draft_id") != draft_id:
            continue
        result = item.result if isinstance(item.result, dict) else {}
        return {
            "id": item.id,
            "status": item.status,
            "provider_id": payload.get("provider_id"),
            "provider_label": payload.get("provider_label"),
            "error": item.last_error,
            "error_code": result.get("error_code"),
            "asset_id": result.get("asset_id"),
        }
    return {"status": "none"}


def library_generation_status(
    session: Session, workspace_id: str, asset_id: str,
) -> dict[str, Any]:
    """The latest library generation for this image, or none."""
    from trendrelay_api.media_models import MediaAsset

    asset = session.get(MediaAsset, asset_id)
    if asset is None or asset.workspace_id != workspace_id:
        raise LookupError("Library asset not found.")
    rows = session.scalars(
        select(DurableJob)
        .where(
            DurableJob.workspace_key == workspace_id,
            DurableJob.kind == JOB_KIND,
        )
        .order_by(DurableJob.created_at.desc())
    ).all()
    for item in rows:
        payload = item.payload if isinstance(item.payload, dict) else {}
        if payload.get("source") != "library" or payload.get("subject_asset_id") != asset_id:
            continue
        result = item.result if isinstance(item.result, dict) else {}
        return {
            "id": item.id,
            "status": item.status,
            "provider_id": payload.get("provider_id"),
            "provider_label": payload.get("provider_label"),
            "error": item.last_error,
            "error_code": result.get("error_code"),
            "asset_id": result.get("asset_id"),
        }
    return {"status": "none"}


def _file_library_clip(
    workspace_id: str,
    actor_user_id: str,
    data: bytes,
    title: str,
) -> str:
    """Ingest a finished clip as a Library video. No product link."""
    from trendrelay_api.integrations.mcp import intake
    from trendrelay_api.media_library import (
        JOB_SESSION_FACTORY,
        create_ingest_job,
        run_ingest_job,
    )
    from trendrelay_api.media_models import MediaAsset
    from trendrelay_api.product_creative_drafts import _upload_root

    sniffed = intake._sniff_media_type(data)
    suffix = intake._VIDEO_TYPES.get(sniffed or "")
    if not suffix:
        raise VideoError("unavailable", "The provider did not return a video.")
    if len(data) > intake.MAX_VIDEO_BYTES:
        raise VideoError("unavailable", "The video is larger than this server accepts.")
    digest = hashlib.sha256(data).hexdigest()
    root = _upload_root()
    root.mkdir(parents=True, exist_ok=True)
    saved = root / f"{digest[:20]}{suffix}"
    saved.write_bytes(data)
    label = (title or "Video").strip()[:300] or "Video"
    job = create_ingest_job(
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        path=str(saved),
        title=label,
        source_type="video-generation",
        source_sha256=digest,
        factory=JOB_SESSION_FACTORY,
    )
    if not (job.get("status") == "succeeded" and job.get("asset_id")):
        job_id = job.get("id")
        if not job_id or job.get("status") != "queued":
            raise RuntimeError(job.get("error") or "Import did not produce a Library asset.")
        job = run_ingest_job(
            str(job_id),
            worker_id=f"video-generation-{digest[:12]}",
            factory=JOB_SESSION_FACTORY,
        )
    result = job.get("result") or {}
    asset_id = job.get("asset_id") or result.get("asset_id")
    if job.get("status") != "succeeded" or not asset_id:
        raise RuntimeError(job.get("error") or "Import did not produce a Library asset.")
    with JOB_SESSION_FACTORY() as check:
        asset = check.get(MediaAsset, asset_id)
        if asset is None or asset.workspace_id != workspace_id:
            raise RuntimeError("Import did not produce a Library asset in this workspace.")
    return str(asset_id)


def _run_adapter(
    provider: VideoProvider,
    prompt: str,
    image: bytes,
    mime: str,
    object_key: str,
    transport: Transport | None,
) -> bytes:
    adapter = ADAPTERS.get(provider.id)
    if adapter is None:
        raise VideoError("unavailable", f"{provider.label} has no adapter.")
    return adapter(provider, prompt, image, mime, object_key, transport=transport)


def run_job(
    job_id: str,
    worker_id: str = "video-generation-worker",
    *,
    factory: sessionmaker[Session] | None = None,
    transport: Transport | None = None,
) -> None:
    """Claim one generation, call only that provider, and file the video.

    max_attempts is 1 and a VideoError is not retried. A moderation refusal
    in particular must not be sent again, to this provider or to another.
    """
    from trendrelay_api.database import SessionFactory
    from trendrelay_api.product_creative_drafts import get_draft, submit_media

    session_factory = factory or SessionFactory
    try:
        record = claim_job(job_id, worker_id, lease_seconds=LEASE_SECONDS, factory=session_factory)
    except (FileNotFoundError, PermissionError):
        return
    payload = record["payload"] if isinstance(record.get("payload"), dict) else {}
    provider_id = str(payload.get("provider_id") or "")
    try:
        provider = PROVIDERS[provider_id]
        if payload.get("source") == "library":
            prompt = payload.get("prompt")
            if not isinstance(prompt, str) or not prompt.strip():
                raise VideoError("unavailable", "This generation has no prompt.")
            asset_id = str(payload.get("subject_asset_id") or "")
            with session_factory() as session:
                image, mime = _subject_bytes(session, payload["workspace_id"], asset_id)
            object_key = f"library-clips/{asset_id}/{job_id}.mp4"
            data = _run_adapter(provider, prompt, image, mime, object_key, transport)
            filed_id = _file_library_clip(
                str(payload.get("workspace_id") or ""),
                str(payload.get("actor_user_id") or ""),
                data,
                f"{provider.label} video",
            )
            complete_job(
                job_id,
                worker_id,
                {
                    "provider_id": provider.id,
                    "asset_id": filed_id,
                    "error_code": None,
                },
                factory=session_factory,
            )
        else:
            with session_factory() as session:
                view = get_draft(session, payload["workspace_id"], payload["draft_id"])
                image, mime = _subject_bytes(
                    session, payload["workspace_id"], payload["subject_asset_id"],
                )
                prompt = str(view["prompt"])
            object_key = f"product-clips/{payload['draft_id']}/{job_id}.mp4"
            data = _run_adapter(provider, prompt, image, mime, object_key, transport)
            encoded = base64.b64encode(data).decode("ascii")
            with session_factory() as session:
                filed = submit_media(
                    session,
                    payload["workspace_id"],
                    payload["actor_user_id"],
                    payload["draft_id"],
                    media_base64=encoded,
                    filename=f"{provider.id}-clip.mp4",
                )
            complete_job(
                job_id,
                worker_id,
                {
                    "provider_id": provider.id,
                    "draft_id": payload["draft_id"],
                    "asset_id": filed.get("asset_id"),
                    "error_code": None,
                },
                factory=session_factory,
            )
    except VideoError as error:
        fail_job(
            job_id,
            worker_id,
            f"{error.code}: {error}",
            retry_allowed=False,
            factory=session_factory,
        )
    except Exception as error:
        fail_job(
            job_id,
            worker_id,
            f"unavailable: {error}",
            retry_allowed=False,
            factory=session_factory,
        )
