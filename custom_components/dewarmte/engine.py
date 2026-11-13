"""Regel-engine (puur Python, geen Home Assistant-afhankelijkheden).

De engine besluit alleen; de coördinator voert 'planned_writes' uit via
writes.WriteGuard (allowlist, budget) als de regeling actief is.
"""
from __future__ import annotations

import math
from collections import Counter, deque
from dataclasses import dataclass, field, fields

SILENT, NORMAL, BOOST = 2, 0, 1            # waarden van register 775
_VAL = [SILENT, NORMAL, BOOST]             # oplopend vermogen
_IDX = {v: i for i, v in enumerate(_VAL)}
MODE_NAMES = {SILENT: "Stil", NORMAL: "Normaal", BOOST: "Boost"}


@dataclass
class Settings:
    setpoint_basis: str = "supply"         # "supply" of "return"; zie README
    # stooklijn (31 - 0,5 x buiten) + feedforward bij preset-stappen + trage klepbijsturing + geleerde offset
    curve_start: float = 31.0
    curve_slope: float = 0.5
    min_supply: float = 25.0
    max_supply: float = 45.0
    heating_stop_outdoor: float = 18.0
    heating_start_outdoor: float = 16.0
    keep_on_below: float = 10.0            # basislast: hal/toilet/bijkeuken hebben geen TRV
    # trage bijsturing op de klepstand (vervangt de directe kamerboost)
    trim_interval_min: float = 30.0
    trim_step: float = 0.5
    trim_min: float = -3.0
    trim_max: float = 4.0
    valve_high: float = 90.0               # meest open klep gemiddeld ≥ dit en echt tekort: aanvoer omhoog
    valve_low: float = 60.0                # meest open klep gemiddeld ≤ dit en geen tekort: aanvoer omlaag
    trim_deficit_up: float = 0.3
    trim_freeze_min: float = 60.0          # na een doelwijziging (preset) eerst niet bijsturen
    # preset-stappen (prijs/nachtverlaging): aanvoer meteen een stukje mee
    ff_k: float = 0.5                      # K aanvoer per K kamerdoel boven/onder het gebruikelijke doel
    ff_max: float = 2.0
    # kamers die meten met de sensor in de TRV (op de convector): trager en minder zwaar meetellen
    trv_filter_min: float = 30.0
    good_filter_min: float = 5.0
    trv_weight: float = 0.5
    # vraagdetectie
    room_valve_on: float = 10.0
    room_valve_off: float = 3.0
    room_deficit_on: float = 0.4
    room_deficit_off: float = 0.1
    demand_on_delay_s: float = 120.0
    demand_off_delay_s: float = 300.0
    # aan/uit
    min_on_min: float = 30.0
    min_off_min: float = 10.0
    unit_off_delay_min: float = 45.0
    # vermogensmodus: boven silent_above altijd Stil; eronder zelf opschalen
    silent_above: float = 3.0
    silent_release: float = 2.0
    normal_floor_below: float = -9.0
    shortfall_normal_k: float = 2.0
    shortfall_normal_min: float = 10.0
    shortfall_boost_k: float = 3.0
    shortfall_boost_min: float = 15.0
    deescalate_k: float = 0.5
    deescalate_min: float = 20.0
    mode_hold_min: float = 20.0
    # leren: de offset neemt langdurige bijsturing over (symmetrisch, traag, begrensd)
    offset_step: float = 0.5
    offset_limit: float = 2.0
    offset_interval_h: float = 6.0
    offset_trigger: float = 1.0
    # tapwater
    dhw_floor: float = 45.0
    dhw_max: float = 60.0
    dhw_max_negative: float = 70.0
    price_cheap: float = 0.20
    price_normal: float = 0.26
    price_expensive: float = 0.30
    dhw_temp_cheap: float = 60.0
    dhw_temp_normal: float = 55.0
    dhw_temp_mid: float = 50.0
    dhw_hold_min: float = 30.0
    dhw_rank_max: float = 0.10
    dhw_rank_mid: float = 0.25
    dhw_source_min: float = 18.0
    dhw_source_supply: float = 30.0
    dhw_power_on_w: float = 100.0
    legionella_temp: float = 60.0
    legionella_warn_days: float = 14.0
    dhw_usage_temp: float = 50.0          # boiler boven minimaal op een geleerd tapmoment (0 = uit)
    legionella_auto_days: float = 7.0      # automatische desinfectie na zoveel dagen zonder ≥ 60 °C (0 = uit)

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        known = {f.name: f.type for f in fields(cls)}
        kwargs = {}
        for key, value in (data or {}).items():
            if key in known and value is not None:
                kwargs[key] = str(value) if key == "setpoint_basis" else float(value)
        return cls(**kwargs)


