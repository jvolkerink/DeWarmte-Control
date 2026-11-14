"""Registerkaart (alleen lezen) en decodering voor SunRain, Pomp T, vermogens- en warmtemeters.

Waarden zijn afgeleid uit de fabrikantbestanden en uit echte payloads van deze installatie.
"""
from __future__ import annotations

from dataclasses import dataclass

from .params import PARAMS

FAST, MEDIUM, CONFIG = 10, 30, 300  # polltiers in seconden

NO_FAULT = (0, 0xFFFF)  # 65535 wordt door de SunRain gemeld terwijl hij gewoon draait


@dataclass(frozen=True)
class Reg:
    key: str
    slave: int
    addr: int
    words: int = 1          # 2 = 32-bit (hoog woord eerst)
    scale: float = 1.0
    signed: bool = False
    invalid: tuple = ()     # gedecodeerde waarden die "geen sensor" betekenen


@dataclass(frozen=True)
class Block:
    slave: int
    start: int
    count: int
    tier: int
    label: str


SUN, PT, PM_AO, PM_PT, HM_AO, HM_PT = 1, 2, 5, 7, 14, 16

REGS: list[Reg] = [
    # --- SunRain (slave 1): meetwaarden
    Reg("run_state_1", SUN, 0), Reg("run_state_2", SUN, 1),
    Reg("relay_1", SUN, 25), Reg("relay_2", SUN, 26),
    Reg("switch_1", SUN, 29), Reg("switch_2", SUN, 30),
    Reg("fault_code", SUN, 33), Reg("subfault_code", SUN, 34),
    Reg("f_comp_set", SUN, 39), Reg("f_comp", SUN, 64), Reg("f_fan", SUN, 65),
    Reg("eev_steps", SUN, 66), Reg("v_ac", SUN, 68), Reg("i_ac", SUN, 69, scale=0.1),
    Reg("i_comp", SUN, 70, scale=0.1),
    Reg("t_sat_hi", SUN, 72, signed=True), Reg("t_sat_lo", SUN, 73, signed=True),
    Reg("t_ambient", SUN, 74, signed=True), Reg("t_coil_ext", SUN, 75, signed=True),
    Reg("t_coil_int", SUN, 76, signed=True), Reg("t_air_return", SUN, 77, signed=True),
    Reg("t_exhaust", SUN, 78, signed=True),
    Reg("t_return", SUN, 79, signed=True, invalid=(-50,)),
    Reg("t_supply", SUN, 80, signed=True, invalid=(-50,)),
    Reg("pump_pwm", SUN, 87), Reg("flow_raw", SUN, 88),
    Reg("t_user_return", SUN, 89, signed=True, invalid=(-50,)),
    # --- SunRain: instellingen (alleen lezen!)
    Reg("set_cooling", SUN, 768), Reg("set_heating", SUN, 769),
    Reg("mode", SUN, 772), Reg("on_off", SUN, 773), Reg("power_mode", SUN, 775),
    Reg("control_method", SUN, 372),
    Reg("hyst_ac", SUN, 282), Reg("hyst_floor", SUN, 283),
    Reg("heat_upper_limit", SUN, 366), Reg("heat_lower_limit", SUN, 367),
    Reg("flow_low_protection", SUN, 390),
    # --- Pomp T (slave 2)
    Reg("pt_err1", PT, 50), Reg("pt_err2", PT, 51),
    Reg("dhw_bottom", PT, 52, signed=True), Reg("dhw_top", PT, 53, signed=True),
    Reg("t_source_in", PT, 56, signed=True), Reg("t_source_out", PT, 58, signed=True),
    Reg("disinfection", PT, 60), Reg("pt_err3", PT, 62),
    Reg("pt_on_off", PT, 1), Reg("pt_heater_manual", PT, 5), Reg("pt_set", PT, 6),
    Reg("pt_restart_diff", PT, 7), Reg("pt_heater_temp", PT, 8), Reg("pt_heater_delay", PT, 9),
    Reg("legionella_temp", PT, 10), Reg("legionella_duration", PT, 11),
    Reg("pt_inlet_low_protection", PT, 13), Reg("legionella_hour", PT, 19),
    Reg("pt_inlet_high_protection", PT, 20), Reg("pt_outlet_low_protection", PT, 21),
    Reg("legionella_period", PT, 22), Reg("pt_set_pv", PT, 24), Reg("pt_set_grid", PT, 25),
    Reg("pt_heater_on_error", PT, 26), Reg("pt_hysteresis", PT, 27),
]

