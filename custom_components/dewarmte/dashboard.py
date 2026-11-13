"""Dashboard-generator: schrijft /config/dewarmte/dashboard.yaml met de echte entity-id's.

De entity-id's worden bij elke start opgezocht in het entiteitenregister, dus het dashboard klopt
ook als je entiteiten hernoemt. Kaarten: standaard + Mushroom + ApexCharts (HACS).
"""
from __future__ import annotations

from collections.abc import Callable

from .registers import RAW
from .schema import H, SPOTS, W
from .writes import SERVICE

Resolve = Callable[[str, str], str]

SERVICE_GROUPS = [
    ("Pomp T – tapwater", 2, 0, 99),
    ("SunRain – setpoints en modus", 1, 768, 799),
    ("SunRain – stooklimieten, compensatie, hysterese", 1, 250, 285),
    ("SunRain – ontdooien", 1, 286, 292),
    ("SunRain – beveiligingen en schakelaars", 1, 293, 340),
    ("SunRain – vorst, compressor, ventilator, pomp", 1, 341, 402),
    ("SunRain – ventilator/pomp handmatig", 1, 800, 899),
    ("SunRain – sterilisatie en retour", 1, 2048, 2099),
]


def _label(x: int, y: int, entity: str, **extra) -> dict:
    el = {"type": "state-label", "entity": entity,
          "style": {"left": f"{x / W * 100:.2f}%", "top": f"{(y + 9) / H * 100:.2f}%", "color": "#ffffff",
                    "font-weight": "bold", "font-size": "clamp(8px, 1.15vw, 15px)", "white-space": "nowrap",
                    "pointer-events": "auto"}}
    el.update(extra)
    return el


def _overlay(src: str, entity: str) -> dict:
    return {"type": "conditional", "conditions": [{"entity": entity, "state": "on"}],
            "elements": [{"type": "image", "image": f"/local/dewarmte/{src}", "tap_action": {"action": "none"},
                          "style": {"left": "50%", "top": "50%", "width": "100%"}}]}


def schema_card(r: Resolve) -> dict:
    elements = [_overlay("flow_ao.svg", r("binary_sensor", "circulation_ao")),
                _overlay("flow_pt.svg", r("binary_sensor", "pomp_t_running")),
                _overlay("compressor.svg", r("binary_sensor", "compressor")),
                _overlay("storing.svg", r("binary_sensor", "fault"))]
    for x, y, _w, _label_txt, platform, key in SPOTS:
        elements.append(_label(x, y, r(platform, key)))
    return {"type": "picture-elements", "image": "/local/dewarmte/hydrauliek.svg", "elements": elements}


def _press(entity: str, name: str, icon: str, confirm: str | None = None) -> dict:
    action = {"action": "perform-action", "perform_action": "button.press", "target": {"entity_id": entity}}
    if confirm:
        action["confirmation"] = {"text": confirm}
    return {"type": "button", "entity": entity, "name": name, "icon": icon, "show_state": False,
            "tap_action": action, "hold_action": {"action": "more-info"}}


def _apex(title: str, series: list[dict], hours: int = 12, height: int = 230, yaxis: list | None = None,
          **extra) -> dict:
    """Leesbare standaard: korte periode, zoombaar, geen dubbele legenda, nette assen."""
    card = {"type": "custom:apexcharts-card", "graph_span": f"{hours}h", "update_interval": "1min",
            "header": {"show": True, "title": title, "show_states": True, "colorize_states": True},
            "all_series_config": {"stroke_width": 1.5, "curve": "smooth", "show": {"legend_value": False}},
            "series": series,
            "yaxis": yaxis or [{"decimals": 0, "apex_config": {"tickAmount": 4}}],
            "apex_config": {"chart": {"height": height, "zoom": {"enabled": True},
                                      "toolbar": {"show": True, "tools": {"zoom": True, "zoomin": True, "zoomout": True,
                                                                          "reset": True, "pan": True, "download": False,
                                                                          "selection": False}}},
                            "legend": {"show": False}, "grid": {"strokeDashArray": 3},
                            "xaxis": {"labels": {"datetimeFormatter": {"hour": "HH:mm"}}}}}
    card.update(extra)
    return card


