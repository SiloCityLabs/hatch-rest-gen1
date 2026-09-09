"""Hatch Rest sensors."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import HatchBabyRestEntity, HatchBabyRestUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hatch Rest sensors."""
    coordinator: HatchBabyRestUpdateCoordinator = config_entry.runtime_data
    async_add_entities([HatchBabyRestProgramsSensor(coordinator)])


class HatchBabyRestProgramsSensor(HatchBabyRestEntity, SensorEntity):
    """Sensor summarizing on-device program slots."""

    def __init__(self, coordinator: HatchBabyRestUpdateCoordinator) -> None:
        """Initialize sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.unique_id}_program_count"
        self._attr_name = (
            f"{self.device_name.title()} Programs"
            if self.device_name
            else "Hatch Rest Programs"
        )
        self._attr_icon = "mdi:calendar-clock"
        self._attr_native_unit_of_measurement = "programs"

    @property
    def native_value(self) -> int:
        """Return count of existing program slots."""
        data = self.coordinator.data or {}
        return int(data.get("program_count") or 0)

    @property
    def extra_state_attributes(self) -> dict:
        """Expose full program list."""
        data = self.coordinator.data or {}
        return {
            "enabled_program_count": data.get("enabled_program_count", 0),
            "programs": data.get("programs", {}),
        }