# Vermogensmeters (slave 5 = Pomp AO, 7 = Pomp T) en Mainone-warmtemeters (14 = AO, 16 = T)
for _slave, _p in ((PM_AO, "ao"), (PM_PT, "pt")):
    REGS += [
        Reg(f"{_p}_pm_energy", _slave, 0, 2, 0.01),
        Reg(f"{_p}_pm_v", _slave, 12, scale=0.1), Reg(f"{_p}_pm_i", _slave, 13, scale=0.01),
        Reg(f"{_p}_pm_w", _slave, 14), Reg(f"{_p}_pm_pf", _slave, 16, scale=0.001),
        Reg(f"{_p}_pm_hz", _slave, 17, scale=0.01),
    ]
for _slave, _p in ((HM_AO, "ao"), (HM_PT, "pt")):
    REGS += [
        Reg(f"{_p}_hm_energy", _slave, 0, 2, 0.01),
        Reg(f"{_p}_hm_t_in", _slave, 4, 2, 0.01), Reg(f"{_p}_hm_t_ret", _slave, 6, 2, 0.01),
        Reg(f"{_p}_hm_t_diff", _slave, 8, 2, 0.01),
        Reg(f"{_p}_hm_flow", _slave, 12, 2, 0.001667),   # L/min
        Reg(f"{_p}_hm_kw", _slave, 14, 2, 0.01),
        Reg(f"{_p}_hm_error", _slave, 16),
    ]

BLOCKS: list[Block] = [
    # snel: status en meetwaarden die de regeling gebruikt
    Block(SUN, 0, 15, FAST, "SunRain status"), Block(SUN, 25, 16, FAST, "SunRain relais/storing"),
    Block(SUN, 63, 47, FAST, "SunRain meetwaarden"),
    Block(PT, 50, 4, FAST, "Pomp T fouten/tank"), Block(PT, 56, 1, FAST, "Pomp T bron in"),
    Block(PT, 58, 1, FAST, "Pomp T bron uit"), Block(PT, 60, 1, FAST, "Pomp T desinfectie"),
    # middel: setpoints, modus, meters
    Block(SUN, 768, 11, MEDIUM, "SunRain setpoint/modus"),
    Block(SUN, 114, 4, MEDIUM, "SunRain extra meetwaarden"), Block(SUN, 240, 5, MEDIUM, "SunRain sensoren 2"),
    Block(PT, 62, 1, MEDIUM, "Pomp T fouten 3"),
    Block(PM_AO, 0, 18, MEDIUM, "Meter AO"), Block(PM_PT, 0, 18, MEDIUM, "Meter T"),
    Block(HM_AO, 0, 18, MEDIUM, "Warmtemeter AO"), Block(HM_PT, 0, 18, MEDIUM, "Warmtemeter T"),
    # traag: alle instellingen (alleen gelezen)
    Block(SUN, 250, 125, CONFIG, "SunRain parameters 250-374"), Block(SUN, 375, 28, CONFIG, "SunRain parameters 375-402"),
    Block(SUN, 780, 11, CONFIG, "SunRain curves 780-790"),
    Block(SUN, 815, 5, CONFIG, "SunRain parameters 815-819"), Block(SUN, 822, 2, CONFIG, "SunRain parameters 822-823"),
    Block(SUN, 826, 2, CONFIG, "SunRain parameters 826-827"), Block(SUN, 830, 1, CONFIG, "SunRain ventilator"),
    Block(SUN, 835, 3, CONFIG, "SunRain pomp/PFC"), Block(SUN, 864, 4, CONFIG, "SunRain versie"),
    Block(SUN, 2048, 16, CONFIG, "SunRain sterilisatie/retour"),
    Block(PT, 1, 1, CONFIG, "Pomp T aan/uit"), Block(PT, 5, 7, CONFIG, "Pomp T instellingen"),
    Block(PT, 13, 1, CONFIG, "Pomp T bronbeveiliging laag"), Block(PT, 19, 4, CONFIG, "Pomp T desinfectie/beveiliging"),
    Block(PT, 24, 4, CONFIG, "Pomp T PV/net"),
    Block(HM_AO, 24, 7, CONFIG, "Warmtemeter AO druk"), Block(HM_PT, 24, 7, CONFIG, "Warmtemeter T druk"),
]

TIER_BY_ADDR: dict[tuple[int, int], int] = {}
for _b in BLOCKS:
    for _a in range(_b.start, _b.start + _b.count):
        TIER_BY_ADDR[(_b.slave, _a)] = min(_b.tier, TIER_BY_ADDR.get((_b.slave, _a), 10**9))


def max_age(tier: int) -> float:
    return 3 * tier + 30 if tier < CONFIG else 3600.0


