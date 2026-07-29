"""Manage TrendRelay's native-Windows self-hosted Postiz service."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".tools" / "postiz-app" / "source"
DATA = ROOT / ".data" / "postiz-selfhost"
LOGS = DATA / "logs"
POSTGRES_DATA = DATA / "postgres-data"
REDIS_DATA = DATA / "redis"
UPLOADS = DATA / "uploads"
PRIVATE_ENV = SOURCE / ".env"
LOCAL_ROUTE_SOURCE = ROOT / "integrations" / "postiz" / "local-session.route.ts"
LOCAL_ROUTE_TARGET = (
    SOURCE
    / "apps"
    / "frontend"
    / "src"
    / "app"
    / "api"
    / "trendrelay-local-session"
    / "route.ts"
)
POSTGRES_PORT = 54329
REDIS_PORT = 6389
TEMPORAL_PORT = 7233
BACKEND_URL = "http://127.0.0.1:3000"
ORCHESTRATOR_URL = "http://127.0.0.1:3002/health/status"
FRONTEND_URL = "http://localhost:4200"
LOCAL_SESSION_URL = f"{FRONTEND_URL}/api/trendrelay-local-session"
POSTIZ_INTEGRATIONS_CONTROLLER = (
    SOURCE / "apps/backend/src/api/routes/integrations.controller.ts"
)
POSTIZ_ADD_PROVIDER_COMPONENT = (
    SOURCE / "apps/frontend/src/components/launches/add.provider.component.tsx"
)
ADMIN_EMAIL = "admin@trendrelay.local"
POSTIZ_REPOSITORY = "https://github.com/gitroomhq/postiz-app.git"
POSTIZ_REVISION = "7236213ea4520bd67b45688c2787d1f4586b3b51"
POSTIZ_VERSION = "2.21.7"
PREPARED_MARKER = DATA / "prepared-revision.txt"
IS_WINDOWS = os.name == "nt"

for output in (sys.stdout, sys.stderr):
    if hasattr(output, "reconfigure"):
        output.reconfigure(encoding="utf-8", errors="replace")


def _request(
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 2,
) -> tuple[int, bytes, Any]:
    payload = json.dumps(body).encode() if body is not None else None
    request_headers = {"Accept": "application/json", **(headers or {})}
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=payload, headers=request_headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(), response.headers
    except urllib.error.HTTPError as error:
        return error.code, error.read(), error.headers


def _healthy(url: str, timeout: float = 1) -> bool:
    try:
        status, _, _ = _request(url, timeout=timeout)
        return status < 500
    except (OSError, urllib.error.URLError):
        return False


def _wait(url: str, timeout: float, label: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _healthy(url):
            return
        time.sleep(0.25)
    raise RuntimeError(f"{label} did not become ready at {url}.")


def _find(pattern: str) -> Path | None:
    matches = sorted(Path(os.environ.get("LOCALAPPDATA", "")).glob(pattern))
    return matches[0] if matches else None


def native_commands() -> dict[str, Path | None]:
    postgres = Path(r"C:\Program Files\PostgreSQL\17\bin\pg_ctl.exe")
    return {
        "postgres": postgres if postgres.is_file() else None,
        "redis": _find(
            "Microsoft/WinGet/Packages/taizod1024.redis-windows-fork_*/"
            "Redis-8.8.0-Windows-x64-msys2/redis-server.exe"
        ),
        "redis_cli": _find(
            "Microsoft/WinGet/Packages/taizod1024.redis-windows-fork_*/"
            "Redis-8.8.0-Windows-x64-msys2/redis-cli.exe"
        ),
        "temporal": _find(
            "Microsoft/WinGet/Packages/Temporal.TemporalCLI_*/temporal.exe"
        ),
        "corepack": Path(command)
        if (command := shutil.which("corepack.cmd" if IS_WINDOWS else "corepack"))
        else None,
    }


def _postgres_ready() -> bool:
    executable = Path(r"C:\Program Files\PostgreSQL\17\bin\pg_isready.exe")
    if not executable.is_file():
        return False
    result = subprocess.run(
        [str(executable), "-h", "127.0.0.1", "-p", str(POSTGRES_PORT)],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _redis_ready(commands: dict[str, Path | None]) -> bool:
    if not commands["redis"]:
        return False
    try:
        with socket.create_connection(("127.0.0.1", REDIS_PORT), timeout=1) as client:
            client.sendall(b"*1\r\n$4\r\nPING\r\n")
            return client.recv(64).startswith(b"+PONG")
    except OSError:
        return False


def _temporal_ready(commands: dict[str, Path | None]) -> bool:
    executable = commands["temporal"]
    if not executable:
        return False
    result = subprocess.run(
        [
            str(executable),
            "operator",
            "cluster",
            "health",
            "--address",
            f"127.0.0.1:{TEMPORAL_PORT}",
        ],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def status_report() -> dict[str, Any]:
    commands = native_commands()
    return {
        "platform": "native-windows" if IS_WINDOWS else "unsupported",
        "source_ready": (SOURCE / "pnpm-lock.yaml").is_file(),
        "dependencies_ready": (SOURCE / "node_modules").is_dir(),
        "configuration_ready": PRIVATE_ENV.is_file(),
        "local_admin_ready": (DATA / "admin-password.txt").is_file(),
        "api_key_ready": (DATA / "api-key.txt").is_file(),
        "postgres": _postgres_ready(),
        "redis": _redis_ready(commands),
        "temporal": _temporal_ready(commands),
        "backend": _healthy(f"{BACKEND_URL}/auth/can-register"),
        "orchestrator": _healthy(ORCHESTRATOR_URL),
        "frontend": _healthy(f"{FRONTEND_URL}/auth"),
        "dashboard_url": LOCAL_SESSION_URL,
        "provider_api_url": BACKEND_URL,
    }


def _private_value(path: Path, factory: Any) -> str:
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    value = factory()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return value


def _postgres_password() -> str:
    path = DATA / "postgres-password.txt"
    if not path.is_file():
        raise RuntimeError(
            "Postiz PostgreSQL is not initialized. Run the native Postiz setup first."
        )
    return path.read_text(encoding="utf-8").strip()


def ensure_private_configuration() -> None:
    if not SOURCE.is_dir():
        raise RuntimeError("Pinned Postiz source is missing.")
    password = _postgres_password()
    admin_password = _private_value(
        DATA / "admin-password.txt", lambda: secrets.token_urlsafe(32)
    )
    jwt_secret = _private_value(
        DATA / "jwt-secret.txt", lambda: secrets.token_urlsafe(48)
    )
    for directory in (LOGS, REDIS_DATA, UPLOADS):
        directory.mkdir(parents=True, exist_ok=True)
    upload_path = UPLOADS.resolve().as_posix()
    from urllib.parse import quote

    values = {
        "DATABASE_URL": (
            f"postgresql://postiz:{quote(password, safe='')}@"
            f"127.0.0.1:{POSTGRES_PORT}/postiz"
        ),
        "REDIS_URL": f"redis://127.0.0.1:{REDIS_PORT}",
        "TEMPORAL_ADDRESS": f"127.0.0.1:{TEMPORAL_PORT}",
        "JWT_SECRET": jwt_secret,
        "MAIN_URL": FRONTEND_URL,
        "FRONTEND_URL": FRONTEND_URL,
        "NEXT_PUBLIC_BACKEND_URL": BACKEND_URL,
        "BACKEND_INTERNAL_URL": BACKEND_URL,
        "STORAGE_PROVIDER": "local",
        "UPLOAD_DIRECTORY": upload_path,
        "NEXT_PUBLIC_UPLOAD_DIRECTORY": "/uploads",
        "NEXT_PUBLIC_UPLOAD_STATIC_DIRECTORY": "/uploads",
        "NOT_SECURED": "true",
        "IS_GENERAL": "true",
        "DISABLE_REGISTRATION": "false",
        "API_LIMIT": "100",
        "NX_ADD_PLUGINS": "false",
        "TRENDRELAY_LOCAL_ADMIN": "true",
        "POSTIZ_LOCAL_ADMIN_EMAIL": ADMIN_EMAIL,
        "POSTIZ_LOCAL_ADMIN_PASSWORD": admin_password,
    }
    preserved: list[str] = []
    if PRIVATE_ENV.is_file():
        for line in PRIVATE_ENV.read_text(encoding="utf-8-sig").splitlines():
            key = line.split("=", 1)[0].strip() if "=" in line else ""
            if not key or key not in values:
                preserved.append(line)
    rendered = [f'{key}="{value}"' for key, value in values.items()]
    PRIVATE_ENV.write_text(
        "\n".join([*rendered, *preserved]) + "\n",
        encoding="utf-8",
    )
    if not LOCAL_ROUTE_SOURCE.is_file():
        raise RuntimeError("TrendRelay's Postiz local-session route is missing.")
    LOCAL_ROUTE_TARGET.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(LOCAL_ROUTE_SOURCE, LOCAL_ROUTE_TARGET)
    _apply_provider_readiness_overlay()


def _apply_provider_readiness_overlay() -> None:
    backend = POSTIZ_INTEGRATIONS_CONTROLLER.read_text(encoding="utf-8")
    backend_needle = """      const { codeVerifier, state, url } =
        await integrationProvider.generateAuthUrl(getExternalUrl);
