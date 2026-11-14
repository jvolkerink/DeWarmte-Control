"""Schrijfbeveiliging: allowlist, minimale tussentijd en uur-/dagbudget (puur Python).

Alleen deze vier registers kunnen ooit geschreven worden. Beveiligingen,
ontdooi-instellingen, compressorlimieten enz. staan er bewust niet in.
Instellingen in de SunRain staan mogelijk in EEPROM; het budget houdt het
aantal schrijfacties per dag laag zodat dat geheugen niet slijt.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .params import PARAMS

# (slave, register): (min, max)
ALLOWED: dict[tuple[int, int], tuple[int, int]] = {
    (1, 769): (20, 50),   # aanvoersetpoint verwarming (hele °C)
    (1, 773): (0, 1),     # unit aan/uit
    (1, 775): (0, 2),     # vermogensmodus 0 Normaal, 1 Boost, 2 Stil
    (2, 6): (40, 70),     # tapwatersetpoint Pomp T
}


# Service-instellingen: elk 16-bit register dat volgens de fabrikanttabel schrijfbaar is (SunRain en
# Pomp T), met de grenzen uit die tabel. Alleen te schrijven als de servicemodus aan staat.
# Statusbits/commando's (816/817: ontdooien forceren, fabrieksinstellingen, koudemiddel terugwinnen,
# compressor forceren) en de bytes van 780/781 zijn bewust NIET schrijfbaar.
SERVICE: dict[tuple[int, int], tuple[int, int]] = {}
for _s, _a, _b, _w, _sc, _sg, _n, _u, _wr, _lo, _hi in PARAMS:
    if _wr and _b is None and _w == 1 and _s in (1, 2):
        if (_s, _a) in ALLOWED:
            SERVICE[(_s, _a)] = ALLOWED[(_s, _a)]
        elif _lo is not None and _hi is not None:
            SERVICE[(_s, _a)] = (int(_lo), int(_hi))
        else:
            SERVICE[(_s, _a)] = (-50, 100) if _sg else (0, 100)


# Commando-bits: alleen deze bits mogen ooit omgezet worden, en alleen via lezen-aanpassen-schrijven,
# zodat de andere bits van het register blijven zoals ze zijn. 816 bevat ook o.a. "fabrieksinstellingen"
# (bit 13) en "koudemiddel terugwinnen" (bit 5): die blijven onaanraakbaar.
BIT_COMMANDS: dict[tuple[int, int], int] = {
    (1, 816): 1 << 4,     # Crew Control bit 4 "System Emptying mode" (排空) = watersysteem ontluchten
}


def bit_write_value(slave: int, reg: int, current: int, mask: int, on: bool) -> int:
    """Nieuwe registerwaarde; weigert alles wat buiten de toegestane bits zou veranderen."""
    allowed = BIT_COMMANDS.get((slave, reg), 0)
    if not mask or mask & ~allowed:
        raise ValueError(f"bits {mask:#06x} van {slave}/{reg} zijn niet schrijfbaar")
    if not 0 <= current <= 0xFFFF:
        raise ValueError("ongeldige huidige waarde")
    new = (current | mask) if on else (current & ~mask)
    if (new ^ current) & ~allowed:
        raise ValueError("zou andere bits veranderen")
    return new


@dataclass(frozen=True)
class Budget:
    min_gap_min: float
    per_hour: int
    per_day: int


BUDGETS: dict[tuple[int, int], Budget] = {
    (1, 769): Budget(15, 4, 40),
    (1, 773): Budget(10, 4, 12),
    (1, 775): Budget(20, 3, 16),
    (2, 6): Budget(30, 2, 12),
}
TOTAL_PER_HOUR = 10
TOTAL_PER_DAY = 80
MAX_CONSECUTIVE_FAILURES = 3


class WriteGuard:
    def __init__(self) -> None:
        self._log: dict[tuple[int, int], deque[float]] = {k: deque() for k in ALLOWED}
        self._all: deque[float] = deque()
        self.failures = 0

    def _prune(self, now: float) -> None:
        for q in (*self._log.values(), self._all):
            while q and now - q[0] > 86400:
                q.popleft()

    def check(self, slave: int, reg: int, value: int, now: float) -> str | None:
        """None = toegestaan, anders de reden waarom niet."""
        key = (slave, reg)
        if key not in ALLOWED:
            return "register staat niet op de allowlist"
        lo, hi = ALLOWED[key]
        if not lo <= value <= hi:
            return f"waarde {value} buiten {lo}..{hi}"
        self._prune(now)
        b, q = BUDGETS[key], self._log[key]
        if q and now - q[-1] < b.min_gap_min * 60:
            return f"minimaal {b.min_gap_min:g} min tussen wijzigingen"
        if sum(1 for t in q if now - t <= 3600) >= b.per_hour:
            return f"uurbudget ({b.per_hour}) op"
        if len(q) >= b.per_day:
            return f"dagbudget ({b.per_day}) op"
        if sum(1 for t in self._all if now - t <= 3600) >= TOTAL_PER_HOUR:
            return "totaal uurbudget op"
        if len(self._all) >= TOTAL_PER_DAY:
            return "totaal dagbudget op"
        return None

    def record(self, slave: int, reg: int, now: float, ok: bool) -> None:
        """Ook mislukte pogingen tellen mee voor het budget."""
        self._log[(slave, reg)].append(now)
        self._all.append(now)
        self.failures = 0 if ok else self.failures + 1

    @property
    def tripped(self) -> bool:
        return self.failures >= MAX_CONSECUTIVE_FAILURES

    def counts(self, now: float) -> dict[str, int]:
        self._prune(now)
        return {"laatste_uur": sum(1 for t in self._all if now - t <= 3600), "laatste_24u": len(self._all)}