def _s(entity: str, name: str, minutes: int = 5, **kw) -> dict:
    d = {"entity": entity, "name": name, "group_by": {"func": "avg", "duration": f"{minutes}min"}}
    d.update(kw)
    return d


def build(r: Resolve, rooms: list[str]) -> dict:
    s = lambda k: r("sensor", k)          # noqa: E731
    b = lambda k: r("binary_sensor", k)   # noqa: E731

    def tile(entity, name=None, icon=None, **kw):
        c = {"type": "custom:mushroom-entity-card", "entity": entity, "fill_container": True}
        if name:
            c["name"] = name
        if icon:
            c["icon"] = icon
        c.update(kw)
        return c

    report_md = {"type": "markdown", "title": "Laatste uurrapport",
                 "content": "**{{ states('%s') }}**\n\n{{ state_attr('%s', 'tekst') or 'Nog geen rapport (komt elk heel uur).' }}"
                            % (s("report"), s("report"))}
    actions_md = {"type": "markdown", "title": "Laatste acties",
                  "content": "{%% set a = state_attr('%s', 'acties') or [] %%}{%% for x in a | reverse %%}"
                             "{{ '✓' if x.ok else '✗' }} {{ x.time }} **{{ x.name }}** {{ x.old }} → {{ x.new }}"
                             "{%% if x.why %%} _({{ x.why }})_{%% endif %%}\n{%% else %%}Nog geen acties.{%% endfor %%}"
                             % s("last_action")}

    overview = {"title": "Overzicht", "path": "overzicht", "icon": "mdi:heat-pump", "type": "sections",
                "max_columns": 3, "sections": [
        {"type": "grid", "column_span": 3, "cards": [
            {"type": "heading", "heading": "Installatie"},
            {**schema_card(r), "grid_options": {"columns": "full"}}]},
        {"type": "grid", "cards": [
            {"type": "heading", "heading": "Regeling"},
            tile(r("switch", "control"), "Regeling actief", tap_action={"action": "toggle"}),
            tile(s("planned"), "Gewenst"),
            tile(s("supply_target"), "Gewenste aanvoer"),
            tile(s("mode_desired"), "Gewenste modus"),
            tile(b("demand"), "Warmtevraag"),
            tile(b("base_load"), "Basislast"),
            tile(s("trim"), "Klepbijsturing"),
            tile(s("feedforward"), "Preset-correctie"),
            tile(s("offset"), "Geleerde offset"),
            tile(s("blocks"), "Blokkades")]},
        {"type": "grid", "cards": [
            {"type": "heading", "heading": "Pomp AO"},
            tile(s("t_supply"), "Aanvoer"), tile(s("t_return"), "Retour"),
            tile(s("f_comp"), "Compressor"), tile(s("power_mode"), "Modus"),
            tile(s("p_el_ao"), "Elektrisch"), tile(s("p_th_ao"), "Warmte"),
            tile(s("cop_ao"), "COP"), tile(s("run_state"), "Toestand")]},
        {"type": "grid", "cards": [
            {"type": "heading", "heading": "Meldingen"},
            {**report_md, "grid_options": {"columns": "full"}},
            {**actions_md, "grid_options": {"columns": "full"}},
            _press(r("button", "report_now"), "Uurrapport nu versturen", "mdi:message-text-clock")]},
    ]}

    temps = _apex("Temperaturen", [
        _s(s("t_supply"), "Aanvoer", color="#ff6b5e"),
        _s(s("supply_target"), "Gewenst", color="#ffb35e", curve="stepline", stroke_dash=4),
        _s(s("t_return"), "Retour", color="#58b7ff"),
        _s(s("outdoor"), "Buiten", 15, color="#9be37c")],
        yaxis=[{"decimals": 0, "min": "~0", "max": "~45", "apex_config": {"tickAmount": 5}}])
    comp = _apex("Compressor", [
        _s(s("f_comp"), "Frequentie", color="#ffd166", unit="Hz", type="area", opacity=0.25)],
        yaxis=[{"decimals": 0, "min": 0, "max": "~60", "apex_config": {"tickAmount": 4}}], height=180)
    power = _apex("Vermogen (kW)", [
        _s(s("p_th_ao"), "Warmte AO", 10, color="#ff6b5e", unit="kW"),
        _s(s("p_el_ao"), "Elektrisch AO", 10, color="#ffd166", transform="return x / 1000;", unit="kW"),
        _s(s("p_el_pt"), "Elektrisch Pomp T", 10, color="#c9a0ff", transform="return x / 1000;", unit="kW")],
        yaxis=[{"decimals": 1, "min": 0, "max": "~3", "apex_config": {"tickAmount": 4}}])
    cop = _apex("COP per uur", [
        {"entity": s("cop_ao"), "name": "COP", "type": "column", "color": "#7cf0a0",
         "group_by": {"func": "avg", "duration": "1h"}}], hours=24, height=180,
        yaxis=[{"decimals": 1, "min": 0, "max": "~6", "apex_config": {"tickAmount": 3}}])
    rooms_chart = _apex("Kamers", [
        {"entity": e, "name": e.split(".")[-1].replace("_", " "), "attribute": "current_temperature",
         "group_by": {"func": "avg", "duration": "10min"}, "unit": "°C"} for e in rooms],
        yaxis=[{"decimals": 1, "min": "~17", "max": "~22", "apex_config": {"tickAmount": 5}}])
    valves_chart = _apex("Klepstand gemiddeld", [
        _s(s("demand_level"), "Klep", 10, color="#58b7ff", type="area", opacity=0.3, unit="%")],
        yaxis=[{"decimals": 0, "min": 0, "max": 100, "apex_config": {"tickAmount": 4}}], height=160)
    dhw_chart = _apex("Tapwater", [
        _s(s("dhw_top"), "Boven", 10, color="#ff6b5e"), _s(s("dhw_bottom"), "Onder", 10, color="#58b7ff"),
        _s(s("pt_set"), "Setpoint", 10, color="#ffd166", curve="stepline", stroke_dash=4)], hours=24,
        yaxis=[{"decimals": 0, "min": "~35", "max": "~65", "apex_config": {"tickAmount": 6}}])

    charts = {"title": "Grafieken", "path": "grafieken", "icon": "mdi:chart-line",
              "cards": [temps, comp, power, cop, rooms_chart, valves_chart, dhw_chart]}

    room_cards: list[dict] = [{"type": "thermostat", "entity": e} for e in rooms]
    valves = {"type": "entities", "title": "Klepstand en doel per kamer", "entities": []}
    for e in rooms:
        valves["entities"] += [
            {"type": "attribute", "entity": e, "attribute": "valve_open_percent", "name": f"{e.split('.')[-1]} klep",
             "suffix": "%", "icon": "mdi:valve"},
            {"type": "attribute", "entity": e, "attribute": "temperature", "name": f"{e.split('.')[-1]} doel",
             "suffix": "°C", "icon": "mdi:thermometer"}]
    rooms_view = {"title": "Kamers", "path": "kamers", "icon": "mdi:home-thermometer",
                  "cards": [{"type": "grid", "columns": 2, "square": False, "cards": room_cards}, valves,
                            {"type": "entities", "title": "Warmtevraag", "entities": [
                                b("demand"), s("demand_level"), s("worst_deficit"), b("season"), b("base_load")]}]}

    dhw_view = {"title": "Tapwater", "path": "tapwater", "icon": "mdi:water-boiler", "cards": [
        {"type": "vertical-stack", "cards": [
            {"type": "custom:mushroom-title-card", "title": "Tapwater", "subtitle": "Pomp T"},
            {"type": "horizontal-stack", "cards": [tile(s("dhw_top"), "Boven"), tile(s("dhw_bottom"), "Onder")]},
            {"type": "horizontal-stack", "cards": [tile(s("pt_set"), "Setpoint"), tile(s("dhw_advice"), "Gewenst (prijs)")]},
            {"type": "horizontal-stack", "cards": [tile(s("legionella_days"), "Dagen sinds ≥ 60 °C"),
                                                   tile(b("legionella"), "Legionella-waarschuwing")]}]},
        {"type": "vertical-stack", "cards": [
            {"type": "custom:mushroom-title-card", "title": "Handmatige desinfectie",
             "subtitle": "Alleen warmtepomp, geen element. Doel = register 10, duur = register 11."},
            tile(s("disinfection"), "Status"),
            {"type": "horizontal-stack", "cards": [
                _press(r("button", "disinfect_start"), "Start", "mdi:bacteria-outline",
                       "Desinfectie starten? Pomp T warmt op tot de desinfectietemperatuur."),
                _press(r("button", "disinfect_stop"), "Stop", "mdi:stop-circle-outline")]},
            {"type": "entities", "title": "Pomp T", "entities": [
                s("p_el_pt"), s("e_pt"), s("t_source_in"), r("sensor", "r2_58"), b("pomp_t_running"),
                b("source_support"), r("sensor", "r2_60"), r("sensor", "r2_50"), r("sensor", "r2_51")]}]},
        dhw_chart]}

    svc_cards: list[dict] = [
        {"type": "vertical-stack", "cards": [
            {"type": "markdown", "content":
             "## ⚠️ Service-instellingen\nZet **Servicemodus** aan om te wijzigen; die gaat na 30 minuten vanzelf uit. "
             "Elke wijziging komt in het uurrapport en in `/config/dewarmte/servicelog.csv` (met oude waarde). "
             "Beveiligingen (druk, ontdooien, compressor) alleen wijzigen als je weet wat je doet. "
             "Registers gemarkeerd met *door regeling beheerd* worden door de regeling overschreven "
             "zolang *Regeling actief* aan staat."},
            tile(r("switch", "service_mode"), "Servicemodus", tap_action={"action": "toggle"})]}]
    for title, slave, lo, hi in SERVICE_GROUPS:
        ents = [r("number", f"n{slave}_{a}") for (sl, a) in sorted(SERVICE) if sl == slave and lo <= a <= hi]
        if ents:
            svc_cards.append({"type": "entities", "title": title, "state_color": False, "entities": ents})
    service_view = {"title": "Service", "path": "service", "icon": "mdi:wrench", "cards": svc_cards}

    faults = [r("binary_sensor", x.key) for x in RAW
              if isinstance(x.bit, int) and x.slave == 1 and x.addr in range(2, 15)]
    diag_view = {"title": "Diagnose", "path": "diagnose", "icon": "mdi:stethoscope", "cards": [
        {"type": "entities", "title": "Communicatie en schrijven", "entities": [
            s("bus"), b("critical_ok"), s("writes_24h"), s("last_action"), b("fault"), b("defrost"),
            r("sensor", "r1_33"), r("sensor", "r1_34")]},
        {"type": "entity-filter", "entities": faults, "state_filter": ["on"],
         "card": {"type": "entities", "title": "Actieve storingsbits SunRain"},
         "show_empty": True},
        {"type": "markdown", "content": "Alle ruwe registers staan onder Instellingen → Apparaten → "
                                         "*SunRain monoblock*, *Pomp T*, *kWh-meter* en *Warmtemeter* (Diagnostiek)."}]}

    return {"title": "DeWarmte", "views": [overview, charts, rooms_view, dhw_view, service_view, diag_view]}
