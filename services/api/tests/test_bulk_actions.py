import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import bulk_actions
from trendrelay_api.bulk_actions import AssetView, BulkAction
from trendrelay_api.media_models import MediaAsset, MediaAssetVersion
from trendrelay_api.models import Base


@pytest.fixture
def store():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def add_asset(store, asset_id: str, *, kind: str = "video", blurred: bool = False) -> str:
    with store.begin() as session:
        session.add(
            MediaAsset(
                id=asset_id,
                workspace_id="w1",
                title=f"Clip {asset_id}",
                media_kind=kind,
                source_type="download",
                original_path=f"C:/media/{asset_id}.mp4",
                original_sha256=asset_id.ljust(64, "0"),
                mime_type="video/mp4",
                size_bytes=10,
                created_by="tester",
            )
        )
        if blurred:
            session.add(
                MediaAssetVersion(
                    workspace_id="w1",
                    asset_id=asset_id,
                    version_kind="blurred",
                    path=f"C:/media/{asset_id}-blurred.mp4",
                    sha256="b" * 64,
                    mime_type="video/mp4",
                    size_bytes=10,
                )
            )
    return asset_id


@pytest.fixture
def recorder(monkeypatch):
    """A stand-in tool, so these tests describe the machinery not face blurring."""
    queued: list[str] = []

    action = BulkAction(
        id="probe",
        label="Probe",
        verb="Probing",
        description="Test action.",
        roles=frozenset({"owner"}),
        max_batch=3,
        enqueue=lambda workspace, asset, factory=None: (queued.append(asset.id), {"id": f"job_{asset.id}"})[1],
        ineligible=lambda asset: (
            "Already has a blurred version" if "blurred" in asset.version_kinds else None
        ),
    )
    monkeypatch.setitem(bulk_actions.REGISTRY, "probe", action)
    return queued


def test_an_unknown_action_names_what_is_available() -> None:
    with pytest.raises(ValueError, match="Unknown library action 'nope'"):
        bulk_actions.resolve("nope")


def test_every_selected_asset_gets_an_outcome(store, recorder) -> None:
    """A bare 202 would hide that half the selection was never touched."""
    add_asset(store, "a1")
    add_asset(store, "a2", blurred=True)

    outcome = bulk_actions.run("w1", "probe", ["a1", "a2", "ghost"], factory=store)

    assert outcome["counts"] == {"queued": 1, "skipped": 1, "failed": 0, "missing": 1}
    assert len(outcome["results"]) == 3
    by_id = {item["asset_id"]: item for item in outcome["results"]}
    assert by_id["a1"]["status"] == "queued"
    assert by_id["a2"]["detail"] == "Already has a blurred version"
    assert by_id["ghost"]["status"] == "missing"


def test_only_eligible_assets_are_queued(store, recorder) -> None:
    add_asset(store, "a1")
    add_asset(store, "a2", blurred=True)

    bulk_actions.run("w1", "probe", ["a1", "a2"], factory=store)

    assert recorder == ["a1"]


def test_one_failure_does_not_stop_the_rest(store, monkeypatch) -> None:
    """Partial failure is the normal case, not a reason to abandon the batch."""
    add_asset(store, "a1")
    add_asset(store, "a2")
    add_asset(store, "a3")

    def flaky(workspace, asset, factory=None):
        if asset.id == "a2":
            raise RuntimeError("disk full")
        return {"id": f"job_{asset.id}"}

    monkeypatch.setitem(
        bulk_actions.REGISTRY,
        "probe",
        BulkAction(
            id="probe", label="Probe", verb="Probing", description="",
            roles=frozenset({"owner"}), max_batch=10, enqueue=flaky,
        ),
    )

    outcome = bulk_actions.run("w1", "probe", ["a1", "a2", "a3"], factory=store)

    assert outcome["counts"]["queued"] == 2
    assert outcome["counts"]["failed"] == 1
    failed = next(item for item in outcome["results"] if item["status"] == "failed")
    assert failed["detail"] == "disk full"


def test_a_batch_over_the_cap_is_refused_rather_than_truncated(store, recorder) -> None:
    """Silently dropping items would look like success while skipping work."""
    ids = [add_asset(store, f"a{index}") for index in range(4)]

    with pytest.raises(ValueError, match="up to 3 items at a time"):
        bulk_actions.run("w1", "probe", ids, factory=store)

    assert recorder == []


def test_an_empty_selection_is_refused(store, recorder) -> None:
    with pytest.raises(ValueError, match="Select at least one"):
        bulk_actions.run("w1", "probe", [], factory=store)


def test_an_unavailable_tool_queues_nothing(store, monkeypatch) -> None:
    add_asset(store, "a1")
    monkeypatch.setitem(
        bulk_actions.REGISTRY,
        "probe",
        BulkAction(
            id="probe", label="Probe", verb="Probing", description="",
            roles=frozenset({"owner"}), max_batch=10,
            enqueue=lambda workspace, asset, factory=None: pytest.fail("must not enqueue"),
            availability=lambda: (False, "OpenCV is not installed"),
        ),
    )

    with pytest.raises(RuntimeError, match="OpenCV is not installed"):
        bulk_actions.run("w1", "probe", ["a1"], factory=store)


def test_another_workspace_asset_is_not_touched(store, recorder) -> None:
    add_asset(store, "a1")
    with store.begin() as session:
        session.add(
            MediaAsset(
                id="other", workspace_id="w2", title="Other", media_kind="video",
                source_type="download", original_path="C:/media/other.mp4",
                original_sha256="other".ljust(64, "0"), mime_type="video/mp4",
                size_bytes=10,
                created_by="tester",
            )
        )

    outcome = bulk_actions.run("w1", "probe", ["a1", "other"], factory=store)

    assert recorder == ["a1"]
    assert next(i for i in outcome["results"] if i["asset_id"] == "other")["status"] == "missing"


