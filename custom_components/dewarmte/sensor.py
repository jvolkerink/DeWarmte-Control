"""Sensoren: metingen, beslissingen, acties, uurrapport en alle ruwe registers."""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (SensorDeviceClass, SensorEntity, SensorEntityDescription,
                                             SensorStateClass)
from homeassistant.const import (PERCENTAGE, EntityCategory, UnitOfEnergy, UnitOfFrequency, UnitOfPower,
                                 UnitOfTemperature)
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, VERSION
from .engine import MODE_NAMES
from .registers import RAW, run_state_text

T, M = SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT
C = UnitOfTemperature.CELSIUS


@dataclass(frozen=True, kw_only=True)
class DWSensor(SensorEntityDescription):
    value_fn: Callable[[dict], Any]
    attrs_fn: Callable[[dict], dict] | None = None


def v(key):
    return lambda d: d["v"].get(key)


def dec(key):
    return lambda d: d["d"].get(key)


def _txt(text):
    return None if text is None else str(text)[:255]


def _mode_name(d):
    val = d["v"].get("power_mode")
    return None if val is None else MODE_NAMES.get(int(val), f"onbekend ({int(val)})")


def _bus(d):
    bad = [x["name"] for x in d["s"].values() if not x["ok"]]
    return "alles OK" if not bad else _txt("Geen verse data: " + ", ".join(bad))


SENSORS: tuple[DWSensor, ...] = (
    # --- metingen
    DWSensor(key="outdoor", name="Buitentemperatuur (gebruikt)", device_class=T, state_class=M, native_unit_of_measurement=C, value_fn=dec("outdoor_used")),
    DWSensor(key="t_supply", name="Aanvoertemperatuur", device_class=T, state_class=M, native_unit_of_measurement=C, value_fn=v("t_supply")),
    DWSensor(key="t_return", name="Retourtemperatuur", device_class=T, state_class=M, native_unit_of_measurement=C, value_fn=v("t_return")),
    DWSensor(key="f_comp", name="Compressorfrequentie", device_class=SensorDeviceClass.FREQUENCY, state_class=M, native_unit_of_measurement=UnitOfFrequency.HERTZ, value_fn=v("f_comp")),
    DWSensor(key="run_state", name="Bedrijfstoestand", value_fn=lambda d: run_state_text(d["v"].get("run_state_1"))),
    DWSensor(key="flow_ao", name="Debiet pomp AO", state_class=M, native_unit_of_measurement="L/min", value_fn=v("ao_hm_flow")),
    DWSensor(key="p_th_ao", name="Thermisch vermogen pomp AO", device_class=SensorDeviceClass.POWER, state_class=M, native_unit_of_measurement=UnitOfPower.KILO_WATT, value_fn=v("ao_hm_kw")),
    DWSensor(key="p_el_ao", name="Elektrisch vermogen pomp AO", device_class=SensorDeviceClass.POWER, state_class=M, native_unit_of_measurement=UnitOfPower.WATT, value_fn=v("ao_pm_w")),
    DWSensor(key="cop_ao", name="COP pomp AO (momentaan)", state_class=M, value_fn=v("cop_ao")),
    DWSensor(key="e_ao", name="Energie pomp AO", device_class=SensorDeviceClass.ENERGY, state_class=SensorStateClass.TOTAL_INCREASING, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, value_fn=v("ao_pm_energy")),
    DWSensor(key="p_el_pt", name="Elektrisch vermogen pomp T", device_class=SensorDeviceClass.POWER, state_class=M, native_unit_of_measurement=UnitOfPower.WATT, value_fn=v("pt_pm_w")),
    DWSensor(key="e_pt", name="Energie pomp T", device_class=SensorDeviceClass.ENERGY, state_class=SensorStateClass.TOTAL_INCREASING, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, value_fn=v("pt_pm_energy")),
    DWSensor(key="dhw_top", name="Tapwater boven", device_class=T, state_class=M, native_unit_of_measurement=C, value_fn=v("dhw_top")),
    DWSensor(key="dhw_bottom", name="Tapwater onder", device_class=T, state_class=M, native_unit_of_measurement=C, value_fn=v("dhw_bottom")),
    DWSensor(key="t_source_in", name="Pomp T bron in", device_class=T, state_class=M, native_unit_of_measurement=C, value_fn=v("t_source_in")),
    # --- werkelijke instellingen in de pompen (alleen gelezen)
    DWSensor(key="set_769", name="Werkelijk aanvoersetpoint (769)", device_class=T, native_unit_of_measurement=C, value_fn=v("set_heating")),
    DWSensor(key="power_mode", name="Werkelijke vermogensmodus (775)", value_fn=_mode_name),
    DWSensor(key="control_method", name="Regelmethode unit (372)", value_fn=v("control_method")),
    DWSensor(key="pt_set", name="Werkelijk tapwatersetpoint Pomp T", device_class=T, native_unit_of_measurement=C, value_fn=v("pt_set")),
    # --- beslissingen van de engine
    DWSensor(key="demand_level", name="Warmtevraag (gem. klepopening)", state_class=M, native_unit_of_measurement=PERCENTAGE, value_fn=dec("demand_level")),
    DWSensor(key="worst_deficit", name="Grootste kamertekort", state_class=M, native_unit_of_measurement=C, value_fn=dec("worst_deficit")),
    DWSensor(key="base_curve", name="Stooklijn basis", device_class=T, native_unit_of_measurement=C, value_fn=dec("base_curve")),
    DWSensor(key="supply_target", name="Gewenst aanvoersetpoint", device_class=T, native_unit_of_measurement=C, value_fn=dec("supply_target"),
             attrs_fn=lambda d: {"basis": d["d"]["base_curve"], "preset_feedforward": d["d"].get("feedforward"),
                                 "klepbijsturing": d["d"].get("trim"), "geleerde_offset": d["d"]["offset"],
                                 "reden": d["d"].get("trim_reason"), "kamertekort_gefilterd": d["d"].get("room_deficits")}),
    DWSensor(key="trim", name="Klepbijsturing", state_class=M, native_unit_of_measurement=C, value_fn=dec("trim"),
             attrs_fn=lambda d: {"reden": d["d"].get("trim_reason")}),
    DWSensor(key="feedforward", name="Preset-correctie", state_class=M, native_unit_of_measurement=C, value_fn=dec("feedforward")),
    DWSensor(key="offset", name="Geleerde offset", state_class=M, native_unit_of_measurement=C, value_fn=dec("offset")),
    DWSensor(key="shortfall", name="Setpointtekort", state_class=M, native_unit_of_measurement=C, value_fn=dec("shortfall")),
    DWSensor(key="mode_desired", name="Gewenste vermogensmodus", value_fn=dec("power_mode_name"), attrs_fn=lambda d: {"reden": d["d"]["power_mode_reason"]}),
    DWSensor(key="dhw_advice", name="Gewenst tapwatersetpoint", device_class=T, native_unit_of_measurement=C, value_fn=dec("dhw_setpoint_advice"), attrs_fn=lambda d: {"reden": d["d"]["dhw_reason"]}),
    DWSensor(key="legionella_days", name="Dagen sinds tapwater ≥ 60 °C", state_class=M, native_unit_of_measurement="d", value_fn=dec("legionella_days")),
    DWSensor(key="planned", name="Gewenste wijzigingen", value_fn=lambda d: _txt(d["d"]["summary"]), attrs_fn=lambda d: {"wijzigingen": d["d"]["planned_writes"], "blokkades": d["d"]["blocks"], "opmerkingen": d["d"]["notes"]}),
    DWSensor(key="blocks", name="Blokkades", value_fn=lambda d: _txt("; ".join(d["d"]["blocks"]) or "geen")),
    DWSensor(key="bus", name="Modbus-status", value_fn=_bus, attrs_fn=lambda d: {str(k): x for k, x in d["s"].items()}),
)


