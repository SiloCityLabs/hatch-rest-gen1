"""Hatch Rest coordinator."""

from datetime import timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import PyHatchBabyRestAsync
from .const import DOMAIN, PyHatchBabyRestSound
from .programs import HatchRestProgram

_LOGGER = logging.getLogger(__name__)


class HatchBabyRestUpdateCoordinator(DataUpdateCoordinator):
    """Hatch Rest data update coordinator."""

    def __init__(
        self,
        hass: HomeAssistant,
        unique_id: str | None,
        hatch_rest_device: PyHatchBabyRestAsync,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=60),
        )
        self.unique_id = unique_id
        self.hatch_rest_device = hatch_rest_device
        self.programs: dict[int, HatchRestProgram] = {}
        self._programs_loaded = False
        self._last_data: dict[str, Any] = {}

    def get_current_data(self) -> dict[str, Any]:
        """Get the current state of the Hatch Rest device."""
        active = [p for p in self.programs.values() if p.exists]
        data: dict[str, Any] = {
            "brightness": self.hatch_rest_device.brightness,
            "color": self.hatch_rest_device.color,
            "power": self.hatch_rest_device.power,
            "sound": self.hatch_rest_device.sound,
            "volume": self.hatch_rest_device.volume,
            "programs": {idx: p.as_dict() for idx, p in self.programs.items()},
            "program_count": len(active),
            "enabled_program_count": sum(1 for p in active if p.enabled),
        }
        _LOGGER.debug("Data updated: %s", data)
        return data

    async def async_refresh_programs(self) -> dict[int, HatchRestProgram]:
        """Pull all on-device program slots over BLE."""
        programs = await self.hatch_rest_device.get_programs()
        self.programs = programs
        self._programs_loaded = True
        self.async_set_updated_data(self.get_current_data())
        return programs

    async def _async_update_data(self) -> dict[str, Any]:
        _LOGGER.debug("Starting coordinator async update")
        self._last_data = self.data if self.data else {}
        try:
            await self.hatch_rest_device.refresh_data()
            if not self._programs_loaded:
                try:
                    self.programs = await self.hatch_rest_device.get_programs()
                    self._programs_loaded = True
                except Exception as prog_err:  # noqa: BLE001
                    _LOGGER.warning(
                        "Initial program load failed (will retry on demand): %r",
                        prog_err,
                    )
            else:
                # Keep cache; device state refresh does not re-pull all slots.
                self.programs = self.hatch_rest_device.programs or self.programs
        except Exception as e:
            _LOGGER.warning(
                "_async_update_data failed to refresh Hatch Rest data: %r", e
            )
            if self._last_data:
                _LOGGER.debug("Using cached data due to _async_update_data failure")
                return self._last_data
            raise UpdateFailed(f"Device update failed: {e}") from e
        else:
            return self.get_current_data()


class HatchBabyRestEntity(CoordinatorEntity[HatchBabyRestUpdateCoordinator]):
    """Hatch Rest entity."""

    def __init__(self, coordinator: HatchBabyRestUpdateCoordinator) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._hatch_rest_device = coordinator.hatch_rest_device
        self._attr_unique_id = coordinator.unique_id

    @property
    def device_info(self) -> DeviceInfo:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return device specific attributes."""
        if not all((self._hatch_rest_device.address, self.unique_id)):
            raise ValueError("Missing bluetooth address for hatch rest device")

        assert self._hatch_rest_device.address
        assert self.unique_id

        return DeviceInfo(
            connections={(dr.CONNECTION_BLUETOOTH, self._hatch_rest_device.address)},
            identifiers={(DOMAIN, self.unique_id)},
            manufacturer="Hatch",
            model="Rest",
            name=self.device_name,
        )

    @property
    def device_name(self):
        """Return the name of the device."""
        return self._hatch_rest_device.name
