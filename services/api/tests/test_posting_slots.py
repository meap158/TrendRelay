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
