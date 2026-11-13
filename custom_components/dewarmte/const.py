"""Constanten (geen Home Assistant-imports, zodat ze ook los te testen zijn)."""
DOMAIN = "dewarmte"
VERSION = "3.3.0"
PLATFORMS = ["sensor", "binary_sensor", "switch", "button", "number"]

CONF_UP_TOPIC = "up_topic"
CONF_DOWN_TOPIC = "down_topic"
CONF_ROOMS = "rooms"
CONF_TRV_ROOMS = "trv_sensor_rooms"   # kamers die meten met de sensor in de TRV
CONF_OUTDOOR = "outdoor_entity"
CONF_PRICE = "price_entity"
CONF_PRICE_LEVEL = "price_level_entity"
CONF_BASIS = "setpoint_basis"
CONF_NOTIFY = "notify_service"     # bijv. mobile_app_iphone (zonder "notify.")

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