"""
    backend_replacement = (
        backend_needle
        + """
      if (!url || /(?:^|[?&])(client_id|client_key|app_id)=undefined(?:&|$)/.test(url)) {
        return {
          err: true,
          reason: 'This platform app is not configured yet. Open TrendRelay Tools, choose Postiz Agent, and complete the platform setup first.',
        };
      }
"""
    )
    if backend_replacement not in backend:
        if backend_needle not in backend:
            raise RuntimeError(
                "Postiz provider readiness backend overlay no longer applies."
            )
        POSTIZ_INTEGRATIONS_CONTROLLER.write_text(
            backend.replace(backend_needle, backend_replacement, 1),
            encoding="utf-8",
        )

    frontend = POSTIZ_ADD_PROVIDER_COMPONENT.read_text(encoding="utf-8")
    frontend_needle = """          const { url, err } = await (
"""
    frontend_replacement = """          const { url, err, reason } = await (
"""
    if frontend_replacement not in frontend:
        if frontend_needle not in frontend:
            raise RuntimeError(
                "Postiz provider readiness frontend overlay no longer applies."
            )
        frontend = frontend.replace(frontend_needle, frontend_replacement, 1)
    message_needle = """              t(
                'could_not_connect_to_platform',
                'Could not connect to the platform'
              ),
"""
    message_replacement = """              reason ||
                t(
                  'could_not_connect_to_platform',
                  'Could not connect to the platform'
                ),
"""
    if message_replacement not in frontend:
        if message_needle not in frontend:
            raise RuntimeError(
                "Postiz provider error-message overlay no longer applies."
            )
        frontend = frontend.replace(message_needle, message_replacement, 1)
    POSTIZ_ADD_PROVIDER_COMPONENT.write_text(frontend, encoding="utf-8")


def _stream(name: str, process: subprocess.Popen[str]) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        print(f"[Postiz/{name}] {line.rstrip()}", flush=True)


def _start(
    name: str,
    command: list[str],
    *,
    cwd: Path = ROOT,
    stream: bool = False,
) -> subprocess.Popen[str]:
    creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0
    output: Any = subprocess.PIPE if stream else subprocess.DEVNULL
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=output,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creation_flags,
        start_new_session=not IS_WINDOWS,
    )
    if stream:
        threading.Thread(target=_stream, args=(name, process), daemon=True).start()
    print(f"[Postiz] Started {name} monitor for PID {process.pid}.", flush=True)
    return process


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if IS_WINDOWS:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)


def _bootstrap_admin() -> None:
    password = (DATA / "admin-password.txt").read_text(encoding="utf-8").strip()
    registration = {
        "email": ADMIN_EMAIL,
        "password": password,
        "provider": "LOCAL",
        "company": "TrendRelay Local",
    }
    status, _, headers = _request(
        f"{BACKEND_URL}/auth/register",
        method="POST",
        body=registration,
        timeout=20,
    )
    auth = headers.get("auth")
    if status != 200 or not auth:
        status, _, headers = _request(
            f"{BACKEND_URL}/auth/login",
            method="POST",
            body={
                "email": ADMIN_EMAIL,
                "password": password,
                "provider": "LOCAL",
            },
            timeout=20,
        )
        auth = headers.get("auth")
    if status != 200 or not auth:
        raise RuntimeError("Could not establish the local Postiz admin session.")
    (DATA / "admin-auth-token.txt").write_text(auth, encoding="utf-8")
    api_key_path = DATA / "api-key.txt"
    if not api_key_path.is_file():
        status, response_body, _ = _request(
            f"{BACKEND_URL}/user/api-key/rotate",
            method="POST",
            headers={"auth": auth},
            timeout=20,
        )
        try:
            api_key = json.loads(response_body).get("apiKey")
        except json.JSONDecodeError as error:
            raise RuntimeError("Postiz did not return a local API key.") from error
        if status not in (200, 201) or not api_key:
            raise RuntimeError("Postiz did not create a local API key.")
        api_key_path.write_text(api_key, encoding="utf-8")


def _start_infrastructure(
    commands: dict[str, Path | None], owned: list[subprocess.Popen[str]]
) -> bool:
    postgres_owned = False
    if not _postgres_ready():
        executable = commands["postgres"]
        if not executable or not POSTGRES_DATA.is_dir():
            raise RuntimeError(
                "Native PostgreSQL 17 or its Postiz data directory is missing."
            )
        log = LOGS / "postgres.log"
        result = subprocess.run(
            [
                str(executable),
                "-D",
                str(POSTGRES_DATA),
                "-l",
                str(log),
                "-o",
                f"-h 127.0.0.1 -p {POSTGRES_PORT}",
                "start",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout).strip())
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not _postgres_ready():
            time.sleep(0.25)
        if not _postgres_ready():
            raise RuntimeError("Native PostgreSQL did not become ready.")
        postgres_owned = True

    if not _redis_ready(commands):
        executable = commands["redis"]
        if not executable:
            raise RuntimeError("Native Redis is missing.")
        owned.append(
            _start(
                "Redis",
                [
                    str(executable),
                    "--bind",
                    "127.0.0.1",
                    "--port",
                    str(REDIS_PORT),
                    "--dir",
                    str(REDIS_DATA),
                    "--appendonly",
                    "no",
                ],
            )
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not _redis_ready(commands):
            time.sleep(0.25)
        if not _redis_ready(commands):
            raise RuntimeError("Native Redis did not become ready.")

    if not _temporal_ready(commands):
        executable = commands["temporal"]
        if not executable:
            raise RuntimeError("Temporal CLI is missing.")
        owned.append(
            _start(
                "Temporal",
                [
                    str(executable),
                    "server",
                    "start-dev",
                    "--ip",
                    "127.0.0.1",
                    "--port",
                    str(TEMPORAL_PORT),
                    "--ui-port",
                    "8233",
                ],
                cwd=DATA,
            )
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not _temporal_ready(commands):
            time.sleep(0.5)
        if not _temporal_ready(commands):
            raise RuntimeError("Temporal did not become ready.")

    return postgres_owned


def _run_checked(
    command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None
) -> None:
    result = subprocess.run(
        command,
        cwd=cwd,
        env={**os.environ, **(env or {})},
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed with exit code {result.returncode}: {command[0]}"
        )


def _install_native_dependencies() -> dict[str, Path | None]:
    commands = native_commands()
    packages = {
        "postgres": "PostgreSQL.PostgreSQL.17",
        "redis": "taizod1024.redis-windows-fork",
        "temporal": "Temporal.TemporalCLI",
    }
    winget = shutil.which("winget")
    for name, package in packages.items():
        if commands[name]:
            continue
        if not winget:
            raise RuntimeError(f"{name} is missing and winget is unavailable.")
        print(f"[Postiz setup] Installing native {name}...", flush=True)
        _run_checked(
            [
                winget,
                "install",
                "--id",
                package,
                "-e",
                "--silent",
                "--accept-package-agreements",
                "--accept-source-agreements",
            ]
        )
        commands = native_commands()
        if not commands[name]:
            raise RuntimeError(
                f"{name} installed but was not discoverable. Restart Windows, then run start.cmd again."
            )
    return commands


def _prepare_source(commands: dict[str, Path | None]) -> None:
    git = shutil.which("git")
    if not git:
        raise RuntimeError("Git is required to prepare self-hosted Postiz.")
    SOURCE.mkdir(parents=True, exist_ok=True)
    if not (SOURCE / ".git").is_dir():
        _run_checked([git, "init"], cwd=SOURCE)
        _run_checked([git, "remote", "add", "origin", POSTIZ_REPOSITORY], cwd=SOURCE)
    revision = subprocess.run(
        [git, "rev-parse", "HEAD"],
        cwd=SOURCE,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    if revision != POSTIZ_REVISION:
        print(f"[Postiz setup] Fetching Postiz {POSTIZ_VERSION}...", flush=True)
        _run_checked(
            [git, "fetch", "--depth", "1", "origin", POSTIZ_REVISION], cwd=SOURCE
        )
        _run_checked([git, "checkout", "--detach", "FETCH_HEAD"], cwd=SOURCE)
    if not (SOURCE / "node_modules").is_dir():
        corepack = commands["corepack"]
        if not corepack:
            raise RuntimeError("Corepack is required to install Postiz dependencies.")
        print("[Postiz setup] Installing pinned Postiz dependencies...", flush=True)
        _run_checked(
            [
                str(corepack),
                "pnpm",
                "install",
                "--frozen-lockfile",
                "--reporter=append-only",
            ],
            cwd=SOURCE,
        )


def _prepare_postgres(commands: dict[str, Path | None]) -> None:
    pg_ctl = commands["postgres"]
    if not pg_ctl:
        raise RuntimeError("Native PostgreSQL 17 is missing.")
    bin_dir = pg_ctl.parent
    initdb = bin_dir / "initdb.exe"
    psql = bin_dir / "psql.exe"
    password_path = DATA / "postgres-password.txt"
    if not POSTGRES_DATA.is_dir() or not (POSTGRES_DATA / "PG_VERSION").is_file():
        DATA.mkdir(parents=True, exist_ok=True)
        password = _private_value(password_path, lambda: secrets.token_urlsafe(32))
        print(
            "[Postiz setup] Initializing the isolated PostgreSQL cluster...", flush=True
        )
        _run_checked(
            [
                str(initdb),
                "-D",
                str(POSTGRES_DATA),
                "-U",
                "postiz",
                f"--pwfile={password_path}",
                "--encoding=UTF8",
                "--auth-host=scram-sha-256",
                "--auth-local=trust",
            ]
        )
    password = _postgres_password()
    was_ready = _postgres_ready()
    if not was_ready:
        LOGS.mkdir(parents=True, exist_ok=True)
        _run_checked(
            [
                str(pg_ctl),
                "-D",
                str(POSTGRES_DATA),
                "-l",
                str(LOGS / "postgres.log"),
                "-o",
                f"-h 127.0.0.1 -p {POSTGRES_PORT}",
                "start",
            ]
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not _postgres_ready():
            time.sleep(0.25)
    query = subprocess.run(
        [
            str(psql),
            "-h",
            "127.0.0.1",
            "-p",
            str(POSTGRES_PORT),
            "-U",
            "postiz",
            "-d",
            "postgres",
            "-tAc",
            "SELECT 1 FROM pg_database WHERE datname='postiz'",
        ],
        env={**os.environ, "PGPASSWORD": password},
        capture_output=True,
        text=True,
        check=False,
    )
    if query.returncode != 0:
        raise RuntimeError("Could not inspect the isolated Postiz database.")
    if query.stdout.strip() != "1":
        _run_checked(
            [
                str(bin_dir / "createdb.exe"),
                "-h",
                "127.0.0.1",
                "-p",
                str(POSTGRES_PORT),
                "-U",
                "postiz",
                "postiz",
            ],
            env={"PGPASSWORD": password},
        )


def prepare_service() -> int:
    if not IS_WINDOWS:
        print(
            "Native self-hosted Postiz preparation currently supports Windows.",
            file=sys.stderr,
        )
        return 1
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        commands = _install_native_dependencies()
        _prepare_source(commands)
        _prepare_postgres(commands)
        ensure_private_configuration()
        corepack = commands["corepack"]
        if not corepack:
            raise RuntimeError("Corepack is required to initialize Postiz.")
        print(
            "[Postiz setup] Applying the isolated Postiz database schema...", flush=True
        )
        _run_checked(
            [
                str(corepack),
                "pnpm",
                "dlx",
                "prisma@6.5.0",
                "db",
                "push",
                "--accept-data-loss",
                "--skip-generate",
                "--schema",
                "./libraries/nestjs-libraries/src/database/prisma/schema.prisma",
            ],
            cwd=SOURCE,
        )
        PREPARED_MARKER.write_text(POSTIZ_REVISION + "\n", encoding="utf-8")
        print(f"[Postiz setup] Native Postiz {POSTIZ_VERSION} is ready.", flush=True)
        return 0
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1


def run_service() -> int:
    if not IS_WINDOWS:
        print("Native self-hosted Postiz currently supports Windows.", file=sys.stderr)
        return 1
    commands = native_commands()
    missing = [
        name
        for name in ("postgres", "redis", "temporal", "corepack")
        if not commands[name]
    ]
    if missing:
        print(
            "Missing native Postiz dependencies: " + ", ".join(missing), file=sys.stderr
        )
        return 1
    if not (SOURCE / "node_modules").is_dir():
        print(
            "Postiz dependencies are missing. Run the Postiz preparation step.",
            file=sys.stderr,
        )
        return 1

    ensure_private_configuration()
    owned: list[subprocess.Popen[str]] = []
    postgres_owned = False
    try:
        postgres_owned = _start_infrastructure(commands, owned)
        corepack = str(commands["corepack"])
        if not _healthy(f"{BACKEND_URL}/auth/can-register"):
            owned.append(
                _start(
                    "Backend",
                    [corepack, "pnpm", "--filter", "./apps/backend", "run", "dev"],
                    cwd=SOURCE,
                    stream=False,
                )
            )
        _wait(f"{BACKEND_URL}/auth/can-register", 120, "Postiz backend")
        _bootstrap_admin()

        if not _healthy(ORCHESTRATOR_URL):
            owned.append(
                _start(
                    "Orchestrator",
                    [corepack, "pnpm", "--filter", "./apps/orchestrator", "run", "dev"],
                    cwd=SOURCE,
                    stream=False,
                )
            )
        if not _healthy(f"{FRONTEND_URL}/auth"):
            owned.append(
                _start(
                    "Frontend",
                    [corepack, "pnpm", "--filter", "./apps/frontend", "run", "dev"],
                    cwd=SOURCE,
                    stream=True,
                )
            )
        _wait(ORCHESTRATOR_URL, 180, "Postiz orchestrator")
        _wait(f"{FRONTEND_URL}/auth", 180, "Postiz frontend")
        print(f"[Postiz] Ready at {LOCAL_SESSION_URL}", flush=True)

        while True:
            for process in owned:
                code = process.poll()
                if code is not None:
                    raise RuntimeError(
                        f"A managed Postiz process exited with code {code}."
                    )
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        for process in reversed(owned):
            _stop(process)
        if postgres_owned and commands["postgres"]:
            subprocess.run(
                [
                    str(commands["postgres"]),
                    "-D",
                    str(POSTGRES_DATA),
                    "-m",
                    "fast",
                    "stop",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "run", "status", "configure"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.command == "prepare":
        return prepare_service()
    if args.command == "status":
        report = status_report()
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            for name, value in report.items():
                print(f"{name}: {value}")
        return (
            0
            if all(
                report[name]
                for name in (
                    "source_ready",
                    "dependencies_ready",
                    "configuration_ready",
                )
            )
            else 1
        )
    if args.command == "configure":
        ensure_private_configuration()
        print("Private Postiz configuration and local-session overlay are ready.")
        return 0
    return run_service()


if __name__ == "__main__":
    raise SystemExit(main())