SLAVE_DEVICES = {1: ("SunRain monoblock", "SunRain"), 2: ("Pomp T (tapwater)", "Pomp T"),
                 5: ("kWh-meter pomp AO", "Meter"), 7: ("kWh-meter pomp T", "Meter"),
                 14: ("Warmtemeter pomp AO", "Mainone"), 16: ("Warmtemeter pomp T", "Mainone")}
UNITS = {"°C": (T, C), "Hz": (SensorDeviceClass.FREQUENCY, UnitOfFrequency.HERTZ), "V": (SensorDeviceClass.VOLTAGE, "V"),
         "A": (SensorDeviceClass.CURRENT, "A"), "W": (SensorDeviceClass.POWER, UnitOfPower.WATT),
         "kW": (SensorDeviceClass.POWER, UnitOfPower.KILO_WATT), "kWh": (SensorDeviceClass.ENERGY, UnitOfEnergy.KILO_WATT_HOUR),
         "L/min": (None, "L/min"), "K": (None, "K")}


def main_device(entry) -> DeviceInfo:
    return DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name="DeWarmte",
                      manufacturer="DeWarmte", model="Warmtepompregeling", sw_version=VERSION)


def slave_device(entry, slave: int) -> DeviceInfo:
    name, maker = SLAVE_DEVICES[slave]
    return DeviceInfo(identifiers={(DOMAIN, f"{entry.entry_id}_{slave}")}, name=name, manufacturer=maker,
                      model=f"Modbus slave {slave}")


