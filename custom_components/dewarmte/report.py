"""Uurrapport: verzamelt per regelcyclus en maakt elk uur een leesbare samenvatting (puur Python)."""
from __future__ import annotations

import time
from collections import Counter

MODE = {0: "Normaal", 1: "Boost", 2: "Stil"}


def _avg(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 1) if xs else None


def _fmt(x, unit="", nd=1) -> str:
    return "–" if x is None else f"{x:.{nd}f}{unit}"


class HourReport:
    def __init__(self) -> None:
        self.reset({})

    def reset(self, v: dict) -> None:
        self.start = time.time()
        self.n = 0
        self.outdoor: list[float] = []
        self.supply: list[float] = []
        self.target: list[float] = []
        self.dhw: list[float] = []
        self.running = 0
        self.demand = 0
        self.modes: Counter = Counter()
        self.blocks: Counter = Counter()
        self.actions: list[dict] = []
        self.refused: Counter = Counter()
        self.e0 = {k: v.get(k) for k in ("ao_pm_energy", "ao_hm_energy", "pt_pm_energy")}

    def sample(self, v: dict, d: dict) -> None:
        self.n += 1
        for lst, val in ((self.outdoor, d.get("outdoor_used")), (self.supply, v.get("t_supply")),
                         (self.target, d.get("supply_target")), (self.dhw, v.get("dhw_top"))):
            if val is not None:
                lst.append(float(val))
        self.running += bool(v.get("compressor_running"))
        self.demand += bool(d.get("heat_demand"))
        if v.get("power_mode") is not None:
            self.modes[MODE.get(int(v["power_mode"]), str(v["power_mode"]))] += 1
        for b in d.get("blocks", []):
            self.blocks[b] += 1
        for e in ("ao_pm_energy", "ao_hm_energy", "pt_pm_energy"):
            if self.e0.get(e) is None and v.get(e) is not None:
                self.e0[e] = v[e]

    def action(self, item: dict) -> None:
        self.actions.append(item)

    def refuse(self, name: str, reason: str) -> None:
        self.refused[f"{name}: {reason}"] += 1

    def build(self, v: dict, d: dict, active: bool, counts: dict) -> tuple[str, str, dict]:
        def delta(k):
            a, b = self.e0.get(k), v.get(k)
            return None if a is None or b is None or b < a else round(b - a, 2)

        el, th, el_t = delta("ao_pm_energy"), delta("ao_hm_energy"), delta("pt_pm_energy")
        cop = round(th / el, 2) if el and th is not None and el > 0.05 else None
        run_pct = round(100 * self.running / self.n) if self.n else None
        lines = []
        if not active:
            lines.append("⚠️ Regeling staat UIT (alleen meekijken).")
        if self.actions:
            lines.append("Gedaan:")
            for a in self.actions:
                mark = "✓" if a["ok"] else "✗"
                lines.append(f"  {mark} {a['time']} {a['name']}: {a['old']} → {a['new']}"
                             + (f" ({a['why']})" if a.get("why") else "") + ("" if a["ok"] else f" – {a['error']}"))
        else:
            lines.append("Geen wijzigingen nodig geweest.")
        lines.append(f"Buiten gem. {_fmt(_avg(self.outdoor), ' °C')} | aanvoer gem. {_fmt(_avg(self.supply), ' °C')}"
                     f" (doel {_fmt(_avg(self.target), ' °C')})")
        lines.append(f"Compressor {_fmt(run_pct, ' %', 0)} van het uur aan | warmtevraag "
                     f"{_fmt(round(100 * self.demand / self.n) if self.n else None, ' %', 0)}")
        if self.modes:
            lines.append("Modus: " + ", ".join(f"{m} {round(100 * c / self.n)}%" for m, c in self.modes.most_common()))
        lines.append(f"Pomp AO {_fmt(el, ' kWh', 2)} elektrisch, {_fmt(th, ' kWh', 2)} warmte, COP {_fmt(cop, '', 2)}"
                     f" | Pomp T {_fmt(el_t, ' kWh', 2)}")
        lines.append(f"Tapwater boven {_fmt(v.get('dhw_top'), ' °C', 0)}, setpoint {_fmt(v.get('pt_set'), ' °C', 0)}"
                     f" | nu: aanvoer {_fmt(d.get('supply_target'), ' °C')} gewenst, {d.get('power_mode_name', '–')}")
        if self.blocks:
            lines.append("Blokkades: " + ", ".join(f"{b} ({round(100 * c / self.n)}%)" for b, c in self.blocks.most_common(4)))
        if self.refused:
            lines.append("Uitgesteld: " + ", ".join(k for k, _ in self.refused.most_common(4)))
        lines.append(f"Schrijfacties: {counts.get('laatste_uur', 0)} dit uur, {counts.get('laatste_24u', 0)} in 24 u")
        title = time.strftime("DeWarmte %H:%M – ") + (f"{len(self.actions)} wijziging(en)" if self.actions else "geen wijzigingen")
        data = {"van": time.strftime("%H:%M", time.localtime(self.start)), "acties": self.actions,
                "kwh_el_ao": el, "kwh_th_ao": th, "cop_uur": cop, "compressor_pct": run_pct,
                "buiten_gem": _avg(self.outdoor), "aanvoer_gem": _avg(self.supply)}
        return title, "\n".join(lines), data
