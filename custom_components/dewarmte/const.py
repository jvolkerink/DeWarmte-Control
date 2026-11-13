"""Constanten (geen Home Assistant-imports, zodat ze ook los te testen zijn)."""
DOMAIN = "dewarmte"
VERSION = "3.5.1"
PLATFORMS = ["sensor", "binary_sensor", "switch", "button", "number"]

CONF_UP_TOPIC = "up_topic"
CONF_DOWN_TOPIC = "down_topic"
CONF_ROOMS = "rooms"
CONF_TRV_ROOMS = "trv_sensor_rooms"   # kamers die meten met de sensor in de TRV
CONF_OUTDOOR = "outdoor_entity"
CONF_PRICE = "price_entity"
CONF_PRICE_LEVEL = "price_level_entity"
CONF_BASIS = "setpoint_basis"
CONF_NOTIFY = "notify_service"
CONF_PRESENCE = "presence_entity"    # aan = iemand thuis
CONF_PRICE_SERVICE = "price_service"  # bijv. tibber.get_prices
CONF_SCHEDULES = "warm_periods"       # extra warme periodes per kamer (tekst)
CONF_COLD_EXCLUDE = "cold_alert_exclude"  # kamers zonder "blijft te koud"-melding

# sleutel: (min, max, stap, label NL, label EN) - standaardwaarden staan in engine.Settings
SETTING_FIELDS = {
    "curve_start": (20, 45, 0.5, "Stooklijn: aanvoer bij 0 °C buiten (°C)", "Heating curve: supply at 0 °C outside (°C)"),
    "curve_slope": (0.1, 1.5, 0.05, "Stooklijn: helling (K per K buiten)", "Heating curve: slope (K per K outside)"),
    "min_supply": (15, 40, 1, "Minimale aanvoer (°C)", "Minimum supply (°C)"),
    "max_supply": (30, 55, 1, "Maximale aanvoer (°C)", "Maximum supply (°C)"),
    "heating_stop_outdoor": (10, 25, 1, "Stookgrens: geen verwarming boven (°C buiten)", "No heating above (°C outside)"),
    "keep_on_below": (-10, 20, 0.5, "Basislast: unit blijft aan in stookseizoen onder (°C buiten)", "Base load: unit stays on in heating season below (°C outside)"),
    "valve_high": (60, 100, 5, "Aanvoer omhoog als de meest open klep gemiddeld boven (%) staat", "Raise supply when most-open valve averages above (%)"),
    "valve_low": (20, 90, 5, "Aanvoer omlaag als de meest open klep gemiddeld onder (%) staat", "Lower supply when most-open valve averages below (%)"),
    "ff_k": (0, 2, 0.1, "Aanvoer mee bij preset-stap (K per K kamerdoel)", "Supply follow on preset step (K per K room target)"),
    "dhw_usage_temp": (0, 60, 1, "Tapwater boven minimaal op geleerde tapmomenten (°C, 0 = uit)", "DHW top minimum at learned usage times (°C, 0 = off)"),
    "legionella_auto_days": (0, 21, 1, "Automatische desinfectie na zoveel dagen zonder ≥ 60 °C (0 = uit)", "Automatic disinfection after days without ≥ 60 °C (0 = off)"),
    "silent_above": (-5, 10, 0.5, "Altijd Stil boven (°C buiten)", "Always Silent above (°C outside)"),
    "silent_release": (-5, 10, 0.5, "Opschalen toegestaan onder (°C buiten)", "Scaling allowed below (°C outside)"),
    "dhw_floor": (35, 60, 1, "Tapwater minimum (°C)", "DHW minimum (°C)"),
    "dhw_max": (45, 70, 1, "Tapwater maximum bij normale prijs (°C)", "DHW maximum at normal price (°C)"),
    "dhw_max_negative": (45, 70, 1, "Tapwater maximum bij negatieve prijs (°C)", "DHW maximum at negative price (°C)"),
    "price_cheap": (-0.5, 1, 0.01, "Prijs goedkoop onder (EUR/kWh)", "Cheap below (EUR/kWh)"),
    "price_normal": (-0.5, 1, 0.01, "Prijs normaal onder (EUR/kWh)", "Normal below (EUR/kWh)"),
    "price_expensive": (-0.5, 1, 0.01, "Prijs duur vanaf (EUR/kWh)", "Expensive from (EUR/kWh)"),
    "dhw_source_min": (5, 30, 1, "Pomp T: bronwater minimaal (°C)", "Pomp T: minimum source water (°C)"),
    "dhw_source_supply": (20, 45, 1, "Aanvoer tijdens bronondersteuning Pomp T (°C)", "Supply during Pomp T source support (°C)"),
}

# Prijsplanner (planner.PlanSettings): sleutel -> (min, max, stap, NL, EN)
PLAN_FIELDS = {
    "band_up": (0, 3, 0.5, "Prijsslim: max. voorverwarmen boven normaal doel (K)", "Price: max preheat above normal target (K)"),
    "band_down": (0, 3, 0.5, "Prijsslim: max. laten zakken onder normaal doel (K)", "Price: max setback below normal target (K)"),
    "gain": (0.5, 6, 0.5, "Prijsslim: gevoeligheid voor prijsverschil", "Price: sensitivity to price difference"),
    "hold_min": (15, 180, 15, "Prijsslim: minimale duur van een kamerdoel (min)", "Price: minimum hold of a room target (min)"),
}
PLAN_TIMES = {"night_start": "Nachtverlaging vanaf (uu:mm)", "morning_weekday": "Ochtend werkdag vanaf (uu:mm)",
              "morning_weekend": "Ochtend weekend vanaf (uu:mm)"}
