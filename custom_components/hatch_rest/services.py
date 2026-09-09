"""Hatch Rest services for on-device programs."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_extract_config_entry_ids

from .const import (
    DOMAIN,
    PROGRAM_SLOT_COUNT,
    SERVICE_CLEAR_PROGRAM,
    SERVICE_ENABLE_PROGRAM,
    SERVICE_REFRESH_PROGRAMS,
    SERVICE_SET_PROGRAM,
    SERVICE_SYNC_CLOCK,
    PyHatchBabyRestSound,
)
from .coordinator import HatchBabyRestUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

DAY_NAMES = [
    "sunday",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
]

TRACK_NAMES = [s.name for s in PyHatchBabyRestSound]

SET_PROGRAM_SCHEMA = vol.Schema(
    {
        vol.Required("index"): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=PROGRAM_SLOT_COUNT)
        ),
        vol.Optional("replace", default=False): cv.boolean,
        vol.Optional("name"): cv.string,
        vol.Optional("enabled"): cv.boolean,
        vol.Optional("time"): cv.string,
        vol.Optional("duration_seconds"): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=65535)
        ),
        vol.Optional("days"): vol.All(cv.ensure_list, [vol.In(DAY_NAMES)]),
        vol.Optional("power"): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
        vol.Optional("volume"): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
        vol.Optional("track"): vol.Any(
            vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
            vol.In(TRACK_NAMES),
            vol.In([n.capitalize() for n in TRACK_NAMES]),
        ),
        vol.Optional("color"): vol.Schema(
            {
                vol.Optional("r"): vol.All(vol.Coerce(int), vol.Range(0, 255)),
                vol.Optional("g"): vol.All(vol.Coerce(int), vol.Range(0, 255)),
                vol.Optional("b"): vol.All(vol.Coerce(int), vol.Range(0, 255)),
                vol.Optional("a"): vol.All(vol.Coerce(int), vol.Range(0, 255)),
            }
        ),
        vol.Optional("light_on"): cv.boolean,
        vol.Optional("sound_on"): cv.boolean,
        vol.Optional("sleep_timer"): cv.boolean,
        vol.Optional("buttons"): vol.All(vol.Coerce(int), vol.Range(min=0, max=65535)),
    },
    extra=vol.ALLOW_EXTRA,
)

CLEAR_PROGRAM_SCHEMA = vol.Schema(
    {
        vol.Required("index"): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=PROGRAM_SLOT_COUNT)
        ),
    },
    extra=vol.ALLOW_EXTRA,
)

ENABLE_PROGRAM_SCHEMA = vol.Schema(
    {
        vol.Required("index"): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=PROGRAM_SLOT_COUNT)
        ),
        vol.Required("enabled"): cv.boolean,
    },
    extra=vol.ALLOW_EXTRA,
)


def _normalize_service_data(data: dict) -> dict:
    """Normalize track names → ints for the API layer."""
    out = dict(data)
    track = out.get("track")
    if isinstance(track, str):
        key = track.lower()
        out["track"] = PyHatchBabyRestSound[key].value
    return out


async def _coordinators_from_call(
    hass: HomeAssistant, call: ServiceCall
) -> list[HatchBabyRestUpdateCoordinator]:
    entry_ids = await async_extract_config_entry_ids(hass, call)
    coordinators: list[HatchBabyRestUpdateCoordinator] = []
    for entry_id in entry_ids:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry and entry.domain == DOMAIN and entry.runtime_data is not None:
            coordinators.append(entry.runtime_data)
    if coordinators:
        return coordinators

    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.runtime_data is not None:
            coordinators.append(entry.runtime_data)
    return coordinators


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register domain services once."""
    if hass.services.has_service(DOMAIN, SERVICE_REFRESH_PROGRAMS):
        return

    async def handle_refresh(call: ServiceCall) -> None:
        for coordinator in await _coordinators_from_call(hass, call):
            await coordinator.async_refresh_programs()

    async def handle_set(call: ServiceCall) -> None:
        data = _normalize_service_data(dict(call.data))
        for coordinator in await _coordinators_from_call(hass, call):
            written = await coordinator.hatch_rest_device.set_program_from_service(data)
            coordinator.programs[written.index] = written
            coordinator.async_set_updated_data(coordinator.get_current_data())

    async def handle_clear(call: ServiceCall) -> None:
        index = int(call.data["index"])
        for coordinator in await _coordinators_from_call(hass, call):
            await coordinator.hatch_rest_device.clear_program(index)
            cleared = coordinator.hatch_rest_device.programs.get(index)
            if cleared is not None:
                coordinator.programs[index] = cleared
            coordinator.async_set_updated_data(coordinator.get_current_data())

    async def handle_enable(call: ServiceCall) -> None:
        index = int(call.data["index"])
        enabled = bool(call.data["enabled"])
        for coordinator in await _coordinators_from_call(hass, call):
            written = await coordinator.hatch_rest_device.set_program_enabled(
                index, enabled
            )
            coordinator.programs[written.index] = written
            coordinator.async_set_updated_data(coordinator.get_current_data())

    async def handle_sync_clock(call: ServiceCall) -> None:
        for coordinator in await _coordinators_from_call(hass, call):
            await coordinator.hatch_rest_device.sync_clock()

    hass.services.async_register(DOMAIN, SERVICE_REFRESH_PROGRAMS, handle_refresh)
    hass.services.async_register(
        DOMAIN, SERVICE_SET_PROGRAM, handle_set, schema=SET_PROGRAM_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_PROGRAM, handle_clear, schema=CLEAR_PROGRAM_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ENABLE_PROGRAM, handle_enable, schema=ENABLE_PROGRAM_SCHEMA
    )
    hass.services.async_register(DOMAIN, SERVICE_SYNC_CLOCK, handle_sync_clock)


@callback
def async_unload_services(hass: HomeAssistant) -> None:
    """Remove domain services when last entry unloads."""
    if hass.config_entries.async_entries(DOMAIN):
        return
    for service in (
        SERVICE_REFRESH_PROGRAMS,
        SERVICE_SET_PROGRAM,
        SERVICE_CLEAR_PROGRAM,
        SERVICE_ENABLE_PROGRAM,
        SERVICE_SYNC_CLOCK,
    ):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
