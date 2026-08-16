"""A workspace's own recurring posting times.

There is deliberately no default schedule. A list of invented hours looks
considered while being arbitrary, and a slot that nobody chose is worse than an
empty calendar: it invites posting at a time the workspace never picked. So a
workspace starts with nothing and either adds times by hand or applies one of
the presets below, which are named for the audience behaviour they assume so
the assumption can be judged rather than trusted.

Times are wall-clock in the workspace's own timezone; the caller sends the hour
it means and the interface schedules against the same clock the operator reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from trendrelay_api.database import SessionFactory
from trendrelay_api.models import PublishingSlot

EVERY_DAY = -1
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MAX_SLOTS = 40


@dataclass(frozen=True)
class SlotPreset:
    id: str
    label: str
    summary: str
    times: tuple[tuple[int, int], ...]


PRESETS: tuple[SlotPreset, ...] = (
    SlotPreset(
        id="commute",
        label="Commute hours",
        summary="Before work and on the way home, for an audience in your own timezone.",
        times=((7, 30), (8, 30), (17, 30), (18, 30)),
    ),
    SlotPreset(
        id="evening",
        label="Evening peak",
        summary="The hours most short-form video is watched locally.",
        times=((18, 0), (20, 0), (21, 30)),
    ),
    SlotPreset(
        id="spread",
        label="Spread through the day",
        summary="One post every few hours, for testing when your audience is actually awake.",
        times=((9, 0), (12, 0), (15, 0), (18, 0), (21, 0)),
    ),
    SlotPreset(
        id="offset",
        label="Reach another timezone",
        summary=(
            "Overnight locally, which lands in the working day roughly eight to twelve "
            "hours ahead. Use when your audience is not where you are."
        ),
        times=((23, 0), (2, 0), (5, 0)),
    ),
)


def preset_payload() -> list[dict[str, Any]]:
    return [
        {
            "id": preset.id,
            "label": preset.label,
            "summary": preset.summary,
            "times": [f"{hour:02d}:{minute:02d}" for hour, minute in preset.times],
        }
        for preset in PRESETS
    ]


def parse_time(value: str) -> tuple[int, int]:
    """Read `HH:MM`, refusing anything a clock would not show."""
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"'{value}' is not a time of day. Use HH:MM, such as 18:30.")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError as error:
        raise ValueError(f"'{value}' is not a time of day. Use HH:MM, such as 18:30.") from error
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"'{value}' is not a time of day. Use HH:MM, such as 18:30.")
    return hour, minute


def _serialize(slot: PublishingSlot) -> dict[str, Any]:
    return {
        "id": slot.id,
        "weekday": slot.weekday,
        "weekday_label": (
            "Every day" if slot.weekday == EVERY_DAY else WEEKDAY_NAMES[slot.weekday]
        ),
        "hour": slot.hour,
        "minute": slot.minute,
        "time": f"{slot.hour:02d}:{slot.minute:02d}",
    }


def list_slots(
    workspace_id: str, *, factory=None, session: Session | None = None
) -> list[dict[str, Any]]:
    if session is not None:
        slots = session.scalars(
            select(PublishingSlot)
            .where(PublishingSlot.workspace_id == workspace_id)
            .order_by(PublishingSlot.weekday, PublishingSlot.hour, PublishingSlot.minute)
        ).all()
        return [_serialize(slot) for slot in slots]
    session_factory = factory or SessionFactory
    with session_factory() as active:
        slots = active.scalars(
            select(PublishingSlot)
            .where(PublishingSlot.workspace_id == workspace_id)
            .order_by(PublishingSlot.weekday, PublishingSlot.hour, PublishingSlot.minute)
        ).all()
        return [_serialize(slot) for slot in slots]


def replace_slots(
    workspace_id: str,
    entries: list[dict[str, Any]],
    *,
    factory=None,
    session: Session | None = None,
) -> list[dict[str, Any]]:
    """Set the workspace's slots to exactly `entries`, discarding duplicates.

    Replacing rather than merging keeps the stored set identical to what the
    operator sees, so removing a slot in the interface actually removes it.
    """
    parsed: list[tuple[int, int, int]] = []
    for entry in entries:
        weekday = int(entry.get("weekday", EVERY_DAY))
        if not EVERY_DAY <= weekday <= 6:
            raise ValueError("A slot repeats every day or on one weekday.")
        raw = entry.get("time")
        if isinstance(raw, str):
            hour, minute = parse_time(raw)
        else:
            hour, minute = int(entry.get("hour", -1)), int(entry.get("minute", 0))
            if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                raise ValueError("A slot needs an hour between 0 and 23.")
        parsed.append((weekday, hour, minute))

    unique = sorted(set(parsed))
    if len(unique) > MAX_SLOTS:
        raise ValueError(f"Keep it to {MAX_SLOTS} slots or fewer.")

    def replace(active: Session) -> list[dict[str, Any]]:
        active.execute(
            delete(PublishingSlot).where(PublishingSlot.workspace_id == workspace_id)
        )
        for weekday, hour, minute in unique:
            active.add(
                PublishingSlot(
                    workspace_id=workspace_id, weekday=weekday, hour=hour, minute=minute
                )
            )
        active.flush()
        return list_slots(workspace_id, session=active)

    if session is not None:
        return replace(session)
    session_factory = factory or SessionFactory
    with session_factory() as active, active.begin():
        return replace(active)
