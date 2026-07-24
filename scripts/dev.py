"""Unified hot-reload development runner for TrendRelay."""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IS_WINDOWS = os.name == "nt"

for output in (sys.stdout, sys.stderr):
    if hasattr(output, "reconfigure"):
        output.reconfigure(encoding="utf-8", errors="replace")


@dataclass(frozen=True)
class Service:
    name: str
    command: list[str]
    color: str
    health_url: str | None = None


@dataclass
class RunningService:
    definition: Service
    process: subprocess.Popen[str]
    output_thread: threading.Thread


COLORS = {
    "cyan": "\033[96m",
    "green": "\033[92m",
    "magenta": "\033[95m",
    "yellow": "\033[93m",
    "reset": "\033[0m",
}


def paint(text: str, color: str) -> str:
    if not sys.stdout.isatty() or os.getenv("NO_COLOR"):
        return text
    return f"{COLORS[color]}{text}{COLORS['reset']}"


def stream_output(service: Service, process: subprocess.Popen[str]) -> None:
    assert process.stdout is not None
    prefix = paint(f"[{service.name}]", service.color)
    for line in process.stdout:
        print(f"{prefix} {line.rstrip()}", flush=True)


def start_service(service: Service) -> RunningService:
    creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0
    process = subprocess.Popen(
        service.command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=creation_flags,
        start_new_session=not IS_WINDOWS,
    )
    thread = threading.Thread(
        target=stream_output,
        args=(service, process),
        name=f"{service.name.lower()}-output",
        daemon=True,
    )
    thread.start()
    print(
        f"{paint(f'[{service.name}]', service.color)} Started monitor for PID {process.pid}."
    )
    return RunningService(service, process, thread)


def stop_service(running: RunningService) -> None:
    process = running.process
    if process.poll() is not None:
        return

    print(f"Stopping {running.definition.name} (PID {process.pid})...")
    if IS_WINDOWS:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)

    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def service_is_healthy(service: Service, timeout: float = 0.8) -> bool:
    if not service.health_url:
        return False
    try:
        request = urllib.request.Request(service.health_url, method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status < 500
    except urllib.error.HTTPError as error:
        return error.code < 500
    except (OSError, urllib.error.URLError):
        return False


def build_services(include_desktop: bool) -> list[Service]:
    python = ROOT / ".venv" / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
    npm = "npm.cmd" if IS_WINDOWS else "npm"
    services = [
        Service(
            "Backend",
            [
                str(python),
                "-m",
                "uvicorn",
                "trendrelay_api.main:app",
                "--app-dir",
                "services/api/src",
                "--host",
                "0.0.0.0",
                "--port",
                "8080",
                "--reload",
                "--reload-dir",
                "services/api/src",
            ],
            "cyan",
            "http://127.0.0.1:8080/healthz",
        ),
        Service(
            "Frontend",
            [
                npm,
                "run",
                "dev",
                "--workspace=@trendrelay/web",
                "--",
                "--hostname",
                "0.0.0.0",
                "--port",
                "3000",
            ],
            "green",
            "http://127.0.0.1:3000/",
        ),
    ]
    services.append(
        Service(
            "Worker",
            [str(python), "scripts/worker.py", "--watch"],
            "yellow",
        )
    )
    if include_desktop:
        services.append(Service("Desktop", [npm, "run", "dev:desktop"], "magenta"))
    return services


def partition_services(services: list[Service]) -> tuple[list[Service], list[Service]]:
    reused: list[Service] = []
    startable: list[Service] = []
    for service in services:
        if service.health_url and service_is_healthy(service):
            reused.append(service)
        else:
            startable.append(service)
    return reused, startable


def wait_until_healthy(service: Service, timeout: float = 30) -> bool:
    if not service.health_url:
        return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if service_is_healthy(service):
            return True
        time.sleep(0.25)
    return False


def validation_errors(include_desktop: bool, services: list[Service]) -> list[str]:
    errors: list[str] = []
    python = Path(services[0].command[0])
    if not python.is_file():
        errors.append("Python environment missing. Run start.cmd first.")
    for executable in ("node", "npm"):
        if not shutil.which(executable):
            errors.append(f"{executable} is not available on PATH.")
    if include_desktop:
        electron = (
            ROOT
            / "node_modules"
            / "electron"
            / "dist"
            / ("electron.exe" if IS_WINDOWS else "electron")
        )
        if not electron.is_file():
            errors.append(
                "Electron binary missing. Run start-electron.bat to repair it automatically."
            )
    return errors


def print_banner(include_desktop: bool) -> None:
    width = 62
    print("=" * width)
    print("           TrendRelay - Unified Dev Runner")
    print("=" * width)
    print("   - Backend:  http://0.0.0.0:8080")
    print("   - API docs: http://0.0.0.0:8080/docs")
    print("   - Frontend: http://0.0.0.0:3000")
    print("   - Worker:    durable SQL queue (hot reload)")
    print(
        f"   - Desktop:  {'enabled' if include_desktop else 'disabled (use start-electron.bat)'}"
    )
    print("=" * width)
    print("\nStarting or reusing hot-reload services (staggered)...\n")


def open_browser_app(include_desktop: bool) -> bool:
    if include_desktop:
        return False
    print("Opening browser...")
    return webbrowser.open("http://127.0.0.1:3000/")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--desktop", action="store_true", help="also launch the Electron shell"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate commands and configuration without starting services",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    services = build_services(args.desktop)
    errors = validation_errors(args.desktop, services)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    print_banner(args.desktop)
    if args.check:
        print("Unified runner checks passed.")
        return 0

    reused, startable = partition_services(services)
    for service in reused:
        print(
            f"{paint(f'[{service.name}]', service.color)} Reusing healthy service at {service.health_url}."
        )

    running: list[RunningService] = []
    try:
        for index, service in enumerate(startable):
            running.append(start_service(service))
            if service.health_url and not wait_until_healthy(service):
                print(
                    f"{service.name} did not become ready at {service.health_url} within 30 seconds."
                )
                return 1
            if index < len(startable) - 1:
                time.sleep(0.25)

        open_browser_app(args.desktop)

        next_health_check = time.monotonic() + 2
        while True:
            for item in running:
                return_code = item.process.poll()
                if return_code is not None:
                    print(f"{item.definition.name} exited with code {return_code}.")
                    return return_code or 1
            if reused and time.monotonic() >= next_health_check:
                for service in reused:
                    if not service_is_healthy(service):
                        print(f"Reused {service.name} service is no longer available.")
                        return 1
                next_health_check = time.monotonic() + 2
            time.sleep(0.25)
    except KeyboardInterrupt:
        print("\nShutdown requested. Stopping TrendRelay...")
        return 0
    finally:
        for item in reversed(running):
            stop_service(item)


if __name__ == "__main__":
    raise SystemExit(main())
