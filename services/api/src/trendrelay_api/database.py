"""Database engine and request-scoped transaction helpers."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from trendrelay_api.config import get_settings

#: How long a connection waits for a lock before giving up. Generous, because
#: the thing it is usually waiting for is one FFmpeg-adjacent worker writing a
#: job row, which takes milliseconds - and failing a deploy is far worse than
#: pausing it.
BUSY_TIMEOUT_MS = 15_000


def create_database_engine(url: str | None = None):
    database_url = url or get_settings().database_url
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    if database_url.startswith("sqlite:///"):
        Path(database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url, pool_pre_ping=True, connect_args=connect_args)
    if database_url.startswith("sqlite"):
        _use_sqlite_concurrently(engine)
    return engine


def _use_sqlite_concurrently(engine) -> None:
    """Make two processes sharing one file a supported arrangement.

    The API and the durable worker both write this database, and in SQLite's
    default rollback-journal mode that is a race rather than a queue: a reader
    holds a shared lock, a writer needs an exclusive one, and a transaction that
    reads before it writes can be refused outright with "database is locked" -
    no matter how long it was willing to wait, because a lock upgrade cannot
    wait its turn. Deploying a campaign does exactly that, several times in a
    row, while the worker is sweeping its queues.

    WAL is most of the answer: writers stop blocking readers, so the two
    processes stop queueing behind each other for ordinary work, and the
    timeout covers two writers arriving together.

    What WAL does not fix - and measuring was the only way to find this out - is
    a transaction that reads and then writes while another connection holds the
    write lock. That one cannot wait, because waiting would be a real deadlock,
    so SQLite refuses at once and the timeout is never consulted. The textbook
    answer is `BEGIN IMMEDIATE` on every transaction, and it is the wrong answer
    here: it takes the write lock for read-only work too, and this API holds a
    session open for the whole request - including requests that drive a browser
    or an encoder for minutes. That would trade a rare error for a frozen
    database. The narrow retry in `jobs` covers the real case instead.

    `synchronous=NORMAL` is the standard companion to WAL: durable across a
    process crash, which is the failure this database can actually have, and it
    trades only the machine losing power mid-write for a large speed gain.
    """

    @event.listens_for(engine, "connect")
    def _apply(connection, _record) -> None:  # pragma: no cover - driver callback
        cursor = connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


engine = create_database_engine()
SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    with SessionFactory() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
