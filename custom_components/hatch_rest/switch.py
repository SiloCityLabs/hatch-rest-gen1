"""Hatch Rest switch."""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import PROGRAM_SLOT_COUNT
from .coordinator import HatchBabyRestEntity, HatchBabyRestUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hatch Rest switches."""
    coordinator: HatchBabyRestUpdateCoordinator = config_entry.runtime_data
    entities: list[SwitchEntity] = [HatchBabyRestSwitch(coordinator)]
    entities.extend(
        HatchBabyRestProgramSwitch(coordinator, index)
        for index in range(1, PROGRAM_SLOT_COUNT + 1)
    )
    async_add_entities(entities, update_before_add=True)


class HatchBabyRestSwitch(HatchBabyRestEntity, SwitchEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """Hatch Rest power switch entity."""

    @property
    def is_on(self) -> bool | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return whether the switch is on or not."""
        _LOGGER.debug("switch is_on = %s", self.coordinator.data.get("power"))
        return self.coordinator.data.get("power")

    @property
    def name(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the name of the entity."""
        if self._hatch_rest_device.name:
            return f"{self._hatch_rest_device.name.title()} Switch"
        return None

    async def async_turn_on(self, **_):
        """Turn on the Hatch Rest device."""
        if not self.is_on:
            _LOGGER.debug("switch setting on")
            await self._hatch_rest_device.turn_power_on()
            self.coordinator.async_set_updated_data(self.coordinator.get_current_data())

    async def async_turn_off(self, **_):
        """Turn off the Hatch Rest device."""
        if self.is_on:
            _LOGGER.debug("switch setting off")
            await self._hatch_rest_device.turn_power_off()
            self.coordinator.async_set_updated_data(self.coordinator.get_current_data())


class HatchBabyRestProgramSwitch(HatchBabyRestEntity, SwitchEntity):
    """Enable/disable (or create) one on-device program slot."""

    _attr_entity_registry_enabled_default = True

    def __init__(
        self, coordinator: HatchBabyRestUpdateCoordinator, index: int
    ) -> None:
        """Initialize program enable switch."""
        super().__init__(coordinator)
        self._index = index
        self._attr_unique_id = f"{coordinator.unique_id}_program_{index}_enabled"
        self._attr_icon = "mdi:calendar-check"

    @property
    def name(self) -> str:
        """Return entity name."""
        program = self.coordinator.programs.get(self._index)
        label = (program.name if program and program.name else f"Program {self._index}")
        base = self.device_name.title() if self.device_name else "Hatch Rest"
        return f"{base} {label} Enabled"

    @property
    def available(self) -> bool:
        """Available whenever the coordinator has data."""
        return self.coordinator.last_update_success

    @property
    def is_on(self) -> bool:
        """Return whether this program slot is enabled."""
        program = self.coordinator.programs.get(self._index)
        return bool(program and program.exists and program.enabled)

    @property
    def extra_state_attributes(self) -> dict:
        """Expose slot details for dashboards."""
        program = self.coordinator.programs.get(self._index)
        if not program:
            return {"index": self._index, "exists": False}
        return program.as_dict()

    async def async_turn_on(self, **_) -> None:
        """Enable slot (creates a default program if empty)."""
        written = await self._hatch_rest_device.set_program_enabled(self._index, True)
        self.coordinator.programs[self._index] = written
        self.coordinator.async_set_updated_data(self.coordinator.get_current_data())

    async def async_turn_off(self, **_) -> None:
        """Disable slot (keeps the program definition on-device)."""
        program = self.coordinator.programs.get(self._index)
        if not program or not program.exists:
            return
        written = await self._hatch_rest_device.set_program_enabled(self._index, False)
        self.coordinator.programs[self._index] = written
        self.coordinator.async_set_updated_data(self.coordinator.get_current_data())
