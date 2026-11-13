"""Schakelaar 'Regeling actief': uit = alleen meekijken, er wordt niets geschreven."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .sensor import main_device


async def async_setup_entry(hass, entry, async_add_entities):
    c = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ControlSwitch(c, entry), ServiceSwitch(c, entry)])


class ControlSwitch(CoordinatorEntity, SwitchEntity):
    _attr_has_entity_name = True
    _attr_name = "Regeling actief"
    _attr_icon = "mdi:heat-pump"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_control"
        self._attr_device_info = main_device(entry)

    @property
    def is_on(self) -> bool:
        return self.coordinator.control_enabled

    @property
    def extra_state_attributes(self):
        return {"uitgeschakeld_omdat": self.coordinator.trip_reason}

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_control(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_control(False, "handmatig uitgezet")


class ServiceSwitch(CoordinatorEntity, SwitchEntity):
    """Ontgrendelt de service-instellingen voor 30 minuten; gaat daarna vanzelf uit."""

    _attr_has_entity_name = True
    _attr_name = "Servicemodus"
    _attr_icon = "mdi:lock-open-variant"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_service_mode"
        self._attr_device_info = main_device(entry)

    @property
    def is_on(self) -> bool:
        return self.coordinator.service_mode

    @property
    def extra_state_attributes(self):
        import time  # noqa: PLC0415
        left = max(0, self.coordinator.service_until - time.monotonic())
        return {"minuten_over": round(left / 60)}

    async def async_turn_on(self, **kwargs) -> None:
        self.coordinator.set_service_mode(True)

    async def async_turn_off(self, **kwargs) -> None:
        self.coordinator.set_service_mode(False)
