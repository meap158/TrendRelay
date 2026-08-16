"""Two processes, one file.

The API and the durable worker both write this database. In SQLite's default
rollback-journal mode that is a race rather than a queue, and the symptom is
"database is locked" thrown at whoever asked for something - a campaign deploy,
usually, because it inserts several job rows in a row while the worker is
sweeping its queues.
"""

from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import text

from trendrelay_api.database import BUSY_TIMEOUT_MS, create_database_engine


def pragma(engine, name: str):
    with engine.connect() as connection:
        return connection.execute(text(f"PRAGMA {name}")).scalar()


def test_a_file_database_is_opened_in_wal(tmp_path) -> None:
    """The fix that matters: writers stop blocking readers."""
    engine = create_database_engine(f"sqlite:///{tmp_path / 'a.db'}")

    assert pragma(engine, "journal_mode") == "wal"


def test_a_connection_waits_for_a_lock_rather_than_failing(tmp_path) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'b.db'}")

    assert pragma(engine, "busy_timeout") == BUSY_TIMEOUT_MS


def test_the_journal_mode_sticks_to_the_file(tmp_path) -> None:
    """It is a property of the database, so a later opener inherits it."""
    path = tmp_path / "c.db"
    create_database_engine(f"sqlite:///{path}").connect().close()

    reopened = create_database_engine(f"sqlite:///{path}")

    assert pragma(reopened, "journal_mode") == "wal"


def test_concurrent_writers_all_get_through(tmp_path) -> None:
    """The actual bug: several inserts at once, from more than one connection.

    Sixteen writers against one file is well past what a deploy does, and in
    the mode this database used to open in it is enough to produce the failure
    that was reported.
    """
    engine = create_database_engine(f"sqlite:///{tmp_path / 'd.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE jobs (id TEXT PRIMARY KEY)"))

    def insert(number: int) -> None:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO jobs (id) VALUES (:id)"), {"id": f"job-{number}"}
            )

    with ThreadPoolExecutor(max_workers=8) as pool:
        # `list` so an exception in any worker is raised here rather than
        # discarded, which is the difference between a test and a decoration.
        list(pool.map(insert, range(16)))

    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM jobs")).scalar() == 16


def test_reading_does_not_block_a_write(tmp_path) -> None:
    """The exact shape of the reported failure: a sweep open while a deploy writes."""
    engine = create_database_engine(f"sqlite:///{tmp_path / 'e.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE jobs (id TEXT PRIMARY KEY)"))

    with engine.connect() as reader:
        reader.execute(text("SELECT count(*) FROM jobs")).scalar()
        # Reader still open, mid-transaction, exactly as the worker's sweep is.
        with engine.begin() as writer:
            writer.execute(text("INSERT INTO jobs (id) VALUES ('while-reading')"))

    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM jobs")).scalar() == 1


def test_an_in_memory_database_still_works(tmp_path) -> None:
    """Every test in this suite uses one, so it must survive the pragmas."""
    engine = create_database_engine("sqlite://")

    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE t (id INTEGER)"))
        connection.execute(text("INSERT INTO t VALUES (1)"))
        assert connection.execute(text("SELECT count(*) FROM t")).scalar() == 1


# --- the reported failure -----------------------------------------------------


def test_a_job_created_inside_a_writing_transaction_does_not_block(tmp_path) -> None:
    """The reported failure, in miniature.

    Deploying a campaign writes to the request's session - activating the
    campaign - and then asks for a publishing job per destination. If that job
    opens its own connection it queues behind the caller's own uncommitted
    write and sits there until the busy timeout gives up, which is the API
    blocking on itself. The wall-clock assertion is the point: without sharing
    the transaction this takes the full timeout and then fails.
    """
    import time

    from sqlalchemy.orm import sessionmaker

    from trendrelay_api.jobs import create_job_record
    from trendrelay_api.main import app  # noqa: F401  registers every model
    from trendrelay_api.models import Base, DurableJob

    engine = create_database_engine(f"sqlite:///{tmp_path / 'deploy.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    started = time.monotonic()
    with factory.begin() as session:
        # Stand in for activating the campaign: a write on the request session.
        session.add(DurableJob(
            id="already-writing", workspace_key="ws", kind="k", status="queued",
            payload={}, attempt_count=0, max_attempts=1, cancellation_requested=False,
        ))
        session.flush()
        # Now the publishing jobs, on the same transaction.
        for number in range(2):
            create_job_record(
                f"publish-{number}", "ws", "social_publish", {}, session=session
            )
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, "creating a job waited on the caller's own transaction"
    with factory() as session:
        assert session.get(DurableJob, "publish-0") is not None
        assert session.get(DurableJob, "publish-1") is not None


def test_a_failed_deploy_leaves_no_publishing_jobs_behind(tmp_path) -> None:
    """Sharing the transaction is also what makes the rollback honest."""
    import pytest
    from sqlalchemy.orm import sessionmaker

    from trendrelay_api.jobs import create_job_record
    from trendrelay_api.main import app  # noqa: F401  registers every model
    from trendrelay_api.models import Base, DurableJob

    engine = create_database_engine(f"sqlite:///{tmp_path / 'rollback.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with pytest.raises(RuntimeError), factory.begin() as session:
        create_job_record("half-done", "ws", "social_publish", {}, session=session)
        raise RuntimeError("preflight refused the rest of the batch")

    with factory() as session:
        assert session.get(DurableJob, "half-done") is None
