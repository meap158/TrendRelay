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

    health_gate = source.index("if service.health_url and not wait_until_healthy(")
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
