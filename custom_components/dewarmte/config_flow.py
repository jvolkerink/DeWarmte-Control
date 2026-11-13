"""Config flow en opties."""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (CONF_BASIS, CONF_DOWN_TOPIC, CONF_NOTIFY, CONF_PRESENCE, CONF_PRICE_SERVICE, PLAN_FIELDS, PLAN_TIMES, CONF_OUTDOOR, CONF_PRICE, CONF_PRICE_LEVEL, CONF_ROOMS, CONF_TRV_ROOMS,
                    CONF_UP_TOPIC, DOMAIN, SETTING_FIELDS)
from .engine import Settings
from .planner import PlanSettings


def _entity_schema(defaults: dict) -> dict:
    def ent(domain, multiple=False):
        return selector.EntitySelector(selector.EntitySelectorConfig(domain=domain, multiple=multiple))

    def sug(key):
        return {"suggested_value": defaults.get(key)}

    return {
        vol.Required(CONF_ROOMS, default=defaults.get(CONF_ROOMS, [])): ent("climate", True),
        vol.Optional(CONF_TRV_ROOMS, description=sug(CONF_TRV_ROOMS)): ent("climate", True),
        vol.Optional(CONF_OUTDOOR, description=sug(CONF_OUTDOOR)): ent("sensor"),
        vol.Optional(CONF_PRICE, description=sug(CONF_PRICE)): ent("sensor"),
        vol.Optional(CONF_PRICE_LEVEL, description=sug(CONF_PRICE_LEVEL)): ent("sensor"),
        vol.Optional(CONF_NOTIFY, description=sug(CONF_NOTIFY)): selector.TextSelector(),
        vol.Optional(CONF_PRESENCE, description=sug(CONF_PRESENCE)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["input_boolean", "binary_sensor", "group", "person", "zone"])),
        vol.Optional(CONF_PRICE_SERVICE, description={"suggested_value": defaults.get(CONF_PRICE_SERVICE, "tibber.get_prices")}):
            selector.TextSelector(),
    }


class DeWarmteConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict = {}

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        errors = {}
        if user_input is not None:
            up, down = user_input[CONF_UP_TOPIC].strip(), user_input[CONF_DOWN_TOPIC].strip()
            if not up or not down or up == down:
                errors["base"] = "invalid_topics"
            else:
                self._data = {CONF_UP_TOPIC: up, CONF_DOWN_TOPIC: down}
                return await self.async_step_entities()
        schema = vol.Schema({vol.Required(CONF_UP_TOPIC): str, vol.Required(CONF_DOWN_TOPIC): str})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_entities(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="DeWarmte", data={**self._data, **user_input})
        return self.async_show_form(step_id="entities", data_schema=vol.Schema(_entity_schema({})))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return DeWarmteOptionsFlow()


class DeWarmteOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current = {**self.config_entry.data, **self.config_entry.options}
        defaults = Settings.from_dict(current)
        schema = _entity_schema(current)
        schema[vol.Required(CONF_BASIS, default=defaults.setpoint_basis)] = selector.SelectSelector(
            selector.SelectSelectorConfig(options=["supply", "return"], translation_key="setpoint_basis"))
        for key, (lo, hi, step, _nl, _en) in SETTING_FIELDS.items():
            schema[vol.Required(key, default=getattr(defaults, key))] = selector.NumberSelector(
                selector.NumberSelectorConfig(min=lo, max=hi, step=step, mode=selector.NumberSelectorMode.BOX))
        plan = PlanSettings.from_dict(current)
        for key, (lo, hi, step, _nl, _en) in PLAN_FIELDS.items():
            schema[vol.Required(key, default=getattr(plan, key))] = selector.NumberSelector(
                selector.NumberSelectorConfig(min=lo, max=hi, step=step, mode=selector.NumberSelectorMode.BOX))
        for key in PLAN_TIMES:
            schema[vol.Required(key, default=getattr(plan, key))] = selector.TextSelector()
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))
