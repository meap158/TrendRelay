import json
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
    monkeypatch.setattr(dev, "service_is_healthy", lambda _service: next(results))
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

    assert r"scripts\check_node_dependencies.mjs" in launcher
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

    assert worker.command[-2:] == ["scripts/worker.py", "--watch"]
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
    assert services["Postiz"].restart_on_exit is False
    assert services["Postiz"].required is False
    assert services["Backend"].required is True


def test_browser_app_opens_after_startup(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(dev.webbrowser, "open", lambda url: opened.append(url) or True)

    assert dev.open_browser_app(False) is True
    assert opened == ["http://127.0.0.1:3001/"]

    assert dev.open_browser_app(True) is False
    assert opened == ["http://127.0.0.1:3001/"]


def test_browser_opens_only_after_frontend_health_gate() -> None:
    source = (Path(__file__).resolve().parents[1] / "scripts" / "dev.py").read_text(
        encoding="utf-8"
    )

    health_gate = source.index(
        "if service.health_url and service.required and not wait_until_healthy("
    )
    browser_open = source.index("open_browser_app(args.desktop)")
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
