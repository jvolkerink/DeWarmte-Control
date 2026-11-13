"""DeWarmte Control 3.2 - regeling van SunRain en Pomp T via DR164/MQTT."""
from __future__ import annotations

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

import logging
import os
import shutil

import yaml

from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from . import dashboard, schema
from .const import CONF_ROOMS, DOMAIN, PLATFORMS
from .coordinator import DeWarmteCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not await mqtt.async_wait_for_mqtt_client(hass):
        raise ConfigEntryNotReady("MQTT is niet beschikbaar")
    coordinator = DeWarmteCoordinator(hass, entry)
    await coordinator.async_setup()
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    try:
        await _write_dashboard(hass, entry)
    except Exception:  # noqa: BLE001 - dashboard mag de regeling nooit tegenhouden
        _LOGGER.exception("Dashboard schrijven mislukt")
    return True


async def _write_dashboard(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Schrijft /config/www/dewarmte/*.svg en /config/dewarmte/dashboard.yaml met de echte entity-id's."""
    reg = er.async_get(hass)
    missing: list[str] = []

    def resolve(platform: str, key: str) -> str:
        eid = reg.async_get_entity_id(platform, DOMAIN, f"{entry.entry_id}_{key}")
        if eid is None:
            missing.append(f"{platform}.{key}")
            return f"{platform}.dewarmte_{slugify(key)}"
        return eid

    cfg = {**entry.data, **entry.options}
    content = dashboard.build(resolve, list(cfg.get(CONF_ROOMS, [])))
    www, out = hass.config.path("www", "dewarmte"), hass.config.path("dewarmte", "dashboard.yaml")

    def _write() -> None:
        os.makedirs(www, exist_ok=True)
        for name, fn in schema.FILES.items():
            with open(os.path.join(www, name), "w", encoding="utf-8") as fh:
                fh.write(fn())
        os.makedirs(os.path.dirname(out), exist_ok=True)
        tmp = out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("# Automatisch gemaakt door DeWarmte Control bij elke start. Niet met de hand wijzigen.\n")
            yaml.safe_dump(content, fh, allow_unicode=True, sort_keys=False, width=120)
        shutil.move(tmp, out)

    await hass.async_add_executor_job(_write)
    if missing:
        _LOGGER.warning("Dashboard: %d entiteiten niet gevonden: %s", len(missing), ", ".join(missing[:10]))


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        coordinator: DeWarmteCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return ok
