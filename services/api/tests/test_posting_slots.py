import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.integrations import posting_slots
from trendrelay_api.models import Base


@pytest.fixture
def slots():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_a_workspace_starts_with_no_slots(slots) -> None:
    """An invented posting schedule looks considered while being arbitrary."""
    assert posting_slots.list_slots("w1", factory=slots) == []


def test_presets_are_offered_but_never_applied_on_their_own(slots) -> None:
    presets = posting_slots.preset_payload()

    assert {preset["id"] for preset in presets} >= {"commute", "evening", "spread"}
    # Every preset explains the assumption it makes about the audience.
    assert all(preset["summary"] for preset in presets)
    assert posting_slots.list_slots("w1", factory=slots) == []


def test_saving_slots_replaces_rather_than_merges(slots) -> None:
    """Replacing is what makes removing a slot in the interface actually remove it."""
    posting_slots.replace_slots("w1", [{"time": "09:00"}, {"time": "18:30"}], factory=slots)
    saved = posting_slots.replace_slots("w1", [{"time": "21:00"}], factory=slots)

    assert [slot["time"] for slot in saved] == ["21:00"]


def test_clearing_every_slot_is_allowed(slots) -> None:
    posting_slots.replace_slots("w1", [{"time": "09:00"}], factory=slots)

    assert posting_slots.replace_slots("w1", [], factory=slots) == []


def test_duplicate_times_collapse_to_one_slot(slots) -> None:
    saved = posting_slots.replace_slots(
        "w1", [{"time": "09:00"}, {"time": "09:00"}], factory=slots
    )

    assert len(saved) == 1


def test_slots_are_returned_in_the_order_a_day_runs(slots) -> None:
    saved = posting_slots.replace_slots(
        "w1", [{"time": "21:00"}, {"time": "07:30"}, {"time": "12:00"}], factory=slots
    )

    assert [slot["time"] for slot in saved] == ["07:30", "12:00", "21:00"]


def test_a_slot_can_be_pinned_to_one_weekday(slots) -> None:
    saved = posting_slots.replace_slots(
        "w1", [{"time": "10:00", "weekday": 5}], factory=slots
    )

    assert saved[0]["weekday"] == 5
    assert saved[0]["weekday_label"] == "Saturday"


def test_an_every_day_slot_says_so(slots) -> None:
    saved = posting_slots.replace_slots("w1", [{"time": "10:00"}], factory=slots)

    assert saved[0]["weekday"] == posting_slots.EVERY_DAY
    assert saved[0]["weekday_label"] == "Every day"


def test_one_workspace_cannot_see_another_workspace_slots(slots) -> None:
    posting_slots.replace_slots("w1", [{"time": "09:00"}], factory=slots)
    posting_slots.replace_slots("w2", [{"time": "20:00"}], factory=slots)

    assert [slot["time"] for slot in posting_slots.list_slots("w1", factory=slots)] == ["09:00"]
    assert [slot["time"] for slot in posting_slots.list_slots("w2", factory=slots)] == ["20:00"]


@pytest.mark.parametrize("value", ["25:00", "12:60", "noon", "9", "12:00:00", ""])
def test_a_time_a_clock_would_not_show_is_refused(slots, value: str) -> None:
    with pytest.raises(ValueError, match="not a time of day"):
        posting_slots.replace_slots("w1", [{"time": value}], factory=slots)


def test_a_weekday_outside_the_week_is_refused(slots) -> None:
    with pytest.raises(ValueError, match="every day or on one weekday"):
        posting_slots.replace_slots("w1", [{"time": "09:00", "weekday": 9}], factory=slots)


def test_a_refused_save_leaves_the_stored_slots_untouched(slots) -> None:
    posting_slots.replace_slots("w1", [{"time": "09:00"}], factory=slots)

    with pytest.raises(ValueError):
        posting_slots.replace_slots("w1", [{"time": "12:00"}, {"time": "99:00"}], factory=slots)

    assert [slot["time"] for slot in posting_slots.list_slots("w1", factory=slots)] == ["09:00"]


def test_too_many_slots_is_refused(slots) -> None:
    entries = [{"time": f"{hour:02d}:{minute:02d}"} for hour in range(24) for minute in (0, 30)]

    with pytest.raises(ValueError, match="or fewer"):
        posting_slots.replace_slots("w1", entries, factory=slots)


