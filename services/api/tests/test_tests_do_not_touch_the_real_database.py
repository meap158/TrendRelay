"""The suite must not write to `.data/trendrelay.db`, and must not phone out.

An API test overrides `get_session`, so its requests use an in-memory
database. The durable queue does not use the request's session - a queued job
outlives the transaction that asked for it - so it takes `SessionFactory`,
which is bound to the developer's real database. In production they are the
same file; under test they were not, and job rows landed in the real one.

The worker then drained them. 181 fixture jobs across four workspace ids that
never existed - "Shopee product 0" through "99", all carrying one made-up
Shopee URL - were fetched from the marketplace for real, at a couple of
seconds apart, from the developer's address. The queue is a side effect that
leaves the process, which is what makes this worth a test of its own rather
than a note in a fixture.
"""

from __future__ import annotations

from pathlib import Path


def test_the_job_factory_is_not_bound_to_the_projects_database():
    """Whatever the durable queue writes to, it is not `.data/trendrelay.db`."""
    from trendrelay_api.database import SessionFactory

    bind = SessionFactory.kw.get("bind")
    assert bind is not None, "the session factory has no bind at all"

    url = str(bind.url)
    assert "trendrelay.db" not in url, (
        f"durable jobs would be written to the real database ({url}); the "
        "conftest fixture that rebinds SessionFactory is not in effect"
    )


def test_queueing_a_job_writes_where_the_fixture_points(tmp_path):
    """The path that leaked, exercised end to end.

    `enqueue` captured `SessionFactory` as a default argument, so this also
    covers the rebinding actually reaching a captured default rather than only
    the module attribute.
    """
    from trendrelay_api import shopee_enrichment
    from trendrelay_api.jobs import get_job_record
    from trendrelay_api.opportunity_models import Product

    product = Product(
        workspace_id="ws-not-real",
        catalog_key="key-1",
        name="A fixture that must not be fetched",
        marketplace="shopee",
        product_url="https://shopee.vn/product/1/2",
        created_by="tester",
    )
    # Not added to any session: enqueue reads the fields it needs and writes a
    # job row, which is the only thing under test here.
    product.id = "product-fixture"

    queued = shopee_enrichment.enqueue("ws-not-real", [product])
    assert len(queued) == 1

    # Readable back through the same rebound factory...
    record = get_job_record(queued[0])
    assert record is not None
    assert record["payload"]["product_id"] == "product-fixture"

    # ...and absent from the real database, which is the point.
    real = Path(__file__).resolve().parents[3] / ".data" / "trendrelay.db"
    if real.exists():
        import sqlite3

        connection = sqlite3.connect(real)
        try:
            found = connection.execute(
                "select count(*) from durable_jobs where id = ?", (queued[0],)
            ).fetchone()[0]
        finally:
            connection.close()
        assert found == 0, "the job was written to the developer's own database"
