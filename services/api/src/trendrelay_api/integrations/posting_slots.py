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
from trendrelay_api.models import (
    PagePostingSchedule,
    PostingSchedulePreset,
    PublishingSlot,
    utc_now,
)

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


def _normalise_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate, deduplicate and serialise schedule entries."""
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
    if not unique:
        raise ValueError("A preset needs at least one posting time.")
    if len(unique) > MAX_SLOTS:
        raise ValueError(f"Keep it to {MAX_SLOTS} slots or fewer.")
    return [
        {"weekday": weekday, "time": f"{hour:02d}:{minute:02d}"}
        for weekday, hour, minute in unique
    ]


def _builtin_payload() -> list[dict[str, Any]]:
    return [
        {
            "id": preset.id,
            "label": preset.label,
            "summary": preset.summary,
            "kind": "builtin",
            "times": [f"{hour:02d}:{minute:02d}" for hour, minute in preset.times],
            "slots": [
                {"weekday": EVERY_DAY, "time": f"{hour:02d}:{minute:02d}"}
                for hour, minute in preset.times
            ],
        }
        for preset in PRESETS
    ]


def preset_payload(
    workspace_id: str | None = None, *, session: Session | None = None
) -> list[dict[str, Any]]:
    """Built-in starting points plus the workspace's saved presets."""
    payload = _builtin_payload()
    if not workspace_id or session is None:
        return payload
    custom = session.scalars(
        select(PostingSchedulePreset)
        .where(PostingSchedulePreset.workspace_id == workspace_id)
        .order_by(PostingSchedulePreset.label)
    ).all()
    payload.extend({
        "id": item.id,
        "label": item.label,
        "summary": item.summary,
        "kind": "custom",
        "slots": item.slots,
        "times": [entry["time"] for entry in item.slots if entry.get("weekday", -1) == -1],
    } for item in custom)
    return payload


def preset_by_id(
    workspace_id: str, preset_id: str, *, session: Session
) -> dict[str, Any] | None:
    return next(
        (item for item in preset_payload(workspace_id, session=session) if item["id"] == preset_id),
        None,
    )


def create_preset(
    workspace_id: str, label: str, summary: str, entries: list[dict[str, Any]], *, session: Session
) -> dict[str, Any]:
    clean_label = label.strip()
    if not clean_label:
        raise ValueError("Name this preset.")
    if any(item["label"].casefold() == clean_label.casefold()
           for item in preset_payload(workspace_id, session=session)):
        raise ValueError("A preset with that name already exists.")
    item = PostingSchedulePreset(
        workspace_id=workspace_id,
        label=clean_label,
        summary=summary.strip(),
        slots=_normalise_entries(entries),
    )
    session.add(item)
    session.flush()
    return preset_by_id(workspace_id, item.id, session=session) or {}


def assign_page(
    workspace_id: str, page_key: str, preset_id: str | None, *, session: Session
) -> dict[str, str] | None:
    key = page_key.strip()
    if not key:
        raise ValueError("A page assignment needs a page key.")
    found = session.scalar(select(PagePostingSchedule).where(
        PagePostingSchedule.workspace_id == workspace_id,
        PagePostingSchedule.page_key == key,
    ))
    if preset_id is None:
        if found:
            session.delete(found)
            session.flush()
        return None
    if not preset_by_id(workspace_id, preset_id, session=session):
        raise ValueError("That posting preset is not available in this workspace.")
    if found:
        found.preset_id = preset_id
        found.updated_at = utc_now()
    else:
        found = PagePostingSchedule(
            workspace_id=workspace_id, page_key=key, preset_id=preset_id
        )
        session.add(found)
    session.flush()
    return {"page_key": key, "preset_id": preset_id}


def page_assignments(workspace_id: str, *, session: Session) -> dict[str, str]:
    rows = session.scalars(select(PagePostingSchedule).where(
        PagePostingSchedule.workspace_id == workspace_id
    )).all()
    return {row.page_key: row.preset_id for row in rows}


@dataclass(frozen=True)
class ResolvedSlot:
    weekday: int
    hour: int
    minute: int


def resolved_slots(
    workspace_id: str,
    *,
    session: Session,
    page_key: str | None = None,
    override_preset_id: str | None = None,
) -> tuple[list[ResolvedSlot | PublishingSlot], dict[str, Any]]:
    """Resolve destination override, then page assignment, then workspace slots."""
    preset_id = override_preset_id
    source = "campaign" if preset_id else "workspace"
    if not preset_id and page_key:
        assigned = session.scalar(select(PagePostingSchedule).where(
            PagePostingSchedule.workspace_id == workspace_id,
            PagePostingSchedule.page_key == page_key,
        ))
        if assigned:
            preset_id = assigned.preset_id
            source = "page"
    if preset_id:
        preset = preset_by_id(workspace_id, preset_id, session=session)
        if preset:
            slots = []
            for entry in preset["slots"]:
                hour, minute = parse_time(entry["time"])
                slots.append(ResolvedSlot(int(entry.get("weekday", EVERY_DAY)), hour, minute))
            return slots, {
                "source": source, "preset_id": preset_id, "label": preset["label"],
                "slot_count": len(slots),
            }
    slots = session.scalars(select(PublishingSlot).where(
        PublishingSlot.workspace_id == workspace_id
    )).all()
    return list(slots), {
        "source": "workspace", "preset_id": None, "label": "Workspace posting times",
        "slot_count": len(slots),
    }


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
    normalised = [] if not entries else _normalise_entries(entries)
    unique = [
        (entry["weekday"], *parse_time(entry["time"])) for entry in normalised
    ]

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
