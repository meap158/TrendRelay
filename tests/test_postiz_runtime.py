import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.postiz as postiz
import scripts.postiz_service as postiz_service


def make_args(video: Path, *extra: str):
    return postiz.build_parser().parse_args(
        [
            "short-video",
            "--video",
            str(video),
            "--caption",
            "A short launch video",
            "--date",
            "2099-01-01T12:00:00Z",
            "--target",
            "tiktok=tiktok-1",
            *extra,
        ]
    )


def test_dry_run_has_no_provider_calls(monkeypatch, capsys, tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    monkeypatch.setattr(
        postiz,
        "run_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError),
    )

    assert postiz.short_video(make_args(video)) == 0
    output = capsys.readouterr().out
    assert '"external_action": "create_draft"' in output
    assert "Dry run only" in output


def test_execution_requires_confirmation(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    assert postiz.short_video(make_args(video, "--execute")) == 2


def test_executes_once_and_deletes_runtime_plan(monkeypatch, tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    monkeypatch.setattr(postiz, "LEDGER_PATH", tmp_path / "operations.json")
    monkeypatch.setattr(postiz, "RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(postiz, "check_provider", lambda: 0)
    calls: list[list[str]] = []

    def fake_provider(arguments, capture=False):
        calls.append(arguments)
        if arguments[0] == "upload":
            return SimpleNamespace(
                returncode=0,
                stdout='Uploaded\n{"path":"https://cdn.postiz.example/clip.mp4"}\n',
                stderr="",
            )
        plan_path = Path(arguments[2])
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
        assert payload["posts"][0]["value"][0]["image"][0]["path"].startswith(
            "https://"
        )
        return SimpleNamespace(
            returncode=0, stdout='Created\n{"id":"post-1"}\n', stderr=""
        )

    monkeypatch.setattr(postiz, "run_provider", fake_provider)
    args = make_args(video, "--execute", "--confirm-external-action")

    assert postiz.short_video(args) == 0
    assert (
        json.loads(postiz.LEDGER_PATH.read_text(encoding="utf-8")).popitem()[1][
            "status"
        ]
        == "created"
    )
    assert list(postiz.RUNTIME_DIR.glob("*.json")) == []
    assert postiz.short_video(args) == 3
    assert len(calls) == 2


def test_winget_install_targets_and_refreshes_only_community_source(
    monkeypatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        if len(calls) == 1:
            raise postiz_service.CommandFailure(command, 0x8A15004B)

    monkeypatch.setattr(postiz_service, "_run_checked", fake_run)
    postiz_service._install_winget_package("winget.exe", "Example.Package")

    assert calls[0][5:7] == ["--source", "winget"]
    assert calls[1] == ["winget.exe", "source", "update", "--name", "winget"]
    assert calls[2] == calls[0]


def test_winget_source_failure_explains_non_blocking_recovery(monkeypatch) -> None:
    def always_fail(command, **_kwargs):
        raise postiz_service.CommandFailure(command, 0x8A15004B)

    monkeypatch.setattr(postiz_service, "_run_checked", always_fail)
    with pytest.raises(
        RuntimeError,
        match="TrendRelay can run without Postiz",
    ):
        postiz_service._install_winget_package("winget.exe", "Example.Package")