def decode(reg: Reg, words: dict[int, int]) -> float | None:
    """words: {adres: ruwe 16-bit waarde} voor één slave."""
    if reg.words == 2:
        if reg.addr not in words or reg.addr + 1 not in words:
            return None
        value = ((words[reg.addr] << 16) | words[reg.addr + 1]) * reg.scale
    else:
        if reg.addr not in words:
            return None
        raw = words[reg.addr]
        if reg.signed and raw >= 0x8000:
            raw -= 0x10000
        value = raw * reg.scale
    value = round(value, 4)
    return None if value in reg.invalid else value


def bit(value: float | None, n: int) -> bool | None:
    return None if value is None else bool((int(value) >> n) & 1)


def run_state_text(rs1: float | None) -> str:
    if rs1 is None:
        return "onbekend"
    checks = [(8, "Ontdooien"), (13, "Storingsstop"), (12, "Constante-temperatuurstop"),
              (14, "Draait"), (15, "Wacht op start"), (1, "Antivries 1"), (2, "Antivries 2")]
    for n, text in checks:
        if bit(rs1, n):
            return text
    return "Uit/standby"


def decode_all(store: dict[tuple[int, int], tuple[int, float]], now: float) -> dict[str, float | None]:
    """store: {(slave, adres): (ruwe waarde, monotonic tijdstip)} -> {sleutel: waarde}."""
    per_slave: dict[int, dict[int, int]] = {}
    for (slave, addr), (raw, ts) in store.items():
        tier = TIER_BY_ADDR.get((slave, addr), CONFIG)
        if now - ts <= max_age(tier):
            per_slave.setdefault(slave, {})[addr] = raw
    out = {reg.key: decode(reg, per_slave.get(reg.slave, {})) for reg in REGS}
    out["raw"] = decode_raw(per_slave)
    # afgeleide waarden
    # Warmtemeter met foutcode (bijv. 128 = geen ultrasoon signaal, vaak lucht in de leiding): flow en
    # vermogen zijn dan 0 maar niet echt. Liever 'onbekend' dan een valse 0.
    for _p in ("ao", "pt"):
        err = out.get(f"{_p}_hm_error")
        if err:
            for k in ("flow", "kw"):
                if f"{_p}_hm_{k}" in out:
                    out[f"{_p}_hm_{k}"] = None
    out["flow_ao"] = out.get("ao_hm_flow")
    # Circulatiepomp: relaisbit van de SunRain zelf; warmtemeter alleen als reserve.
    r1 = out.get("relay_1")
    out["circulation_ao"] = bit(r1, 6) if r1 is not None else (
        None if out["flow_ao"] is None else out["flow_ao"] > 1.0)
    out["t_ambient_sun"] = out.get("t_ambient")
    out["fault_active"] = None
    fc, sf = out.get("fault_code"), out.get("subfault_code")
    if fc is not None and sf is not None:
        out["fault_active"] = (int(fc) not in NO_FAULT) or (int(sf) not in NO_FAULT)
    f = out.get("f_comp")
    out["compressor_running"] = None if f is None else f > 0
    out["defrost"] = bit(out.get("run_state_1"), 8)
    pw, th = out.get("ao_pm_w"), out.get("ao_hm_kw")
    out["cop_ao"] = None
    if pw is not None and th is not None and pw >= 150 and (f or 0) > 0:
        out["cop_ao"] = round(th * 1000 / pw, 2)
    return out


# ---------------------------------------------------------------- alle registers (ruw)
@dataclass(frozen=True)
class Raw:
    key: str
    slave: int
    addr: int
    bit: int | str | None
    words: int
    scale: float
    signed: bool
    name: str
    unit: str | None
    writable: bool


RAW: list[Raw] = []
for _s, _a, _b, _w, _sc, _sg, _n, _u, _wr, _lo, _hi in PARAMS:
    _k = f"r{_s}_{_a}" + ("" if _b is None else f"_{_b}")
    RAW.append(Raw(_k, _s, _a, _b, _w, _sc, _sg, _n, _u, bool(_wr)))


def decode_raw(per_slave: dict[int, dict[int, int]]) -> dict[str, float | bool | None]:
    out: dict[str, float | bool | None] = {}
    for r in RAW:
        words = per_slave.get(r.slave, {})
        if r.bit is None:
            out[r.key] = decode(Reg(r.key, r.slave, r.addr, r.words, r.scale, r.signed), words)
        elif r.addr not in words:
            out[r.key] = None
        elif r.bit == "lo":
            out[r.key] = words[r.addr] & 0xFF
        elif r.bit == "hi":
            out[r.key] = words[r.addr] >> 8
        else:
            out[r.key] = bool((words[r.addr] >> int(r.bit)) & 1)
    return out
