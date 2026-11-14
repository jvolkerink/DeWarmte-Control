"""Prijsplanner: kamerdoelen en tapwater op basis van de dynamische stroomprijs (puur Python).

Idee: warmte is goedkoop "op te slaan" in het huis als het nu goedkoper is dan in de uren
eromheen. Per moment vergelijken we de prijs nu met de gemiddelde prijs in het venster
[nu − W, nu + W] (W = zo lang houdt het huis een graad extra vast). Is het nu goedkoper:
voorverwarmen (tot +band_up). Is het nu duurder: iets laten zakken (tot −band_down) en
de opgeslagen warmte gebruiken; daarna in goedkopere uren weer bijstoken.

W wordt geleerd: per kamer meten we hoe snel hij afkoelt als de klep dicht is.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, fields
from datetime import datetime, timedelta

WEEKEND = (5, 6)


@dataclass
class PlanSettings:
    band_up: float = 1.5            # max. voorverwarmen boven het normale doel (K)
    band_down: float = 1.0          # max. laten zakken onder het normale doel (K)
    gain: float = 2.5               # K offset per (prijsverschil / prijsspreiding)
    step: float = 0.5               # doelen in stappen van 0,5 K
    hold_min: float = 45.0          # minimaal zo lang een offset vasthouden
    window_default_h: float = 3.0
    window_min_h: float = 2.0
    window_max_h: float = 6.0
    night_start: str = "23:00"
    morning_weekday: str = "05:00"
    morning_weekend: str = "07:00"
    override_max_h: float = 3.0     # handmatige wijziging respecteren tot het volgende blok, max. zo lang
    lead_min: float = 30.0          # warme periode zoveel eerder beginnen (opwarmtijd)
    dhw_rank_max: float = 0.10      # goedkoopste 10 % van het komende etmaal: boiler naar maximum
    dhw_rank_mid: float = 0.25      # goedkoopste 25 %: boiler naar middenstand

    @classmethod
    def from_dict(cls, d: dict) -> "PlanSettings":
        known = {f.name for f in fields(cls)}
        out = cls()
        for k, v in (d or {}).items():
            if k in known and v is not None:
                setattr(out, k, type(getattr(out, k))(v))
        return out


def _hm(text: str) -> tuple[int, int]:
    h, m = text.split(":")
    return int(h), int(m)


DAYS = {"ma": 0, "di": 1, "wo": 2, "do": 3, "vr": 4, "za": 5, "zo": 6}


def _days(spec: str) -> set[int]:
    spec = spec.strip().lower()
    if spec in ("dagelijks", "elke dag", "alle"):
        return set(range(7))
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = (DAYS[x.strip()] for x in part.split("-", 1))
            out |= set(range(a, b + 1)) if a <= b else set(range(a, 7)) | set(range(0, b + 1))
        elif part:
            out.add(DAYS[part])
    return out


def parse_schedules(text: str | None) -> dict[str, dict]:
    """Regels: `climate.badkamer | 21 | ma-vr 05:30-07:30, 20:00-22:30 | za-zo 08:00-10:00, 20:00-22:30`."""
    out: dict[str, dict] = {}
    for line in (text or "").replace(";", "\n").splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3 or not parts[0].startswith("climate."):
            continue
        try:
            temp = float(parts[1].replace(",", "."))
        except ValueError:
            continue
        periods = []
        for grp in parts[2:]:
            if " " not in grp:
                continue
            day_spec, ranges = grp.split(" ", 1)
            days = _days(day_spec)
            for r in ranges.split(","):
                if "-" not in r:
                    continue
                a, b = r.strip().split("-", 1)
                ha, ma = _hm(a.strip())
                hb, mb = _hm(b.strip())
                periods.append((days, ha * 60 + ma, hb * 60 + mb))
        if periods:
            out[parts[0]] = {"temp": temp, "periods": periods}
    return out


def _q(x: float, step: float) -> float:
    return round(round(x / step) * step, 2)


class PricePlanner:
    def __init__(self, s: PlanSettings, state: dict | None = None, schedules: str | None = None) -> None:
        self.s = s
        self.schedules = parse_schedules(schedules)
        st = state or {}
        self.cool_rate: dict[str, float] = dict(st.get("cool_rate") or {})   # K/u (positief = afkoelen)
        self.prices: list[tuple[float, float]] = []                           # (epoch start, €/kWh)
        self.prices_t: float | None = None
        self.offset = 0.0
        self._offset_t: float | None = None
        self._block: str | None = None
        self._hist: dict[str, list[tuple[float, float]]] = {}                 # room -> [(wall, temp)]
        self.last_written: dict[str, tuple[float, float]] = {}                # room -> (target, wall)
        self.override_until: dict[str, float] = {}
        self.reason = "nog geen prijzen"

    def export_state(self) -> dict:
        return {"cool_rate": self.cool_rate}

    # ------------------------------------------------------------ prijzen
    def set_prices(self, rows: list[dict], wall: float) -> int:
        out = []
        for r in rows or []:
            try:
                t = datetime.fromisoformat(str(r["start_time"]).replace("Z", "+00:00")).timestamp()
                out.append((t, float(r["price"])))
            except (KeyError, TypeError, ValueError):
                continue
        if out:
            self.prices = sorted(set(out))
            self.prices_t = wall
        return len(out)

    def _slot_len(self) -> float:
        if len(self.prices) < 2:
            return 3600.0
        return max(900.0, min(3600.0, self.prices[1][0] - self.prices[0][0]))

    def _mean(self, a: float, b: float) -> float | None:
        """Tijdgewogen gemiddelde prijs over [a, b) (slots die het interval overlappen)."""
        ln = self._slot_len()
        tot = w = 0.0
        for t, p in self.prices:
            ov = min(b, t + ln) - max(a, t)
            if ov > 0:
                tot += p * ov
                w += ov
        return tot / w if w >= min(900.0, (b - a) / 2) else None

    def price_now(self, wall: float) -> float | None:
        cur = [p for t, p in self.prices if t <= wall < t + self._slot_len()]
        return cur[-1] if cur else None

    def horizon(self, wall: float, hours: float = 24) -> list[float]:
        return [p for t, p in self.prices if wall - self._slot_len() < t <= wall + hours * 3600]

    def rank_now(self, wall: float) -> float | None:
        """0 = goedkoopste moment van het komende etmaal, 1 = duurste."""
        now, hor = self.price_now(wall), self.horizon(wall)
        if now is None or len(hor) < 8:
            return None
        return sum(1 for p in hor if p < now) / len(hor)

    @property
    def window_h(self) -> float:
        rates = [r for r in self.cool_rate.values() if r > 0.05]
        if not rates:
            return self.s.window_default_h
        rates.sort()
        med = rates[len(rates) // 2]
        return max(self.s.window_min_h, min(self.s.window_max_h, 1.0 / med))

    def raw_offset(self, wall: float) -> tuple[float, str]:
        s = self.s
        hour = wall - (wall % 3600)
        now = self._mean(hour, hour + 3600)                 # gemiddelde van dit klokuur (tegen kwartierruis)
        if now is None:
            return 0.0, "geen prijs voor nu"
        if now < 0:
            return s.band_up, "negatieve prijs: maximaal voorverwarmen"
        look = max(3.0, min(6.0, self.window_h + 1.0))
        w = look * 3600
        # Alleen vooruit kijken: warmte kan alleen naar later verschoven worden, niet naar vroeger.
        # Vergelijk dit uur met het goedkoopste en het duurste uurblok dat binnen W + 1 u (3-6 u) begint.
        blocks = []
        t = wall - (wall % 900) + 900
        while t <= wall + w:
            m = self._mean(t, t + 3600)
            if m is not None:
                blocks.append(m)
            t += 900
        hor = sorted(self.horizon(wall))
        if not blocks or len(hor) < 8:
            return 0.0, "te weinig prijsvoorspelling"
        low, high = min(blocks), max(blocks)
        spread = max(0.05, hor[int(0.9 * (len(hor) - 1))] - hor[int(0.1 * (len(hor) - 1))])
        raw = s.gain * ((high - now) + (low - now)) / (2 * spread)
        raw = max(-s.band_down, min(s.band_up, raw))
        word = ("goedkoper dan straks: voorverwarmen" if raw >= 0.25 else
                "duurder dan straks: iets laten zakken" if raw <= -0.25 else "ongeveer gelijk: normaal doel")
        return raw, (f"nu {now:.3f}, komende {look:.0f} u {low:.3f}–{high:.3f} €/kWh: {word}")

    # ------------------------------------------------------------ schema
    def block(self, wall: float) -> str:
        dt = datetime.fromtimestamp(wall)
        ns = _hm(self.s.night_start)
        mh = _hm(self.s.morning_weekend if dt.weekday() in WEEKEND else self.s.morning_weekday)
        hm = (dt.hour, dt.minute)
        return "nacht" if (hm >= ns or hm < mh) else "dag"

    def _next_boundary(self, wall: float, room: str | None = None) -> float:
        t = wall
        b = (self.block(wall), self.warm_period(room, wall) if room else None)
        for _ in range(48 * 12):
            t += 300
            if (self.block(t), self.warm_period(room, t) if room else None) != b:
                return t
        return wall + 12 * 3600

    def update_offset(self, wall: float) -> float:
        s = self.s
        raw, why = self.raw_offset(wall)
        want = _q(raw, s.step)
        blk = self.block(wall)
        held = self._offset_t is not None and wall - self._offset_t < s.hold_min * 60
        urgent = "negatieve" in why
        if self._offset_t is None or blk != self._block or urgent or (
                not held and abs(want - self.offset) >= s.step):
            if want != self.offset or self._offset_t is None:
                self._offset_t = wall
            self.offset = want
        self._block = blk
        self.reason = why
        return self.offset

    # ------------------------------------------------------------ kamers
    @staticmethod
    def base_target(presets: dict, night: bool, home: bool) -> float | None:
        key = ("eco" if night else "comfort") + ("_temp" if home else "_away_temp")
        val = presets.get(key) or presets.get(("eco" if night else "comfort") + "_temp")
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    def warm_period(self, room: str, wall: float) -> float | None:
        """Doel van een actieve warme periode (inclusief voorloop), anders None."""
        sch = self.schedules.get(room)
        if not sch:
            return None
        lead = self.s.lead_min
        for back in (0, 1):                      # ook periodes die gisteren begonnen (over middernacht)
            dt = datetime.fromtimestamp(wall) - timedelta(days=back)
            minute = (datetime.fromtimestamp(wall) - dt.replace(hour=0, minute=0, second=0, microsecond=0)
                      ).total_seconds() / 60
            for days, start, end in sch["periods"]:
                if dt.weekday() not in days:
                    continue
                stop = end if end > start else end + 1440
                if start - lead <= minute < stop:
                    return sch["temp"]
        return None

    def room_target(self, room: str, presets: dict, home: bool, wall: float,
                    current_target: float | None) -> tuple[float | None, str]:
        s = self.s
        night = self.block(wall) == "nacht"
        base = self.base_target(presets, night, home)
        if base is None:
            return None, "geen preset-temperaturen"
        warm = self.warm_period(room, wall) if home else None
        # handmatige wijziging respecteren
        last = self.last_written.get(room)
        if last and current_target is not None and abs(current_target - last[0]) > 0.05 and wall - last[1] > 120:
            until = min(self._next_boundary(wall, room), wall + s.override_max_h * 3600)
            self.override_until.setdefault(room, until)
        if room in self.override_until:
            if wall < self.override_until[room]:
                return None, "handmatig aangepast: tijdelijk niet sturen"
            self.override_until.pop(room, None)
        label = f"{'nacht (eco)' if night else 'dag (comfort)'}{'' if home else ', afwezig'}"
        if warm is not None:
            base, label = warm, "warme periode"
        target = _q(base + self.offset, 0.5 if s.step >= 0.5 else s.step)
        target = max(base - s.band_down, min(base + s.band_up, target))
        return target, f"{label} {base:g} + prijs {self.offset:+g}"

    def wrote(self, room: str, target: float, wall: float) -> None:
        self.last_written[room] = (target, wall)

    def learn(self, room: str, temp: float | None, valve: float | None, wall: float) -> None:
        """Afkoelsnelheid leren uit perioden met dichte klep (≥ 30 min)."""
        if temp is None or valve is None:
            return
        h = self._hist.setdefault(room, [])
        if valve > 5:
            h.clear()
            return
        h.append((wall, temp))
        while h and wall - h[0][0] > 3600:
            h.pop(0)
        if len(h) >= 2 and wall - h[0][0] >= 1800:
            rate = (h[0][1] - temp) / ((wall - h[0][0]) / 3600)
            if 0 <= rate < 3:
                old = self.cool_rate.get(room)
                self.cool_rate[room] = round(rate if old is None else old + 0.1 * (rate - old), 3)
            h.clear()

    def plan(self, wall: float, hours: int = 24) -> list[dict]:
        """Verwachte prijs-offset per uur (voor dashboard), zonder vasthouden."""
        out = []
        start = wall - wall % 3600
        for i in range(hours):
            t = start + i * 3600
            raw, _ = self.raw_offset(t)
            p = self._mean(t, t + 3600)
            if p is None:
                break
            out.append({"uur": datetime.fromtimestamp(t).strftime("%H:%M"), "prijs": round(p, 4),
                        "offset": _q(raw, self.s.step), "blok": self.block(t)})
        return out
