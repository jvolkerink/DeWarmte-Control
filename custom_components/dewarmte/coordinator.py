"""Coördinator: pollt Modbus, draait de engine, voert toegestane writes uit en maakt het uurrapport."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.components import persistent_notification
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import registers as R
from .bus import ModbusBus, ModbusError, ModbusTimeout, read_registers
from .const import (CONF_COLD_EXCLUDE, CONF_DOWN_TOPIC, CONF_NOTIFY, CONF_OUTDOOR, CONF_PRESENCE, CONF_PRICE_SERVICE, CONF_SCHEDULES, CONF_PRICE, CONF_PRICE_LEVEL, CONF_ROOMS, CONF_TRV_ROOMS,
                    CONF_UP_TOPIC, DOMAIN)
from .engine import Engine, Inputs, Room, Settings
from .insights import Alerts, CycleCounter, DhwLearner, DhwSettings, WeekStats
from .obslog import ObsLog
from .planner import PlanSettings, PricePlanner
from .report import HourReport
from .writes import WriteGuard

HARD_BLOCKS = ("kritieke Modbus-data niet vers", "storing actief", "ontdooien actief")

_LOGGER = logging.getLogger(__name__)
SLAVE_NAMES = {1: "SunRain (pomp AO)", 2: "Pomp T", 5: "Meter pomp AO", 7: "Meter pomp T",
               14: "Warmtemeter AO", 16: "Warmtemeter T"}


def _f(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class DeWarmteCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry,
                         update_interval=timedelta(seconds=R.FAST))
        self.entry = entry
        self.cfg = {**entry.data, **entry.options}
        self.settings = Settings.from_dict(self.cfg)
        self.engine = Engine(self.settings)
        self.bus = ModbusBus(self._send)
        self._raw: dict[tuple[int, int], tuple[int, float]] = {}
        self._sched = {(b.slave, b.start, b.count): {"next": 0.0, "fails": 0, "err": None, "ok": None}
                       for b in R.BLOCKS}
        self._store = Store(hass, 1, f"{DOMAIN}.state")
        self._unsub = None
        self._saved: dict | None = None
        self._last_summary: str | None = None
        self._last_obs = 0.0
        self._obs = ObsLog(hass.config.path("dewarmte"))
        self.guard = WriteGuard()
        self.control_enabled = True          # standaard aan; keuze wordt bewaard
        self.trip_reason: str | None = None
        self.report = HourReport()
        self.last_report: dict = {"titel": None, "tekst": None}
        self.actions: list[dict] = []        # laatste 30 acties
        self._unsub_time = None
        self._last_values: dict = {}
        self._last_decision: dict = {}
        self.service_until = 0.0             # monotonic; servicemodus aan zolang now < service_until
        self.disinfect: dict | None = None   # actieve handmatige desinfectie
        self.vent: dict | None = None        # actief ontluchtprogramma (816 bit 4)
        self.planner = PricePlanner(PlanSettings.from_dict(self.cfg), None, self.cfg.get(CONF_SCHEDULES))
        self.plan_enabled = True
        self._last_plan = 0.0
        self._last_price_fetch = 0.0
        self._price_error: str | None = None
        self.plan_rooms: dict[str, dict] = {}
        self._auto_dis_t = 0.0
        self.alerts = Alerts()
        self.cycles = CycleCounter()
        self.dhw = DhwLearner(DhwSettings(usage_temp=self.settings.dhw_usage_temp or 50.0))
        self.week = WeekStats()
        self.last_week: dict = {"titel": None, "tekst": None}
        self.last_alerts: list[dict] = []
        self._unsub_week = None
        self._saved_t = 0.0

    # ------------------------------------------------------------ levenscyclus
    async def async_setup(self) -> None:
        stored = await self._store.async_load() or {}
        self.engine = Engine(self.settings, stored)
        self.control_enabled = bool(stored.get("control_enabled", True))
        self.plan_enabled = bool(stored.get("plan_enabled", True))
        self.planner = PricePlanner(PlanSettings.from_dict(self.cfg), stored.get("planner"), self.cfg.get(CONF_SCHEDULES))
        self.cycles = CycleCounter(stored.get("cycles"))
        self.dhw = DhwLearner(DhwSettings(usage_temp=self.settings.dhw_usage_temp or 50.0), stored.get("dhw"))
        self.week = WeekStats(stored.get("week"))
        self.last_week = stored.get("last_week") or self.last_week
        self._unsub = await mqtt.async_subscribe(
            self.hass, self.cfg[CONF_UP_TOPIC], self._on_message, 0, encoding=None)
        self._unsub_time = async_track_time_change(self.hass, self._on_hour, minute=0, second=15)
        self._unsub_week = async_track_time_change(self.hass, self._on_week, hour=19, minute=0, second=30)
        start_bg = getattr(self.hass, "async_create_background_task", None)
        if start_bg:
            start_bg(self.async_cleanup_old_discovery(), "dewarmte_mqtt_opruimen")

    async def async_cleanup_old_discovery(self, wait: float = 15.0) -> int:
        """Wist achtergebleven (retained) MQTT-discovery van de oude DeWarmte-app.

        Alleen berichten met unique_id 'dewarmte_…' én een availability_topic onder 'dewarmte/' worden
        gewist, plus de bijbehorende state-topics. Andere MQTT-apparaten blijven onaangeroerd.
        """
        found: set[str] = set()

        @callback
        def _msg(msg) -> None:
            if not getattr(msg, "retain", False) or not msg.payload:
                return
            try:
                data = json.loads(msg.payload)
            except (TypeError, ValueError):
                return
            if (isinstance(data, dict) and str(data.get("unique_id", "")).startswith("dewarmte_")
                    and str(data.get("availability_topic", "")).startswith("dewarmte/")):
                found.add(msg.topic)
                for key in ("state_topic", "command_topic", "availability_topic"):
                    if str(data.get(key, "")).startswith("dewarmte/"):
                        found.add(data[key])

        try:
            unsub = await mqtt.async_subscribe(self.hass, "homeassistant/#", _msg, 0)
        except Exception as err:  # noqa: BLE001 - opruimen is optioneel
            _LOGGER.debug("Opruimen oude MQTT-discovery overgeslagen: %s", err)
            return 0
        await asyncio.sleep(wait)
        unsub()
        for topic in sorted(found):
            await mqtt.async_publish(self.hass, topic, "", qos=0, retain=True)
        if found:
            _LOGGER.warning("Oude MQTT-discovery van de DeWarmte-app gewist: %d topics", len(found))
        return len(found)

    async def async_shutdown(self) -> None:
        for unsub in (self._unsub, self._unsub_time, self._unsub_week):
            if unsub:
                unsub()
        self._unsub = self._unsub_time = self._unsub_week = None
        await self._store.async_save(self._state())
        await super().async_shutdown()

    @callback
    def _on_message(self, msg) -> None:
        payload = msg.payload
        self.bus.feed(payload.encode("latin-1") if isinstance(payload, str) else bytes(payload))

    async def _send(self, frame: bytes) -> None:
        await mqtt.async_publish(self.hass, self.cfg[CONF_DOWN_TOPIC], frame, qos=0, retain=False)

    def _state(self) -> dict:
        return {**self.engine.export_state(), "control_enabled": self.control_enabled,
                "plan_enabled": self.plan_enabled, "planner": self.planner.export_state(),
                "cycles": self.cycles.export_state(), "dhw": self.dhw.export_state(),
                "week": self.week.export_state(), "last_week": self.last_week}

    async def async_set_control(self, enabled: bool, reason: str | None = None) -> None:
        self.control_enabled = enabled
        self.trip_reason = None if enabled else reason
        if enabled:
            self.guard.failures = 0
        await self._store.async_save(self._state())
        _LOGGER.warning("Regeling %s%s", "AAN" if enabled else "UIT", f": {reason}" if reason else "")
        self.async_update_listeners()

    # ------------------------------------------------------------ schrijven
    async def _execute(self, decision: dict) -> None:
        hard = [b for b in decision["blocks"] if b in HARD_BLOCKS]
        if not self.control_enabled or hard:
            return
        now = time.monotonic()
        for p in decision["planned_writes"]:
            if self.disinfect and (p["slave"], p["register"]) == (2, 6):
                continue                                                   # desinfectie heeft voorrang
            if self.vent and p["slave"] == 1:
                continue                                                   # ontluchten: pomp AO met rust laten
            slave, reg, value = p["slave"], p["register"], int(p["value"])
            why = self.guard.check(slave, reg, value, now)
            if why:
                self.report.refuse(p["name"], why)
                continue
            item = {"time": time.strftime("%H:%M"), "name": p["name"], "register": f"{slave}/{reg}",
                    "old": self._txt(p, p["current"]), "new": p["value_text"], "why": p.get("note") or self._why(p, decision),
                    "ok": False, "error": None}
            try:
                await self.bus.write_single(slave, reg, value)
                item["ok"] = True
                self._raw[(slave, reg)] = (value, time.monotonic())       # echo bevestigd
                for key, st in self._sched.items():                        # blok direct opnieuw lezen
                    if key[0] == slave and key[1] <= reg < key[1] + key[2]:
                        st["next"] = 0.0
            except ModbusError as err:
                item["error"] = str(err)
            self.guard.record(slave, reg, now, item["ok"])
            self.report.action(item)
            self.actions = (self.actions + [item])[-30:]
            _LOGGER.info("Schrijfactie %s %s → %s: %s", item["name"], item["old"], item["new"],
                         "OK" if item["ok"] else item["error"])
            if self.guard.tripped:
                await self.async_set_control(False, f"{self.guard.failures} schrijfacties achter elkaar mislukt")
                persistent_notification.async_create(
                    self.hass, f"De regeling is uitgeschakeld: schrijven mislukt ({item['error']}). "
                    "Controleer de DR164/RS485 en zet de schakelaar 'Regeling actief' weer aan.",
                    title="DeWarmte: regeling gestopt", notification_id="dewarmte_storing")
                await self._notify("DeWarmte: regeling gestopt", f"Schrijven mislukt: {item['error']}")
                return

    @staticmethod
    def _txt(p: dict, cur) -> str:
        if cur is None:
            return "?"
        if p["register"] == 773:
            return "aan" if cur else "uit"
        if p["register"] == 775:
            return {0: "Normaal", 1: "Boost", 2: "Stil"}.get(int(cur), str(cur))
        return f"{int(cur)} °C"

    @staticmethod
    def _why(p: dict, d: dict) -> str:
        if p["register"] == 775:
            return d.get("power_mode_reason", "")
        if p["register"] == 773:
            if p["value"]:
                return "bronondersteuning Pomp T" if d.get("source_support") else (
                    "warmtevraag" if d.get("heat_demand") else "basislast")
            return "geen warmtevraag"
        if p["register"] == 769:
            src = "warmtevraag" if d.get("heat_demand") else ("basislast" if d.get("base_load") else "bronondersteuning")
            return (f"{src}: stooklijn {d.get('base_curve')} + preset {d.get('feedforward')} + klep {d.get('trim')}"
                    f" + offset {d.get('offset')} ({d.get('trim_reason')})")
        return ""

    # ------------------------------------------------------------ directe writes (service, desinfectie)
    async def _write_now(self, slave: int, reg: int, value: int, name: str, old, new: str, why: str,
                         service: bool = False) -> dict:
        item = {"time": time.strftime("%H:%M"), "name": name, "register": f"{slave}/{reg}", "old": old,
                "new": new, "why": why, "ok": False, "error": None}
        try:
            await self.bus.write_single(slave, reg, value, service=service)
            item["ok"] = True
            self._raw[(slave, reg)] = (value & 0xFFFF, time.monotonic())
            for key, st in self._sched.items():
                if key[0] == slave and key[1] <= reg < key[1] + key[2]:
                    st["next"] = 0.0
        except ModbusError as err:
            item["error"] = str(err)
        if not service:
            self.guard.record(slave, reg, time.monotonic(), item["ok"])
        self.report.action(item)
        self.actions = (self.actions + [item])[-30:]
        _LOGGER.info("%s %s: %s → %s: %s", "Service" if service else "Schrijfactie", name, old, new,
                     "OK" if item["ok"] else item["error"])
        return item

    # ------------------------------------------------------------ servicemodus
    @property
    def service_mode(self) -> bool:
        return time.monotonic() < self.service_until

    def set_service_mode(self, on: bool) -> None:
        self.service_until = time.monotonic() + 1800 if on else 0.0
        _LOGGER.warning("Servicemodus %s", "AAN (30 min)" if on else "UIT")
        self.async_update_listeners()

    async def async_service_write(self, slave: int, reg: int, value: int, name: str) -> None:
        from homeassistant.exceptions import HomeAssistantError  # noqa: PLC0415
        if not self.service_mode:
            raise HomeAssistantError("Zet eerst de schakelaar 'Servicemodus' aan.")
        if not self._status().get(slave, {}).get("ok"):
            raise HomeAssistantError(f"Geen verse Modbus-data van slave {slave}; wijziging niet verstuurd.")
        old = self._raw.get((slave, reg), (None,))[0]
        if old is not None and old >= 0x8000 and value < 0:
            old -= 0x10000
        item = await self._write_now(slave, reg, value, f"[service] {name}", old, str(value), "handmatig", service=True)
        self.service_until = max(self.service_until, time.monotonic() + 600)   # na een wijziging nog ≥ 10 min open
        await self.hass.async_add_executor_job(self._obs.service_log, {**item, "datum": time.strftime("%Y-%m-%d")})
        self.async_update_listeners()
        if not item["ok"]:
            raise HomeAssistantError(f"Schrijven mislukt: {item['error']}")

    # ------------------------------------------------------------ watersysteem ontluchten (816 bit 4)
    VENT_MASK = 1 << 4
    VENT_MAX_MIN = 30

    async def _vent_write(self, on: bool, why: str) -> dict:
        item = {"time": time.strftime("%H:%M"), "name": "watersysteem ontluchten " + ("start" if on else "stop"),
                "register": "1/816 bit 4", "old": "?", "new": "aan" if on else "uit", "why": why,
                "ok": False, "error": None}
        try:
            old, new = await self.bus.write_bits(1, 816, self.VENT_MASK, on)
            item.update(ok=True, old=f"{old:#06x}", new=f"{new:#06x}")
            self._raw[(1, 816)] = (new, time.monotonic())
            for key, st in self._sched.items():
                if key[0] == 1 and key[1] <= 816 < key[1] + key[2]:
                    st["next"] = 0.0
        except ModbusError as err:
            item["error"] = str(err)
        self.report.action(item)
        self.actions = (self.actions + [item])[-30:]
        _LOGGER.warning("Ontluchten %s: %s", "start" if on else "stop", "OK" if item["ok"] else item["error"])
        return item

    async def async_start_vent(self) -> None:
        from homeassistant.exceptions import HomeAssistantError  # noqa: PLC0415
        if self.vent:
            return
        if not self.service_mode:
            raise HomeAssistantError("Zet eerst de schakelaar 'Servicemodus' aan.")
        if not self._status().get(1, {}).get("ok"):
            raise HomeAssistantError("Geen verse Modbus-data van de SunRain; ontluchten niet gestart.")
        item = await self._vent_write(True, f"handmatig (max {self.VENT_MAX_MIN} min)")
        if not item["ok"]:
            raise HomeAssistantError(f"Ontluchten starten mislukt: {item['error']}")
        self.vent = {"start": time.monotonic(), "started": time.strftime("%H:%M"), "seen": False}
        await self._notify_both("DeWarmte: ontluchten gestart",
                                f"Watersysteem ontluchten gestart (816 bit 4, {item['old']} → {item['new']}). "
                                f"De regeling laat pomp AO zolang met rust; uiterlijk na {self.VENT_MAX_MIN} min stopt het.")
        self.async_update_listeners()

    async def async_stop_vent(self, reason: str = "handmatig gestopt") -> None:
        v, self.vent = self.vent, None
        item = await self._vent_write(False, reason)
        if v or not item["ok"]:
            await self._notify_both("DeWarmte: ontluchten " + ("gestopt" if item["ok"] else "stoppen mislukt"),
                                    reason if item["ok"] else f"{reason}; fout: {item['error']}")
        self.async_update_listeners()

    async def _vent_step(self) -> None:
        v = self.vent
        if not v:
            return
        for key, st in self._sched.items():                 # tijdens ontluchten 816 elke cyclus lezen
            if key[0] == 1 and key[1] <= 816 < key[1] + key[2]:
                st["next"] = 0.0
        raw = self._raw.get((1, 816))
        bit = None if raw is None or raw[1] < v["start"] else bool(raw[0] & self.VENT_MASK)
        age = time.monotonic() - v["start"]
        if bit:
            v["seen"] = True
        if v["seen"] and bit is False:
            self.vent = None
            await self._notify_both("DeWarmte: ontluchten klaar",
                                    f"De SunRain heeft het ontluchten na {age / 60:.0f} min zelf beëindigd.")
            self.async_update_listeners()
        elif not v["seen"] and age > 120:
            await self.async_stop_vent("bit 4 niet bevestigd door de SunRain binnen 2 min")
        elif age > self.VENT_MAX_MIN * 60:
            await self.async_stop_vent(f"maximale duur {self.VENT_MAX_MIN} min bereikt")

    def vent_text(self) -> str:
        v = self.vent
        if not v:
            return "uit"
        return f"bezig sinds {v['started']}" + ("" if v["seen"] else " (wacht op bevestiging)")

    # ------------------------------------------------------------ handmatige desinfectie
    async def _auto_disinfection(self, decision: dict) -> None:
        """Na X dagen zonder ≥ 60 °C: desinfectie op het goedkoopste moment (uiterlijk een dag later)."""
        days, limit = decision.get("legionella_days"), self.settings.legionella_auto_days
        if (not limit or days is None or days < limit or self.disinfect or not self.control_enabled
                or time.time() - self._auto_dis_t < 86400):
            return
        rank = self.planner.rank_now(time.time())
        if (rank is not None and rank <= 0.1) or days >= limit + 1 or (rank is None and days >= limit):
            self._auto_dis_t = time.time()
            await self.async_start_disinfection(f"automatisch: {days:.1f} dagen niet ≥ 60 °C"
                                                + ("" if rank is None else f", prijsrang {rank:.0%}"))

    async def async_start_disinfection(self, reason: str = "handmatig") -> None:
        if self.disinfect:
            return
        v = self._last_values.get("raw", {})
        target = int(min(70, max(60, v.get("r2_10") or 60)))
        hold = int(min(90, max(10, v.get("r2_11") or 30)))
        prev = self._last_values.get("pt_set")
        item = await self._write_now(2, 6, target, "desinfectie gestart", "?" if prev is None else f"{int(prev)} °C",
                                     f"{target} °C", f"{reason}; vasthouden {hold} min, alleen warmtepomp")
        if not item["ok"]:
            await self._notify_both("DeWarmte: desinfectie niet gestart", f"Schrijven mislukt: {item['error']}")
            return
        self.week.disinfections += 1
        self.disinfect = {"phase": "opwarmen", "target": target, "hold": hold, "start": time.monotonic(),
                          "reached": None, "max_top": None, "started": time.strftime("%H:%M")}
        grid = v.get("r2_25")
        warn = f" Let op: 'Set temperature when powered by grid' (reg 25) staat op {int(grid)} °C." if grid and grid < target else ""
        await self._notify_both("DeWarmte: desinfectie gestart",
                                f"{reason.capitalize()}. Pomp T warmt op tot {target} °C en houdt dat {hold} min vast (zonder element).{warn}")
        self.async_update_listeners()

    async def async_stop_disinfection(self, reason: str = "handmatig gestopt", ok: bool = False) -> None:
        dis, self.disinfect = self.disinfect, None
        if not dis:
            return
        back = self._last_decision.get("dhw_setpoint_advice") or 45
        await self._write_now(2, 6, int(back), "desinfectie klaar" if ok else "desinfectie gestopt",
                              f"{dis['target']} °C", f"{int(back)} °C", reason)
        top = dis["max_top"]
        await self._notify_both("DeWarmte: desinfectie " + ("geslaagd" if ok else "gestopt"),
                                f"{reason}. Hoogste temperatuur boven in de boiler: "
                                f"{'–' if top is None else f'{top:.0f} °C'}. Setpoint terug naar {int(back)} °C.")
        self.async_update_listeners()

    async def _disinfect_step(self, v: dict) -> None:
        d = self.disinfect
        if not d:
            return
        now, top = time.monotonic(), v.get("dhw_top")
        if top is not None:
            d["max_top"] = top if d["max_top"] is None else max(d["max_top"], top)
        if d["phase"] == "opwarmen" and top is not None and top >= d["target"] - 1:
            d["phase"], d["reached"] = "vasthouden", now
        if d["phase"] == "vasthouden" and now - d["reached"] >= d["hold"] * 60:
            await self.async_stop_disinfection(f"{d['target']} °C {d['hold']} min vastgehouden", ok=True)
        elif d["phase"] == "opwarmen" and now - d["start"] > 4 * 3600:
            await self.async_stop_disinfection(f"{d['target']} °C niet gehaald binnen 4 uur")

    def disinfect_text(self) -> str:
        d = self.disinfect
        if not d:
            return "uit"
        if d["phase"] == "vasthouden":
            left = max(0, d["hold"] - (time.monotonic() - d["reached"]) / 60)
            return f"vasthouden {d['target']} °C, nog {left:.0f} min"
        return f"opwarmen naar {d['target']} °C (gestart {d['started']})"

    async def _notify_both(self, title: str, message: str) -> None:
        persistent_notification.async_create(self.hass, message, title=title, notification_id="dewarmte_desinfectie")
        await self._notify(title, message)

    # ------------------------------------------------------------ uurrapport
    async def _notify(self, title: str, message: str, urgent: bool = False) -> None:
        svc = (self.cfg.get(CONF_NOTIFY) or "").strip().removeprefix("notify.")
        if not svc:
            return
        payload: dict = {"title": title, "message": message}
        if urgent and svc.startswith("mobile_app"):
            payload["data"] = {"push": {"interruption-level": "time-sensitive"}}
        try:
            await self.hass.services.async_call("notify", svc, payload, blocking=False)
        except Exception as err:  # noqa: BLE001 - een melding mag de regeling nooit stoppen
            _LOGGER.warning("Melding via notify.%s mislukt: %s", svc, err)

    async def _on_hour(self, _now=None) -> None:
        await self.async_send_report()

    # ------------------------------------------------------------ weekrapport
    def _energies(self) -> dict:
        v = self._last_values
        return {k: v.get(k) for k in ("ao_pm_energy", "ao_hm_energy", "pt_pm_energy")}

    async def _on_week(self, _now=None) -> None:
        if time.localtime().tm_wday == 6:          # zondag
            await self.async_send_week_report(reset=True)

    async def async_send_week_report(self, reset: bool = False) -> None:
        wall = time.time()
        title, text, data = self.week.build(wall, self._energies(), self.cycles, self.dhw,
                                            self.dhw.s.usage_temp, self._last_decision.get("legionella_days"))
        if not reset:
            title += " (tussenstand)"
        self.last_week = {"titel": title, "tekst": text, **data}
        persistent_notification.async_create(self.hass, text, title=title, notification_id="dewarmte_weekrapport")
        await self._notify(title, text)
        if reset:
            self.week.reset(wall, self._energies())
        await self._store.async_save(self._state())
        self.async_update_listeners()

    # ------------------------------------------------------------ meldingen en leren
    def _room_rows(self) -> list[dict]:
        rows = []
        for ent in self.cfg.get(CONF_ROOMS, []):
            st = self.hass.states.get(ent)
            if st is None or st.state in ("unavailable", "unknown"):
                continue
            a = st.attributes
            window = str(a.get("window_state", "off")).lower() == "on"
            rows.append({"id": ent, "name": a.get("friendly_name", ent), "current": _f(a.get("current_temperature")),
                         "target": _f(a.get("temperature")), "heating": st.state == "heat" and not window})
        return rows

    async def _insights_step(self, v: dict, decision: dict, status: dict) -> None:
        wall = time.time()
        self.cycles.update(v.get("compressor_running"), wall, bool(v.get("defrost")))
        heating_t = v.get("pt_pm_w") is not None and v["pt_pm_w"] >= 100
        if self.dhw.observe(wall, v.get("dhw_top"), v.get("dhw_bottom"), heating_t):
            self.week.draw(wall, self.dhw.events[-1][1])
        rows = self._room_rows()
        excl = self.cfg.get(CONF_COLD_EXCLUDE)
        if excl is None:
            excl = [e for e in self.cfg.get(CONF_ROOMS, []) if "toilet" in e]
        pt_err = {k: v.get(k) for k in ("pt_err1", "pt_err2", "pt_err3")}
        pt_fault = None if all(x is None for x in pt_err.values()) else any(x for x in pt_err.values())
        ao_code = f"code {v.get('fault_code')}/{v.get('subfault_code')}"
        pt_code = ", ".join(f"{k[-1]}: {int(x)}" for k, x in pt_err.items() if x)
        events = self.alerts.update(time.monotonic(), bool(status.get(1, {}).get("ok")), v.get("fault_active"),
                                    ao_code, pt_fault, pt_code or "–", [r for r in rows if r["id"] not in excl])
        for kind, text in events:
            title = "DeWarmte: hersteld" if kind == "hersteld" else "DeWarmte: let op"
            self.last_alerts = (self.last_alerts + [{"tijd": time.strftime("%d-%m %H:%M"), "soort": kind,
                                                     "tekst": text}])[-20:]
            if kind == "melding":
                self.week.alerts += 1
            _LOGGER.warning("%s: %s", title, text)
            persistent_notification.async_create(self.hass, text, title=title, notification_id="dewarmte_melding")
            await self._notify(title, text, urgent=kind == "melding")
        el = None
        if v.get("ao_pm_w") is not None or v.get("pt_pm_w") is not None:
            el = (v.get("ao_pm_w") or 0) + (v.get("pt_pm_w") or 0)
        price = self.planner.price_now(wall)
        if price is None and self.cfg.get(CONF_PRICE):
            st = self.hass.states.get(self.cfg[CONF_PRICE])
            price = _f(st.state) if st is not None else None
        self.week.sample(wall, rows, el, price, self._energies_from(v))

    @staticmethod
    def _energies_from(v: dict) -> dict:
        return {k: v.get(k) for k in ("ao_pm_energy", "ao_hm_energy", "pt_pm_energy")}

    def _dhw_need(self, v: dict) -> tuple[float | None, str]:
        if not self.settings.dhw_usage_temp or self.disinfect or not self.control_enabled:
            return None, ""
        mean = self.planner._mean if self.planner.prices else None   # noqa: SLF001
        return self.dhw.need(time.time(), v.get("dhw_top"), v.get("pt_restart_diff"), self.settings.dhw_max, mean)

    def dhw_info(self) -> dict:
        wall = time.time()
        nxt = self.dhw.next_usage(wall)
        return {"volgend_tapmoment": None if nxt is None else time.strftime("%a %H:%M", time.localtime(nxt)),
                "geleerd": self.dhw.windows(wall), "tapmomenten_28d": len(self.dhw.events),
                "opwarmsnelheid_k_per_uur": round(self.dhw.rate, 1), "minimaal": self.dhw.s.usage_temp,
                "voorbereiding": self.dhw.prep,
                "laatste": [time.strftime("%a %d-%m %H:%M", time.localtime(t)) + f" ({top:.0f} °C)"
                            for t, top in self.dhw.events[-8:]][::-1]}

    async def async_send_report(self) -> None:
        title, text, data = self.report.build(self._last_values, self._last_decision, self.control_enabled,
                                              self.guard.counts(time.monotonic()))
        text += f"\nCompressorstarts laatste uur: {self.cycles.last_hour(time.time())} | tapwater: {self.dhw.reason}"
        self.last_report = {"titel": title, "tekst": text, **data}
        persistent_notification.async_create(self.hass, text, title=title, notification_id="dewarmte_uurrapport")
        await self._notify(title, text)
        self.report.reset(self._last_values)
        self.async_update_listeners()

    # ------------------------------------------------------------ polling
    async def _poll(self) -> None:
        start = time.monotonic()
        dead: set[int] = set()
        for block in sorted(R.BLOCKS, key=lambda b: b.tier):
            st = self._sched[(block.slave, block.start, block.count)]
            now = time.monotonic()
            if block.slave in dead:
                continue
            if block.tier != R.FAST and (now < st["next"] or now - start > 8.0):
                continue
            try:
                out, failed = await read_registers(self.bus, block.slave, block.start, block.count)
            except ModbusError as err:
                st["fails"] += 1
                st["err"] = str(err)
                st["next"] = now + min(1800.0, block.tier * 2 ** min(st["fails"], 8))
                if isinstance(err, ModbusTimeout):
                    dead.add(block.slave)
                if st["fails"] in (1, 10, 100):
                    _LOGGER.warning("Modbus-fout %s (%s): %s", block.label, block.slave, err)
                continue
            ts = time.monotonic()
            for addr, value in out.items():
                self._raw[(block.slave, addr)] = (value, ts)
            st.update(fails=0, err=None, ok=ts if out else st["ok"], next=ts + block.tier)
            if failed and st.get("failed") != failed:
                _LOGGER.debug("%s: registers zonder antwoord %s", block.label, failed)
            st["failed"] = failed

    def _status(self) -> dict[int, dict]:
        now = time.monotonic()
        out: dict[int, dict] = {}
        for slave in sorted({b.slave for b in R.BLOCKS}):
            blocks = [b for b in R.BLOCKS if b.slave == slave]
            tier = min(b.tier for b in blocks)
            fast = [b for b in blocks if b.tier == tier]
            scheds = [self._sched[(b.slave, b.start, b.count)] for b in fast]
            ages = [None if s["ok"] is None else now - s["ok"] for s in scheds]
            ok = all(a is not None and a <= R.max_age(tier) for a in ages)
            out[slave] = {"name": SLAVE_NAMES.get(slave, str(slave)), "ok": ok,
                          "age_s": None if any(a is None for a in ages) else round(max(ages)),
                          "error": next((s["err"] for s in scheds if s["err"]), None)}
        return out

    # ------------------------------------------------------------ invoer voor de engine
    def _room(self, entity_id: str) -> Room:
        st = self.hass.states.get(entity_id)
        if st is None or st.state in ("unavailable", "unknown"):
            return Room(entity_id, available=False)
        a = st.attributes
        valve = _f(a.get("valve_open_percent"))
        if valve is None and isinstance(a.get("vtherm_over_climate_valve"), dict):
            valve = _f(a["vtherm_over_climate_valve"].get("valve_open_percent"))
        return Room(entity_id, True, st.state, _f(a.get("current_temperature")), _f(a.get("temperature")), valve,
                    trv_sensor=entity_id in (self.cfg.get(CONF_TRV_ROOMS) or []))

    def _inputs(self, v: dict, status: dict) -> Inputs:
        outdoor = None
        ent = self.cfg.get(CONF_OUTDOOR)
        if ent:
            st = self.hass.states.get(ent)
            if st is not None and (time.time() - st.last_updated.timestamp()) < 1800:
                outdoor = _f(st.state)
        if outdoor is None:
            outdoor = v.get("t_ambient")
        price = level = None
        if self.cfg.get(CONF_PRICE):
            st = self.hass.states.get(self.cfg[CONF_PRICE])
            price = _f(st.state) if st is not None else None
        if self.cfg.get(CONF_PRICE_LEVEL):
            st = self.hass.states.get(self.cfg[CONF_PRICE_LEVEL])
            level = st.state if st is not None else None
        need, need_reason = self._dhw_need(v)
        return Inputs(
            now=time.monotonic(), wall=time.time(), outdoor=outdoor,
            rooms=[self._room(e) for e in self.cfg.get(CONF_ROOMS, [])],
            f_comp=v.get("f_comp"), t_supply=v.get("t_supply"), t_return=v.get("t_return"),
            fault=v.get("fault_active"), defrost=v.get("defrost"),
            set_769=v.get("set_heating"), on_773=v.get("on_off"), power_775=v.get("power_mode"),
            dhw_top=v.get("dhw_top"), dhw_set=v.get("pt_set"), t_source_in=v.get("t_source_in"),
            pomp_t_power_w=v.get("pt_pm_w"), disinfection=v.get("disinfection"),
            price=price, price_level=level, critical_ok=bool(status.get(1, {}).get("ok")),
            price_rank=self.planner.rank_now(time.time()) if self.plan_enabled else None,
            plan_offset=self.planner.offset if self._plan_active() else None,
            dhw_need=need, dhw_need_reason=need_reason)

    # ------------------------------------------------------------ prijsplanner
    def _plan_active(self) -> bool:
        return self.plan_enabled and self.control_enabled and bool(self.planner.prices)

    async def async_set_plan(self, enabled: bool) -> None:
        self.plan_enabled = enabled
        await self._store.async_save(self._state())
        self.async_update_listeners()

    def _home(self) -> bool:
        ent = self.cfg.get(CONF_PRESENCE)
        if not ent:
            return True
        st = self.hass.states.get(ent)
        if st is None or st.state in ("unavailable", "unknown"):
            return True
        return st.state in ("on", "home") or (st.state.isdigit() and int(st.state) > 0)

    async def _fetch_prices(self) -> None:
        svc = (self.cfg.get(CONF_PRICE_SERVICE) or "tibber.get_prices").strip()
        if "." not in svc:
            return
        domain, name = svc.split(".", 1)
        if not self.hass.services.has_service(domain, name):
            self._price_error = f"dienst {svc} niet gevonden"
            return
        today = time.strftime("%Y-%m-%d")
        end = time.strftime("%Y-%m-%d", time.localtime(time.time() + 86400))
        try:
            resp = await self.hass.services.async_call(
                domain, name, {"start": f"{today} 00:00:00", "end": f"{end} 23:59:59"},
                blocking=True, return_response=True)
        except Exception as err:  # noqa: BLE001 - prijzen zijn optioneel
            self._price_error = str(err)
            _LOGGER.warning("Prijzen ophalen via %s mislukt: %s", svc, err)
            return
        rows = []
        prices = (resp or {}).get("prices", resp)
        if isinstance(prices, dict):
            for val in prices.values():
                if isinstance(val, list):
                    rows = val
                    break
        elif isinstance(prices, list):
            rows = prices
        n = self.planner.set_prices(rows, time.time())
        self._price_error = None if n else "geen prijzen in antwoord"

    async def _plan_step(self) -> None:
        now = time.time()
        if now - self._last_price_fetch >= 1800 or (not self.planner.prices and now - self._last_price_fetch >= 300):
            self._last_price_fetch = now
            await self._fetch_prices()
        if now - self._last_plan < 60:
            return
        self._last_plan = now
        p = self.planner
        p.update_offset(now)
        home = self._home()
        rooms: dict[str, dict] = {}
        for ent in self.cfg.get(CONF_ROOMS, []):
            st = self.hass.states.get(ent)
            if st is None or st.state in ("unavailable", "unknown", "off"):
                rooms[ent] = {"status": "uit" if st is not None and st.state == "off" else "niet beschikbaar"}
                # Kamer uit: vergeet ons laatste doel, anders lijkt het doel na weer aanzetten (vaak 7 °C)
                # een handmatige wijziging en blijft de kamer tot 3 uur ongestuurd.
                p.last_written.pop(ent, None)
                p.override_until.pop(ent, None)
                continue
            a = st.attributes
            cur_t, valve = _f(a.get("current_temperature")), _f(a.get("valve_open_percent"))
            p.learn(ent, cur_t, valve, now)
            current = _f(a.get("temperature"))
            last = p.last_written.get(ent)
            if last and current is not None and abs(current - last[0]) <= 0.05:
                p.override_until.pop(ent, None)          # weer terug op ons doel (bijv. na raam dicht)
            target, why = p.room_target(ent, a.get("preset_temperatures") or {}, home, now, current)
            rooms[ent] = {"doel": target, "nu": current, "reden": why,
                          "afkoeling_k_per_uur": p.cool_rate.get(ent)}
            if target is None or not self._plan_active() or current is None or abs(target - current) < 0.25:
                if target is not None and current is not None and abs(target - current) < 0.25 and not last:
                    p.wrote(ent, current, now)
                continue
            try:
                await self.hass.services.async_call("climate", "set_temperature",
                                                    {"entity_id": ent, "temperature": target}, blocking=True)
                ok, err = True, None
                p.wrote(ent, target, now)
            except Exception as exc:  # noqa: BLE001
                ok, err = False, str(exc)
            item = {"time": time.strftime("%H:%M"), "name": f"kamerdoel {a.get('friendly_name', ent)}",
                    "register": ent, "old": f"{current:g} °C", "new": f"{target:g} °C", "why": why,
                    "ok": ok, "error": err}
            self.report.action(item)
            self.actions = (self.actions + [item])[-30:]
            _LOGGER.info("Kamerdoel %s: %s → %s (%s)%s", ent, current, target, why, "" if ok else f" FOUT {err}")
        self.plan_rooms = rooms

    def plan_info(self) -> dict:
        p = self.planner
        now = time.time()
        return {"actief": self._plan_active(), "reden": p.reason, "blok": p.block(now), "thuis": self._home(),
                "venster_uur": round(p.window_h, 1), "prijsrang_nu": p.rank_now(now),
                "prijs_nu": p.price_now(now), "fout": self._price_error, "kamers": self.plan_rooms,
                "afkoelsnelheid": p.cool_rate, "planning": p.plan(now)}

    # ------------------------------------------------------------ hoofdcyclus
    async def _async_update_data(self) -> dict:
        await self._poll()
        values = R.decode_all(self._raw, time.monotonic())
        status = self._status()
        await self._plan_step()
        decision = self.engine.update(self._inputs(values, status))
        await self._execute(decision)
        await self._disinfect_step(values)
        await self._vent_step()
        await self._auto_disinfection(decision)
        await self._insights_step(values, decision, status)
        if self.service_until and not self.service_mode:
            self.service_until = 0.0
            _LOGGER.warning("Servicemodus automatisch UIT")
        self.report.sample(values, decision)
        self._last_values, self._last_decision = values, decision

        if decision["summary"] != self._last_summary:
            self._last_summary = decision["summary"]
            _LOGGER.debug("Gepland: %s | blokkades: %s", decision["summary"], ", ".join(decision["blocks"]) or "geen")
        state = self._state()
        quiet = {k: x for k, x in state.items() if k != "week"}        # weekcijfers veranderen elke cyclus
        if quiet != self._saved or time.monotonic() - self._saved_t >= 600:
            self._saved, self._saved_t = quiet, time.monotonic()
            self._store.async_delay_save(self._state, 5)
        if time.monotonic() - self._last_obs >= 60:
            self._last_obs = time.monotonic()
            self.hass.async_add_executor_job(self._obs.append, self._obs_row(values, decision, status))
        return {"v": values, "d": decision, "s": status}

    @staticmethod
    def _obs_row(v: dict, d: dict, s: dict) -> dict:
        row = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")}
        for key in ("t_ambient", "t_supply", "t_return", "f_comp", "run_state_1", "relay_2", "fault_code",
                    "set_heating", "mode", "on_off", "power_mode", "control_method", "ao_hm_flow", "ao_hm_kw",
                    "ao_pm_w", "pt_pm_w", "dhw_top", "dhw_bottom", "t_source_in", "pt_set", "cop_ao"):
            row[key] = v.get(key)
        for key in ("outdoor_used", "heat_demand", "demand_level", "worst_deficit", "base_curve", "room_boost",
                    "feedforward", "trim", "trim_reason",
                    "offset", "supply_target", "shortfall", "unit_on_desired", "source_support",
                    "power_mode_desired", "dhw_setpoint_advice", "legionella_days", "summary", "blocks"):
            row[key] = d.get(key)
        row["bus_ok"] = all(x["ok"] for x in s.values())
        row["base_load"] = d.get("base_load")
        return row
