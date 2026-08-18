"""Every timestamp that leaves this API says which zone it is in.

SQLite has no timezone type, so an aware `datetime` written through a plain
`DateTime` column came back naive, and FastAPI rendered it as
`2026-08-18T14:27:41` with no offset. A browser reads that as *local* time:
on a machine seven hours ahead of UTC, a job that finished a minute ago showed
as "7h ago", and every other time in the interface was stale by the same
amount.

The values in the database were always UTC. What was missing was saying so.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.models import Base, UserProfile, Workspace, utc_now

# Imported for the side effect of registering every table on `Base.metadata`,
# as the neighbouring suites do: a metadata that has seen only these models
# cannot resolve the foreign keys the others declare.
import trendrelay_api.main  # noqa: E402,F401  isort:skip


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as active:
        yield active


def test_a_stored_moment_comes_back_knowing_it_is_utc(session) -> None:
    """The whole bug in one assertion."""
    session.add(UserProfile(id="u1", email="a@example.test"))
    session.commit()

    found = session.get(UserProfile, "u1")

    assert found.created_at.tzinfo is not None, "a naive timestamp is an ambiguous one"
    assert found.created_at.utcoffset() == timedelta(0)


def test_it_serialises_with_an_offset_a_browser_cannot_misread(session) -> None:
    # `new Date("2026-08-18T14:27:41")` is local time; the same string with an
    # offset is not. This is the difference the interface was reading wrong.
    session.add(UserProfile(id="u2", email="b@example.test"))
    session.commit()

    rendered = session.get(UserProfile, "u2").created_at.isoformat()

    assert rendered.endswith("+00:00"), rendered


def test_an_offset_moment_is_kept_as_the_moment_it_was(session) -> None:
    """Not the clock face it was written on.

    08:30 in Bangkok is 01:30 UTC. Storing "08:30" and calling it UTC is how a
    seven-hour error gets written down as though it were data.
    """
    bangkok = timezone(timedelta(hours=7))
    session.add(Workspace(
        id="ws1", name="W", slug="w", created_by="u1",
        created_at=datetime(2026, 7, 20, 8, 30, tzinfo=bangkok),
    ))
    session.commit()

    found = session.get(Workspace, "ws1")

    assert found.created_at == datetime(2026, 7, 20, 1, 30, tzinfo=UTC)


def test_the_difference_between_two_stored_moments_is_real(session) -> None:
    """What "7h ago" is computed from.

    Reading a stored moment must not shift it relative to now, whatever zone
    the machine is in - that shift was the visible symptom.
    """
    before = utc_now()
    session.add(UserProfile(id="u3", email="c@example.test"))
    session.commit()

    elapsed = session.get(UserProfile, "u3").created_at - before

    assert timedelta(seconds=-5) < elapsed < timedelta(seconds=5), elapsed
