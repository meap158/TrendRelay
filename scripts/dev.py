"""Unified hot-reload development runner for TrendRelay."""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass, field
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
    environment: dict[str, str] | None = None
    health_timeout: float = 30
    health_probe_timeout: float = 0.8
    health_failure_limit: int = 3
    relay_output: bool = True
    port: int | None = None
    restart_on_exit: bool = False
    restart_limit: int = 5
    restart_window: float = 60
    required: bool = True


@dataclass
class RunningService:
    definition: Service
    process: subprocess.Popen[str]
    output_thread: threading.Thread | None
    restart_times: list[float] = field(default_factory=list)


COLORS = {
    "cyan": "\033[96m",
    "green": "\033[92m",
    "magenta": "\033[95m",
    "yellow": "\033[93m",
    "reset": "\033[0m",
}


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def _kill_port_holders(port: int) -> bool:
    try:
        if IS_WINDOWS:
            result = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            pids: list[int] = []
            for line in result.stdout.splitlines():
                parts = line.split()
                if (
                    len(parts) >= 5
                    and f":{port}" in parts[1]
                    and parts[3] == "LISTENING"
                ):
                    try:
                        pids.append(int(parts[4]))
                    except ValueError:
                        pass
            for pid in dict.fromkeys(pids):
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        else:
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            for token in result.stdout.split():
                try:
                    os.kill(int(token), signal.SIGTERM)
                except (ValueError, OSError):
                    pass
    except (OSError, subprocess.TimeoutExpired):
        pass
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if _port_is_free(port):
            return True
        time.sleep(0.25)
    return False


def find_free_port(preferred: int, name: str, max_attempts: int = 20) -> int:
    if _port_is_free(preferred):
        return preferred
    print(f"Port {preferred} is in use. Attempting to free it for {name}...")
    if _kill_port_holders(preferred):
        print(f"Port {preferred} is now free.")
        return preferred
    print(f"Could not free port {preferred}. Searching for an alternative...")
    for offset in range(1, max_attempts):
        candidate = preferred + offset
        if _port_is_free(candidate):
            print(f"{name} will use port {candidate} instead of {preferred}.")
            return candidate
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    print(f"{name} will use port {port} instead of {preferred}.")
    return port


def _cleanup_stale_nextjs() -> None:
    next_dir = ROOT / "apps" / "web" / ".next"
    if not next_dir.is_dir():
        return
    pid_file = next_dir / "dev" / "pid"
    if pid_file.is_file():
        try:
            pid = int(pid_file.read_text().strip())
            if IS_WINDOWS:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                os.kill(pid, signal.SIGTERM)
        except (ValueError, OSError):
            pass
    dev_dir = next_dir / "dev"
    if dev_dir.is_dir():
        shutil.rmtree(dev_dir, ignore_errors=True)


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
    creation_flags = (
        subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        if IS_WINDOWS
        else 0
    )
    process = subprocess.Popen(
        service.command,
        cwd=ROOT,
        stdout=subprocess.PIPE if service.relay_output else subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=creation_flags,
        start_new_session=not IS_WINDOWS,
        env={**os.environ, **(service.environment or {})},
    )
    thread: threading.Thread | None = None
    if service.relay_output:
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


def restart_exited_service(
    running: RunningService, now: float | None = None
) -> RunningService | None:
    service = running.definition
    if not service.restart_on_exit:
        return None

    restarted_at = time.monotonic() if now is None else now
    recent_restarts = [
        timestamp
        for timestamp in running.restart_times
        if restarted_at - timestamp < service.restart_window
    ]
    if len(recent_restarts) >= service.restart_limit:
        print(
            f"{service.name} exited {service.restart_limit + 1} times within "
            f"{service.restart_window:g} seconds; stopping TrendRelay."
        )
        return None

    attempt = len(recent_restarts) + 1
    return_code = running.process.poll()
    print(
        f"{service.name} watcher exited with code {return_code}; "
        f"restarting ({attempt}/{service.restart_limit})..."
    )
    replacement = start_service(service)
    replacement.restart_times = [*recent_restarts, restarted_at]
    return replacement


def stop_service(running: RunningService) -> None:
    process = running.process
    if process.poll() is not None:
        return

    print(f"Stopping {running.definition.name} (PID {process.pid})...")
    if IS_WINDOWS and running.definition.name == "Postiz":
        try:
            process.send_signal(signal.CTRL_BREAK_EVENT)
            process.wait(timeout=15)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass

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


def service_is_healthy(service: Service, timeout: float | None = None) -> bool:
    if not service.health_url:
        return False
    try:
        request = urllib.request.Request(service.health_url, method="GET")
        with urllib.request.urlopen(
            request,
            timeout=service.health_probe_timeout if timeout is None else timeout,
        ) as response:
            return response.status < 500
    except urllib.error.HTTPError:
        return False
    except (OSError, urllib.error.URLError):
        return False


