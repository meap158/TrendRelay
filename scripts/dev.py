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
    # Readiness may include a first compile; liveness polling stays snappy.
    ready_probe_timeout: float = 15
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
    # Free twice in a row before believing it. `npm run dev` spawns the real
    # server as a grandchild, so killing the wrapper can leave the socket held
    # for a moment longer; a single passing check hands the port to a
    # replacement that then dies on EADDRINUSE, restarts, and burns its budget.
    deadline = time.monotonic() + 8
    confirmations = 0
    while time.monotonic() < deadline:
        if _port_is_free(port):
            confirmations += 1
            if confirmations >= 2:
                return True
        else:
            confirmations = 0
        time.sleep(0.25)
    return False


def find_free_port(
    preferred: int, name: str, max_attempts: int = 20, *, may_terminate: bool = True
) -> int:
    """Choose a port, freeing it if something else is squatting on it.

    `may_terminate=False` makes this read-only, which is what `--check` needs:
    that flag is documented as validating "without starting services", and a
    check that kills the stack it was asked to inspect is worse than no check.
    Finding a port in use is not an error there - it usually means TrendRelay
    is already running, which is the thing being checked for.
    """
    if _port_is_free(preferred):
        return preferred
    if not may_terminate:
        print(f"Port {preferred} is in use (something is already serving it).")
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
    # The exiting process can outlive its own exit code and keep the socket, so
    # the replacement would hit EADDRINUSE and exit, restart, and fail again
    # until the budget ran out and took the whole stack down.
    if service.port and not _port_is_free(service.port):
        print(f"Freeing port {service.port} before restarting {service.name}...")
        _kill_port_holders(service.port)
    replacement = start_service(service)
    replacement.restart_times = [*recent_restarts, restarted_at]
    return replacement


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


def build_services(include_desktop: bool, *, may_terminate: bool = True) -> list[Service]:
    python = ROOT / ".venv" / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
    npm = "npm.cmd" if IS_WINDOWS else "npm"

    _cleanup_stale_nextjs()
    backend_port = find_free_port(8011, "Backend", may_terminate=may_terminate)
    frontend_port = find_free_port(3001, "Frontend", may_terminate=may_terminate)

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
            # Poll for changes rather than subscribe to them.
            #
            # The event-driven watcher stopped delivering on a long-running
            # backend: edits to the API, and a touch of main.py, left the worker
            # from hours earlier still serving. A fresh process with these exact
            # arguments reloads correctly, so the watch is being lost rather
            # than never set up - which fits ReadDirectoryChangesW dropping a
            # subscription under load and never getting it back.
            #
            # Polling cannot be lost that way, and it is a stat() over a few
            # hundred files. A reloader that silently stops is worse than one
            # that costs a little: the failure looks like the code not working.
            environment={"WATCHFILES_FORCE_POLLING": "1"},
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
        if service_is_healthy(service, service.ready_probe_timeout):
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


RUNNER_LOCK = ROOT / ".data" / "dev-runner.pid"


def _process_alive(pid: int) -> bool:
    """Whether a pid is a live process. Never trusts a stale file."""
    if pid <= 0:
        return False
    try:
        if IS_WINDOWS:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=10,
            )
            return str(pid) in result.stdout
        os.kill(pid, 0)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def existing_runner() -> int | None:
    """The pid of another dev runner, if one is genuinely still alive.

    Two runners is the failure this exists to stop, and it is not a rare
    mistake: each one supervises its services and restarts them when they exit,
    so the second one kills the first one's frontend to take the port, the
    first one restarts it, and they trade the port until a restart budget runs
    out. The visible symptom is EADDRINUSE and an app that will not start,
    which points at the port rather than at the two runners fighting over it.
    """
    try:
        pid = int(RUNNER_LOCK.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    if pid == os.getpid() or not _process_alive(pid):
        # A crashed runner leaves its file behind; that must not block a start.
        RUNNER_LOCK.unlink(missing_ok=True)
        return None
    return pid


def claim_runner_lock() -> None:
    RUNNER_LOCK.parent.mkdir(parents=True, exist_ok=True)
    RUNNER_LOCK.write_text(str(os.getpid()), encoding="utf-8")


def release_runner_lock() -> None:
    try:
        if int(RUNNER_LOCK.read_text(encoding="utf-8").strip()) == os.getpid():
            RUNNER_LOCK.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


def main() -> int:
    args = parse_args()
    running_pid = existing_runner()
    if running_pid and not args.check:
        print(
            f"TrendRelay is already running in another terminal (PID {running_pid}). "
            "Stop that one first, or use it - two runners fight over the same "
            "ports and neither wins.",
            file=sys.stderr,
        )
        return 1

    # A check must not disturb what it is checking.
    services = build_services(args.desktop, may_terminate=not args.check)
    errors = validation_errors(args.desktop, services)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    print_banner(args.desktop, services)
    if args.check:
        if running_pid:
            print(f"A dev runner is already active (PID {running_pid}).")
        print("Unified runner checks passed.")
        return 0

    reused, startable = partition_services(services)
    for service in reused:
        print(
            f"{paint(f'[{service.name}]', service.color)} Reusing healthy service at {service.health_url}."
        )

    running: list[RunningService] = []
    # Claimed only once this process is actually going to supervise services,
    # so a failed validation never leaves a lock behind.
    claim_runner_lock()
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
                        # A dev server can crash and recompile slowly. Recycle it
                        # and let the restart budget decide when to give up,
                        # instead of taking the whole stack down on one miss.
                        print(
                            f"Restarted {replacement.definition.name} did not become "
                            f"ready at {replacement.definition.health_url}; recycling it."
                        )
                        stop_service(replacement)
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
        release_runner_lock()
        for item in reversed(running):
            stop_service(item)


if __name__ == "__main__":
    raise SystemExit(main())