def _actions(c):
    a = c.actions[-1] if c.actions else None
    return None if a is None else _txt(f"{a['time']} {a['name']} {a['old']} → {a['new']}" + ("" if a["ok"] else " (mislukt)"))


EXTRA: tuple[DWSensor, ...] = (
    DWSensor(key="last_action", name="Laatste actie", value_fn=lambda d: _actions(d["c"]),
             attrs_fn=lambda d: {"acties": d["c"].actions[-30:]}),
    DWSensor(key="report", name="Laatste uurrapport", value_fn=lambda d: _txt(d["c"].last_report.get("titel")),
             attrs_fn=lambda d: d["c"].last_report),
    DWSensor(key="plan_offset", name="Prijs-offset kamers", icon="mdi:cash-clock", state_class=M,
             native_unit_of_measurement=C, value_fn=lambda d: d["c"].planner.offset,
             attrs_fn=lambda d: d["c"].plan_info()),
    DWSensor(key="vent", name="Ontluchten", icon="mdi:air-filter", value_fn=lambda d: d["c"].vent_text()),
    DWSensor(key="disinfection", name="Desinfectie", icon="mdi:bacteria-outline", value_fn=lambda d: d["c"].disinfect_text(),
             attrs_fn=lambda d: d["c"].disinfect or {}),
    DWSensor(key="week_report", name="Weekrapport", icon="mdi:calendar-week",
             value_fn=lambda d: _txt(d["c"].last_week.get("titel") or "nog geen weekrapport"),
             attrs_fn=lambda d: d["c"].last_week),
    DWSensor(key="starts_hour", name="Compressorstarts laatste uur", icon="mdi:counter", state_class=M,
             value_fn=lambda d: d["c"].cycles.last_hour(time.time())),
    DWSensor(key="starts_today", name="Compressorstarts vandaag", icon="mdi:counter",
             value_fn=lambda d: d["c"].cycles.today(time.time()),
             attrs_fn=lambda d: {"looptijden_min": list(d["c"].cycles.runs)[-10:]}),
    DWSensor(key="dhw_plan", name="Tapwaterplanning", icon="mdi:shower-head",
             value_fn=lambda d: _txt(d["c"].dhw.reason), attrs_fn=lambda d: d["c"].dhw_info()),
    DWSensor(key="alerts", name="Actieve meldingen", icon="mdi:alert-circle-outline",
             value_fn=lambda d: len(d["c"].alerts.active),
             attrs_fn=lambda d: {"actief": list(d["c"].alerts.active.values()), "laatste": d["c"].last_alerts[::-1]}),
    DWSensor(key="writes_24h", name="Schrijfacties laatste 24 uur", state_class=M, entity_category=EntityCategory.DIAGNOSTIC,
             value_fn=lambda d: d["c"].guard.counts(time.monotonic())["laatste_24u"]),
)


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list = [DWSensorEntity(coordinator, entry, desc) for desc in SENSORS + EXTRA]
    entities += [DWRawSensor(coordinator, entry, r) for r in RAW if r.bit is None or isinstance(r.bit, str)]
    async_add_entities(entities)


class DWSensorEntity(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    entity_description: DWSensor

    def __init__(self, coordinator, entry, description: DWSensor) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = main_device(entry)

    def _data(self):
        return {**self.coordinator.data, "c": self.coordinator}

    @property
    def native_value(self):
        return self.entity_description.value_fn(self._data())

    @property
    def extra_state_attributes(self):
        fn = self.entity_description.attrs_fn
        return fn(self._data()) if fn else None


class DWRawSensor(CoordinatorEntity, SensorEntity):
    """Eén register uit de fabrikanttabel (alleen lezen)."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry, reg) -> None:
        super().__init__(coordinator)
        self._key = reg.key
        self._attr_unique_id = f"{entry.entry_id}_{reg.key}"
        self._attr_name = f"{reg.name} ({reg.addr}{'' if reg.bit is None else '.' + str(reg.bit)})"[:120]
        self._attr_device_info = slave_device(entry, reg.slave)
        dc, unit = UNITS.get(reg.unit, (None, None))
        self._attr_device_class, self._attr_native_unit_of_measurement = dc, unit
        if reg.unit == "kWh":
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        elif unit is not None:
            self._attr_state_class = M
        self._attr_extra_state_attributes = {"slave": reg.slave, "register": reg.addr,
                                             "schrijfbaar_volgens_handleiding": reg.writable}

    @property
    def native_value(self):
        return self.coordinator.data["v"].get("raw", {}).get(self._key)
