"""File Attribution video creatives through xAI, one draft at a time.

The stored prompt is sent as confirmed. This script does not rewrite it,
does not publish, and does not start or stop the API or the worker.

    python scripts/fill_creative_videos.py setup-zdr
    python scripts/fill_creative_videos.py fill --draft <draft_id>
    python scripts/fill_creative_videos.py fill --product <id> --subject-asset <id> --recipe <recipe>

`fill` spends one video generation per draft that does not already have a
clip. Run it when the operator has asked for that clip.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

if __name__ == "__main__" and VENV_PYTHON.is_file() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    raise SystemExit(subprocess.call([str(VENV_PYTHON), __file__, *sys.argv[1:]]))

API_SOURCE = ROOT / "services" / "api" / "src"
if str(API_SOURCE) not in sys.path:
    sys.path.insert(0, str(API_SOURCE))

from trendrelay_api.product_creative_recipes import RECIPES  # noqa: E402

API_BASE_DEFAULT = "http://127.0.0.1:8011"
LISTING_FIELDS = ["title", "description", "gallery"]
CLIP_DIR = ROOT / ".data" / "creative-videos"
PROMPT_WAIT_SECONDS = 45
PROMPT_POLL_SECONDS = 2
#: A signed upload address carries its credentials in the query string.
_QUERY = re.compile(r"(https?://[^\s\"'?#]+)\?[^\s\"']*")

#: Operator rules that have to already be in the stored prompt. A miss means
#: the API process is still serving an older copy of the recipe module.
_SINGLE_FAN = (
    "only one product, so this is the only pass",
    "until late in the clip",
    "stay where they were",
    "thick, matte",
    "No nipples are visible",
    "camel toe",
)
_HIKARU = (
    "thick, matte",
    "No nipples are visible",
    "camel toe",
    "not a doll",
    "phone-camera detail",
)

VIDEO_RECIPES = tuple(
    name for name, spec in RECIPES.items() if "video" in spec["kinds"]
)


class FillFailure(Exception):
    """One draft that this command will not keep trying."""

    def __init__(self, code: str, message: str, *, draft_id: str = "", retry: bool = False) -> None:
        self.code = code
        self.draft_id = draft_id
        self.retry = retry
        super().__init__(message)


def public_text(text: str, limit: int = 300) -> str:
    """One short line safe to print. Query strings are dropped."""
    return _QUERY.sub(r"\1", " ".join(str(text).split()))[:limit]


def missing_phrases(recipe: str, prompt: str, *, together: bool) -> list[str]:
    """Phrases the stored prompt must already contain, or an empty list."""
    if recipe == "ceiling_fan_transition" and not together:
        needed = _SINGLE_FAN
    elif recipe in {"hikaru_walk", "hikaru_lookbook"}:
        needed = _HIKARU
    else:
        needed = ()
    return [item for item in needed if item not in prompt]


def stale_message(missing: list[str]) -> str:
    shown = "; ".join(missing)
    return (
        "The stored prompt is missing: "
        + shown
        + ". The API is still serving the previous recipe text. "
        "Wait for the dev supervisor to reload services/api/src, then run this command again. "
        "Do not edit the prompt."
    )


def _toml_basic(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise FillFailure("setup-zdr", "A Publish setting contains a newline.")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def setup_zdr() -> int:
    """Write the Grok Build bucket file when it is missing.

    The file has to live under the home directory because that is the only
    place Grok Build reads it. `fill` does not read it. A restart of Grok
    Build is left to the operator.
    """
    from trendrelay_api.env_store import effective_value

    path = Path.home() / ".grok" / "managed_config.toml"
    if path.is_file() and path.stat().st_size > 0:
        print(f"present {path}")
        print("fill does not read this file. Grok video tools load it only after Grok Build is restarted.")
        return 0
    values = {
        "R2_ACCOUNT_ID": effective_value("R2_ACCOUNT_ID").strip(),
        "R2_BUCKET": effective_value("R2_BUCKET").strip(),
        "R2_ACCESS_KEY_ID": effective_value("R2_ACCESS_KEY_ID").strip(),
        "R2_SECRET_ACCESS_KEY": effective_value("R2_SECRET_ACCESS_KEY").strip(),
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        print("FAIL setup-zdr - setup-zdr missing " + " ".join(missing))
        return 1
    endpoint = f"https://{values['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com"
    body = "\n".join([
        "# Written by scripts/fill_creative_videos.py setup-zdr.",
        "# Grok Build reads this only after a restart. fill does not.",
        "",
        "[tools.zdr_video_output_s3]",
        f"bucket = {_toml_basic(values['R2_BUCKET'])}",
        f"endpoint = {_toml_basic(endpoint)}",
        'region = "auto"',
        'key_prefix = "product-clips/"',
        "expires_secs = 3600",
        "",
        "[tools.zdr_video_output_s3.read_write]",
        f"access_key_id = {_toml_basic(values['R2_ACCESS_KEY_ID'])}",
        f"secret_access_key = {_toml_basic(values['R2_SECRET_ACCESS_KEY'])}",
        "",
        "[tools.zdr_video_output_s3.read_only]",
        f"access_key_id = {_toml_basic(values['R2_ACCESS_KEY_ID'])}",
        f"secret_access_key = {_toml_basic(values['R2_SECRET_ACCESS_KEY'])}",
        "",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    print(f"wrote {path}")
    print("Restart Grok Build before using its video tools. This command does not restart it.")
    return 0


def _request(api_base: str, method: str, path: str, payload: dict | None = None, timeout: int = 180) -> tuple[int, dict]:
    url = api_base.rstrip("/") + path
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            raw = response.read()
    except urllib.error.HTTPError as error:
        status = error.code
        raw = error.read()
    except (OSError, urllib.error.URLError) as error:
        raise FillFailure(
            "api",
            f"The API at {api_base} is not answering. Do not start it. {public_text(error)}",
            retry=True,
        ) from error
    try:
        loaded = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FillFailure("api", f"The API returned something that is not JSON (HTTP {status}).") from error
    if not isinstance(loaded, dict):
        raise FillFailure("api", f"The API returned an unexpected response (HTTP {status}).")
    if status >= 400:
        detail = loaded.get("detail") if isinstance(loaded.get("detail"), str) else f"HTTP {status}"
        raise FillFailure("api", public_text(detail), retry=status >= 500)
    return status, loaded


def workspace_id(api_base: str, requested: str | None) -> str:
    if requested:
        return requested
    _status, body = _request(api_base, "GET", "/api/workspaces", timeout=30)
    rows = body.get("workspaces") or []
    if len(rows) == 1 and isinstance(rows[0], dict) and rows[0].get("id"):
        return str(rows[0]["id"])
    found = [str(item.get("id")) for item in rows if isinstance(item, dict) and item.get("id")]
    raise FillFailure(
        "workspace",
        "Pass --workspace. Workspaces answering here: " + (", ".join(found) or "none") + ".",
    )


def _draft_body(product_id: str, recipe: str, subject_asset: str) -> dict:
    return {
        "product_id": product_id,
        "kind": "video",
        "recipe": recipe,
        "background_enabled": False,
        "subject_asset_ids": [subject_asset],
        "listing_fields": LISTING_FIELDS,
        "together": False,
    }


def _prompt_ready(api_base: str, workspace: str, product_id: str, recipe: str, subject_asset: str) -> None:
    """Preview until the live API is serving the current recipe text."""
    deadline = time.monotonic() + PROMPT_WAIT_SECONDS
    last = FillFailure("api", f"The API at {api_base} is not answering. Do not start it.")
    while True:
        try:
            _status, body = _request(
                api_base, "POST",
                f"/api/workspaces/{workspace}/attribution/creative-drafts/preview",
                _draft_body(product_id, recipe, subject_asset),
                timeout=30,
            )
            prompt = str((body.get("draft") or {}).get("prompt") or "")
            missing = missing_phrases(recipe, prompt, together=False)
            if not missing:
                return
            last = FillFailure("stale", stale_message(missing), retry=True)
        except FillFailure as error:
            last = error
            if not error.retry:
                raise
        if time.monotonic() >= deadline:
            raise last
        time.sleep(PROMPT_POLL_SECONDS)


def _read_draft(api_base: str, workspace: str, draft_id: str) -> dict:
    _status, body = _request(
        api_base, "GET",
        f"/api/workspaces/{workspace}/attribution/creative-drafts/{draft_id}",
        timeout=30,
    )
    draft = body.get("draft")
    if not isinstance(draft, dict):
        raise FillFailure("api", "The draft read did not return a draft.")
    return draft


def _discard(api_base: str, workspace: str, draft_id: str) -> None:
    _request(
        api_base, "POST",
        f"/api/workspaces/{workspace}/attribution/creative-drafts/{draft_id}/discard",
        {},
        timeout=30,
    )


def _ensure_key() -> None:
    """Prefer a saved XAI_API_KEY. Otherwise use the logged-in Grok session.

    The value is placed in this process only and is never printed.
    """
    from trendrelay_api.env_store import effective_value

    if effective_value("XAI_API_KEY").strip():
        return
    path = Path.home() / ".grok" / "auth.json"
    found = ""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        loaded = None
    records = loaded.values() if isinstance(loaded, dict) else ()
    for record in records:
        if isinstance(record, dict):
            candidate = str(record.get("key") or "").strip()
            if candidate:
                found = candidate
                break
    if not found:
        raise FillFailure(
            "auth",
            "XAI_API_KEY is unset and ~/.grok/auth.json has no session key.",
        )
    os.environ["XAI_API_KEY"] = found


def _local_mp4(draft_id: str) -> bytes | None:
    path = CLIP_DIR / f"{draft_id}.mp4"
    if not path.is_file():
        return None
    data = path.read_bytes()
    if _is_mp4(data):
        return data
    return None


def _is_mp4(data: bytes) -> bool:
    return len(data) >= 12 and data[4:8] == b"ftyp"


def _clip_bytes(draft_id: str, prompt: str, workspace: str, asset_id: str) -> bytes:
    cached = _local_mp4(draft_id)
    if cached is not None:
        print(f"using local clip {draft_id}", file=sys.stderr, flush=True)
        return cached
    from trendrelay_api.integrations.media_hosting import MediaHostingUnavailable, get_object

    key = f"product-clips/{draft_id}.mp4"
    try:
        stored = get_object(key)
    except MediaHostingUnavailable:
        stored = b""
    if _is_mp4(stored):
        print(f"using stored clip {draft_id}", file=sys.stderr, flush=True)
        _write_local(draft_id, stored)
        return stored
    _ensure_key()
    from trendrelay_api.database import SessionFactory
    from trendrelay_api.integrations.video_generation import (
        PROVIDERS,
        VideoError,
        _subject_bytes,
        generate_xai,
    )

    with SessionFactory() as session:
        image, mime = _subject_bytes(session, workspace, asset_id)
    print(f"generating {draft_id}", file=sys.stderr, flush=True)
    try:
        data = generate_xai(PROVIDERS["xai"], prompt, image, mime, key)
    except VideoError as error:
        raise FillFailure(error.code, public_text(error)) from error
    except Exception as error:
        raise FillFailure("unavailable", public_text(error)) from error
    if not _is_mp4(data):
        raise FillFailure("unavailable", "The video service returned a file that is not an MP4.")
    _write_local(draft_id, data)
    return data


def _write_local(draft_id: str, data: bytes) -> None:
    CLIP_DIR.mkdir(parents=True, exist_ok=True)
    (CLIP_DIR / f"{draft_id}.mp4").write_bytes(data)


def _submit(api_base: str, workspace: str, draft_id: str, recipe: str, data: bytes) -> dict:
    _status, body = _request(
        api_base, "POST",
        f"/api/workspaces/{workspace}/attribution/creative-drafts/{draft_id}/media",
        {
            "media_base64": base64.b64encode(data).decode("ascii"),
            "filename": f"{recipe}.mp4",
        },
    )
    return body


def _finish_line(recipe: str, draft_id: str, body: dict) -> None:
    draft = body.get("draft") if isinstance(body.get("draft"), dict) else {}
    assets = draft.get("ingested_asset_ids") or []
    asset_id = body.get("asset_id") or (assets[-1] if assets else "")
    if (
        draft.get("status") == "succeeded"
        and draft.get("owed") == 0
        and draft.get("linked") is True
        and asset_id
    ):
        print(f"OK {recipe} {draft_id} {asset_id}")
        return
    raise FillFailure("incomplete", "The file was submitted and the draft is not linked.")


def _subject_id(draft: dict) -> str:
    assets = draft.get("subject_assets") or []
    for item in assets:
        if isinstance(item, dict) and item.get("asset_id") and not item.get("missing"):
            return str(item["asset_id"])
    raise FillFailure(
        "subject",
        "This draft has no Library subject. Queue a new one with --subject-asset.",
    )


def _already_done(draft: dict) -> bool:
    assets = draft.get("ingested_asset_ids") or []
    return bool(
        draft.get("status") == "succeeded"
        and draft.get("owed") == 0
        and draft.get("linked") is True
        and assets
    )


def fill_draft(api_base: str, workspace: str, draft_id: str) -> None:
    draft = _read_draft(api_base, workspace, draft_id)
    recipe = str(draft.get("recipe") or "")
    if _already_done(draft):
        print(f"OK {recipe} {draft_id} {draft['ingested_asset_ids'][-1]}")
        return
    if draft.get("status") == "discarded":
        raise FillFailure("discarded", "This draft was discarded. Queue a new one.")
    if draft.get("kind") != "video":
        raise FillFailure("kind", "This command files a video.")
    if int(draft.get("product_count") or 1) > 1:
        raise FillFailure("group", "A draft of several products is not sent.")
    together = bool(draft.get("together"))
    missing = missing_phrases(recipe, str(draft.get("prompt") or ""), together=together)
    if missing:
        raise FillFailure("stale", stale_message(missing))
    try:
        data = _clip_bytes(draft_id, str(draft["prompt"]), workspace, _subject_id(draft))
        body = _submit(api_base, workspace, draft_id, recipe, data)
    except FillFailure as error:
        if not error.draft_id:
            error.draft_id = draft_id
        if error.code == "moderation":
            try:
                _discard(api_base, workspace, draft_id)
            except FillFailure:
                pass
        raise
    _finish_line(recipe, draft_id, body)


def fill_recipe(api_base: str, workspace: str, product_id: str, subject_asset: str, recipe: str) -> None:
    if recipe not in VIDEO_RECIPES:
        raise FillFailure("recipe", f"{recipe} is not a video recipe.")
    _prompt_ready(api_base, workspace, product_id, recipe, subject_asset)
    _status, created = _request(
        api_base, "POST",
        f"/api/workspaces/{workspace}/attribution/creative-drafts",
        _draft_body(product_id, recipe, subject_asset),
    )
    draft = created.get("draft") if isinstance(created.get("draft"), dict) else {}
    draft_id = str(draft.get("id") or "")
    if not draft_id:
        raise FillFailure("api", "The draft was not created.")
    missing = missing_phrases(recipe, str(draft.get("prompt") or ""), together=False)
    if missing:
        try:
            _discard(api_base, workspace, draft_id)
        except FillFailure:
            pass
        raise FillFailure("stale", stale_message(missing), draft_id=draft_id)
    fill_draft(api_base, workspace, draft_id)


def _report(recipe: str, error: FillFailure) -> None:
    print(f"FAIL {recipe} {error.draft_id or '-'} {error.code} {public_text(error)}")


def fill(args: argparse.Namespace) -> int:
    drafts = list(args.draft or [])
    recipes = list(args.recipe or [])
    if drafts and (args.product or args.subject_asset or recipes):
        print("FAIL - - usage Pass either --draft, or --product with --subject-asset and --recipe.")
        return 2
    if not drafts and not (args.product and args.subject_asset and recipes):
        print("FAIL - - usage Pass --draft, or --product, --subject-asset, and --recipe.")
        return 2
    try:
        workspace = workspace_id(args.api_base, args.workspace)
    except FillFailure as error:
        _report("-", error)
        return 1
    failed = 0
    if drafts:
        for draft_id in drafts:
            try:
                fill_draft(args.api_base, workspace, draft_id)
            except FillFailure as error:
                failed += 1
                if not error.draft_id:
                    error.draft_id = draft_id
                recipe = "-"
                try:
                    recipe = str(_read_draft(args.api_base, workspace, draft_id).get("recipe") or "-")
                except FillFailure:
                    pass
                _report(recipe, error)
        return 1 if failed else 0
    for recipe in recipes:
        try:
            fill_recipe(args.api_base, workspace, args.product, args.subject_asset, recipe)
        except FillFailure as error:
            failed += 1
            _report(recipe, error)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate and file Attribution video creatives, one at a time.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup-zdr", help="Write ~/.grok/managed_config.toml when it is missing.")
    fill_parser = commands.add_parser("fill", help="Create, generate, and submit one video draft at a time.")
    fill_parser.add_argument("--product")
    fill_parser.add_argument("--subject-asset")
    fill_parser.add_argument("--recipe", action="append", default=[])
    fill_parser.add_argument("--draft", action="append", default=[])
    fill_parser.add_argument("--workspace", default=None)
    fill_parser.add_argument("--api-base", default=API_BASE_DEFAULT)
    args = parser.parse_args(argv)
    if args.command == "setup-zdr":
        try:
            return setup_zdr()
        except FillFailure as error:
            print(f"FAIL setup-zdr - {error.code} {public_text(error)}")
            return 1
    return fill(args)


if __name__ == "__main__":
    raise SystemExit(main())
