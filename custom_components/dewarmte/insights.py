"""Bewaking en leren (puur Python, los te testen).

- Alerts:       directe meldingen bij communicatieverlies, storingen en kamers die te koud blijven.
- CycleCounter: compressorstarts tellen (pendelbewaking).
- DhwLearner:   leert tapmomenten uit dalingen in de boiler en zorgt dat het water dan warm genoeg is,
                opgewarmd in het goedkoopste stuk vóór dat moment.
- WeekStats:    verzamelt een week aan comfort, energie, kosten en starts voor het weekrapport.
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

DAYS = ("ma", "di", "wo", "do", "vr", "za", "zo")


def _lt(wall: float) -> time.struct_time:
    return time.localtime(wall)


def _daytype(wall: float) -> str:
    return "weekend" if _lt(wall).tm_wday >= 5 else "werkdag"


def _slot(wall: float, minutes: int = 30) -> int:
    t = _lt(wall)
    return (t.tm_hour * 60 + t.tm_min) // minutes


def _hm(slot: int, minutes: int = 30) -> str:
    m = slot * minutes
    return f"{m // 60:02d}:{m % 60:02d}"


# ====================================================================== meldingen
@dataclass
class AlertSettings:
    modbus_min: float = 10.0       # zo lang geen verse kritieke data
    fault_min: float = 2.0         # storing zo lang actief
    cold_k: float = 1.0            # kamer zoveel onder doel
    cold_h: float = 2.0            # ... zo lang
    rearm_min: float = 30.0        # zo lang weer normaal voordat dezelfde melding opnieuw kan


class Alerts:
    """Houdt per melding bij sinds wanneer de conditie geldt; meldt één keer en bij herstel."""

    def __init__(self, s: AlertSettings | None = None) -> None:
        self.s = s or AlertSettings()
        self._since: dict[str, float] = {}
        self._fired: dict[str, float] = {}      # sleutel -> tijdstip melding
        self._clear: dict[str, float] = {}      # sleutel -> sinds wanneer weer normaal
        self._target: dict[str, float] = {}     # laatste kamerdoel (stap omhoog = timer opnieuw)
        self.count = 0
        self.active: dict[str, str] = {}

    def _eval(self, key: str, cond: bool, hold_s: float, now: float, text: str,
              recover: str | None, out: list[tuple[str, str]]) -> None:
        if cond:
            self._clear.pop(key, None)
            t0 = self._since.setdefault(key, now)
            if key not in self._fired and now - t0 >= hold_s:
                self._fired[key] = now
                self.active[key] = text
                self.count += 1
                out.append(("melding", text))
            return
        self._since.pop(key, None)
        if key in self._fired:
            t1 = self._clear.setdefault(key, now)
            if recover and key in self.active and now - t1 >= 120:
                out.append(("hersteld", recover))
            if now - t1 >= 120:
                self.active.pop(key, None)
            if now - t1 >= self.s.rearm_min * 60:
                self._fired.pop(key, None)
                self._clear.pop(key, None)

    def update(self, now: float, critical_ok: bool, ao_fault: bool | None, ao_code: str,
               pt_fault: bool | None, pt_code: str, rooms: list[dict],
               hm_errors: dict | None = None) -> list[tuple[str, str]]:
        """rooms: [{"id", "name", "current", "target", "heating"}] - geeft [(soort, tekst)] terug."""
        s, out = self.s, []
        self._eval("modbus", not critical_ok, s.modbus_min * 60, now,
                   f"Geen verse data van de SunRain sinds {s.modbus_min:g} min. Controleer de DR164/RS485 en MQTT.",
                   "Communicatie met de warmtepomp is hersteld.", out)
        self._eval("fault_ao", bool(ao_fault), s.fault_min * 60, now,
                   f"Pomp AO meldt een storing ({ao_code}).", "Storing pomp AO is opgeheven.", out)
        self._eval("fault_pt", bool(pt_fault), s.fault_min * 60, now,
                   f"Pomp T meldt een storing ({pt_code}).", "Storing Pomp T is opgeheven.", out)
        for name, code in (hm_errors or {}).items():
            self._eval(f"hm:{name}", bool(code), 10 * 60, now,
                       f"Warmtemeter {name} meldt foutcode {code} (128 = geen meetsignaal, meestal lucht in de leiding). "
                       "Debiet en warmte zijn zolang onbekend; overweeg te ontluchten.",
                       f"Warmtemeter {name} meet weer normaal.", out)
        for r in rooms:
            key = f"cold:{r['id']}"
            cur, tgt = r.get("current"), r.get("target")
            if tgt is not None:
                old = self._target.get(key)
                if old is not None and tgt > old + 0.05:
                    self._since.pop(key, None)        # doel omhoog gezet: eerst opwarmtijd gunnen
                self._target[key] = tgt
            cold = bool(r.get("heating")) and cur is not None and tgt is not None and tgt - cur > s.cold_k
            self._eval(key, cold, s.cold_h * 3600, now,
                       f"{r['name']} blijft te koud: {cur:.1f} °C bij doel {tgt:.1f} °C (al {s.cold_h:g} uur)."
                       if cold else "", None, out)
        return out


# ====================================================================== compressorstarts
class CycleCounter:
    def __init__(self, state: dict | None = None, min_off_s: float = 60.0) -> None:
        st = state or {}
        self.starts: deque[float] = deque(st.get("starts", []))     # muurtijden van starts (max 8 dagen)
        self.min_off_s = min_off_s
        self._on: bool | None = None
        self._off_since: float | None = None
        self._on_since: float | None = None
        self.runs: deque[float] = deque(st.get("runs", []), maxlen=500)   # looptijden (min)

    def export_state(self) -> dict:
        return {"starts": list(self.starts), "runs": list(self.runs)}

    def update(self, running: bool | None, wall: float, defrost: bool = False) -> bool:
        """True als er zojuist een start is geteld."""
        while self.starts and wall - self.starts[0] > 8 * 86400:
            self.starts.popleft()
        if running is None:
            return False
        started = False
        if running and self._on is False and not defrost:
            if self._off_since is not None and wall - self._off_since >= self.min_off_s:
                self.starts.append(wall)
                started = True
            self._on_since = wall if started or self._on_since is None else self._on_since
        if not running and self._on:
            self._off_since = wall
            if self._on_since is not None:
                self.runs.append(round((wall - self._on_since) / 60, 1))
            self._on_since = None
        if running and self._on is None:
            self._on_since = wall
        if not running and self._on is None:
            self._off_since = wall
        self._on = running
        return started

    def last_hour(self, wall: float) -> int:
        return sum(1 for t in self.starts if wall - t <= 3600)

    def today(self, wall: float) -> int:
        t = _lt(wall)
        midnight = wall - (t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec)
        return sum(1 for x in self.starts if x >= midnight)

    def per_hour(self, wall: float, since: float) -> dict[int, int]:
        out: dict[int, int] = {}
        for t in self.starts:
            if since <= t <= wall:
                h = int(t // 3600)
                out[h] = out.get(h, 0) + 1
        return out


# ====================================================================== tapwater leren
@dataclass
class DhwSettings:
    usage_temp: float = 50.0        # minimaal boven in de boiler op een tapmoment
    margin_k: float = 1.0
    loss_k_h: float = 0.4           # stilstandsverlies boiler
    rate_k_h: float = 4.0           # opwarmsnelheid Pomp T (wordt geleerd)
    lookahead_h: float = 6.0        # zo ver vooruit mag hij vooraf opwarmen
    prob: float = 0.4               # tapmoment als het op ≥ 40 % van zulke dagen voorkomt
    half_life_d: float = 14.0
    keep_days: int = 28
    drop_bottom_k: float = 3.0      # daling onder in 20 min = tappen
    drop_top_k: float = 2.0         # daling boven in 20 min = tappen
    window_min: float = 20.0


class DhwLearner:
    def __init__(self, s: DhwSettings | None = None, state: dict | None = None) -> None:
        self.s = s or DhwSettings()
        st = state or {}
        self.events: list[list] = [list(e) for e in st.get("events", [])]   # [muurtijd, top bij start]
        self.first: float | None = st.get("first")
        self.rate: float = float(st.get("rate", self.s.rate_k_h))
        self._hist: deque[tuple[float, float, float]] = deque()            # (t, top, bottom)
        self._last_event = 0.0
        self._heat: tuple[float, float] | None = None                       # (t, top) begin opwarmen
        self.prep: dict | None = None
        self.reason = "nog aan het leren"

    def export_state(self) -> dict:
        return {"events": self.events, "first": self.first, "rate": round(self.rate, 2)}

    # ---------------------------------------------------------------- waarnemen
    def observe(self, wall: float, top: float | None, bottom: float | None, heating: bool) -> bool:
        """Registreert tapmomenten en de opwarmsnelheid. True als er net een tapmoment is herkend."""
        if self.first is None:
            self.first = wall
        if top is None or bottom is None:
            return False
        s = self.s
        self._hist.append((wall, top, bottom))
        while self._hist and wall - self._hist[0][0] > s.window_min * 60:
            self._hist.popleft()
        # opwarmsnelheid leren uit stukken waarin Pomp T draait
        if heating:
            if self._heat is None:
                self._heat = (wall, top)
            elif wall - self._heat[0] >= 1800 and top > self._heat[1]:
                r = (top - self._heat[1]) / ((wall - self._heat[0]) / 3600)
                self.rate = max(1.0, min(20.0, 0.8 * self.rate + 0.2 * r))
                self._heat = (wall, top)
        else:
            self._heat = None
        # tappen: snelle daling (en niet tijdens opwarmen, dan stijgt het juist)
        t_max = max(x[1] for x in self._hist)
        b_max = max(x[2] for x in self._hist)
        hit = (b_max - bottom >= s.drop_bottom_k) or (t_max - top >= s.drop_top_k)
        if hit and wall - self._last_event >= 45 * 60:
            self._last_event = wall
            start_top = self._hist[0][1]
            self.events.append([round(wall), start_top])
            self.events = [e for e in self.events if wall - e[0] <= s.keep_days * 86400]
            return True
        return False

    # ---------------------------------------------------------------- voorspellen
    def _days_seen(self, wall: float, daytype: str) -> float:
        if self.first is None:
            return 0.0
        start = max(self.first, wall - self.s.keep_days * 86400)
        n, t = 0.0, start
        while t < wall:
            if _daytype(t) == daytype:
                age = (wall - t) / 86400
                n += 0.5 ** (age / self.s.half_life_d) * min(1.0, (wall - t) / 86400)
            t += 86400
        return n

    def moments(self, wall: float) -> dict[str, list[int]]:
        """Geleerde tapmomenten per dagtype: lijst met halfuur-slots waarin getapt wordt."""
        s = self.s
        out: dict[str, list[int]] = {}
        for dt in ("werkdag", "weekend"):
            days = self._days_seen(wall, dt)
            if days < 2:
                out[dt] = []
                continue
            score = [0.0] * 48
            for t, _top in self.events:
                if _daytype(t) != dt:
                    continue
                w = 0.5 ** (((wall - t) / 86400) / s.half_life_d)
                k = _slot(t)
                score[k] += w
                score[(k - 1) % 48] += 0.25 * w
                score[(k + 1) % 48] += 0.25 * w
            out[dt] = [k for k in range(48) if score[k] / days >= s.prob]
        return out

    def windows(self, wall: float) -> dict[str, list[str]]:
        res = {}
        for dt, slots in self.moments(wall).items():
            spans, run = [], []
            for k in slots:
                if run and k != run[-1] + 1:
                    spans.append(run)
                    run = []
                run.append(k)
            if run:
                spans.append(run)
            res[dt] = [f"{_hm(r[0])}–{_hm(r[-1] + 1)}" for r in spans]
        return res

    def next_usage(self, wall: float, hours: float = 24) -> float | None:
        """Begin van het eerstvolgende geleerde tapmoment na nu (muurtijd), of None."""
        m = self.moments(wall)
        if not any(m.values()):
            return None
        step = 1800
        t = wall - (wall % step) + step
        for _ in range(int(hours * 2)):
            hit = _slot(t) in m.get(_daytype(t), [])
            before = _slot(t - step) in m.get(_daytype(t - step), [])
            if hit and not before:
                return t
            t += step
        return None

    # ---------------------------------------------------------------- sturen
    def need(self, wall: float, top: float | None, restart_k: float | None, dhw_max: float,
             mean_price: Callable[[float, float], float | None] | None = None) -> tuple[float | None, str]:
        """Minimaal tapwatersetpoint nu (of None) zodat het water op het volgende tapmoment warm genoeg is."""
        s = self.s
        use = self.next_usage(wall)
        if use is None or top is None:
            self.prep = None
            self.reason = "geen geleerd tapmoment" if use is None else "geen boilertemperatuur"
            return None, self.reason
        when = time.strftime("%H:%M", _lt(use))
        hours_left = (use - wall) / 3600
        if self.prep and (wall >= self.prep["until"] or top >= self.prep["goal"] + s.loss_k_h * max(0.0, hours_left)):
            self.prep = None                                   # klaar of tapmoment voorbij
        if self.prep:
            self.reason = f"opwarmen voor tapmoment {self.prep['when']} (≥ {s.usage_temp:g} °C)"
            return self.prep["set"], self.reason
        goal = s.usage_temp + s.margin_k
        expected = top - s.loss_k_h * hours_left
        if expected >= goal:
            self.reason = f"tapmoment {when}: boiler is dan nog ~{expected:.0f} °C, niets nodig"
            return None, self.reason
        if hours_left > s.lookahead_h:
            self.reason = f"tapmoment {when}: later opwarmen"
            return None, self.reason
        need_h = max(0.25, (goal - (top - s.loss_k_h)) / self.rate)
        latest = use - need_h * 3600 - 1800                      # half uur reserve
        start = latest
        if mean_price is not None:
            best = None
            t = max(wall, use - s.lookahead_h * 3600)
            while t <= latest:
                p = mean_price(t, t + need_h * 3600)
                if p is not None and (best is None or p < best[0] - 1e-6):
                    best = (p, t)
                t += 900
            if best is not None:
                start = best[1]
        if wall + 60 < start:
            self.reason = (f"tapmoment {when}: opwarmen vanaf {time.strftime('%H:%M', _lt(start))} "
                           f"(~{need_h * 60:.0f} min nodig)")
            return None, self.reason
        restart = 5.0 if restart_k is None else max(1.0, float(restart_k))
        set_t = min(dhw_max, max(goal + 1, math.ceil(top + restart + 1)))
        self.prep = {"set": set_t, "goal": goal, "until": use, "when": when}
        self.reason = f"opwarmen voor tapmoment {when} (≥ {s.usage_temp:g} °C)"
        return set_t, self.reason


# ====================================================================== weekstatistiek
class WeekStats:
    def __init__(self, state: dict | None = None) -> None:
        st = state or {}
        self.start: float | None = st.get("start")
        self.rooms: dict[str, dict] = st.get("rooms", {})
        self.e0: dict = st.get("e0", {})
        self.cost = float(st.get("cost", 0.0))
        self.kwh = float(st.get("kwh", 0.0))
        self.price_sum = float(st.get("price_sum", 0.0))
        self.price_h = float(st.get("price_h", 0.0))
        self.draws: list = st.get("draws", [])
        self.alerts = int(st.get("alerts", 0))
        self.disinfections = int(st.get("disinfections", 0))
        self._last: float | None = None

    def export_state(self) -> dict:
        return {"start": self.start, "rooms": self.rooms, "e0": self.e0, "cost": round(self.cost, 4),
                "kwh": round(self.kwh, 4), "price_sum": round(self.price_sum, 4), "price_h": round(self.price_h, 4),
                "draws": self.draws, "alerts": self.alerts, "disinfections": self.disinfections}

    def reset(self, wall: float, energies: dict) -> None:
        self.__init__({"start": wall, "e0": {k: v for k, v in energies.items() if v is not None}})
        self._last = wall

    def sample(self, wall: float, rooms: list[dict], el_w: float | None, price: float | None, energies: dict) -> None:
        if self.start is None:
            self.reset(wall, energies)
            return
        for k, val in energies.items():
            if val is not None and k not in self.e0:
                self.e0[k] = val
        dt = 0.0 if self._last is None else max(0.0, min(300.0, wall - self._last))
        self._last = wall
        if dt <= 0:
            return
        h = dt / 3600
        for r in rooms:
            cur, tgt = r.get("current"), r.get("target")
            if not r.get("heating") or cur is None or tgt is None:
                continue
            st = self.rooms.setdefault(r["id"], {"name": r["name"], "h": 0.0, "dev": 0.0, "abs": 0.0,
                                                 "ok": 0.0, "cold": 0.0})
            d = cur - tgt
            st["h"] += h
            st["dev"] += d * h
            st["abs"] += abs(d) * h
            st["ok"] += h if abs(d) <= 0.5 else 0.0
            st["cold"] += h if d < -1.0 else 0.0
        if price is not None:
            self.price_sum += price * h
            self.price_h += h
            if el_w is not None:
                kwh = max(0.0, el_w) / 1000 * h
                self.kwh += kwh
                self.cost += kwh * price

    def draw(self, wall: float, top: float | None) -> None:
        self.draws.append([round(wall), top])

    def build(self, wall: float, energies: dict, cycles: CycleCounter, learner: DhwLearner,
              usage_temp: float, legionella_days: float | None) -> tuple[str, str, dict]:
        def delta(k):
            a, b = self.e0.get(k), energies.get(k)
            return None if a is None or b is None or b < a else round(b - a, 1)

        start = self.start or wall - 7 * 86400
        el, th, el_t = delta("ao_pm_energy"), delta("ao_hm_energy"), delta("pt_pm_energy")
        cop = round(th / el, 2) if el and th is not None and el > 0.5 else None
        lines: list[str] = []
        lines.append("Comfort per kamer (gem. afwijking, % binnen ±0,5 K, uren > 1 K te koud):")
        rooms_out = {}
        for rid, st in sorted(self.rooms.items(), key=lambda x: x[1]["name"]):
            if st["h"] < 1:
                continue
            dev, ok = st["dev"] / st["h"], 100 * st["ok"] / st["h"]
            rooms_out[rid] = {"naam": st["name"], "afwijking": round(dev, 2), "binnen_pct": round(ok),
                              "uren_koud": round(st["cold"], 1)}
            lines.append(f"  {st['name']}: {dev:+.1f} K, {ok:.0f} %, {st['cold']:.1f} u")
        if not rooms_out:
            lines.append("  (nog geen gegevens)")
        lines.append(f"Energie: pomp AO {_f(el)} kWh elektrisch → {_f(th)} kWh warmte (COP {_f(cop, 2)}), "
                     f"Pomp T {_f(el_t)} kWh")
        avg_p = self.price_sum / self.price_h if self.price_h > 0.5 else None
        saved = None
        if avg_p is not None and self.kwh > 0.5:
            fixed = self.kwh * avg_p
            saved = fixed - self.cost
            lines.append(f"Kosten warmtepompen €{self.cost:.2f} (gem. €{self.cost / self.kwh:.3f}/kWh). "
                         f"Bij vast stoken tegen de weekgemiddelde prijs €{avg_p:.3f}: €{fixed:.2f} → "
                         f"{'bespaard' if saved >= 0 else 'duurder'} €{abs(saved):.2f}")
        per_h = cycles.per_hour(wall, start)
        n_starts = sum(per_h.values())
        busy = sum(1 for n in per_h.values() if n > 3)
        runs = [r for r in cycles.runs if r > 0]
        avg_run = sum(runs[-100:]) / len(runs[-100:]) if runs else None
        lines.append(f"Compressor: {n_starts} starts, max {max(per_h.values(), default=0)} per uur, "
                     f"{busy} uur met > 3 starts" + (f", gem. looptijd {avg_run:.0f} min" if avg_run else ""))
        if busy >= 7:
            lines.append("  ⚠️ Vaak pendelen: overweeg het buffervat of een hogere minimale looptijd.")
        cold = sum(1 for _t, top in self.draws if top is not None and top < usage_temp)
        win = learner.windows(wall)
        lines.append(f"Tapwater: {len(self.draws)} tapmomenten herkend, {cold} keer onder {usage_temp:g} °C. "
                     f"Geleerd – werkdag: {', '.join(win.get('werkdag') or ['nog niets'])}; "
                     f"weekend: {', '.join(win.get('weekend') or ['nog niets'])}")
        lines.append(f"Legionella: {'–' if legionella_days is None else f'{legionella_days:.1f}'} dagen sinds ≥ 60 °C, "
                     f"{self.disinfections} desinfectie(s) deze week | meldingen: {self.alerts}")
        title = "DeWarmte weekrapport " + time.strftime("%d-%m", _lt(start)) + " t/m " + time.strftime("%d-%m", _lt(wall))
        data = {"van": time.strftime("%Y-%m-%d", _lt(start)), "tot": time.strftime("%Y-%m-%d", _lt(wall)),
                "kamers": rooms_out, "kwh_el_ao": el, "kwh_th_ao": th, "cop_week": cop, "kwh_el_pt": el_t,
                "kosten": round(self.cost, 2), "besparing": None if saved is None else round(saved, 2),
                "gem_prijs": None if avg_p is None else round(avg_p, 4), "starts": n_starts,
                "uren_pendelen": busy, "tapmomenten": len(self.draws), "tap_te_koud": cold, "geleerd": win}
        return title, "\n".join(lines), data


def _f(x, nd=1) -> str:
    return "–" if x is None else f"{x:.{nd}f}"