@dataclass
class Room:
    name: str
    available: bool = True
    hvac_mode: str | None = None
    current: float | None = None
    target: float | None = None
    valve: float | None = None             # 0..100 %
    trv_sensor: bool = False               # meet met de sensor in de TRV (op de convector)

    @property
    def valid(self) -> bool:
        return bool(self.available and self.hvac_mode not in (None, "off")
                    and self.current is not None and self.target is not None and self.target >= 5)

    @property
    def deficit(self) -> float:
        return (self.target - self.current) if self.valid else 0.0


@dataclass
class Inputs:
    now: float                              # monotonic seconden
    wall: float                             # epoch seconden
    outdoor: float | None = None
    rooms: list[Room] = field(default_factory=list)
    f_comp: float | None = None
    t_supply: float | None = None
    t_return: float | None = None
    fault: bool | None = None
    defrost: bool | None = None
    set_769: float | None = None
    on_773: float | None = None
    power_775: float | None = None
    dhw_top: float | None = None
    dhw_set: float | None = None
    t_source_in: float | None = None
    pomp_t_power_w: float | None = None
    disinfection: float | None = None
    price: float | None = None
    price_level: str | None = None
    price_rank: float | None = None         # 0 = goedkoopste moment komend etmaal, 1 = duurste (planner)
    plan_offset: float | None = None        # prijs-offset van de kamerdoelen (planner)
    dhw_need: float | None = None          # minimaal tapwatersetpoint voor een geleerd tapmoment
    dhw_need_reason: str = ""
    critical_ok: bool = False


