"""Hatch Rest buttons."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import HatchBabyRestEntity, HatchBabyRestUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hatch Rest buttons."""
    coordinator: HatchBabyRestUpdateCoordinator = config_entry.runtime_data
    async_add_entities(
        [
            HatchBabyRestSyncClockButton(coordinator),
            HatchBabyRestRefreshProgramsButton(coordinator),
        ]
    )


class HatchBabyRestSyncClockButton(HatchBabyRestEntity, ButtonEntity):
    """Push HA local time to the Rest device clock."""

    def __init__(self, coordinator: HatchBabyRestUpdateCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.unique_id}_sync_clock"
        self._attr_icon = "mdi:clock-check-outline"
        base = self.device_name.title() if self.device_name else "Hatch Rest"
        self._attr_name = f"{base} Sync Clock"

    async def async_press(self) -> None:
        """Sync device clock."""
        await self._hatch_rest_device.sync_clock()


class HatchBabyRestRefreshProgramsButton(HatchBabyRestEntity, ButtonEntity):
    """Re-read all program slots from the device."""

    def __init__(self, coordinator: HatchBabyRestUpdateCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.unique_id}_refresh_programs"
        self._attr_icon = "mdi:calendar-sync"
        base = self.device_name.title() if self.device_name else "Hatch Rest"
        self._attr_name = f"{base} Refresh Programs"

    async def async_press(self) -> None:
        """Refresh programs."""
        await self.coordinator.async_refresh_programs()
