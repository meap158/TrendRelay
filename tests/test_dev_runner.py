import json
import os
from pathlib import Path

import scripts.dev as dev


class HealthyResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def test_health_probe_accepts_existing_service(monkeypatch) -> None:
    service = dev.Service("Frontend", ["npm"], "green", "http://127.0.0.1:3000/")
    monkeypatch.setattr(
        dev.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: HealthyResponse(),
    )

    assert dev.service_is_healthy(service) is True


def test_health_probe_rejects_unavailable_service(monkeypatch) -> None:
    service = dev.Service("Frontend", ["npm"], "green", "http://127.0.0.1:3000/")
    monkeypatch.setattr(
        dev.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline")),
    )

    assert dev.service_is_healthy(service) is False


def test_desktop_validation_requires_electron_binary(
    monkeypatch, tmp_path: Path
) -> None:
    python = tmp_path / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    services = [dev.Service("Backend", [str(python)], "cyan")]
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    monkeypatch.setattr(dev.shutil, "which", lambda _name: "available")

    errors = dev.validation_errors(True, services)

    assert errors == [
        "Electron binary missing. Run start-electron.bat to repair it automatically."
    ]


def test_desktop_service_has_no_health_probe() -> None:
    desktop = dev.build_services(True)[-1]

    assert desktop.name == "Desktop"
    assert desktop.health_url is None


def test_partition_reuses_healthy_frontend_and_starts_desktop(monkeypatch) -> None:
    frontend = dev.Service("Frontend", ["npm"], "green", "http://127.0.0.1:3000/")
    desktop = dev.Service("Desktop", ["npm"], "magenta")
    monkeypatch.setattr(dev, "service_is_healthy", lambda service: service is frontend)

    reused, startable = dev.partition_services([frontend, desktop])

    assert reused == [frontend]
    assert startable == [desktop]


def test_wait_until_healthy_retries_until_service_is_ready(monkeypatch) -> None:
    backend = dev.Service(
        "Backend", ["python"], "cyan", "http://127.0.0.1:8080/healthz"
    )
    results = iter([False, False, True])
    monkeypatch.setattr(dev, "service_is_healthy", lambda _service, *_timeout: next(results))
    monkeypatch.setattr(dev.time, "sleep", lambda _seconds: None)

    class Process:
        def poll(self):
            return None

    running = dev.RunningService(backend, Process(), None)
    assert dev.wait_until_healthy(running) is True


