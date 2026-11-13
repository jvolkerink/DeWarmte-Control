"""Hydraulisch schema: SVG-basis, stromingslagen en de posities van de live waarden.

Eén bron voor tekening én dashboard, zodat de waarden altijd in de vakjes vallen.
Coördinaten in een viewBox van 1200 x 700.
"""
from __future__ import annotations

W, H = 1200, 700

# (x, y, breedte, label, platform, sleutel)
SPOTS: list[tuple[int, int, int, str, str, str]] = [
    (150, 195, 120, "Buiten", "sensor", "outdoor"),
    (95, 445, 100, "Compressor", "sensor", "f_comp"),
    (205, 445, 100, "Modus", "sensor", "power_mode"),
    (95, 500, 100, "Elektrisch", "sensor", "p_el_ao"),
    (205, 500, 100, "COP", "sensor", "cop_ao"),
    (318, 212, 110, "Aanvoer", "sensor", "t_supply"),
    (318, 290, 110, "Gewenst", "sensor", "supply_target"),
    (645, 145, 100, "Klep gem.", "sensor", "demand_level"),
    (645, 345, 100, "Kamertekort", "sensor", "worst_deficit"),
    (430, 580, 110, "Debiet", "sensor", "flow_ao"),
    (560, 580, 110, "Warmte", "sensor", "p_th_ao"),
    (430, 660, 110, "Retour", "sensor", "t_return"),
    (560, 660, 110, "Setpoint unit", "sensor", "set_769"),
    (880, 505, 85, "Bron in", "sensor", "t_source_in"),
    (970, 505, 85, "Bron uit", "sensor", "r2_58"),
    (880, 565, 85, "Vermogen", "sensor", "p_el_pt"),
    (970, 565, 85, "Element", "sensor", "r2_5"),
    (925, 620, 175, "Desinfectie", "sensor", "disinfection"),
    (1105, 445, 95, "Boven", "sensor", "dhw_top"),
    (1105, 520, 95, "Setpoint", "sensor", "pt_set"),
    (1105, 600, 95, "Onder", "sensor", "dhw_bottom"),
    (870, 55, 120, "Regeling", "switch", "control"),
    (1000, 55, 120, "Warmtevraag", "binary_sensor", "demand"),
    (1125, 55, 110, "Storing", "binary_sensor", "fault"),
]

RED, BLUE = "#ff6b5e", "#58b7ff"
SUPPLY = ["M260 250 H380", "M380 130 V330", "M380 130 H440", "M380 330 H440"]
RETURN = ["M700 130 H760", "M700 330 H760", "M760 130 V620", "M760 620 H150 V520"]
PT_IN = ["M760 470 H830"]
PT_OUT = ["M830 580 H760"]
TANK = ["M1020 470 H1050", "M1020 600 H1050"]


def _spot(x, y, w, label):
    return (f'<rect x="{x - w / 2:.0f}" y="{y - 26}" width="{w}" height="52" rx="10" fill="#0a1622" '
            f'stroke="#33506a" stroke-width="1.5"/>'
            f'<text x="{x}" y="{y - 10}" text-anchor="middle" fill="#8fa8bd" font-size="12">{label}</text>')


def _paths(ds, color, width=10, extra=""):
    return "".join(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}" '
                   f'stroke-linecap="round" stroke-linejoin="round" {extra}/>' for d in ds)


