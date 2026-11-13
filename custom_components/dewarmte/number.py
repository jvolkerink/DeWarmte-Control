"""Service-instellingen: elk schrijfbaar register van SunRain en Pomp T, alleen te wijzigen in servicemodus."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .registers import RAW
from .sensor import UNITS, slave_device
from .writes import ALLOWED, SERVICE


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    regs = [r for r in RAW if (r.slave, r.addr) in SERVICE and r.bit is None]
    async_add_entities(ServiceNumber(coordinator, entry, r) for r in regs)


class ServiceNumber(CoordinatorEntity, NumberEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_step = 1

    def __init__(self, coordinator, entry, reg) -> None:
        super().__init__(coordinator)
        self._reg = reg
        self._attr_unique_id = f"{entry.entry_id}_n{reg.slave}_{reg.addr}"
        regulated = " · door regeling beheerd" if (reg.slave, reg.addr) in ALLOWED else ""
        self._attr_name = f"{reg.name} ({reg.addr}){regulated}"[:120]
        self._attr_device_info = slave_device(entry, reg.slave)
        self._attr_native_min_value, self._attr_native_max_value = SERVICE[(reg.slave, reg.addr)]
        unit = UNITS.get(reg.unit, (None, None))[1]
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = "mdi:wrench"

    @property
    def native_value(self):
        return self.coordinator.data["v"].get("raw", {}).get(self._reg.key)

    @property
    def extra_state_attributes(self):
        return {"slave": self._reg.slave, "register": self._reg.addr,
                "servicemodus": self.coordinator.service_mode}

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_service_write(self._reg.slave, self._reg.addr, int(round(value)), self._attr_name)
