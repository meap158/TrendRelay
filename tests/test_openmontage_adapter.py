from pathlib import Path

import pytest

from trendrelay_api.integrations import openmontage


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
    monkeypatch, tmp_path: Path
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"immutable source media")
    output = tmp_path / "productions"
    monkeypatch.setattr(openmontage, "PRODUCTIONS_ROOT", output)
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
    assert (
        openmontage.get_production(proposal["id"])["source"]["rights_basis"] == "owned"
    )


def test_approval_requires_active_provider(monkeypatch, tmp_path: Path) -> None:
    production_id = "production_0123456789abcdef"
    path = tmp_path / production_id / "proposal.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"id":"production_0123456789abcdef","status":"awaiting_approval",'
        '"source":{"sha256":"abc"},"plan":{"budget_cap_usd":1}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(openmontage, "PRODUCTIONS_ROOT", tmp_path)
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
