"""Process state that must not leak from one test into the next.

Most of this suite is independent, but a few modules keep a decision at module
scope because the process is meant to keep it: the face detector remembers that
the GPU was lost so it stops trying, and that memory is exactly what a test
about losing the GPU has to set. Left set, it is read by every test that runs
afterwards, and the failure lands somewhere else entirely - here it was three
tests in `test_face_identity`, which ask which provider is chosen and get the
answer the previous file arranged.

The larger leak was out of the process entirely: into the developer's own
database, and from there onto the internet. See below.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


@pytest.fixture(scope="session", autouse=True)
def _durable_jobs_never_reach_the_real_database():
    """Point the durable-queue session factory at a database of our own.

    An API test overrides `get_session`, so the request reads and writes an
    in-memory database. The durable queue deliberately does not use the
    request's session - a queued job has to outlive the transaction that asked
    for it - so `create_job_record` and everything that wraps it take
    `factory=SessionFactory`, which is bound to `.data/trendrelay.db`. In
    production those are the same database. Under test they are not, and the
    job rows went to the real one.

    They did not sit there quietly. The worker drains that queue, so a test
    that queued a hundred fixture products had the running worker fetch a
    hundred Shopee pages for them - the same made-up URL a hundred times, from
    the developer's address. Found as 181 jobs across four workspace ids that
    do not exist, named "Shopee product 0" through "99" and "A pyjama set".

    Rebound rather than replaced, because `SessionFactory` is captured as a
    default argument in twenty-nine places and a default is bound when the
    function is defined - reassigning the module attribute would leave every
    one of them pointing at the real database. `sessionmaker.configure` mutates
    the object those defaults already hold, so they all follow it.

    Session-scoped: the engine is shared, and `_clean_durable_jobs` below gives
    each test an empty queue.
    """
    from trendrelay_api.database import SessionFactory
    from trendrelay_api.model_registry import load_all_models

    base = load_all_models()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    base.metadata.create_all(engine)
    real = SessionFactory.kw.get("bind")
    SessionFactory.configure(bind=engine)
    try:
        yield engine
    finally:
        SessionFactory.configure(bind=real)


@pytest.fixture(autouse=True)
def _clean_durable_jobs(_durable_jobs_never_reach_the_real_database):
    """An empty queue per test, since the engine above is shared."""
    from trendrelay_api.models import Base

    engine = _durable_jobs_never_reach_the_real_database
    table = Base.metadata.tables.get("durable_jobs")
    yield
    if table is not None:
        with engine.begin() as connection:
            connection.execute(table.delete())


@pytest.fixture(autouse=True)
def _restore_gpu_availability():
    """Give each test the GPU state it would have had on its own.

    Restored rather than reset: a test that deliberately disables the GPU still
    sees its own change, and only what it leaves behind is undone.
    """
    from trendrelay_api.integrations import face_detect_onnx

    disabled = face_detect_onnx._GPU_DISABLED
    disabled_at = face_detect_onnx._GPU_DISABLED_AT
    yield
    face_detect_onnx._GPU_DISABLED = disabled
    face_detect_onnx._GPU_DISABLED_AT = disabled_at