def build_services(include_desktop: bool) -> list[Service]:
    python = ROOT / ".venv" / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
    npm = "npm.cmd" if IS_WINDOWS else "npm"

    _cleanup_stale_nextjs()
    backend_port = find_free_port(8011, "Backend")
    frontend_port = find_free_port(3001, "Frontend")

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
                str(backend_port),
                "--reload",
                "--reload-dir",
                "services/api/src",
            ],
            "cyan",
            f"http://127.0.0.1:{backend_port}/api/auth/local-session",
            restart_on_exit=True,
            port=backend_port,
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
                str(frontend_port),
            ],
            "green",
            f"http://127.0.0.1:{frontend_port}/",
            {"NEXT_PUBLIC_API_URL": f"http://127.0.0.1:{backend_port}"},
            restart_on_exit=True,
            port=frontend_port,
            health_timeout=120,
        ),
    ]
    services.append(
        Service(
            "Postiz",
            [str(python), "scripts/postiz_service.py", "run"],
            "magenta",
            "http://localhost:4200/auth",
            health_timeout=480,
            health_probe_timeout=5,
            health_failure_limit=6,
            relay_output=False,
            required=False,
            port=4200,
        )
    )
    services.append(
        Service(
            "Worker",
            [str(python), "scripts/worker.py", "--watch"],
            "yellow",
            restart_on_exit=True,
        )
    )
    if include_desktop:
        services.append(
            Service(
                "Desktop",
                [npm, "run", "dev:desktop"],
                "magenta",
                environment={
                    "TRENDRELAY_API_URL": f"http://127.0.0.1:{backend_port}",
                    "TRENDRELAY_WEB_URL": f"http://localhost:{frontend_port}",
                },
            )
        )
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


def wait_until_healthy(running: RunningService, timeout: float = 30) -> bool:
    service = running.definition
    if not service.health_url:
        return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if running.process.poll() is not None:
            return False
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


def print_banner(include_desktop: bool, services: list[Service]) -> None:
    backend = next((s for s in services if s.name == "Backend"), None)
    frontend = next((s for s in services if s.name == "Frontend"), None)
    backend_port = backend.port if backend else 8011
    frontend_port = frontend.port if frontend else 3001
    width = 62
    print("=" * width)
    print("           TrendRelay - Unified Dev Runner")
    print("=" * width)
    print(f"   - Backend:  http://0.0.0.0:{backend_port}")
    print(f"   - API docs: http://0.0.0.0:{backend_port}/docs")
    print(f"   - Frontend: http://0.0.0.0:{frontend_port}")
    print("   - Postiz:   http://localhost:4200 (optional, starts in background)")
    print("   - Worker:    durable SQL queue (hot reload)")
    print(
        f"   - Desktop:  {'enabled' if include_desktop else 'disabled (use start-electron.bat)'}"
    )
    print("=" * width)
    print("\nStarting or reusing hot-reload services (staggered)...\n")


def open_browser_app(include_desktop: bool, services: list[Service]) -> bool:
    if include_desktop:
        return False
    frontend = next((s for s in services if s.name == "Frontend"), None)
    frontend_port = frontend.port if frontend else 3001
    print("Opening browser...")
    return webbrowser.open(f"http://127.0.0.1:{frontend_port}/")


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

    print_banner(args.desktop, services)
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
            if service.health_url and service.required and not wait_until_healthy(
                running[-1], service.health_timeout
            ):
                print(
                    f"{service.name} did not become ready at {service.health_url} "
                    f"within {service.health_timeout:g} seconds."
                )
                return 1
            if service.health_url and not service.required:
                print(
                    f"{paint(f'[{service.name}]', service.color)} Optional service is "
                    "starting in the background."
                )
            if index < len(startable) - 1:
                time.sleep(0.25)

        open_browser_app(args.desktop, services)

        next_health_check = time.monotonic() + 2
        reused_failures = {service.name: 0 for service in reused}
        while True:
            index = 0
            while index < len(running):
                item = running[index]
                return_code = item.process.poll()
                if return_code is not None:
                    replacement = restart_exited_service(item)
                    if replacement is None:
                        if not item.definition.required:
                            print(
                                f"Optional {item.definition.name} service exited with "
                                f"code {return_code}; TrendRelay will keep running."
                            )
                            running.pop(index)
                            continue
                        print(f"{item.definition.name} exited with code {return_code}.")
                        return return_code or 1
                    running[index] = replacement
                    if replacement.definition.health_url and not wait_until_healthy(
                        replacement, replacement.definition.health_timeout
                    ):
                        print(
                            f"Restarted {replacement.definition.name} did not become "
                            f"ready at {replacement.definition.health_url}."
                        )
                        return 1
                index += 1
            if reused and time.monotonic() >= next_health_check:
                for service in list(reused):
                    if service_is_healthy(service):
                        reused_failures[service.name] = 0
                        continue
                    reused_failures[service.name] += 1
                    if reused_failures[service.name] >= service.health_failure_limit:
                        if not service.required:
                            print(
                                f"Optional reused {service.name} service is no longer "
                                "available; TrendRelay will keep running."
                            )
                            reused.remove(service)
                            continue
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