def test_custom_presets_are_saved_and_join_the_built_ins(slots) -> None:
    with slots() as session, session.begin():
        saved = posting_slots.create_preset(
            "w1", "Launch rhythm", "Weekday mornings", [
                {"weekday": 0, "time": "09:15"},
                {"weekday": 2, "time": "09:15"},
            ], session=session,
        )
        available = posting_slots.preset_payload("w1", session=session)

    assert saved["kind"] == "custom"
    assert saved["slots"] == [
        {"weekday": 0, "time": "09:15"},
        {"weekday": 2, "time": "09:15"},
    ]
    assert {item["id"] for item in available} >= {"commute", saved["id"]}


def test_schedule_resolution_reads_narrowest_statement_first(slots) -> None:
    """Destination, then campaign, then page, then the workspace.

    The campaign sits ahead of the page deliberately: a page assignment is a
    standing property of the account, and hours chosen for a campaign are a
    decision being made now. Behind the page, the setting would work on the
    accounts with no assignment and quietly do nothing on the others.
    """
    posting_slots.replace_slots("w1", [{"time": "11:00"}], factory=slots)
    with slots() as session, session.begin():
        custom = posting_slots.create_preset(
            "w1", "Page prime time", "", [{"time": "19:30"}], session=session
        )
        campaign = posting_slots.create_preset(
            "w1", "Campaign hours", "", [{"time": "06:45"}], session=session
        )
        posting_slots.assign_page(
            "w1", "instagram:@brand", custom["id"], session=session
        )
        page_slots, page_rule = posting_slots.resolved_slots(
            "w1", session=session, page_key="instagram:@brand"
        )
        campaign_slots, campaign_rule = posting_slots.resolved_slots(
            "w1", session=session, page_key="instagram:@brand",
            campaign_preset_id=campaign["id"],
        )
        destination_slots, destination_rule = posting_slots.resolved_slots(
            "w1", session=session, page_key="instagram:@brand",
            campaign_preset_id=campaign["id"], override_preset_id="commute",
        )
        fallback_slots, fallback_rule = posting_slots.resolved_slots(
            "w1", session=session, page_key="youtube:@other"
        )
        # A campaign with no hours of its own still lands on the page's.
        inherited_slots, inherited_rule = posting_slots.resolved_slots(
            "w1", session=session, page_key="instagram:@brand",
            campaign_preset_id=None,
        )

    assert [(item.hour, item.minute) for item in page_slots] == [(19, 30)]
    assert page_rule["source"] == "page"
    assert [(item.hour, item.minute) for item in campaign_slots] == [(6, 45)]
    assert campaign_rule["source"] == "campaign"
    assert campaign_rule["label"] == "Campaign hours"
    assert [(item.hour, item.minute) for item in destination_slots][0] == (7, 30)
    assert destination_rule["source"] == "destination"
    assert fallback_rule["source"] == "workspace"
    assert [(item.hour, item.minute) for item in fallback_slots] == [(11, 0)]
    assert [(item.hour, item.minute) for item in inherited_slots] == [(19, 30)]
    assert inherited_rule["source"] == "page"


def test_a_saved_preset_can_be_deleted_and_its_pages_fall_back(slots) -> None:
    with slots() as session, session.begin():
        saved = posting_slots.create_preset(
            "w1", "Trial hours", "", [{"time": "10:00"}], session=session
        )
        posting_slots.assign_page("w1", "facebook:@brand", saved["id"], session=session)
        posting_slots.delete_preset("w1", saved["id"], session=session)

        assert posting_slots.preset_by_id("w1", saved["id"], session=session) is None
        # The page it was assigned to inherits again rather than pointing at
        # a rhythm that no longer exists.
        assert posting_slots.page_assignments("w1", session=session) == {}


def test_a_built_in_preset_refuses_deletion(slots) -> None:
    """It exists in every workspace and would resurrect on the next read - a
    delete that does not delete is worse than no delete."""
    with slots() as session, session.begin():
        with pytest.raises(ValueError, match="built in"):
            posting_slots.delete_preset("w1", "commute", session=session)


def test_deleting_another_workspaces_preset_is_not_found(slots) -> None:
    with slots() as session, session.begin():
        theirs = posting_slots.create_preset(
            "w2", "Their hours", "", [{"time": "10:00"}], session=session
        )
        with pytest.raises(LookupError):
            posting_slots.delete_preset("w1", theirs["id"], session=session)


