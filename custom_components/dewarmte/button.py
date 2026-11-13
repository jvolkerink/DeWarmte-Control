"""Knop om het uurrapport direct te versturen (handig om meldingen te testen)."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .sensor import main_device


async def async_setup_entry(hass, entry, async_add_entities):
    c = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ReportButton(c, entry), DisinfectStart(c, entry), DisinfectStop(c, entry)])


class ReportButton(CoordinatorEntity, ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "Uurrapport nu versturen"
    _attr_icon = "mdi:message-text-clock"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_report_now"
        self._attr_device_info = main_device(entry)

    async def async_press(self) -> None:
        await self.coordinator.async_send_report()


class DisinfectStart(CoordinatorEntity, ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "Desinfectie starten"
    _attr_icon = "mdi:bacteria-outline"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_disinfect_start"
        self._attr_device_info = main_device(entry)

    async def async_press(self) -> None:
        await self.coordinator.async_start_disinfection()


class DisinfectStop(CoordinatorEntity, ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "Desinfectie stoppen"
    _attr_icon = "mdi:stop-circle-outline"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_disinfect_stop"
        self._attr_device_info = main_device(entry)

    async def async_press(self) -> None:
        await self.coordinator.async_stop_disinfection()
