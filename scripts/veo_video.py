"""Make Attribution video clips with Veo through Gemini Web, start to finish, and check them.

This is `docs/sop-video-generation.md` as a command. Every gate in the SOP is a step here, so
a run either ends with a verified clip in the Library or says which gate failed and why.

    python scripts/veo_video.py doctor                 # no spend: cookie, slot, bridge, API, Check
    python scripts/veo_video.py slots [--apply]        # no spend: which Google account has video
    python scripts/veo_video.py generate --confirm     # spends one video: newest pending draft
    python scripts/veo_video.py generate --confirm --draft <id>
    python scripts/veo_video.py verify --asset <id>    # no spend: tags, file, audio, size

`generate` queues the draft through the API exactly as the Attribution button does, so the
worker files the clip and tags it. It never resends a request, never falls back to another
provider, and does not start or stop the API or the worker (they are the operator's). It does
start the Gemini Web bridge when that is not answering, because the bridge is ours.

Exit codes: 0 done, 1 a gate failed before anything was spent, 2 refused (no --confirm),
3 the generation failed, 4 the clip was made but a check on it failed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

if (
    __name__ == "__main__"
    and VENV_PYTHON.is_file()
    and Path(sys.executable).resolve() != VENV_PYTHON.resolve()
):
    raise SystemExit(subprocess.call([str(VENV_PYTHON), __file__, *sys.argv[1:]]))

API_SOURCE = ROOT / "services" / "api" / "src"
if str(API_SOURCE) not in sys.path:
    sys.path.insert(0, str(API_SOURCE))

HOME = ROOT / ".data" / "gemini-web"
BINARY = HOME / ("bridge.exe" if os.name == "nt" else "bridge")
LAUNCHER = ROOT / "scripts" / "gemini_web_bridge.py"
API_BASE_DEFAULT = os.environ.get("TRENDRELAY_API_URL", "http://127.0.0.1:8011")
BRIDGE_PORT = 4981
PROBE_PORT = 4982
PROBE_LOG = HOME / "probe.log"
#: A clip arrives in about 80 s. The bridge gives up at 10 minutes; so does this wait.
GENERATION_WAIT_SECONDS = 15 * 60
POLL_SECONDS = 10
WANTED_TAGS = ("ai-generated", "ai-video", "veo", "gemini-web")
MAKER = "Veo (Gemini Web)"
MIN_SECONDS, MAX_SECONDS = 5.0, 15.0


class Gate(Exception):
    """A step failed. The message says what, and what to do."""

    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------- pure helpers (tested)


def best_slot(models_by_slot: dict[int, list[str]]) -> int | None:
    """The account slot that has video: the one listing the most models beyond the free one.

    A free account lists only `gemini-auto`; a plan with Veo lists the Pro models too. A slot
    that errored lists nothing. Ties go to the lower slot, which is what the bridge would use.
    """
    ranked = [
        (len([name for name in models if name != "gemini-auto"]), -slot, slot)
        for slot, models in models_by_slot.items()
        if models
    ]
    ranked = [item for item in ranked if item[0] > 0]
    return max(ranked)[2] if ranked else None


def verify_clip(
    asset: dict[str, Any], recipe: str, downloaded: int | None, embedded: bool | None = True,
) -> list[str]:
    """What is wrong with a filed clip. Empty means every Gate C check holds."""
    problems: list[str] = []
    if asset.get("media_kind") != "video":
        problems.append(f"it is a {asset.get('media_kind')!r}, not a video")
    tags = {str(tag).casefold() for tag in (asset.get("hashtags") or [])}
    wanted = [*WANTED_TAGS, *([recipe] if recipe else [])]
    missing = [tag for tag in wanted if tag.casefold() not in tags]
    if missing:
        problems.append("missing tags: " + ", ".join(missing))
    if (asset.get("width"), asset.get("height")) != (720, 1280):
        problems.append(f"size is {asset.get('width')}x{asset.get('height')}, not 720x1280")
    if asset.get("video_codec") != "h264":
        problems.append(f"video codec is {asset.get('video_codec')!r}, not h264")
    if not asset.get("has_audio"):
        problems.append("it has no audio")
    seconds = (asset.get("duration_ms") or 0) / 1000
    if not MIN_SECONDS <= seconds <= MAX_SECONDS:
        problems.append(f"it runs {seconds:.1f} s, outside {MIN_SECONDS:g}-{MAX_SECONDS:g} s")
    if asset.get("creator") != MAKER:
        problems.append(f"creator is {asset.get('creator')!r}, not {MAKER!r}")
    if not str(asset.get("title") or "").endswith(MAKER):
        problems.append(f"the title {asset.get('title')!r} does not name its maker ({MAKER})")
    if embedded is False:
        problems.append("the file itself does not say it is AI-generated (no provenance metadata)")
    size = asset.get("size_bytes")
    if downloaded is None or not size or downloaded != size:
        problems.append(f"the Library file downloads {downloaded} bytes, expected {size}")
    return problems


# ---------------------------------------------------------------- http


def request(
    base: str, method: str, path: str, payload: dict | None = None, timeout: int = 60,
) -> tuple[int, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    call = urllib.request.Request(
        base.rstrip("/") + path, data=data, method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(call, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    except (OSError, urllib.error.URLError) as error:
        raise Gate(
            f"{base} is not answering ({error}). Starting it is the operator's job."
        ) from error
    try:
        return status, (json.loads(raw.decode("utf-8")) if raw else {})
    except (UnicodeDecodeError, json.JSONDecodeError):
        return status, raw


def download_size(base: str, path: str) -> int | None:
    try:
        with urllib.request.urlopen(base.rstrip("/") + path, timeout=120) as response:
            return len(response.read())
    except (OSError, urllib.error.URLError):
        return None


# ---------------------------------------------------------------- bridge


def bridge_models(port: int) -> list[str] | None:
    """Model ids the bridge lists, [] when signed out, None when it is not answering."""
    try:
        status, body = request(f"http://127.0.0.1:{port}", "GET", "/openai/v1/models", timeout=8)
    except Gate:
        return None
    if status != 200 or not isinstance(body, dict):
        return None
    return [str(item.get("id")) for item in (body.get("data") or []) if isinstance(item, dict)]


def start_probe(slot: int) -> subprocess.Popen:
    """A throwaway bridge on a spare port for one account slot. Shares nothing with the real one."""
    env = {**os.environ, "PORT": str(PROBE_PORT)}
    for folder in ("gotmp",):
        (HOME / folder).mkdir(parents=True, exist_ok=True)
    env.update(TMP=str(HOME / "gotmp"), TEMP=str(HOME / "gotmp"))
    if slot:
        env["GEMINI_AUTH_USER"] = str(slot)
    else:
        env.pop("GEMINI_AUTH_USER", None)
    with open(PROBE_LOG, "wb") as log:
        return subprocess.Popen([str(BINARY)], cwd=HOME, env=env, stdout=log, stderr=log)


def probe_slots(slots: range) -> dict[int, list[str]]:
    if not BINARY.is_file():
        raise Gate(f"The bridge is not built yet. Run: python {LAUNCHER.relative_to(ROOT)}")
    found: dict[int, list[str]] = {}
    expired = 0
    for slot in slots:
        process = start_probe(slot)
        try:
            models: list[str] = []
            for _ in range(12):
                time.sleep(2)
                listed = bridge_models(PROBE_PORT)
                if listed:
                    models = listed
                    break
                if process.poll() is not None:
                    break
            found[slot] = models
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        if PROBE_LOG.is_file() and b"invalid or expired" in PROBE_LOG.read_bytes():
            expired += 1
    if expired == len(found):
        raise Gate(
            "The saved cookie no longer signs in (Google rotates it, so a copy goes stale within "
            "hours). Copy a fresh Cookie header into Tools > Video providers > Gemini Web, save, "
            "then run `doctor --restart-bridge`. Do not generate until then: a bridge whose "
            "session has gone stale is served as a guest, so Gemini accepts a video prompt and "
            "never delivers it."
        )
    return found


def ensure_bridge(restart: bool = False) -> list[str]:
    """The main bridge, started when it is not answering. Returns the models it lists."""
    if restart and shutil.which("taskkill") and BINARY.name:
        subprocess.run(["taskkill", "/F", "/IM", BINARY.name], capture_output=True, check=False)
        time.sleep(2)
    models = bridge_models(BRIDGE_PORT)
    if models:
        return models
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    log = open(HOME / "bridge.log", "ab")  # noqa: SIM115 - handed to the child for its lifetime
    subprocess.Popen(
        [sys.executable, str(LAUNCHER)], cwd=ROOT, stdout=log, stderr=log, creationflags=flags,
    )
    answered = 0
    for _ in range(90):  # a first run builds it
        time.sleep(4)
        models = bridge_models(BRIDGE_PORT)
        if models:
            break
        # It answers before its Gemini client has signed in, so an empty list right after the
        # start is not yet a failure. Give it a short grace, then call it not signed in.
        if models is not None:
            answered += 1
            if answered >= 6:
                break
    if not models:
        raise Gate(
            "The bridge is up but not signed in, or did not start. Save a fresh cookie in "
            "Tools > Video providers > Gemini Web, then run this again with --restart-bridge."
        )
    return models


# ---------------------------------------------------------------- steps


def settings_gate() -> dict[str, str]:
    from trendrelay_api.integrations import video_generation as video

    state = {
        "cookie": "saved" if video.effective_value("GEMINI_WEB_COOKIES") else "",
        "enabled": video.effective_value("VIDEO_PROVIDER_GEMINI_WEB_ENABLED"),
        "slot": video.effective_value("GEMINI_WEB_AUTH_USER"),
    }
    if not state["cookie"]:
        raise Gate("No cookie is saved. Tools > Video providers > Gemini Web > paste it and save.")
    if state["enabled"] != "on":
        raise Gate("Gemini Web is switched off. Switch 'Offer Gemini Web' on in Tools and save.")
    if not (HOME / ".env").is_file():
        raise Gate("The bridge env file is missing. Save the Gemini Web card in Tools once.")
    return state


def cookie_gate(slot: str) -> None:
    """Stop before spending when the saved cookie no longer signs in. No spend.

    The running bridge cannot tell us: its /health is a fixed "ok" and /models answers from a
    list it cached when it started. A session that has gone stale is served by Gemini as a guest:
    chats and video prompts are accepted ("I'm generating your video") and saved nowhere, so
    the clip never arrives and the quota may still be counted. A throwaway bridge started from
    the saved cookie fails to sign in ("invalid or expired") in exactly that case.
    """
    number = int(slot) if slot.isdigit() else 0
    found = probe_slots(range(number, number + 1))  # raises when the cookie is stale
    if not found.get(number):
        raise Gate(
            f"Account slot {number} did not sign in with the saved cookie. Check the slot with "
            "`slots`, or paste a fresh cookie in Tools."
        )


def workspace_of(base: str, requested: str | None) -> str:
    if requested:
        return requested
    _status, body = request(base, "GET", "/api/workspaces")
    rows = body.get("workspaces") if isinstance(body, dict) else None
    ids = [str(row["id"]) for row in (rows or []) if isinstance(row, dict) and row.get("id")]
    if len(ids) != 1:
        raise Gate("Pass --workspace. Workspaces answering: " + (", ".join(ids) or "none"))
    return ids[0]


def api_gate(base: str, workspace: str) -> None:
    _status, body = request(base, "GET", f"/api/workspaces/{workspace}/attribution/video-providers")
    ids = [row.get("id") for row in (body.get("providers") if isinstance(body, dict) else []) or []]
    if "gemini_web" not in ids:
        raise Gate(
            "The API does not offer Gemini Web. Run Check in Tools; if it passes, the worker and "
            "API need the operator to restart them so they load the provider."
        )


def pick_draft(base: str, workspace: str, requested: str | None) -> dict[str, Any]:
    root = f"/api/workspaces/{workspace}/attribution/creative-drafts"
    if requested:
        _status, body = request(base, "GET", f"{root}/{requested}")
        draft = body.get("draft") if isinstance(body, dict) else None
    else:
        _status, body = request(base, "GET", f"{root}?kind=video&status=pending&limit=50")
        rows = (body.get("drafts") or body.get("items") or []) if isinstance(body, dict) else []
        ready = [row for row in rows if row.get("subject_assets") and not row.get("together")]
        draft = ready[0] if ready else None
    if not isinstance(draft, dict) or draft.get("kind") != "video":
        raise Gate("No pending single-product video draft with a Library image was found.")
    if draft.get("status") != "pending":
        raise Gate(f"Draft {draft.get('id')} is {draft.get('status')}, not pending.")
    return draft


def wait_for_generation(base: str, workspace: str, draft_id: str) -> dict[str, Any]:
    path = f"/api/workspaces/{workspace}/attribution/creative-drafts/{draft_id}/generation"
    deadline = time.monotonic() + GENERATION_WAIT_SECONDS
    last = ""
    while time.monotonic() < deadline:
        _status, body = request(base, "GET", path)
        generation = body.get("generation") if isinstance(body, dict) else {}
        state = str((generation or {}).get("status"))
        if state != last:
            print(f"  job {generation.get('id')}: {state}", flush=True)
            last = state
        if state == "succeeded":
            return generation
        if state in {"failed", "cancelled"}:
            raise Gate(
                f"{generation.get('error')} ({generation.get('error_code')}). Nothing was resent. "
                "Look at the SOP's failure table before trying again; each attempt can use quota.",
                code=3,
            )
        time.sleep(POLL_SECONDS)
    raise Gate(
        "The job was still running after 15 minutes. Check it again later; do not requeue.", 3,
    )


def verify(base: str, workspace: str, asset_id: str, recipe: str) -> list[str]:
    library = f"/api/workspaces/{workspace}/media/library/assets/{asset_id}"
    _status, body = request(base, "GET", library)
    asset = body.get("asset", body) if isinstance(body, dict) else {}
    size = download_size(base, f"{library}/content/original")
    clip = fetch_original(base, workspace, asset_id)
    embedded = says_generated(clip) if clip else None
    problems = verify_clip(asset, recipe, size, embedded)
    _status, found = request(
        base, "GET", f"/api/workspaces/{workspace}/media/library/assets?q=gemini-web&limit=50",
    )
    rows = (found.get("items") or found.get("assets") or []) if isinstance(found, dict) else []
    if asset_id not in {row.get("id") for row in rows}:
        problems.append("a Library search for 'gemini-web' does not find it")
    return problems


def ffmpeg_path() -> str | None:
    """The project's bundled ffmpeg, else one on PATH."""
    from trendrelay_api.integrations.effects import FFMPEG

    return str(FFMPEG) if Path(str(FFMPEG)).is_file() else shutil.which("ffmpeg")