class Engine:
    def __init__(self, settings: Settings, state: dict | None = None) -> None:
        self.s = settings
        st = state or {}
        fresh = st.get("v") != 3               # 3.3: oude (vastgelopen) offset niet overnemen
        self.offset = 0.0 if fresh else float(st.get("offset", 0.0))
        self.trim = 0.0 if fresh else float(st.get("trim", 0.0))
        self._refs: dict[str, deque] = {k: deque(v, maxlen=144) for k, v in (st.get("refs") or {}).items()}
        self._ref_t: float | None = None
        self._ema: dict[str, float] = {}
        self._prev_target: dict[str, float] = {}
        self._target_changed: float | None = None
        self._last_now: float | None = None
        self._acc_t0: float | None = None
        self._acc_valve: list[float] = []
        self._off_t0: float | None = None
        self._off_sum = 0.0
        self._off_n = 0
        self.trim_reason = "start"
        self.last_hot: float | None = st.get("last_hot")
        self.demand = False
        self.season = True
        self.cold = False
        self._idx = 0
        self._t: dict[str, float] = {}
        self._call_since: float | None = None
        self._idle_since: float | None = None
        self._no_want_since: float | None = None
        self._offset_changed: float | None = None
        self._mode_changed: float | None = None
        self._unit_edge: float | None = None
        self._last_on: float | None = None
        self._dhw_target: float | None = None
        self._dhw_changed: float | None = None

    def export_state(self) -> dict:
        return {"v": 3, "offset": self.offset, "trim": self.trim, "last_hot": self.last_hot,
                "refs": {k: list(v) for k, v in self._refs.items()}}

    def _held(self, key: str, cond: bool, now: float, minutes: float) -> bool:
        if not cond:
            self._t.pop(key, None)
            return False
        t0 = self._t.setdefault(key, now)
        return now - t0 >= minutes * 60

    # ------------------------------------------------------------------ hoofdfunctie
    def update(self, inp: Inputs) -> dict:
        s, now = self.s, inp.now
        notes: list[str] = []
        ta = inp.outdoor
        if ta is None:
            notes.append("buitentemperatuur onbekend: curve rekent met 5 °C")
        if ta is not None:
            if ta >= s.heating_stop_outdoor:
                self.season = False
            elif ta <= s.heating_start_outdoor:
                self.season = True

        valid = [r for r in inp.rooms if r.valid]
        if not valid:
            notes.append("geen kamer met geldige temperatuur/doel")
        dt = 0.0 if self._last_now is None else max(0.0, now - self._last_now)
        self._last_now = now
        eff = self._filter(valid, dt, now)
        valves = [r.valve for r in valid if r.valve is not None]
        deficits = list(eff.values())
        calling = any(v >= s.room_valve_on for v in valves) or any(d >= s.room_deficit_on for d in deficits)
        holding = any(v > s.room_valve_off for v in valves) or any(d > s.room_deficit_off for d in deficits)
        self._update_demand(calling, holding, now)
        worst = max(deficits, default=0.0)
        level = (sum(valves) / len(valves)) if valves else None
        ff = self._feedforward(valid, now)
        if inp.plan_offset is not None:       # planner stuurt de kamers: aanvoer gaat direct mee
            ff = max(-s.ff_max, min(s.ff_max, s.ff_k * inp.plan_offset))

        # tapwater / bronondersteuning Pomp T
        source_support = bool(
            inp.pomp_t_power_w is not None and inp.pomp_t_power_w >= s.dhw_power_on_w
            and inp.t_source_in is not None and inp.t_source_in < s.dhw_source_min)
        dhw = self._dhw(inp, now)

        # aanvoersetpoint
        ta_eff = ta if ta is not None else 5.0
        base = s.curve_start - s.curve_slope * ta_eff
        base_load = self.season and ta_eff < s.keep_on_below
        want = (self.season and (self.demand or base_load)) or source_support
        self._trim_update(valves, worst, now, want and self.season)
        self._learn(now, want and self.season)
        boost = ff + self.trim
        target: float | None = None
        if want:
            target = base + boost + self.offset
            if source_support:
                target = max(target, s.dhw_source_supply)
            target = round(min(max(target, s.min_supply), s.max_supply), 1)

        # aan/uit met vertragingen
        if want:
            self._no_want_since = None
            desired_on: bool | None = True
        else:
            if self._no_want_since is None:
                self._no_want_since = now
            desired_on = False if now - self._no_want_since >= s.unit_off_delay_min * 60 else None
        if inp.on_773 is not None:
            cur_on = bool(inp.on_773)
            if self._last_on is None or cur_on != self._last_on:
                self._unit_edge = None if self._last_on is None else now
                self._last_on = cur_on

        # vermogensmodus
        ref = inp.t_supply if s.setpoint_basis == "supply" else inp.t_return
        running = inp.f_comp is not None and inp.f_comp > 0
        shortfall = (target - ref) if (target is not None and ref is not None and running) else None
        mode, mode_reason = self._power_mode(ta, shortfall, now)

        # voorgenomen writes (nooit uitgevoerd in fase 1)
        blocks: list[str] = []
        if not inp.critical_ok:
            blocks.append("kritieke Modbus-data niet vers")
        if inp.fault:
            blocks.append("storing actief")
        if inp.defrost:
            blocks.append("ontdooien actief")
        planned = self._plan(inp, desired_on, target, mode, dhw, now, blocks)

        return {
            "season_heating": self.season, "heat_demand": self.demand,
            "demand_level": None if level is None else round(level, 1),
            "worst_deficit": round(worst, 2), "outdoor_used": ta,
            "base_curve": round(base, 1), "room_boost": round(boost, 1), "offset": self.offset,
            "feedforward": round(ff, 2), "trim": self.trim, "trim_reason": self.trim_reason,
            "room_deficits": {k: round(v, 2) for k, v in eff.items()},
            "supply_target": target, "shortfall": None if shortfall is None else round(shortfall, 1),
            "unit_on_desired": desired_on, "source_support": source_support, "base_load": base_load,
            "power_mode_desired": mode, "power_mode_name": MODE_NAMES[mode], "power_mode_reason": mode_reason,
            "dhw_setpoint_advice": dhw["target"], "dhw_reason": dhw["reason"], "dhw_recovery": dhw["recovery"],
            "legionella_days": dhw["legionella_days"], "legionella_warning": dhw["legionella_warning"],
            "planned_writes": planned, "blocks": blocks, "notes": notes,
            "summary": "; ".join(f"{p['name']}→{p['value_text']}" for p in planned) or "geen wijzigingen",
        }

    # ------------------------------------------------------------------ onderdelen
    def _update_demand(self, calling: bool, holding: bool, now: float) -> None:
        s = self.s
        if not self.demand:
            if calling:
                if self._call_since is None:
                    self._call_since = now
                if now - self._call_since >= s.demand_on_delay_s:
                    self.demand, self._idle_since = True, None
            else:
                self._call_since = None
        else:
            if holding:
                self._idle_since = None
            else:
                if self._idle_since is None:
                    self._idle_since = now
                if now - self._idle_since >= s.demand_off_delay_s:
                    self.demand, self._call_since = False, None

    def _filter(self, valid: list[Room], dt: float, now: float) -> dict[str, float]:
        """Gefilterd, gewogen kamertekort. Kamers met de TRV-sensor: 30 min middelen en half gewicht."""
        s, out = self.s, {}
        for r in valid:
            tau = (s.trv_filter_min if r.trv_sensor else s.good_filter_min) * 60
            prev = self._ema.get(r.name)
            a = 1.0 if prev is None or dt <= 0 else 1 - math.exp(-dt / tau)
            val = r.deficit if prev is None else prev + a * (r.deficit - prev)
            old_t = self._prev_target.get(r.name)
            if old_t is not None and abs(r.target - old_t) >= 0.3:
                self._target_changed = now
                if prev is not None:
                    val = prev                     # een doelstap is geen "kou": filter niet laten springen
            self._prev_target[r.name] = r.target
            self._ema[r.name] = val
            out[r.name] = val * (s.trv_weight if r.trv_sensor else 1.0)
        return out

    def _feedforward(self, valid: list[Room], now: float) -> float:
        """Doel boven het gebruikelijke doel (meest voorkomend in 24 u) → aanvoer meteen een stukje mee."""
        s = self.s
        if self._ref_t is None or now - self._ref_t >= 600:
            self._ref_t = now
            for r in valid:
                self._refs.setdefault(r.name, deque(maxlen=144)).append(round(r.target * 2) / 2)
        steps = []
        for r in valid:
            hist = self._refs.get(r.name)
            if hist and r.valve is not None:
                ref = Counter(hist).most_common(1)[0][0]
                steps.append(r.target - ref)
        if not steps:
            return 0.0
        return max(-s.ff_max, min(s.ff_max, s.ff_k * sum(steps) / len(steps)))

    def _trim_update(self, valves: list[float], worst: float, now: float, active: bool) -> None:
        """Elke 30 min: meest open klep gemiddeld hoog én echt tekort → +0,5 K; laag en geen tekort → −0,5 K."""
        s = self.s
        if not active or not valves:
            self._acc_t0, self._acc_valve = None, []
            return
        if self._acc_t0 is None:
            self._acc_t0 = now
        self._acc_valve.append(max(valves))
        if now - self._acc_t0 < s.trim_interval_min * 60:
            return
        vmax = sum(self._acc_valve) / len(self._acc_valve)
        self._acc_t0, self._acc_valve = now, []
        if self._target_changed is not None and now - self._target_changed < s.trim_freeze_min * 60:
            self.trim_reason = f"wacht na doelwijziging (klep {vmax:.0f}%)"
            return
        if vmax >= s.valve_high and worst >= s.trim_deficit_up:
            self.trim = min(s.trim_max, self.trim + s.trim_step)
            self.trim_reason = f"klep {vmax:.0f}% en tekort {worst:.1f} K: +{s.trim_step:g}"
        elif vmax <= s.valve_low and worst <= 0.1:
            self.trim = max(s.trim_min, self.trim - s.trim_step)
            self.trim_reason = f"klep {vmax:.0f}% zonder tekort: −{s.trim_step:g}"
        else:
            self.trim_reason = f"klep {vmax:.0f}%, tekort {worst:.1f} K: gelijk"

    def _learn(self, now: float, active: bool) -> None:
        """Elke 6 uur: zat de bijsturing gemiddeld ≥ 1 K hoog (of laag), dan neemt de offset 0,5 K over."""
        s = self.s
        if not active:
            return
        if self._off_t0 is None:
            self._off_t0 = now
        self._off_sum += self.trim
        self._off_n += 1
        if now - self._off_t0 < s.offset_interval_h * 3600:
            return
        avg = self._off_sum / max(1, self._off_n)
        self._off_t0, self._off_sum, self._off_n = now, 0.0, 0
        if avg >= s.offset_trigger and self.offset < s.offset_limit:
            self.offset = min(s.offset_limit, self.offset + s.offset_step)
            self.trim = max(s.trim_min, self.trim - s.offset_step)
        elif avg <= -s.offset_trigger and self.offset > -s.offset_limit:
            self.offset = max(-s.offset_limit, self.offset - s.offset_step)
            self.trim = min(s.trim_max, self.trim + s.offset_step)

    def _power_mode(self, ta: float | None, shortfall: float | None, now: float) -> tuple[int, str]:
        s = self.s
        if ta is not None:
            if ta > s.silent_above:
                self.cold = False
            elif ta <= s.silent_release:
                self.cold = True
        if not self.cold:
            self._idx = 0
            self._t.clear()
            return SILENT, f"buiten boven {s.silent_above:g} °C: altijd Stil"
        floor = 1 if (ta is not None and ta < s.normal_floor_below) else 0
        hold_ok = self._mode_changed is None or now - self._mode_changed >= s.mode_hold_min * 60
        if self._idx < floor:
            self._idx, self._mode_changed = floor, now
        has = shortfall is not None
        if self._idx == 0 and self._held("up1", has and shortfall >= s.shortfall_normal_k, now, s.shortfall_normal_min) and hold_ok:
            self._idx, self._mode_changed = 1, now
            self._t.clear()
        elif self._idx == 1 and self._held("up2", has and shortfall >= s.shortfall_boost_k, now, s.shortfall_boost_min) and hold_ok:
            self._idx, self._mode_changed = 2, now
            self._t.clear()
        elif self._idx > floor and self._held("down", has and shortfall <= s.deescalate_k, now, s.deescalate_min) and hold_ok:
            self._idx -= 1
            self._mode_changed = now
            self._t.clear()
        value = _VAL[self._idx]
        why = "koud: start Stil, schaalt op bij aanhoudend tekort" if self._idx == 0 else "opgeschaald wegens aanhoudend tekort"
        if floor and self._idx == floor:
            why = f"onder {s.normal_floor_below:g} °C: minimaal Normaal"
        return value, why

    def _dhw(self, inp: Inputs, now: float) -> dict:
        s = self.s
        top = inp.dhw_top
        if top is not None and (top >= s.legionella_temp or (inp.disinfection or 0) > 0):
            self.last_hot = inp.wall
        days = None if self.last_hot is None else round((inp.wall - self.last_hot) / 86400, 1)
        warn = days is not None and days > s.legionella_warn_days
        price = inp.price
        rank = inp.price_rank
        if price is None:
            want, reason = s.dhw_floor, "geen prijs: minimum"
        elif price >= 0 and rank is not None:
            if rank <= s.dhw_rank_max:
                want, reason = s.dhw_temp_cheap, f"goedkoopste {s.dhw_rank_max:.0%} van het etmaal"
            elif rank <= s.dhw_rank_mid:
                want, reason = s.dhw_temp_normal, f"goedkoopste {s.dhw_rank_mid:.0%} van het etmaal"
            else:
                want, reason = s.dhw_floor, "niet bij de goedkoopste uren: minimum"
        elif price < 0:
            want, reason = s.dhw_max_negative, "negatieve prijs: maximaal laden"
        elif price < s.price_cheap:
            want, reason = s.dhw_temp_cheap, "goedkoop"
        elif price < s.price_normal:
            want, reason = s.dhw_temp_normal, "normaal"
        elif price < s.price_expensive:
            want, reason = s.dhw_temp_mid, "iets duur"
        else:
            want, reason = s.dhw_floor, "duur: minimum"
        if price is None or price >= 0:
            want = min(want, s.dhw_max)
        want = max(want, s.dhw_floor)
        urgent = price is not None and price < 0
        if inp.dhw_need is not None and inp.dhw_need > want:
            want, reason, urgent = inp.dhw_need, inp.dhw_need_reason or "tapmoment", True
        old = self._dhw_target
        if old is None or urgent or abs(want - old) >= 5 or (
                self._dhw_changed is not None and now - self._dhw_changed >= s.dhw_hold_min * 60 and want != old):
            self._dhw_target, self._dhw_changed = want, now
        recovery = top is not None and top < s.dhw_floor - 2
        return {"target": self._dhw_target, "reason": reason, "recovery": recovery,
                "legionella_days": days, "legionella_warning": warn}

    def _plan(self, inp, desired_on, target, mode, dhw, now, blocks) -> list[dict]:
        s = self.s
        out: list[dict] = []

        def add(slave, reg, name, value, current, text, note=""):
            out.append({"slave": slave, "register": reg, "name": name, "value": value,
                        "value_text": text, "current": current, "note": note})

        if desired_on is not None and inp.on_773 is not None and desired_on != bool(inp.on_773):
            edge_age = None if self._unit_edge is None else now - self._unit_edge
            if desired_on and edge_age is not None and edge_age < s.min_off_min * 60:
                blocks.append("minimale stilstandstijd")
            elif not desired_on and edge_age is not None and edge_age < s.min_on_min * 60:
                blocks.append("minimale looptijd")
            else:
                add(1, 773, "aan/uit", int(desired_on), inp.on_773, "aan" if desired_on else "uit")
        if target is not None and inp.set_769 is not None and abs(target - inp.set_769) >= 0.75:   # hysterese
            add(1, 769, "aanvoersetpoint", int(round(target)), inp.set_769, f"{round(target)} °C")
        if inp.power_775 is not None and int(inp.power_775) != mode and (self.demand or bool(desired_on)):
            add(1, 775, "vermogensmodus", mode, inp.power_775, MODE_NAMES[mode])
        if dhw["target"] is not None and inp.dhw_set is not None and abs(dhw["target"] - inp.dhw_set) >= 1:
            add(2, 6, "tapwatersetpoint", int(dhw["target"]), inp.dhw_set, f"{int(dhw['target'])} °C", dhw["reason"])
        return out
