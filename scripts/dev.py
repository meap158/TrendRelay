"""Unified hot-reload development runner for TrendRelay."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
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
        ),
    ]
    if include_desktop:
        services.append(Service("Desktop", [npm, "run", "dev:desktop"], "magenta"))
    return services


def print_banner(include_desktop: bool) -> None:
    width = 62
    print("=" * width)
    print("           TrendRelay - Unified Dev Runner")
    print("=" * width)
    print("   - Backend:  http://0.0.0.0:8080")
    print("   - API docs: http://0.0.0.0:8080/docs")
    print("   - Frontend: http://0.0.0.0:3000")
    print(
        f"   - Desktop:  {'enabled' if include_desktop else 'disabled (use --desktop)'}"
    )
    print("=" * width)
    print("\nStarting hot-reload servers (staggered)...\n")


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
    python = Path(services[0].command[0])
    if not python.is_file():
        print("Python environment missing. Run start.cmd first.", file=sys.stderr)
        return 1

    print_banner(args.desktop)
    if args.check:
        print("Unified runner checks passed.")
        return 0

    running: list[RunningService] = []
    try:
        for index, service in enumerate(services):
            running.append(start_service(service))
            if index < len(services) - 1:
                time.sleep(1.25)

        while True:
            for item in running:
                return_code = item.process.poll()
                if return_code is not None:
                    print(f"{item.definition.name} exited with code {return_code}.")
                    return return_code or 1
            time.sleep(0.25)
    except KeyboardInterrupt:
        print("\nShutdown requested. Stopping TrendRelay...")
        return 0
    finally:
        for item in reversed(running):
            stop_service(item)


if __name__ == "__main__":
    raise SystemExit(main())