def fetch_original(base: str, workspace: str, asset_id: str) -> Path | None:
    """The Library's original file, saved beside the bridge's files for inspection."""
    target = HOME / f"verify-{asset_id}.mp4"
    try:
        with urllib.request.urlopen(
            f"{base.rstrip('/')}/api/workspaces/{workspace}/media/library/assets/{asset_id}"
            "/content/original", timeout=120,
        ) as response:
            target.write_bytes(response.read())
    except (OSError, urllib.error.URLError):
        return None
    return target


def says_generated(clip: Path) -> bool | None:
    """Whether the file's own metadata says it is AI-generated. None when ffmpeg is missing."""
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        return None
    shown = subprocess.run(
        [ffmpeg, "-i", str(clip)], capture_output=True, text=True, check=False,
    ).stderr
    return "ai_generated" in shown and "AI-generated" in shown


def grab_frame(clip: Path | None) -> Path | None:
    """One frame beside the file, for the human glance the SOP asks for. Best effort."""
    ffmpeg = ffmpeg_path()
    if clip is None or not ffmpeg:
        return None
    frame = clip.with_suffix(".jpg")
    done = subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-ss", "4", "-i", str(clip),
         "-frames:v", "1", "-vf", "scale=360:-1", str(frame)],
        capture_output=True, check=False,
    )
    return frame if done.returncode == 0 and frame.is_file() else None


