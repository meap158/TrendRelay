from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.integrations import openmontage
from trendrelay_api.jobs import create_job_record, get_job_record
from trendrelay_api.models import Base


@pytest.fixture
def job_factory(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(openmontage, "JOB_SESSION_FACTORY", factory)
    return factory


def test_lists_only_guarded_short_form_pipelines(monkeypatch) -> None:
    monkeypatch.setattr(
        openmontage,
        "_load_pipeline",
        lambda name: {
            "name": name,
            "version": "2.0",
            "description": "Short-form pipeline",
            "stability": "beta",
            "orchestration": {"budget_default_usd": 1.0},
            "stages": [{"name": "idea", "human_approval_default": True}],
        },
    )
    pipelines = openmontage.list_pipelines()
    assert {item["id"] for item in pipelines} == {"clip-factory", "podcast-repurpose"}
    assert all(
        any(stage["human_approval_required"] for stage in item["stages"])
        for item in pipelines
    )


def test_proposal_fingerprints_source_and_remains_non_executable(
    monkeypatch, tmp_path: Path, job_factory
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"immutable source media")
    monkeypatch.setattr(
        openmontage,
        "list_pipelines",
        lambda: [
            {
                "id": "clip-factory",
                "budget_default_usd": 1.0,
                "stages": [{"name": "idea", "human_approval_required": True}],
            }
        ],
    )
    monkeypatch.setattr(
        openmontage,
        "provider_status",
        lambda: {
            "installed": True,
            "active": False,
            "revision": "pinned",
            "pipelines_present": True,
        },
    )
    request = openmontage.ProductionRequest(
        title="Three product clips",
        source_asset=str(source),
        source_rights="owned",
        confirm_external_action=True,
    )
    proposal = openmontage.create_proposal(request)
    assert proposal["status"] == "awaiting_approval"
    assert proposal["source"]["sha256"]
    assert proposal["execution"]["enabled"] is False
    assert openmontage.get_production(proposal["id"])["source"]["rights_basis"] == "owned"
    monkeypatch.setattr(
        openmontage,
        "provider_status",
        lambda: {
            "installed": True,
            "active": True,
            "revision": "pinned",
            "pipelines_present": True,
        },
    )
    approved = openmontage.approve_proposal(
        proposal["id"],
        openmontage.ProductionApproval(
            approved_by="owner", confirm_external_action=True
        ),
    )
    assert approved["status"] == "approved"
    assert openmontage.get_production(proposal["id"])["status"] == "approved"
    assert get_job_record(proposal["id"], factory=job_factory)["status"] == "succeeded"


def test_approval_requires_active_provider(monkeypatch, job_factory) -> None:
    production_id = "production_0123456789abcdef"
    create_job_record(
        production_id,
        "local",
        "openmontage_preflight",
        {
            "id": production_id,
            "status": "awaiting_approval",
            "source": {"sha256": "abc"},
            "plan": {"budget_cap_usd": 1},
        },
        max_attempts=1,
        factory=job_factory,
    )
    monkeypatch.setattr(
        openmontage,
        "provider_status",
        lambda: {
            "installed": True,
            "active": False,
            "revision": "pinned",
            "pipelines_present": True,
        },
    )
    with pytest.raises(RuntimeError, match="Activate OpenMontage"):
        openmontage.approve_proposal(
            production_id,
            openmontage.ProductionApproval(
                approved_by="owner", confirm_external_action=True
            ),
        )


def test_core_operations_require_explicit_confirmation() -> None:
    with pytest.raises(PermissionError):
        openmontage.create_proposal(
            openmontage.ProductionRequest(
                title="Unconfirmed proposal",
                source_asset="missing.mp4",
                source_rights="owned",
            )
        )
    with pytest.raises(PermissionError):
        openmontage.approve_proposal(
            "production_0123456789abcdef",
            openmontage.ProductionApproval(approved_by="owner"),
        )