# --- editing one -----------------------------------------------------------------


def test_a_preset_can_be_renamed_without_retiming_it(slots) -> None:
    """A rename is the common edit, and it must not be a way to lose the times.

    Requiring them to be sent back to change a name is how a rename becomes a
    re-timing nobody asked for.
    """
    with slots() as session, session.begin():
        saved = posting_slots.create_preset(
            "w1", "Trial hours", "", [{"time": "10:00"}, {"time": "16:00"}],
            session=session,
        )

        renamed = posting_slots.update_preset(
            "w1", saved["id"], session=session, label="Shop hours",
        )

    assert renamed["label"] == "Shop hours"
    assert renamed["slots"] == saved["slots"]


def test_retiming_a_preset_leaves_the_pages_on_it(slots) -> None:
    """The point of a preset: three pages on one rhythm move when it moves.

    Reassigning them by hand afterwards is the work a preset exists to avoid.
    """
    with slots() as session, session.begin():
        saved = posting_slots.create_preset(
            "w1", "Trial hours", "", [{"time": "10:00"}], session=session,
        )
        posting_slots.assign_page("w1", "facebook:@brand", saved["id"], session=session)

        moved = posting_slots.update_preset(
            "w1", saved["id"], session=session,
            entries=[{"time": "08:00"}, {"time": "20:00"}],
        )

    assert [entry["time"] for entry in moved["slots"]] == ["08:00", "20:00"]
    assert posting_slots.page_assignments("w1", session=session) == {
        "facebook:@brand": saved["id"],
    }


def test_a_preset_keeps_its_own_name_when_renamed_to_it(slots) -> None:
    # Its own name is not a clash with itself, which is what a plain
    # "does this name exist" check would have said.
    with slots() as session, session.begin():
        saved = posting_slots.create_preset(
            "w1", "Trial hours", "", [{"time": "10:00"}], session=session,
        )

        same = posting_slots.update_preset(
            "w1", saved["id"], session=session, label="Trial hours",
        )

    assert same["label"] == "Trial hours"


def test_a_rename_cannot_land_on_another_preset(slots) -> None:
    """Two rows answering to one name leaves the picker showing whichever
    sorted first, which is not a choice anybody made."""
    with slots() as session, session.begin():
        posting_slots.create_preset(
            "w1", "Shop hours", "", [{"time": "10:00"}], session=session,
        )
        other = posting_slots.create_preset(
            "w1", "Trial hours", "", [{"time": "11:00"}], session=session,
        )

        with pytest.raises(ValueError, match="already exists"):
            posting_slots.update_preset(
                "w1", other["id"], session=session, label="Shop hours",
            )


def test_a_rename_cannot_land_on_a_built_in_either(slots) -> None:
    with slots() as session, session.begin():
        mine = posting_slots.create_preset(
            "w1", "Trial hours", "", [{"time": "10:00"}], session=session,
        )

        with pytest.raises(ValueError, match="already exists"):
            posting_slots.update_preset(
                "w1", mine["id"], session=session, label="Commute hours",
            )


def test_a_built_in_preset_refuses_editing(slots) -> None:
    """Same reason it refuses deletion: the next read would undo it."""
    with slots() as session, session.begin():
        with pytest.raises(ValueError, match="built in"):
            posting_slots.update_preset(
                "w1", "commute", session=session, label="My hours",
            )


def test_editing_another_workspaces_preset_is_not_found(slots) -> None:
    with slots() as session, session.begin():
        theirs = posting_slots.create_preset(
            "w2", "Their hours", "", [{"time": "10:00"}], session=session,
        )

        with pytest.raises(LookupError):
            posting_slots.update_preset(
                "w1", theirs["id"], session=session, label="Mine now",
            )


def test_a_preset_cannot_be_emptied(slots) -> None:
    # A preset with no times applies nothing, and a page assigned to it would
    # silently stop posting.
    with slots() as session, session.begin():
        saved = posting_slots.create_preset(
            "w1", "Trial hours", "", [{"time": "10:00"}], session=session,
        )

        with pytest.raises(ValueError, match="at least one posting time"):
            posting_slots.update_preset("w1", saved["id"], session=session, entries=[])
