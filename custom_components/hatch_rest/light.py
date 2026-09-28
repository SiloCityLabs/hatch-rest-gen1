"""Hatch Rest light."""

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    LightEntity,
)
from homeassistant.components.light.const import ColorMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import HatchBabyRestEntity

_LOGGER = logging.getLogger(__name__)

# Used when turning the light on after brightness was set to 0 (toggle off).
_DEFAULT_ON_BRIGHTNESS = 128


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hatch Rest light."""
    coordinator = config_entry.runtime_data
    # only need to update_before_add on one entity -- switch is "master" entity
    async_add_entities([HatchBabyRestLight(coordinator)], update_before_add=False)


class HatchBabyRestLight(HatchBabyRestEntity, LightEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """Hatch Rest light entity."""

    def __init__(self, coordinator) -> None:
        """Initialize light and remember last non-zero brightness."""
        super().__init__(coordinator)
        self._last_brightness = _DEFAULT_ON_BRIGHTNESS

    @property
    def brightness(self) -> int | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the brightness of the light."""
        brightness = self.coordinator.data.get("brightness")
        _LOGGER.debug("light brightness = %s", brightness)
        if isinstance(brightness, int) and brightness > 0:
            self._last_brightness = brightness
        return brightness

    @property
    def color_mode(self) -> ColorMode:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return color mode."""
        return ColorMode.RGB

    @property
    def is_on(self) -> bool:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return if the light is on."""
        # Master power off ⇒ light off
        if self.coordinator.data.get("power") is False:
            return False
        brightness = self.coordinator.data.get("brightness") or 0
        return brightness > 0

    @property
    def name(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the name of the entity."""
        if self._hatch_rest_device.name:
            return f"{self._hatch_rest_device.name.title()} Light"
        return None

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the RGB color of the light."""
        _LOGGER.debug("light rgb_color = %s", self.coordinator.data.get("color"))
        return self.coordinator.data.get("color")

    @property
    def supported_color_modes(self) -> set[ColorMode]:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return supported color modes."""
        return {ColorMode.RGB}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Set the light on."""
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        rgb = kwargs.get(ATTR_RGB_COLOR)

        if not self._hatch_rest_device.power:
            _LOGGER.debug("light power off -- turning on")
            await self._hatch_rest_device.turn_power_on()

        # UI toggle sends no brightness. After turn_off we store 0 on-device, so
        # restore the last non-zero level (or a default) or is_on stays False.
        current = self._hatch_rest_device.brightness or 0
        if brightness is None and current <= 0:
            brightness = self._last_brightness or _DEFAULT_ON_BRIGHTNESS
            _LOGGER.debug("light toggle-on restoring brightness = %s", brightness)

        if brightness is not None:
            _LOGGER.debug("light setting brightness = %s", brightness)
            await self._hatch_rest_device.set_brightness(brightness)
            if brightness > 0:
                self._last_brightness = brightness
        if rgb is not None:
            _LOGGER.debug("light setting RGB = %s", rgb)
            await self._hatch_rest_device.set_color(*rgb)

        self.coordinator.async_set_updated_data(self.coordinator.get_current_data())

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Set the light off (brightness 0; power switch stays independent)."""
        current = self._hatch_rest_device.brightness or 0
        if current > 0:
            self._last_brightness = current
        await self._hatch_rest_device.set_brightness(0)
        self.coordinator.async_set_updated_data(self.coordinator.get_current_data())