def test_windows_services_use_an_isolated_hidden_process_group(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Process:
        stdout = None
        pid = 42

    def fake_popen(*args, **kwargs):
        captured.update(kwargs)
        return Process()

    monkeypatch.setattr(dev, "IS_WINDOWS", True)
    monkeypatch.setattr(dev.subprocess, "Popen", fake_popen)

    dev.start_service(dev.Service("Backend", ["python"], "cyan", relay_output=False))

    assert captured["creationflags"] == (
        dev.subprocess.CREATE_NEW_PROCESS_GROUP | dev.subprocess.CREATE_NO_WINDOW
    )


def test_windows_launcher_applies_migrations_before_starting() -> None:
    launcher = (Path(__file__).resolve().parents[1] / "start.cmd").read_text(
        encoding="utf-8"
    )

    migration = launcher.index("scripts\\db.py upgrade")
    runner = launcher.index("scripts\\dev.py %*")
    assert migration < runner


def test_windows_launcher_uses_observable_dependency_bootstrap() -> None:
    launcher = (Path(__file__).resolve().parents[1] / "start.cmd").read_text(
        encoding="utf-8"
    )

    assert r"scripts\bootstrap.py" in launcher
    assert "--quiet" not in launcher
    assert "npm ci --no-audit --no-fund" in launcher
    assert "Node.js 22 or newer" in launcher
    assert "Python 3.12 or newer" in launcher


def test_windows_launcher_verifies_javascript_runtime_dependencies() -> None:
    root = Path(__file__).resolve().parents[1]
    launcher = (root / "start.cmd").read_text(encoding="utf-8")
    package = json.loads((root / "package.json").read_text(encoding="utf-8"))

    workflow = (root / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert r"scripts\check_node_dependencies.mjs" in launcher
    assert package["packageManager"] == "npm@11.16.0"
    assert "npm install --global npm@11.16.0" in workflow
    assert package["allowScripts"] == {
        "@derhuerst/ffprobe-static@5.3.0": True,
        "@swc/core@1.15.43": True,
        "esbuild@0.25.12": True,
        "esbuild@0.28.1": True,
        "ffmpeg-static@5.3.0": True,
        "unrs-resolver@1.12.2": True,
    }


def test_windows_launcher_check_mode_uses_parsed_flag() -> None:
    launcher = (Path(__file__).resolve().parents[1] / "start.cmd").read_text(
        encoding="utf-8"
    )

    assert 'if "%TRENDRELAY_CHECK_REQUESTED%"=="1" (' in launcher
    assert "TRENDRELAY_START_CHECK" not in launcher


def test_unified_runner_includes_hot_reload_durable_worker() -> None:
    worker = next(
        service for service in dev.build_services(False) if service.name == "Worker"
    )

    assert worker.command[1:3] == ["scripts/worker.py", "--watch"]
    # Told which runner owns it, so a hard stop leaves nothing behind.
    assert worker.command[3] == "--parent-pid"
    assert worker.health_url is None
    assert worker.restart_on_exit is True


def test_reload_services_restart_after_an_unexpected_watcher_exit(monkeypatch) -> None:
    class Process:
        def poll(self):
            return 7

    service = dev.Service(
        "Worker", ["python"], "yellow", restart_on_exit=True, restart_limit=2
    )
    running = dev.RunningService(service, Process(), None, [10.0])
    replacement = dev.RunningService(service, Process(), None)
    monkeypatch.setattr(dev, "start_service", lambda _service: replacement)

    result = dev.restart_exited_service(running, now=20.0)

    assert result is replacement
    assert result.restart_times == [10.0, 20.0]


def test_reload_service_stops_after_repeated_exits(monkeypatch) -> None:
    class Process:
        def poll(self):
            return 3

    service = dev.Service(
        "Backend",
        ["python"],
        "cyan",
        restart_on_exit=True,
        restart_limit=2,
        restart_window=30,
    )
    running = dev.RunningService(service, Process(), None, [90.0, 95.0])
    monkeypatch.setattr(
        dev,
        "start_service",
        lambda _service: (_ for _ in ()).throw(AssertionError("must not restart")),
    )

    assert dev.restart_exited_service(running, now=100.0) is None


def test_backend_frontend_and_worker_are_reload_resilient() -> None:
    services = {service.name: service for service in dev.build_services(False)}

    assert services["Backend"].restart_on_exit is True
    assert services["Frontend"].restart_on_exit is True
    assert services["Worker"].restart_on_exit is True
    assert services["Backend"].required is True
    assert "Postiz" not in services


def test_browser_app_opens_after_startup(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(dev.webbrowser, "open", lambda url: opened.append(url) or True)
    services = dev.build_services(False)

    assert dev.open_browser_app(False, services) is True
    assert opened == ["http://127.0.0.1:3001/"]

    assert dev.open_browser_app(True, services) is False
    assert opened == ["http://127.0.0.1:3001/"]


def test_browser_opens_only_after_frontend_health_gate() -> None:
    source = (Path(__file__).resolve().parents[1] / "scripts" / "dev.py").read_text(
        encoding="utf-8"
    )

    health_gate = source.index(
        "if service.health_url and service.required and not wait_until_healthy("
    )
    browser_open = source.index("open_browser_app(args.desktop, services)")
    assert health_gate < browser_open


def test_runner_passes_its_backend_url_to_browser_and_desktop() -> None:
    services = dev.build_services(True)
    frontend = next(service for service in services if service.name == "Frontend")
    desktop = next(service for service in services if service.name == "Desktop")

    assert frontend.environment == {"NEXT_PUBLIC_API_URL": "http://127.0.0.1:8011"}
    assert desktop.environment == {
        "TRENDRELAY_API_URL": "http://127.0.0.1:8011",
        "TRENDRELAY_WEB_URL": "http://localhost:3001",
    }


def test_windows_launcher_no_longer_prepares_a_local_publishing_service() -> None:
    root = Path(__file__).resolve().parents[1]
    launcher = (root / "start.cmd").read_text(encoding="utf-8")

    assert "postiz" not in launcher.lower()
    assert "postiz" not in (root / "scripts" / "dev.py").read_text(encoding="utf-8").lower()


def test_readiness_allows_a_slow_first_render(monkeypatch) -> None:
    """A dev server compiles the page on the first request.

    Liveness polling stays on a short budget, but readiness must wait long
    enough for that compile, or a healthy server is recycled as dead.
    """
    service = dev.Service("Frontend", ["noop"], "green", "http://127.0.0.1:3001/")
    assert service.health_probe_timeout < 1
    assert service.ready_probe_timeout >= 10

    seen: list[float | None] = []

    def probe(_service, timeout=None):
        seen.append(timeout)
        # Only a request allowed more than a second gets an answer.
        return timeout is not None and timeout > 1

    class Alive:
        def poll(self):
            return None

    monkeypatch.setattr(dev, "service_is_healthy", probe)
    running = dev.RunningService(service, Alive(), None)

    assert dev.wait_until_healthy(running, timeout=2) is True
    assert seen and seen[0] == service.ready_probe_timeout


def test_a_restart_frees_a_port_the_old_process_still_holds(monkeypatch) -> None:
    """A held socket used to make every restart fail with EADDRINUSE."""
    service = dev.Service(
        "Frontend", ["noop"], "green", "http://127.0.0.1:3001/",
        restart_on_exit=True, port=3001,
    )

    class Exited:
        def poll(self):
            return 1

    freed: list[int] = []
    monkeypatch.setattr(dev, "_port_is_free", lambda _port: False)
    monkeypatch.setattr(dev, "_kill_port_holders", lambda port: freed.append(port) or True)
    monkeypatch.setattr(dev, "start_service", lambda definition: dev.RunningService(definition, Exited(), None))

    dev.restart_exited_service(dev.RunningService(service, Exited(), None), now=100.0)

    assert freed == [3001], "the port must be released before the replacement starts"


def test_a_free_port_is_left_alone(monkeypatch) -> None:
    """Nothing is killed when the socket is already available."""
    service = dev.Service("Frontend", ["noop"], "green", restart_on_exit=True, port=3001)

    class Exited:
        def poll(self):
            return 1

    freed: list[int] = []
    monkeypatch.setattr(dev, "_port_is_free", lambda _port: True)
    monkeypatch.setattr(dev, "_kill_port_holders", lambda port: freed.append(port) or True)
    monkeypatch.setattr(dev, "start_service", lambda definition: dev.RunningService(definition, Exited(), None))

    dev.restart_exited_service(dev.RunningService(service, Exited(), None), now=100.0)

    assert freed == []


def _stage_dev_dir(root: Path, name: str, pid: int) -> Path:
    dev_dir = root / "apps" / "web" / name / "dev"
    dev_dir.mkdir(parents=True)
    (dev_dir / "pid").write_text(str(pid), encoding="utf-8")
    return dev_dir


def test_a_stale_dev_server_is_found_where_it_actually_builds(monkeypatch, tmp_path) -> None:
    """The dev server moved directories, and this is what has to follow it.

    `next dev` builds into `.next-dev` so that `next build` cannot overwrite the
    files a running app is serving from. If this cleanup kept looking only in
    `.next`, a leftover dev server would never be killed - it would keep holding
    the frontend port, and the runner would quietly move to another one.
    """
    killed: list[int] = []
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    monkeypatch.setattr(dev.subprocess, "run", lambda cmd, **_: killed.append(int(cmd[2])))
    monkeypatch.setattr(dev.os, "kill", lambda pid, _signal: killed.append(pid))
    monkeypatch.setattr(dev, "IS_WINDOWS", True)
    dev_dir = _stage_dev_dir(tmp_path, ".next-dev", 4242)

    dev._cleanup_stale_nextjs()

    assert killed == [4242]
    assert not dev_dir.exists()


def test_a_dev_server_left_by_the_old_layout_is_still_cleaned(monkeypatch, tmp_path) -> None:
    # A checkout from before the split still has .next/dev/pid in it, and the
    # process it names is exactly what this exists to kill.
    killed: list[int] = []
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    monkeypatch.setattr(dev.subprocess, "run", lambda cmd, **_: killed.append(int(cmd[2])))
    monkeypatch.setattr(dev, "IS_WINDOWS", True)
    legacy = _stage_dev_dir(tmp_path, ".next", 99)

    dev._cleanup_stale_nextjs()

    assert killed == [99]
    assert not legacy.exists()


def test_a_production_build_is_left_alone(monkeypatch, tmp_path) -> None:
    """Only the dev subdirectory goes.

    Removing .next itself would delete a production build every time the app
    started, which is not this function's business.
    """
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    monkeypatch.setattr(dev, "IS_WINDOWS", True)
    build = tmp_path / "apps" / "web" / ".next"
    build.mkdir(parents=True)
    (build / "BUILD_ID").write_text("abc", encoding="utf-8")

    dev._cleanup_stale_nextjs()

    assert (build / "BUILD_ID").is_file()


def test_nothing_to_clean_is_not_an_error(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    dev._cleanup_stale_nextjs()


def test_the_worker_is_told_which_runner_started_it() -> None:
    """So a hard stop of the runner does not leave one behind.

    The runner reclaims its ports on the way back up, which kills a leftover API
    or dev server. The worker holds no port, so nothing ever noticed it: three
    generations were found alive at once, all polling the same SQLite database
    as the API that was being waited on.
    """
    worker = next(
        service for service in dev.build_services(False) if service.name == "Worker"
    )

    assert "--parent-pid" in worker.command
    assert worker.command[worker.command.index("--parent-pid") + 1] == str(os.getpid())


class StoppableProcess:
    """A child that exits when asked, after `waits_before_exit` checks."""

    def __init__(self, waits_before_exit: int = 0) -> None:
        self.pid = 4321
        self.waits_before_exit = waits_before_exit
        self.exited = False
        self.killed = False

    def poll(self):
        return 0 if self.exited else None

    def wait(self, timeout=None):
        if self.waits_before_exit <= 0:
            self.exited = True
            return 0
        self.waits_before_exit -= 1
        raise dev.subprocess.TimeoutExpired("cmd", timeout)

    def kill(self):
        self.killed = True
        self.exited = True


def test_a_service_is_asked_to_stop_before_it_is_forced(monkeypatch) -> None:
    """Forcing corrupts Turbopack's cache database.

    `taskkill /F` is a SIGKILL: killing the dev server mid-write leaves that
    database unreadable, after which every route answers 500 and the only cure
    is deleting the build directory. A normal shutdown is the common case and
    deserves the second it costs to exit properly.
    """
    signals: list[int] = []
    forced: list[list[str]] = []
    monkeypatch.setattr(dev, "IS_WINDOWS", True)
    monkeypatch.setattr(dev.os, "kill", lambda _pid, sig: signals.append(sig))
    monkeypatch.setattr(dev.subprocess, "run", lambda cmd, **_: forced.append(cmd))
    process = StoppableProcess()

    dev.stop_service(dev.RunningService(dev.Service("Frontend", ["npm"], "green"), process, None))

    assert signals == [dev.signal.CTRL_BREAK_EVENT]
    assert forced == [], "a child that stopped on request must not be killed as well"


def test_a_service_that_ignores_the_request_is_still_forced(monkeypatch) -> None:
    # Graceful is a preference, not a promise: a wedged dev server must not keep
    # the runner hanging on shutdown.
    forced: list[list[str]] = []
    monkeypatch.setattr(dev, "IS_WINDOWS", True)
    monkeypatch.setattr(dev.os, "kill", lambda _pid, _sig: None)
    monkeypatch.setattr(dev.subprocess, "run", lambda cmd, **_: forced.append(cmd))
    process = StoppableProcess(waits_before_exit=1)

    dev.stop_service(dev.RunningService(dev.Service("Frontend", ["npm"], "green"), process, None))

    assert forced and forced[0][:2] == ["taskkill", "/PID"]
    assert "/F" in forced[0]


def test_the_backend_is_watched_by_the_runner_not_by_uvicorn(monkeypatch, tmp_path) -> None:
    """`--reload` works and then quietly stops.

    A backend left running for an hour served the code it started with, however
    many times its files were touched, while the identical command in a fresh
    process reloaded every time. The watch is lost rather than never set up, so
    it lives in the supervisor loop now.
    """
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    monkeypatch.setattr(dev.shutil, "which", lambda _name: "available")
    monkeypatch.setattr(dev, "find_free_port", lambda preferred, *_a, **_k: preferred)

    backend = next(
        service for service in dev.build_services(False) if service.name == "Backend"
    )

    assert "--reload" not in backend.command, "two reloaders would fight over one process"
    assert backend.reload_roots == ("services/api/src",)


def test_a_source_snapshot_notices_an_edit(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    source = tmp_path / "api"
    source.mkdir()
    module = source / "thing.py"
    module.write_text("x = 1", encoding="utf-8")

    before = dev.source_snapshot(("api",))
    # Longer, not just different. Two same-length writes inside one filesystem
    # clock tick produce an identical snapshot, which made an earlier version
    # of this test fail only when the suite ran fast enough.
    module.write_text("x = 1  # changed", encoding="utf-8")

    assert dev.source_snapshot(("api",)) != before


def test_a_snapshot_notices_an_edit_that_keeps_the_length(monkeypatch, tmp_path) -> None:
    """Size alone would miss it, so the modification time is compared too."""
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    source = tmp_path / "api"
    source.mkdir()
    module = source / "thing.py"
    module.write_text("x = 1", encoding="utf-8")
    before = dev.source_snapshot(("api",))

    module.write_text("x = 9", encoding="utf-8")
    # Set explicitly rather than hoping the clock moved: this is a test of what
    # the snapshot compares, not of the filesystem's timer resolution.
    stat = module.stat()
    os.utime(module, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    after = dev.source_snapshot(("api",))
    assert after != before
    assert [entry[2] for entry in after] == [entry[2] for entry in before], "same size"


def test_a_service_with_nothing_to_watch_snapshots_nothing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    assert dev.source_snapshot(()) == ()


def test_a_file_that_vanishes_mid_scan_does_not_stop_the_runner(monkeypatch, tmp_path) -> None:
    """A file being written as it is read is not worth crashing over."""
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    source = tmp_path / "api"
    source.mkdir()
    (source / "thing.py").write_text("x = 1", encoding="utf-8")

    def explode(self):
        raise OSError("gone")

    monkeypatch.setattr(dev.Path, "stat", explode)

    assert dev.source_snapshot(("api",)) == ()


# --- the build directory belongs to whatever is serving the port -------------
#
# These pin an ordering, which is not usually worth a test. This one is: the
# reverse order deleted a running server's files and left it answering
# "ENOENT: routes-manifest.json" to every request, permanently, because Next
# writes that manifest on a successful first build and never again.


def _record_order(monkeypatch, *, port_comes_free: bool) -> list[str]:
    order: list[str] = []

    def freeing(preferred, name, *args, **kwargs):
        order.append(f"free:{name}")
        if preferred == 3001 and not port_comes_free:
            return 3002
        return preferred

    monkeypatch.setattr(dev, "find_free_port", freeing)
    monkeypatch.setattr(dev, "_cleanup_stale_nextjs", lambda: order.append("delete"))
    return order


def test_the_port_is_freed_before_its_build_directory_is_deleted(monkeypatch) -> None:
    order = _record_order(monkeypatch, port_comes_free=True)

    dev.build_services(False)

    assert order.index("free:Frontend") < order.index("delete")


def test_a_frontend_that_could_not_take_its_port_refuses_to_start(monkeypatch) -> None:
    """Rather than starting a second server against the first one's directory.

    Two of them write the same webpack cache, which is what "Another write batch
    or compaction is already active" is from the inside.
    """
    order = _record_order(monkeypatch, port_comes_free=False)

    try:
        dev.build_services(False)
    except SystemExit as stop:
        assert "3001" in str(stop)
    else:
        raise AssertionError("starting on a fallback port should have been refused")

    assert "delete" not in order, "a live server's files must survive the refusal"


def test_check_mode_never_deletes_anything(monkeypatch) -> None:
    # `--check` is documented as validating without starting services, and a
    # check that wipes the running stack's build directory is worse than none.
    order = _record_order(monkeypatch, port_comes_free=False)

    dev.build_services(False, may_terminate=False)

    assert "delete" not in order


# --- one runner at a time -----------------------------------------------------
#
# Two runners is the failure behind every "it will not load" this week: the
# second one takes the first's ports and deletes the frontend's build directory
# while it is serving from it, leaving a live server with no files and no way
# back. The lock existed already; it was read at the start and written twenty
# seconds later, with the port probing in between, so two terminals started
# together both passed the read before either wrote.


def test_the_lock_is_taken_atomically_so_a_second_runner_loses(monkeypatch, tmp_path) -> None:
    lock = tmp_path / "dev-runner.pid"
    monkeypatch.setattr(dev, "RUNNER_LOCK", lock)
    first = os.getpid()

    assert dev.claim_runner_lock() is None, "the first runner takes it"

    # A second runner: a different pid, and the first one still alive.
    monkeypatch.setattr(dev.os, "getpid", lambda: 999_001)
    monkeypatch.setattr(dev, "_process_alive", lambda pid: True)

    assert dev.claim_runner_lock() == first, "the second is told who holds it"
    assert lock.read_text(encoding="utf-8").strip() == str(first), "and cannot overwrite it"


def test_a_crashed_runner_does_not_block_the_next_start(monkeypatch, tmp_path) -> None:
    """A lock file outliving its process must not need deleting by hand."""
    lock = tmp_path / "dev-runner.pid"
    monkeypatch.setattr(dev, "RUNNER_LOCK", lock)
    lock.write_text("424242", encoding="utf-8")
    monkeypatch.setattr(dev, "_process_alive", lambda pid: False)

    assert dev.claim_runner_lock() is None
    assert lock.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_releasing_only_removes_a_lock_this_process_holds(monkeypatch, tmp_path) -> None:
    # Otherwise a runner exiting would free the lock of the one that beat it.
    lock = tmp_path / "dev-runner.pid"
    monkeypatch.setattr(dev, "RUNNER_LOCK", lock)
    lock.write_text("424242", encoding="utf-8")

    dev.release_runner_lock()

    assert lock.exists(), "another runner's lock must survive"
