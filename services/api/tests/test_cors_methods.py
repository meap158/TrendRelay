"""The browser must be allowed to use the methods the API actually serves.

A method missing from the CORS list fails at the preflight, which means the
request never reaches the server: nothing is logged, no handler runs, and the
only symptom is "Failed to fetch" in the browser. That is indistinguishable
from the API being down, so it gets diagnosed as a network fault or a dead
server rather than a configuration line.

It has happened twice. Session endpoints written as PUT and DELETE, and the
autopilot panel's PUT, PATCH and DELETE - editing an autopilot, removing a
destination, reordering the queue - all failed that way while the list said
GET and POST. A comment asking the next person to keep the two in step is what
was there before; this is the same request, made in a form that fails loudly.
"""

from __future__ import annotations

from starlette.middleware.cors import CORSMiddleware

from trendrelay_api.main import app

#: Methods a router never declares but every HTTP client may use. HEAD follows
#: GET, and OPTIONS is the preflight itself.
ALWAYS_FINE = {"HEAD", "OPTIONS"}


def configured_methods() -> set[str]:
    """What the CORS middleware permits a browser to send."""
    for middleware in app.user_middleware:
        if middleware.cls is CORSMiddleware:
            options = getattr(middleware, "kwargs", {}) or {}
            return {method.upper() for method in options.get("allow_methods", [])}
    raise AssertionError("the app has no CORS middleware")


def served_methods() -> dict[str, set[str]]:
    """Every method the registered routes answer, by the path that answers it.

    Walked through the wrappers. An included router appears in `app.routes` as
    a `_IncludedRouter`, which holds the real `APIRouter` on `original_router`
    and exposes neither `path` nor `routes` itself. Reading only the top level
    finds the twenty-odd routes declared on the app - every one of them GET -
    and none of the hundreds that arrived by `include_router`. A check that
    stopped there would report perfect alignment while every endpoint this
    exists to protect sat one level down, which is exactly what it did on the
    first attempt.
    """
    found: dict[str, set[str]] = {}

    def walk(routes) -> None:
        for route in routes or []:
            inner = getattr(route, "original_router", None)
            nested = getattr(inner, "routes", None) or getattr(route, "routes", None)
            if nested:
                walk(nested)
            methods = getattr(route, "methods", None)
            path = getattr(route, "path", "")
            if not methods:
                continue
            wanted = {m.upper() for m in methods} - ALWAYS_FINE
            if wanted:
                found.setdefault(path, set()).update(wanted)

    walk(app.routes)
    return found


def test_the_api_serves_no_method_a_browser_is_refused() -> None:
    allowed = configured_methods()
    if "*" in allowed:
        return  # Everything is permitted; nothing to fall out of step with.

    unreachable = {
        path: sorted(methods - allowed)
        for path, methods in served_methods().items()
        if methods - allowed
    }

    assert unreachable == {}, (
        "these endpoints exist but a browser cannot call them - the preflight "
        f"refuses them and the app sees only 'Failed to fetch': {unreachable}"
    )


def test_the_scan_finds_the_methods_it_is_meant_to_check() -> None:
    # Without this, a route table read wrongly would report perfect alignment
    # over an empty set forever.
    served = served_methods()
    every = {method for methods in served.values() for method in methods}

    assert len(served) > 50, f"only found {len(served)} routed paths"
    assert {"GET", "POST"} <= every
    # The three that were unreachable. If the API stops serving them entirely
    # this line should be revisited rather than deleted.
    assert {"PUT", "PATCH", "DELETE"} & every, "expected the API to still serve these"
