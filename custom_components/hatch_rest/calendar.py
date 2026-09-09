"""Hatch Rest calendar for on-device programs."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import logging

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import HatchBabyRestEntity, HatchBabyRestUpdateCoordinator
from .programs import DAY_BITS, HatchRestProgram

_LOGGER = logging.getLogger(__name__)

# Python weekday: Mon=0 … Sun=6 → Hatch bit names
_WEEKDAY_TO_NAME = {
    0: "monday",
    1: "tuesday",
    2: "wednesday",
    3: "thursday",
    4: "friday",
    5: "saturday",
    6: "sunday",
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hatch Rest calendar."""
    coordinator: HatchBabyRestUpdateCoordinator = config_entry.runtime_data
    async_add_entities([HatchBabyRestProgramsCalendar(coordinator)])


class HatchBabyRestProgramsCalendar(HatchBabyRestEntity, CalendarEntity):
    """Expose enabled on-device programs as calendar events."""

    def __init__(self, coordinator: HatchBabyRestUpdateCoordinator) -> None:
        """Initialize calendar."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.unique_id}_programs"
        self._attr_name = (
            f"{self.device_name.title()} Programs"
            if self.device_name
            else "Hatch Rest Programs"
        )

    @property
    def event(self) -> CalendarEvent | None:
        """Return the next upcoming program occurrence."""
        now = dt_util.now()
        events = self._events_between(now, now + timedelta(days=8))
        return events[0] if events else None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return program occurrences in the requested window."""
        return self._events_between(start_date, end_date)

    def _events_between(
        self, start: datetime, end: datetime
    ) -> list[CalendarEvent]:
        programs = list(self.coordinator.programs.values())
        events: list[CalendarEvent] = []
        day = start.date()
        last = end.date()
        while day <= last:
            for program in programs:
                event = self._event_for_day(program, day)
                if event is None:
                    continue
                if event.end <= start or event.start >= end:
                    continue
                events.append(event)
            day += timedelta(days=1)
        events.sort(key=lambda e: e.start)
        return events

    def _event_for_day(
        self, program: HatchRestProgram, day: date
    ) -> CalendarEvent | None:
        if not program.exists or not program.enabled or not program.time_of_day:
            return None
        weekday_name = _WEEKDAY_TO_NAME[day.weekday()]
        if not (program.days.mask & DAY_BITS[weekday_name]):
            # Empty mask → treat as every day (matches "no days set" edge case poorly;
            # app always writes a mask when saving).
            if program.days.mask != 0:
                return None

        start_dt = datetime.combine(day, program.time_of_day, tzinfo=dt_util.DEFAULT_TIME_ZONE)
        duration = program.duration_seconds or 0
        end_dt = start_dt + timedelta(seconds=duration if duration > 0 else 60)
        summary = program.name or f"Program {program.index}"
        description = (
            f"Slot {program.index} · track {program.track} · "
            f"vol {program.volume} · power {program.power}"
        )
        return CalendarEvent(
            start=start_dt,
            end=end_dt,
            summary=summary,
            description=description,
            uid=f"{self.unique_id}_{program.index}_{day.isoformat()}",
        )