# ---------------------------------------------------------------- commands


def cmd_doctor(args: argparse.Namespace) -> int:
    from trendrelay_api.integrations import video_generation as video

    state = settings_gate()
    print(f"settings: cookie {state['cookie']}, enabled, account slot {state['slot'] or '(none)'}")
    cookie_gate(state["slot"])
    print("cookie: the saved cookie signs in")
    models = ensure_bridge(args.restart_bridge)
    print(f"bridge: signed in, {len(models)} models: {', '.join(models)}")
    if models == ["gemini-auto"]:
        raise Gate(
            "This account slot lists only the free model, so it cannot make video. "
            "Run: python scripts/veo_video.py slots --apply, then --restart-bridge."
        )
    checked = video.check_provider("gemini_web")
    print(f"check: {checked['message']}")
    if not checked["ok"]:
        raise Gate("Check failed; see the message above.")
    workspace = workspace_of(args.api_base, args.workspace)
    api_gate(args.api_base, workspace)
    print(f"api: {args.api_base} offers Gemini Web in workspace {workspace}")
    print("doctor: every gate before spending passes")
    return 0


def cmd_slots(args: argparse.Namespace) -> int:
    from trendrelay_api.integrations import video_generation as video

    found = probe_slots(range(args.max_slots))
    for slot, models in found.items():
        print(f"slot {slot}: {', '.join(models) if models else 'not usable'}")
    winner = best_slot(found)
    if winner is None:
        raise Gate("No slot lists a model beyond the free one. Sign in to an account with Veo.")
    print(f"best: slot {winner}")
    if args.apply:
        video.save_settings({"GEMINI_WEB_AUTH_USER": str(winner)})
        print("saved. Restart the bridge to use it: --restart-bridge on the next command.")
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    if not args.confirm:
        print("Refused: this spends one video from the account's daily quota. Add --confirm.")
        return 2
    cookie_gate(settings_gate()["slot"])
    models = ensure_bridge(args.restart_bridge)
    if models == ["gemini-auto"]:
        raise Gate("The saved slot is the free account; run `slots --apply` first.")
    workspace = workspace_of(args.api_base, args.workspace)
    api_gate(args.api_base, workspace)
    draft = pick_draft(args.api_base, workspace, args.draft)
    draft_id, recipe = str(draft["id"]), str(draft.get("recipe") or "")
    print(f"queueing {draft_id} ({recipe}) with Gemini Web")
    _status, queued = request(
        args.api_base, "POST",
        f"/api/workspaces/{workspace}/attribution/creative-drafts/{draft_id}/generate",
        {"provider_id": "gemini_web", "confirm_external_action": True},
    )
    if not isinstance(queued, dict) or "job" not in queued:
        raise Gate(f"The API did not queue the job: {queued}")
    generation = wait_for_generation(args.api_base, workspace, draft_id)
    asset_id = str(generation.get("asset_id") or "")
    if not asset_id:
        raise Gate("The job succeeded but named no Library file.", 4)
    problems = verify(args.api_base, workspace, asset_id, recipe)
    frame = grab_frame(HOME / f"verify-{asset_id}.mp4")
    print(f"library asset: {asset_id}")
    if frame:
        print(f"frame to compare with the product picture: {frame}")
    if problems:
        raise Gate("The clip is filed but fails: " + "; ".join(problems), 4)
    print("generate: filed, tagged and verified")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    workspace = workspace_of(args.api_base, args.workspace)
    problems = verify(args.api_base, workspace, args.asset, args.recipe or "")
    frame = grab_frame(HOME / f"verify-{args.asset}.mp4")
    if frame:
        print(f"frame: {frame}")
    if problems:
        raise Gate("; ".join(problems), 4)
    print("verify: every check holds")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--api-base", default=API_BASE_DEFAULT)
    parser.add_argument("--workspace")
    parser.add_argument("--restart-bridge", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor").set_defaults(run=cmd_doctor)
    slots = commands.add_parser("slots")
    slots.add_argument("--apply", action="store_true")
    slots.add_argument("--max-slots", type=int, default=4)
    slots.set_defaults(run=cmd_slots)
    generate = commands.add_parser("generate")
    generate.add_argument("--confirm", action="store_true")
    generate.add_argument("--draft")
    generate.set_defaults(run=cmd_generate)
    check = commands.add_parser("verify")
    check.add_argument("--asset", required=True)
    check.add_argument("--recipe")
    check.set_defaults(run=cmd_verify)
    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except Gate as gate:
        print(f"STOP: {gate}")
        return gate.code


if __name__ == "__main__":
    sys.exit(main())
