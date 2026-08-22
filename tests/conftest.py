"""Shared safety net for the runner tests."""

from __future__ import annotations

import pytest

import scripts.dev as dev


@pytest.fixture(autouse=True)
def never_stop_the_running_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a test run from taking down the app being developed.

    `build_services()` calls `find_free_port()`, which frees a port by killing
    whatever holds it - and the ports it wants are 8011 and 3001, the API and
    dev server of the app running in the next window. A test that only meant to
    read the worker's command line was stopping TrendRelay every time the suite
    ran, which looked from the outside like the app dying for no reason.

    Reporting every port as free is enough: `find_free_port` returns the
    preferred one immediately and never reaches the killing. Tests that are
    about port handling patch these again inside the test, which runs after
    this fixture and therefore wins.

    Faking the ports as free opens a second trapdoor, though: with port 3001
    "free", `build_services()` walks straight into `_cleanup_stale_nextjs()`
    and deletes `apps/web/.next-dev` - the build directory of the dev server
    running in the next window. That server keeps its port and answers every
    request with a bare 500 from then on, because Next writes
    `routes-manifest.json` on startup and never again. So the cleanup is
    guarded too: it only runs when a test has pointed `ROOT` somewhere else,
    which is exactly what the tests that are about the cleanup itself do.
    """
    monkeypatch.setattr(dev, "_port_is_free", lambda _port: True)
    monkeypatch.setattr(dev, "_kill_port_holders", lambda _port: False)

    real_cleanup = dev._cleanup_stale_nextjs
    real_root = dev.ROOT

    def cleanup_only_away_from_the_repo() -> None:
        if dev.ROOT == real_root:
            return
        real_cleanup()

    monkeypatch.setattr(dev, "_cleanup_stale_nextjs", cleanup_only_away_from_the_repo)
