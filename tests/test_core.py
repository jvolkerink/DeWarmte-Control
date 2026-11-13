"""Draai met:  python3 tests/test_core.py   (geen Home Assistant of pytest nodig)."""
import asyncio, importlib.util, sys, types, os

PKG = os.path.join(os.path.dirname(__file__), "..", "custom_components", "dewarmte")
pkg = types.ModuleType("dw"); pkg.__path__ = [PKG]; sys.modules["dw"] = pkg
def load(name):
    spec = importlib.util.spec_from_file_location(f"dw.{name}", os.path.join(PKG, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec); sys.modules[f"dw.{name}"] = mod; spec.loader.exec_module(mod); return mod
bus, regs, eng = load("bus"), load("registers"), load("engine")

# ---- Modbus-framing (zelfde testframe als modbus_test.sh: slave 2, reg 52, 1 woord)
def test_crc_matches_known_frame():
    assert bus.build_read_request(2, 52, 1).hex(" ") == "02 03 00 34 00 01 c5 f7"

# ---- Nep-DR164 met registerwaarden uit de echte payload
SUN = {0: 4096, 33: 65535, 34: 65535, 39: 60, 64: 39, 74: 9, 79: 22, 80: 23, 89: 65486, 26: 161}
class FakeDR164:
    def __init__(self, bus_, regsmap, bad=(), silent=False):
        self.bus, self.map, self.bad, self.silent, self.sent = bus_, regsmap, set(bad), silent, []
    async def send(self, frame):
        self.sent.append(frame)
        if self.silent: return
        slave, start, count = frame[0], int.from_bytes(frame[2:4], "big"), int.from_bytes(frame[4:6], "big")
        if any(a in self.bad for a in range(start, start + count)):
            self.bus.feed(bus.add_crc(bytes([slave, 0x83, 2]))); return
        words = b"".join(self.map.get((slave, a), 0).to_bytes(2, "big") for a in range(start, start + count))
        resp = bus.add_crc(bytes([slave, 3, 2 * count]) + words)
        self.bus.feed(resp[:4]); self.bus.feed(resp[4:])      # gesegmenteerd aanleveren

def make_bus(**kw):
    holder = {}
    async def send(frame): await holder["dev"].send(frame)
    b = bus.ModbusBus(send, timeout=kw.pop("timeout", 0.2), quiet=0.05, gap=0.0)
    holder["dev"] = FakeDR164(b, {(1, a): v for a, v in SUN.items()}, **kw)
    return b, holder["dev"]

def test_read_and_segmented_frames():
    async def go():
        b, _ = make_bus()
        assert await b.read_holding(1, 79, 2) == [22, 23]
    asyncio.run(go())

def test_adaptive_split_on_exception():
    async def go():
        b, _ = make_bus(bad=[65])
        out, failed = await bus.read_registers(b, 1, 63, 4)
        assert failed == [65] and out[64] == 39 and out[63] == 0 and out[66] == 0
    asyncio.run(go())

def test_timeout_is_not_split_and_late_frame_ignored():
    async def go():
        b, dev = make_bus(silent=True)
        try:
            await b.read_holding(1, 79, 2); assert False
        except bus.ModbusTimeout: pass
        b.feed(bus.add_crc(bytes([1, 3, 4, 0, 22, 0, 23])))   # laat frame: genegeerd
        assert len(dev.sent) == 1
    asyncio.run(go())

# ---- Decodering met echte payloads
def test_decode_sunrain_and_signed():
    store = {(1, a): (v, 100.0) for a, v in SUN.items()}
    v = regs.decode_all(store, 100.0)
    assert v["t_supply"] == 23 and v["t_return"] == 22 and v["t_ambient"] == 9
    assert v["t_user_return"] is None                      # 65486 = -50 = geen sensor
    assert v["fault_active"] is False                      # 65535 = geen storing
    assert v["compressor_running"] is True and regs.run_state_text(v["run_state_1"]) == "Constante-temperatuurstop"

def test_decode_negative_outdoor():
    v = regs.decode_all({(1, 74): (65526, 0.0)}, 0.0)       # -10 °C
    assert v["t_ambient"] == -10

def test_decode_meters_from_real_payload():
    store = {}
    for a, x in {4: 0, 5: 2090, 6: 0, 7: 2049, 8: 0, 9: 41, 12: 0, 13: 7006, 14: 0, 15: 33}.items(): store[(14, a)] = (x, 0.0)
    for a, x in {12: 2316, 13: 224, 14: 390, 16: 753, 17: 5002}.items(): store[(5, a)] = (x, 0.0)
    store[(64 and 1, 64)] = (39, 0.0)
    v = regs.decode_all(store, 0.0)
    assert v["ao_hm_t_in"] == 20.9 and v["ao_hm_t_ret"] == 20.49 and v["ao_hm_t_diff"] == 0.41
    assert abs(v["ao_hm_flow"] - 11.68) < 0.01 and v["ao_hm_kw"] == 0.33
    assert v["ao_pm_w"] == 390 and v["ao_pm_v"] == 231.6 and v["ao_pm_hz"] == 50.02
    assert v["cop_ao"] == round(0.33 * 1000 / 390, 2)

def test_stale_values_dropped():
    assert regs.decode_all({(1, 64): (39, 0.0)}, 500.0)["f_comp"] is None

# ---- Engine
def rooms(valve=0.0, cur=20.0, tgt=20.5):
    return [eng.Room("kamer", True, "heat", cur, tgt, valve)]
def inp(now, **kw):
    base = dict(now=now, wall=1.7e9 + now, outdoor=0.0, rooms=rooms(60, 19.5, 20.5), f_comp=30, t_supply=30, t_return=28,
                on_773=0, set_769=25, power_775=2, critical_ok=True, dhw_top=45, dhw_set=45, price=0.25)
    base.update(kw); return eng.Inputs(**base)

def test_demand_needs_delay_and_curve():
    e = eng.Engine(eng.Settings())
    assert e.update(inp(0))["heat_demand"] is False
    d = e.update(inp(130))
    assert d["heat_demand"] is True
    assert d["base_curve"] == 31.0                       # 31 - 0,5 x 0
    assert d["supply_target"] == 31.0                    # geen directe kamerboost meer
    assert d["unit_on_desired"] is True

def run(e, t0, t1, step=60, **kw):
    d = None
    for t in range(t0, t1, step):
        d = e.update(inp(t, **kw))
    return d

def test_target_never_above_max():
    e = eng.Engine(eng.Settings(max_supply=40.0))
    d = run(e, 0, 6 * 3600, outdoor=-10, rooms=rooms(100, 15, 21))       # hele middag koud met klep open
    assert d["trim"] == 4.0 and d["supply_target"] == 40.0                # 36 + 4 begrensd op 40

# ---- 3.3: rustige regeling
def test_trim_slow_steps_up_and_down():
    e = eng.Engine(eng.Settings())
    d = run(e, 0, 30 * 60 + 60, rooms=rooms(100, 19.5, 20.5))
    assert d["trim"] == 0.5                                              # één stap per half uur
    d = run(e, 31 * 60, 61 * 60 + 60, rooms=rooms(100, 19.5, 20.5))
    assert d["trim"] == 1.0
    d = run(e, 62 * 60, 4 * 3600, rooms=rooms(40, 20.6, 20.5))           # klep weinig open, geen tekort
    assert d["trim"] < 0 and d["supply_target"] < 31.0

def test_trim_holds_between_60_and_90_percent():
    e = eng.Engine(eng.Settings())
    d = run(e, 0, 3 * 3600, rooms=rooms(75, 20.3, 20.5))
    assert d["trim"] == 0.0

def test_preset_step_feedforward_and_freeze():
    e = eng.Engine(eng.Settings())
    run(e, 0, 6 * 3600, rooms=rooms(70, 19.5, 19.5))                     # gebruikelijk doel 19,5
    d = run(e, 6 * 3600, 6 * 3600 + 50 * 60, rooms=rooms(100, 19.5, 20.5))   # goedkoop: +1 K
    assert d["feedforward"] == 0.5 and d["trim"] == 0.0                  # meteen +0,5, klepbijsturing wacht
    assert "wacht" in d["trim_reason"]

def test_trv_sensor_room_counts_less():
    good = eng.Engine(eng.Settings()); trv = eng.Engine(eng.Settings())
    r_good = [eng.Room("k", True, "heat", 19.5, 20.5, 100)]
    r_trv = [eng.Room("k", True, "heat", 19.5, 20.5, 100, trv_sensor=True)]
    for t in range(0, 20 * 60, 60):
        dg = good.update(inp(t, rooms=[eng.Room("k", True, "heat", 20.5, 20.5, 100)] if t == 0 else r_good))
        dt = trv.update(inp(t, rooms=[eng.Room("k", True, "heat", 20.5, 20.5, 100, trv_sensor=True)] if t == 0 else r_trv))
    assert dg["worst_deficit"] > 0.9 and dt["worst_deficit"] < 0.25      # snelle dip telt bij TRV-sensor nauwelijks

def test_offset_reset_and_symmetric_learning():
    e = eng.Engine(eng.Settings(), {"offset": 4.0, "last_hot": None})    # oude, vastgelopen offset
    assert e.offset == 0.0
    e.trim = 2.0
    run(e, 0, 6 * 3600 + 120, rooms=rooms(75, 20.4, 20.5))
    assert e.offset == 0.5 and e.trim == 1.5
    e.trim = -2.0
    run(e, 6 * 3600 + 120, 12 * 3600 + 240, rooms=rooms(75, 20.4, 20.5))
    assert e.offset == 0.0                                                # omlaag even makkelijk als omhoog
    e2 = eng.Engine(eng.Settings(), e.export_state())
    assert e2.trim == e.trim and e2.offset == e.offset

def test_silent_above_3_even_with_shortfall():
    e = eng.Engine(eng.Settings())
    for t in range(0, 3000, 30):
        d = e.update(inp(t, outdoor=8.0, t_supply=20))
    assert d["power_mode_desired"] == eng.SILENT

def test_cold_escalates_then_deescalates():
    e = eng.Engine(eng.Settings())
    seen = []
    for t in range(0, 7200, 30):                          # aanhoudend tekort van >3 K bij -5 °C
        seen.append(e.update(inp(t, outdoor=-5.0, rooms=rooms(80, 19, 21), t_supply=27))["power_mode_desired"])
    assert eng.NORMAL in seen and seen[-1] == eng.BOOST
    for t in range(7200, 12000, 30):                      # setpoint gehaald
        d = e.update(inp(t, outdoor=-5.0, rooms=rooms(80, 19, 21), f_comp=30, t_supply=40))
    assert d["power_mode_desired"] == eng.SILENT

def test_hysteresis_2_3_degrees():
    e = eng.Engine(eng.Settings()); e.update(inp(0, outdoor=1.0))
    assert e.cold is True
    e.update(inp(30, outdoor=2.5)); assert e.cold is True      # tussen 2 en 3: blijft zoals het was
    e.update(inp(60, outdoor=3.5)); assert e.cold is False

def test_no_writes_without_fresh_critical_data_and_fault_block():
    e = eng.Engine(eng.Settings())
    e.update(inp(0)); d = e.update(inp(130, critical_ok=False, fault=True))
    assert "kritieke Modbus-data niet vers" in d["blocks"] and "storing actief" in d["blocks"]

def test_dhw_tiers_and_floor_and_negative():
    for price, want in ((0.10, 60), (0.22, 55), (0.28, 50), (0.40, 45), (-0.02, 70)):
        e = eng.Engine(eng.Settings()); d = e.update(inp(0, price=price))
        assert d["dhw_setpoint_advice"] == want, (price, d["dhw_setpoint_advice"])
    e = eng.Engine(eng.Settings()); assert e.update(inp(0, price=None))["dhw_setpoint_advice"] == 45

def test_source_support_when_pomp_t_runs_on_cold_return():
    e = eng.Engine(eng.Settings())
    d = e.update(inp(0, outdoor=12.0, rooms=rooms(0, 21, 20.5), pomp_t_power_w=300, t_source_in=15))
    assert d["source_support"] is True and d["supply_target"] == 30.0 and d["unit_on_desired"] is True

def test_legionella_tracking():
    e = eng.Engine(eng.Settings()); e.update(inp(0, dhw_top=61))
    d = e.update(inp(86400 * 15, dhw_top=45)); assert d["legionella_days"] == 15.0 and d["legionella_warning"] is True

def test_summer_no_heating_season():
    e = eng.Engine(eng.Settings()); e.update(inp(0, outdoor=20))
    d = e.update(inp(200, outdoor=20)); assert d["season_heating"] is False and d["supply_target"] is None

def test_stop_after_delay_when_warm_outside():
    e = eng.Engine(eng.Settings())
    e.update(inp(0, outdoor=12, on_773=0, rooms=rooms(0, 21, 20.5)))
    e.update(inp(10, outdoor=12, on_773=1, rooms=rooms(0, 21, 20.5)))       # flank: net aangezet
    d = e.update(inp(1000, outdoor=12, on_773=1, rooms=rooms(0, 21, 20.5)))
    assert d["unit_on_desired"] is None                                      # nog binnen 45 min uitstel
    d = e.update(inp(10 + 3000, outdoor=12, on_773=1, rooms=rooms(0, 21, 20.5)))
    assert d["unit_on_desired"] is False and any(p["register"] == 773 and p["value"] == 0 for p in d["planned_writes"])

def test_base_load_keeps_unit_on_when_cold_without_trv_demand():
    e = eng.Engine(eng.Settings())
    d = e.update(inp(0, outdoor=4, on_773=1, rooms=rooms(0, 21, 20.5)))
    assert d["base_load"] is True and d["unit_on_desired"] is True and d["supply_target"] == 29.0
    assert not any(p["register"] == 773 for p in d["planned_writes"])

# ---- Schrijven
wr = load("writes")

def test_fc06_frame_and_echo():
    req = bus.build_write_request(1, 769, 30)
    assert req[:6].hex(" ") == "01 06 03 01 00 1e" and bus.valid_crc(req)
    bus.parse_write_response(req, req)
    try:
        bus.parse_write_response(bus.add_crc(bytes([1, 0x86, 2])), req); assert False
    except bus.ModbusException: pass

def test_bus_refuses_registers_outside_allowlist():
    async def go():
        sent = []
        async def send(f): sent.append(f)
        b = bus.ModbusBus(send, timeout=0.05, quiet=0.0, gap=0.0)
        for slave, reg, val in ((1, 390, 10), (1, 769, 60), (2, 10, 60), (1, 286, 1)):
            try:
                await b.write_single(slave, reg, val); assert False
            except bus.ModbusError as ex:
                assert "geweigerd" in str(ex)
        assert sent == []                                 # er is niets verzonden
    asyncio.run(go())

def test_write_budget():
    g = wr.WriteGuard()
    assert g.check(1, 769, 30, 0) is None
    g.record(1, 769, 0, True)
    assert "minimaal" in g.check(1, 769, 31, 60)
    for i in range(1, 4):
        assert g.check(1, 769, 30, i * 900) is None; g.record(1, 769, i * 900, True)
    assert "uurbudget" in g.check(1, 769, 30, 3600)
    assert g.check(1, 769, 30, 3601) is None
    assert g.check(1, 390, 1, 0) == "register staat niet op de allowlist"
    assert "buiten" in g.check(2, 6, 75, 0)
    for _ in range(3): g.record(2, 6, 99999, False)
    assert g.tripped

def test_hour_report_text():
    rp = load("report").HourReport()
    v = {"t_supply": 30, "compressor_running": True, "power_mode": 2, "ao_pm_energy": 100.0, "ao_hm_energy": 400.0, "dhw_top": 50, "pt_set": 50}
    d = {"outdoor_used": 4, "supply_target": 29.0, "heat_demand": True, "blocks": [], "power_mode_name": "Stil"}
    rp.reset(v); rp.sample(v, d)
    rp.action({"time": "13:05", "name": "aanvoersetpoint", "old": "28 °C", "new": "29 °C", "why": "stooklijn", "ok": True, "error": None})
    title, text, data = rp.build({**v, "ao_pm_energy": 100.5, "ao_hm_energy": 402.0}, d, True, {"laatste_uur": 1, "laatste_24u": 3})
    assert "1 wijziging" in title and "28 °C → 29 °C" in text and data["cop_uur"] == 4.0


# ---- 3.4: prijsplanner
pl = load("planner")
from datetime import datetime as _dt, timedelta as _td

def _prices(start, values, slot=3600):
    return [{"start_time": _dt.fromtimestamp(start + i * slot).isoformat(), "price": v} for i, v in enumerate(values)]

def _day_start():
    d = _dt.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return d.timestamp()

def test_planner_preheats_before_expensive_and_sets_back_in_expensive():
    t0 = _day_start()
    p = pl.PricePlanner(pl.PlanSettings())
    vals = [0.30] * 12 + [0.25, 0.25, 0.25] + [0.50] * 5 + [0.30] * 28     # 12:00-15:00 goedkoop, 15-20 duur
    p.set_prices(_prices(t0, vals), t0)
    off_cheap, why = p.raw_offset(t0 + 13.5 * 3600)
    off_exp, _ = p.raw_offset(t0 + 15.5 * 3600)
    assert off_cheap > 0.5 and "voorverwarmen" in why
    assert off_exp < 0
    assert -1.0 <= off_exp and off_cheap <= 1.5                                # binnen de band

def test_planner_negative_price_max_preheat_and_rank():
    t0 = _day_start()
    p = pl.PricePlanner(pl.PlanSettings())
    p.set_prices(_prices(t0, [0.3] * 10 + [-0.05] + [0.3] * 30), t0)
    assert p.raw_offset(t0 + 10 * 3600 + 600)[0] == 1.5
    assert p.rank_now(t0 + 10 * 3600 + 600) == 0.0

def test_planner_hold_prevents_flapping():
    t0 = _day_start() + 3600
    p = pl.PricePlanner(pl.PlanSettings())
    vals = [0.30, 0.20, 0.40, 0.20, 0.40, 0.20, 0.40] * 6                     # wild op en neer
    p.set_prices(_prices(t0 - 3600, vals, 900), t0)
    changes, last = 0, None
    for i in range(0, 4 * 3600, 60):
        o = p.update_offset(t0 + i)
        if last is not None and o != last:
            changes += 1
        last = o
    assert changes <= 4 * 60 / 45 + 1                                           # hooguit eens per 45 min

def test_planner_room_targets_night_presence_and_override():
    p = pl.PricePlanner(pl.PlanSettings())
    presets = {"comfort_temp": 20.5, "eco_temp": 19.0, "comfort_away_temp": 19.5, "eco_away_temp": 18.0}
    day = _dt.now().replace(hour=14, minute=0, second=0, microsecond=0).timestamp()
    night = _dt.now().replace(hour=2, minute=0, second=0, microsecond=0).timestamp()
    p.offset = 1.0
    assert p.room_target("k", presets, True, day, None)[0] == 21.5
    assert p.room_target("k", presets, False, day, None)[0] == 20.5
    assert p.room_target("k", presets, True, night, None)[0] == 20.0
    p.offset = -1.0
    assert p.room_target("k", presets, True, day, None)[0] == 19.5
    p.wrote("k", 19.5, day)
    tgt, why = p.room_target("k", presets, True, day + 600, 21.0)                # iemand zette 21 °C
    assert tgt is None and "handmatig" in why
    assert p.room_target("k", presets, True, day + 4 * 3600, 21.0)[0] == 19.5   # na max. 3 u weer sturen

def test_planner_learns_cooling_window():
    p = pl.PricePlanner(pl.PlanSettings())
    t = 1_000_000.0
    for i in range(0, 3 * 3600, 300):                                            # 0,5 K/u afkoelen, klep dicht
        p.learn("k", 20.0 - 0.5 * i / 3600, 0, t + i)
    assert abs(p.cool_rate["k"] - 0.5) < 0.05 and abs(p.window_h - 2.0) < 0.2
    p2 = pl.PricePlanner(pl.PlanSettings(), p.export_state())
    assert p2.cool_rate == p.cool_rate

def test_engine_dhw_uses_price_rank_and_plan_feedforward():
    e = eng.Engine(eng.Settings())
    d = e.update(inp(0, dhw_top=46, dhw_set=45, price=0.30, price_rank=0.05, plan_offset=1.0))
    assert d["dhw_setpoint_advice"] == 60.0 and d["feedforward"] == 0.5
    e = eng.Engine(eng.Settings())
    d = e.update(inp(0, dhw_top=46, dhw_set=45, price=0.30, price_rank=0.6))
    assert d["dhw_setpoint_advice"] == 45.0


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try: fn(); print("PASS", name)
            except Exception as ex:
                fails += 1; print("FAIL", name, repr(ex))
    sys.exit(1 if fails else 0)