def test_duplicate_selections_run_once(store, recorder) -> None:
    add_asset(store, "a1")

    outcome = bulk_actions.run("w1", "probe", ["a1", "a1"], factory=store)

    assert recorder == ["a1"]
    assert outcome["counts"]["queued"] == 1


def test_results_follow_the_order_the_caller_selected(store, recorder) -> None:
    add_asset(store, "b")
    add_asset(store, "a")

    outcome = bulk_actions.run("w1", "probe", ["b", "a"], factory=store)

    assert [item["asset_id"] for item in outcome["results"]] == ["b", "a"]


# --- the registered tool ----------------------------------------------------- #


def test_face_blur_skips_a_clip_that_already_has_a_blurred_cut() -> None:
    action = bulk_actions.resolve("face_blur")
    already = AssetView(
        id="a1", title="Clip", media_kind="video",
        original_path="C:/media/a1.mp4", version_kinds=frozenset({"blurred"}),
    )

    assert action.ineligible(already) == "Already has a blurred version"


def test_face_blur_skips_media_that_is_not_video() -> None:
    action = bulk_actions.resolve("face_blur")
    image = AssetView(
        id="a1", title="Photo", media_kind="image",
        original_path="C:/media/a1.jpg", version_kinds=frozenset(),
    )

    assert action.ineligible(image) == "Face blurring applies to video"


def test_face_blur_caps_the_batch_because_a_render_is_slow() -> None:
    assert bulk_actions.resolve("face_blur").max_batch <= 25


def test_the_catalogue_reports_why_a_tool_is_unavailable(monkeypatch) -> None:
    monkeypatch.setitem(
        bulk_actions.REGISTRY,
        "probe",
        BulkAction(
            id="probe", label="Probe", verb="Probing", description="",
            roles=frozenset({"owner"}), max_batch=5,
            enqueue=lambda workspace, asset, factory=None: {},
            availability=lambda: (False, "Install the vision extra"),
        ),
    )

    entry = next(item for item in bulk_actions.catalogue() if item["id"] == "probe")

    assert entry["available"] is False
    assert entry["reason"] == "Install the vision extra"


# --- delete ------------------------------------------------------------------ #


def _library_asset(store, tmp_path, monkeypatch, asset_id="a1"):
    """An asset stored the way the library stores one: its own directory."""
    from trendrelay_api import media_library

    root = tmp_path / "media"
    workspace_dir = root / "w1" / "digest-of-a1"
    workspace_dir.mkdir(parents=True)
    media = workspace_dir / "original.mp4"
    media.write_bytes(b"video")
    monkeypatch.setattr(media_library, "LIBRARY_ROOT", root)
    with store.begin() as session:
        session.add(
            MediaAsset(
                id=asset_id, workspace_id="w1", title="Clip", media_kind="video",
                source_type="douyin-download", original_path=str(media),
                original_sha256=asset_id.ljust(64, "0"), mime_type="video/mp4",
                size_bytes=5, created_by="tester",
            )
        )
    return workspace_dir


def test_delete_removes_the_entry_and_the_library_copies(store, tmp_path, monkeypatch) -> None:
    directory = _library_asset(store, tmp_path, monkeypatch)

    outcome = bulk_actions.run("w1", "delete", ["a1"], factory=store)

    assert outcome["counts"]["queued"] == 1
    assert not directory.exists()
    assert bulk_actions.load_assets("w1", ["a1"], factory=store) == []


def test_delete_leaves_a_file_outside_the_library_root_alone(
    store, tmp_path, monkeypatch
) -> None:
    """A stored path must not be able to direct a delete outside the library."""
    from trendrelay_api import media_library

    root = tmp_path / "media"
    (root / "w1").mkdir(parents=True)
    monkeypatch.setattr(media_library, "LIBRARY_ROOT", root)
    outside = tmp_path / "downloads" / "keep"
    outside.mkdir(parents=True)
    stray = outside / "original.mp4"
    stray.write_bytes(b"precious")
    with store.begin() as session:
        session.add(
            MediaAsset(
                id="a1", workspace_id="w1", title="Clip", media_kind="video",
                source_type="import", original_path=str(stray),
                original_sha256="a".ljust(64, "0"), mime_type="video/mp4",
                size_bytes=5, created_by="tester",
            )
        )

    bulk_actions.run("w1", "delete", ["a1"], factory=store)

    assert stray.exists(), "a path outside the library root must never be removed"
    assert outside.exists()


def test_delete_also_removes_the_version_rows(store, tmp_path, monkeypatch) -> None:
    _library_asset(store, tmp_path, monkeypatch)
    with store.begin() as session:
        session.add(
            MediaAssetVersion(
                workspace_id="w1", asset_id="a1", version_kind="blurred",
                path="C:/media/a1-blurred.mp4", sha256="b" * 64,
                mime_type="video/mp4", size_bytes=5,
            )
        )

    bulk_actions.run("w1", "delete", ["a1"], factory=store)

    with store() as session:
        remaining = session.scalars(
            __import__("sqlalchemy").select(MediaAssetVersion)
        ).all()
    assert remaining == []


def test_delete_is_held_to_the_stricter_roles() -> None:
    """Editors can blur, which is reversible; deleting is not."""
    assert bulk_actions.resolve("delete").roles == frozenset({"owner", "approver"})
    assert "editor" in bulk_actions.resolve("face_blur").roles


def test_delete_applies_to_every_media_kind() -> None:
    action = bulk_actions.resolve("delete")
    image = AssetView(
        id="a1", title="Photo", media_kind="image",
        original_path="C:/media/a1.jpg", version_kinds=frozenset(),
    )

    assert action.ineligible(image) is None