def base_svg() -> str:
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
         'font-family="Arial, Helvetica, sans-serif">',
         '<defs><linearGradient id="bg" x2="0" y2="1"><stop stop-color="#13263a"/><stop offset="1" stop-color="#08131f"/>'
         '</linearGradient><linearGradient id="dev" x2="1" y2="1"><stop stop-color="#5d7488"/><stop offset=".5" '
         'stop-color="#24384a"/><stop offset="1" stop-color="#121e2b"/></linearGradient>'
         '<linearGradient id="tank" x2="0" y2="1"><stop stop-color="#e2574c"/><stop offset=".55" stop-color="#7a5a8c"/>'
         '<stop offset="1" stop-color="#3f7fc0"/></linearGradient></defs>',
         f'<rect width="{W}" height="{H}" rx="24" fill="url(#bg)"/>',
         '<text x="40" y="52" fill="#eef5fb" font-size="26" font-weight="bold">Hydraulisch schema</text>',
         '<text x="40" y="80" fill="#8fa8bd" font-size="15">Pomp AO → Jaga convectoren → collector → Pomp T of bypass → Pomp AO</text>']
    # leidingen (statisch)
    p.append(_paths(SUPPLY, RED, 9))
    p.append(_paths(RETURN, BLUE, 9))
    p.append(_paths(PT_IN, BLUE, 7))
    p.append(_paths(PT_OUT, "#7fa6c9", 7))
    p.append(_paths(TANK, RED, 5))
    p.append('<circle cx="760" cy="470" r="8" fill="#58b7ff"/><circle cx="760" cy="580" r="8" fill="#58b7ff"/>')
    p.append('<text x="772" y="540" fill="#8fa8bd" font-size="12">bypass</text>')
    p.append('<text x="778" y="458" fill="#8fa8bd" font-size="12">collector</text>')
    # pomp AO
    p.append('<rect x="40" y="225" width="220" height="305" rx="20" fill="url(#dev)" stroke="#7a95ab" stroke-width="2.5"/>')
    p.append('<text x="150" y="258" text-anchor="middle" fill="#fff" font-size="20" font-weight="bold">Pomp AO</text>')
    p.append('<text x="150" y="280" text-anchor="middle" fill="#a9bfd1" font-size="13">SunRain R290 · slave 1</text>')
    p.append('<circle cx="150" cy="350" r="52" fill="#0b1722" stroke="#9ab0c2" stroke-width="6"/>')
    p.append('<g fill="#6f8ba2"><path d="M150 304 Q170 336 150 350 Q130 336 150 304"/><path d="M196 350 Q164 370 150 350 '
             'Q164 330 196 350"/><path d="M150 396 Q130 364 150 350 Q170 364 150 396"/><path d="M104 350 Q136 330 150 350 '
             'Q136 370 104 350"/></g>')
    # buffervat (toekomst)
    p.append('<g stroke="#8fa8bd" stroke-width="2" stroke-dasharray="7 6" fill="none"><rect x="292" y="370" width="52" '
             'height="120" rx="22"/><path d="M318 330 V370 M318 490 V620"/></g>')
    p.append('<text x="318" y="512" text-anchor="middle" fill="#8fa8bd" font-size="12">buffervat 100 L</text>'
             '<text x="318" y="527" text-anchor="middle" fill="#8fa8bd" font-size="12">(toekomst)</text>')
    # Jaga
    p.append('<rect x="440" y="88" width="260" height="88" rx="16" fill="url(#dev)" stroke="#7a95ab" stroke-width="2"/>')
    p.append('<text x="455" y="110" fill="#fff" font-size="16" font-weight="bold">Jaga · kamers</text>')
    p.append('<g stroke="#d9e6ef" stroke-width="5" stroke-linecap="round">'
             + "".join(f'<path d="M{x} 125 V162"/>' for x in range(462, 560, 14)) + '</g>')
    p.append('<text x="455" y="194" fill="#8fa8bd" font-size="12">met TRV (Versatile Thermostat)</text>')
    # radiatoren
    p.append('<rect x="440" y="288" width="260" height="84" rx="16" fill="url(#dev)" stroke="#7a95ab" stroke-width="2"/>')
    p.append('<text x="455" y="310" fill="#fff" font-size="16" font-weight="bold">Jaga · hal, toilet,</text>')
    p.append('<text x="455" y="330" fill="#fff" font-size="16" font-weight="bold">bijkeuken</text>')
    p.append('<text x="455" y="352" fill="#a9bfd1" font-size="12">zonder TRV · altijd open</text>')
    # pomp T
    p.append('<rect x="830" y="425" width="190" height="230" rx="18" fill="url(#dev)" stroke="#7a95ab" stroke-width="2.5"/>')
    p.append('<text x="925" y="455" text-anchor="middle" fill="#fff" font-size="18" font-weight="bold">Pomp T</text>')
    p.append('<text x="925" y="474" text-anchor="middle" fill="#a9bfd1" font-size="12">tapwater-booster · slave 2</text>')
    # boiler
    p.append('<rect x="1050" y="390" width="110" height="260" rx="40" fill="url(#tank)" opacity=".9" stroke="#c9d6e2" stroke-width="2"/>')
    p.append('<text x="1105" y="380" text-anchor="middle" fill="#fff" font-size="15" font-weight="bold">Boiler</text>')
    # legenda
    p.append(f'<text x="40" y="685" fill="#8fa8bd" font-size="12"><tspan fill="{RED}">━</tspan> aanvoer   '
             f'<tspan fill="{BLUE}">━</tspan> retour   ┄ toekomstig</text>')
    for x, y, w, label, *_ in SPOTS:
        p.append(_spot(x, y, w, label))
    p.append("</svg>")
    return "".join(p)


def _anim(ds, color, dur="1.2s", width=4):
    return "".join(
        f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linecap="round" '
        f'stroke-dasharray="6 18"><animate attributeName="stroke-dashoffset" from="24" to="0" dur="{dur}" '
        f'repeatCount="indefinite"/></path>' for d in ds)


def _overlay(body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">{body}</svg>')


def flow_ao_svg() -> str:
    return _overlay(_anim(SUPPLY, "#ffe1dc") + _anim(RETURN, "#e3f3ff"))


def flow_pt_svg() -> str:
    return _overlay(_anim(PT_IN + PT_OUT, "#e3f3ff", "1s") + _anim(TANK, "#ffe1dc", "1s", 3)
                    + '<rect x="830" y="425" width="190" height="230" rx="18" fill="none" stroke="#7cf0a0" '
                      'stroke-width="3"><animate attributeName="opacity" values=".3;1;.3" dur="2.5s" '
                      'repeatCount="indefinite"/></rect>')


def compressor_svg() -> str:
    return _overlay('<circle cx="150" cy="350" r="47" fill="#0b1722"/><g><path d="M150 304 Q170 336 150 350 Q130 336 150 304 M196 350 Q164 370 150 350 Q164 330 196 350 '
                    'M150 396 Q130 364 150 350 Q170 364 150 396 M104 350 Q136 330 150 350 Q136 370 104 350" '
                    'fill="#a8d8ff"/><animateTransform attributeName="transform" type="rotate" from="0 150 350" '
                    'to="360 150 350" dur="1.6s" repeatCount="indefinite"/></g>'
                    '<rect x="40" y="225" width="220" height="305" rx="20" fill="none" stroke="#7cf0a0" stroke-width="3"/>')


def fault_svg() -> str:
    return _overlay(f'<rect x="4" y="4" width="{W - 8}" height="{H - 8}" rx="22" fill="none" stroke="#ff4d4d" '
                    'stroke-width="8"><animate attributeName="opacity" values="1;.2;1" dur="1.2s" repeatCount="indefinite"/>'
                    '</rect>')


FILES = {"hydrauliek.svg": base_svg, "flow_ao.svg": flow_ao_svg, "flow_pt.svg": flow_pt_svg,
         "compressor.svg": compressor_svg, "storing.svg": fault_svg}
