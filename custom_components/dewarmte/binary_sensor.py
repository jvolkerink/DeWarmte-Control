"""Binaire sensoren."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (BinarySensorDeviceClass, BinarySensorEntity,
                                                    BinarySensorEntityDescription)
from homeassistant.const import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .registers import RAW
from .sensor import main_device, slave_device


@dataclass(frozen=True, kw_only=True)
class DWBinary(BinarySensorEntityDescription):
    value_fn: Callable[[dict], bool | None]


def v(key):
    return lambda d: d["v"].get(key)


def dec(key):
    return lambda d: d["d"].get(key)


BINARIES: tuple[DWBinary, ...] = (
    DWBinary(key="base_load", name="Basislast actief", value_fn=dec("base_load")),
    DWBinary(key="demand", name="Warmtevraag", value_fn=dec("heat_demand")),
    DWBinary(key="season", name="Stookseizoen", value_fn=dec("season_heating")),
    DWBinary(key="compressor", name="Compressor draait", device_class=BinarySensorDeviceClass.RUNNING, value_fn=v("compressor_running")),
    DWBinary(key="unit_on", name="Unit aan (773)", value_fn=lambda d: None if d["v"].get("on_off") is None else bool(d["v"]["on_off"])),
    DWBinary(key="fault", name="Storing", device_class=BinarySensorDeviceClass.PROBLEM, value_fn=v("fault_active")),
    DWBinary(key="defrost", name="Ontdooien", value_fn=v("defrost")),
    DWBinary(key="source_support", name="Pomp T vraagt bronondersteuning", value_fn=dec("source_support")),
    DWBinary(key="dhw_recovery", name="Tapwater onder minimum", device_class=BinarySensorDeviceClass.PROBLEM, value_fn=dec("dhw_recovery")),
    DWBinary(key="legionella", name="Legionella-waarschuwing", device_class=BinarySensorDeviceClass.PROBLEM, value_fn=dec("legionella_warning")),
    DWBinary(key="circulation_ao", name="Circulatie pomp AO", device_class=BinarySensorDeviceClass.RUNNING,
             value_fn=lambda d: None if d["v"].get("ao_hm_flow") is None else d["v"]["ao_hm_flow"] > 1.0),
    DWBinary(key="pomp_t_running", name="Pomp T actief", device_class=BinarySensorDeviceClass.RUNNING,
             value_fn=lambda d: None if d["v"].get("pt_pm_w") is None else d["v"]["pt_pm_w"] >= 100),
    DWBinary(key="disinfecting", name="Desinfectie actief", device_class=BinarySensorDeviceClass.RUNNING,
             value_fn=lambda d: bool(d["c"].disinfect)),
    DWBinary(key="critical_ok", name="Modbus kritieke data vers", device_class=BinarySensorDeviceClass.CONNECTIVITY, value_fn=lambda d: bool(d["s"].get(1, {}).get("ok"))),
)


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list = [DWBinaryEntity(coordinator, entry, desc) for desc in BINARIES]
    entities += [DWRawBinary(coordinator, entry, r) for r in RAW if isinstance(r.bit, int)]
    async_add_entities(entities)


class DWBinaryEntity(CoordinatorEntity, BinarySensorEntity):
    _attr_has_entity_name = True
    entity_description: DWBinary

    def __init__(self, coordinator, entry, description: DWBinary) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = main_device(entry)

    @property
    def is_on(self):
        return self.entity_description.value_fn({**self.coordinator.data, "c": self.coordinator})


class DWRawBinary(CoordinatorEntity, BinarySensorEntity):
    """Eén statusbit uit de fabrikanttabel."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry, reg) -> None:
        super().__init__(coordinator)
        self._key = reg.key
        self._attr_unique_id = f"{entry.entry_id}_{reg.key}"
        self._attr_name = f"{reg.name} ({reg.addr}.{reg.bit})"[:120]
        self._attr_device_info = slave_device(entry, reg.slave)
        if "fault" in reg.name.lower() or "protection" in reg.name.lower():
            self._attr_device_class = BinarySensorDeviceClass.PROBLEM

    @property
    def is_on(self):
        return self.coordinator.data["v"].get("raw", {}).get(self._key)
