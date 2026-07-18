"""Install and safely operate TrendRelay's pinned Postiz publishing provider."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__:
    from .local_env import load_prefixed_env
else:
    from local_env import load_prefixed_env

ROOT = Path(__file__).resolve().parents[1]
TOOL_ROOT = ROOT / ".tools" / "postiz-agent"
SOURCE_DIR = TOOL_ROOT / "source"
MARKER = TOOL_ROOT / "installed-revision.txt"
REPOSITORY = "https://github.com/gitroomhq/postiz-agent.git"
REVISION = "41c5a9dbd6b2776863e7c05c22e7a385c208321c"
UPSTREAM_VERSION = "2.0.15"
ENTRYPOINT = SOURCE_DIR / "dist" / "index.js"
LEDGER_PATH = ROOT / ".data" / "postiz" / "operations.json"
RUNTIME_DIR = ROOT / ".data" / "postiz" / "runtime"
MEDIA_PLACEHOLDER = "__POSTIZ_UPLOADED_MEDIA_URL__"
SUPPORTED_PROVIDERS = ("tiktok", "instagram", "youtube")
MAX_VIDEO_BYTES = {
    "instagram": 100_000_000,
    "tiktok": 287_600_000,
    "youtube": 128_000_000_000,
}


@dataclass(frozen=True)
class Target:
    provider: str
    integration_id: str


def run_checked(command: list[str], cwd: Path = ROOT) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def install_provider() -> int:
    npm = shutil.which("npm")
    if not npm:
        print("npm is required to install Postiz Agent.", file=sys.stderr)
        return 1
    TOOL_ROOT.mkdir(parents=True, exist_ok=True)
    if not (SOURCE_DIR / ".git").is_dir():
        SOURCE_DIR.mkdir(parents=True, exist_ok=True)
        run_checked(["git", "init"], cwd=SOURCE_DIR)
        run_checked(["git", "remote", "add", "origin", REPOSITORY], cwd=SOURCE_DIR)

    run_checked(["git", "fetch", "--depth", "1", "origin", REVISION], cwd=SOURCE_DIR)
    run_checked(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=SOURCE_DIR)
    run_checked(
        [npm, "install", "--ignore-scripts", "--no-audit", "--no-fund"],
        cwd=SOURCE_DIR,
    )
    run_checked([npm, "run", "build"], cwd=SOURCE_DIR)
    MARKER.write_text(f"{REVISION}\n", encoding="utf-8")
    print(f"Postiz provider installed at revision {REVISION[:12]}.")
    return check_provider()


def provider_command(arguments: list[str]) -> list[str]:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required to run Postiz Agent.")
    return [node, str(ENTRYPOINT), *arguments]


def run_provider(
    arguments: list[str], capture: bool = False
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        provider_command(arguments),
        cwd=SOURCE_DIR,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=capture,
        check=False,
    )


def check_provider() -> int:
    if not MARKER.is_file() or MARKER.read_text(encoding="utf-8").strip() != REVISION:
        print(
            "Postiz provider is not installed at the pinned revision.", file=sys.stderr
        )
        print("Run: postiz.cmd install", file=sys.stderr)
        return 1
    if not ENTRYPOINT.is_file():
        print("Postiz provider build is missing. Re-run installation.", file=sys.stderr)
        return 1
    source_revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=SOURCE_DIR,
        text=True,
        capture_output=True,
        check=False,
    )
    if source_revision.returncode != 0 or source_revision.stdout.strip() != REVISION:
        print(
            "Postiz provider source does not match the pinned revision.",
            file=sys.stderr,
        )
        print("Run: postiz.cmd install", file=sys.stderr)
        return 1
    try:
        result = run_provider(["--version"], capture=True)
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
    if result.returncode != 0:
        print(result.stderr.strip(), file=sys.stderr)
        return result.returncode
    version = result.stdout.strip()
    if version != UPSTREAM_VERSION:
        print(f"Unexpected Postiz version: {version}", file=sys.stderr)
        return 1
    print(f"Postiz provider ready: {version} ({REVISION[:12]})")
    return 0


def parse_target(value: str) -> Target:
    provider, separator, integration_id = value.partition("=")
    provider = provider.strip().lower()
    integration_id = integration_id.strip()
    if not separator or provider not in SUPPORTED_PROVIDERS or not integration_id:
        choices = ", ".join(f"{item}=INTEGRATION_ID" for item in SUPPORTED_PROVIDERS)
        raise argparse.ArgumentTypeError(f"target must be one of: {choices}")
    return Target(provider, integration_id)


def parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("date must be ISO 8601") from error
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("date must include a timezone")
    return parsed


def read_caption(args: argparse.Namespace) -> str:
    if args.caption_file:
        caption = args.caption_file.read_text(encoding="utf-8-sig").strip()
    else:
        caption = (args.caption or "").strip()
    if not caption:
        raise ValueError("Provide a non-empty --caption or --caption-file.")
    return caption


def validate_video(video: Path, targets: list[Target]) -> Path:
    resolved = video.resolve()
    if not resolved.is_file():
        raise ValueError(f"Video not found: {resolved}")
    if resolved.suffix.lower() != ".mp4":
        raise ValueError("Short-form publishing currently requires an MP4 file.")
    size = resolved.stat().st_size
    if size == 0:
        raise ValueError("Video file is empty.")
    maximum = min(MAX_VIDEO_BYTES[target.provider] for target in targets)
    if size > maximum:
        raise ValueError(
            f"Video is {size} bytes; selected platforms allow at most {maximum}."
        )
    return resolved


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def platform_settings(
    target: Target, args: argparse.Namespace, title: str
) -> dict[str, Any]:
    if target.provider == "tiktok":
        return {
            "__type": "tiktok",
            "title": title[:90],
            "privacy_level": args.tiktok_privacy,
            "duet": args.allow_duet,
            "stitch": args.allow_stitch,
            "comment": args.allow_comments,
            "autoAddMusic": args.auto_add_music,
            "brand_content_toggle": args.brand_content,
            "brand_organic_toggle": args.brand_organic,
            "video_made_with_ai": args.made_with_ai,
            "content_posting_method": "DIRECT_POST" if args.tiktok_direct else "UPLOAD",
        }
    if target.provider == "instagram":
        return {
            "__type": "instagram",
            "post_type": "post",
            "is_trial_reel": args.instagram_trial_reel,
        }
    return {
        "__type": "youtube",
        "title": title[:100],
        "type": args.youtube_visibility,
        "selfDeclaredMadeForKids": "yes" if args.made_for_kids else "no",
        "tags": [
            {"value": tag.strip(), "label": tag.strip()}
            for tag in args.youtube_tags.split(",")
            if tag.strip()
        ],
    }


def build_payload(
    args: argparse.Namespace,
    targets: list[Target],
    caption: str,
    media_path: str = MEDIA_PLACEHOLDER,
) -> dict[str, Any]:
    title = (args.title or caption.splitlines()[0]).strip()
    return {
        "type": "schedule" if args.schedule else "draft",
        "creationMethod": "CLI",
        "date": args.date.isoformat(),
        "shortLink": True,
        "tags": [],
        "posts": [
            {
                "integration": {"id": target.integration_id},
                "value": [
                    {
                        "content": caption,
                        "image": [
                            {"id": f"trendrelay-{target.provider}", "path": media_path}
                        ],
                        "delay": 0,
                    }
                ],
                "settings": platform_settings(target, args, title),
            }
            for target in targets
        ],
    }


def operation_id(video_hash: str, payload: dict[str, Any], supplied: str | None) -> str:
    if supplied:
        return supplied
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(f"{video_hash}:{canonical}".encode()).hexdigest()[:24]


def load_ledger() -> dict[str, Any]:
    if not LEDGER_PATH.is_file():
        return {}
    return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))


def write_ledger(ledger: dict[str, Any]) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = LEDGER_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(LEDGER_PATH)


def update_operation(identifier: str, status: str, **details: Any) -> None:
    ledger = load_ledger()
    entry = ledger.get(identifier, {})
    entry.update(
        {
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **details,
        }
    )
    ledger[identifier] = entry
    write_ledger(ledger)


def parse_json_output(output: str) -> Any:
    decoder = json.JSONDecoder()
    matches: list[tuple[int, Any]] = []
    for index, character in enumerate(output):
        if character not in "[{":
            continue
        try:
            value, end = decoder.raw_decode(output[index:])
        except json.JSONDecodeError:
            continue
        matches.append((end, value))
    if not matches:
        raise ValueError("Provider output did not contain JSON.")
    return max(matches, key=lambda match: match[0])[1]


def provider_passthrough(arguments: list[str]) -> int:
    if check_provider() != 0:
        return 1
    return run_provider(arguments).returncode


def short_video(args: argparse.Namespace) -> int:
    targets = list(dict.fromkeys(args.target))
    try:
        caption = read_caption(args)
        video = validate_video(args.video, targets)
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 2
    if args.schedule and args.date <= datetime.now(timezone.utc):
        print("Scheduled publication date must be in the future.", file=sys.stderr)
        return 2

    video_hash = file_sha256(video)
    payload = build_payload(args, targets, caption)
    identifier = operation_id(video_hash, payload, args.operation_id)
    preview = {
        "operation_id": identifier,
        "external_action": "schedule" if args.schedule else "create_draft",
        "video": {
            "path": str(video),
            "sha256": video_hash,
            "bytes": video.stat().st_size,
        },
        "payload": payload,
    }
    if not args.execute:
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        print(
            "Dry run only. Add --execute --confirm-external-action to create the remote post."
        )
        return 0
    if not args.confirm_external_action:
        print("Execution requires --confirm-external-action.", file=sys.stderr)
        return 2
    if check_provider() != 0:
        return 1

    existing = load_ledger().get(identifier)
    if existing and existing.get("status") != "failed_upload":
        print(
            f"Operation {identifier} already has status {existing.get('status')}; refusing a duplicate.",
            file=sys.stderr,
        )
        return 3

    update_operation(
        identifier,
        "uploading",
        video_sha256=video_hash,
        targets=[f"{target.provider}={target.integration_id}" for target in targets],
        publication_type=payload["type"],
        date=payload["date"],
    )
    upload = run_provider(["upload", video.as_posix()], capture=True)
    if upload.returncode != 0:
        update_operation(identifier, "failed_upload", error=upload.stderr[-2000:])
        print(upload.stderr.strip(), file=sys.stderr)
        return upload.returncode
    try:
        upload_result = parse_json_output(upload.stdout)
        uploaded_path = upload_result.get("path") or upload_result.get("url")
        if not uploaded_path:
            raise ValueError("Upload response did not include path or url.")
    except (AttributeError, ValueError) as error:
        update_operation(identifier, "failed_upload", error=str(error))
        print(error, file=sys.stderr)
        return 1

    final_payload = deepcopy(payload)
    for post in final_payload["posts"]:
        post["value"][0]["image"][0]["path"] = uploaded_path
    update_operation(identifier, "creating", uploaded_path=uploaded_path)

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    plan_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            prefix="short-video-",
            dir=RUNTIME_DIR,
            encoding="utf-8",
            delete=False,
        ) as plan_file:
            json.dump(final_payload, plan_file, ensure_ascii=False, indent=2)
            plan_path = Path(plan_file.name)
        creation = run_provider(
            ["posts:create", "--json", str(plan_path)], capture=True
        )
    finally:
        if plan_path:
            plan_path.unlink(missing_ok=True)

    if creation.returncode != 0:
        update_operation(identifier, "uncertain", error=creation.stderr[-2000:])
        print(creation.stderr.strip(), file=sys.stderr)
        print(
            "Post creation is uncertain; inspect Postiz before retrying.",
            file=sys.stderr,
        )
        return creation.returncode
    try:
        creation_result = parse_json_output(creation.stdout)
    except ValueError as error:
        update_operation(identifier, "uncertain", error=str(error))
        print(error, file=sys.stderr)
        return 1

    update_operation(identifier, "created", result=creation_result)
    print(
        json.dumps(
            {
                "operation_id": identifier,
                "status": "created",
                "result": creation_result,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("install", help="install the pinned Postiz provider")
    subparsers.add_parser("check", help="verify the provider build and revision")
    subparsers.add_parser("auth-login", help="start Postiz OAuth device authorization")
    subparsers.add_parser("auth-status", help="verify Postiz authentication")
    subparsers.add_parser("integrations", help="list connected social integrations")

    video = subparsers.add_parser(
        "short-video", help="upload and draft/schedule an MP4"
    )
    video.add_argument("--video", type=Path, required=True)
    caption = video.add_mutually_exclusive_group(required=True)
    caption.add_argument("--caption")
    caption.add_argument("--caption-file", type=Path)
    video.add_argument("--title")
    video.add_argument("--date", type=parse_datetime, required=True)
    video.add_argument("--target", type=parse_target, action="append", required=True)
    video.add_argument(
        "--schedule", action="store_true", help="schedule instead of creating a draft"
    )
    video.add_argument(
        "--execute", action="store_true", help="perform upload and remote creation"
    )
    video.add_argument("--confirm-external-action", action="store_true")
    video.add_argument("--operation-id")
    video.add_argument(
        "--tiktok-privacy",
        choices=(
            "PUBLIC_TO_EVERYONE",
            "MUTUAL_FOLLOW_FRIENDS",
            "FOLLOWER_OF_CREATOR",
            "SELF_ONLY",
        ),
        default="SELF_ONLY",
    )
    video.add_argument("--tiktok-direct", action="store_true")
    video.add_argument("--allow-duet", action="store_true")
    video.add_argument("--allow-stitch", action="store_true")
    video.add_argument("--allow-comments", action="store_true")
    video.add_argument("--auto-add-music", choices=("yes", "no"), default="no")
    video.add_argument("--brand-content", action="store_true")
    video.add_argument("--brand-organic", action="store_true")
    video.add_argument("--made-with-ai", action="store_true")
    video.add_argument("--instagram-trial-reel", action="store_true")
    video.add_argument(
        "--youtube-visibility",
        choices=("private", "unlisted", "public"),
        default="private",
    )
    video.add_argument("--youtube-tags", default="")
    video.add_argument("--made-for-kids", action="store_true")
    return parser


def main() -> int:
    load_prefixed_env(ROOT / ".env", "POSTIZ_")
    args = build_parser().parse_args()
    if args.command == "install":
        return install_provider()
    if args.command == "check":
        return check_provider()
    if args.command == "auth-login":
        return provider_passthrough(["auth:login"])
    if args.command == "auth-status":
        return provider_passthrough(["auth:status"])
    if args.command == "integrations":
        return provider_passthrough(["integrations:list"])
    return short_video(args)


if __name__ == "__main__":
    raise SystemExit(main())
