from __future__ import annotations

from scripts import dev


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return b"ready"


class _ImmediateThread:
    def __init__(self, *, target, **_kwargs):
        self.target = target

    def start(self) -> None:
        self.target()


def test_dev_runner_warms_every_primary_tab(monkeypatch) -> None:
    requested: list[str] = []

    def open_route(request, **_kwargs):
        requested.append(request.full_url)
        return _Response()

    monkeypatch.setattr(dev.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(dev.urllib.request, "urlopen", open_route)

    dev.warm_primary_web_routes([
        dev.Service(name="Frontend", command=[], color="", port=3001),
    ])

    assert requested == [
        f"http://127.0.0.1:3001{route}" for route in dev.PRIMARY_WEB_ROUTES
    ]
