"""Config flow for the ParkStart integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import ParkStartConfigEntry, create_client
from .api import ParkStartAuthError, ParkStartError
from .const import (
    CONF_MAX_DURATION,
    CONF_MUNICIPALITY,
    CONF_SCAN_INTERVAL,
    DEFAULT_MAX_DURATION,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MUNICIPALITIES,
)

_LOGGER = logging.getLogger(__name__)

_EMAIL_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
)
_PASSWORD_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_MUNICIPALITY): SelectSelector(
            SelectSelectorConfig(
                options=list(MUNICIPALITIES),
                mode=SelectSelectorMode.DROPDOWN,
                translation_key=CONF_MUNICIPALITY,
            )
        ),
        vol.Required(CONF_EMAIL): _EMAIL_SELECTOR,
        vol.Required(CONF_PASSWORD): _PASSWORD_SELECTOR,
    }
)


class ParkStartConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ParkStart."""

    VERSION = 1

    async def _async_try_login(self, data: dict[str, Any]) -> dict[str, str]:
        """One login attempt. Returns form errors."""
        client = create_client(self.hass, data)
        try:
            await client.async_login()
        except ParkStartAuthError:
            return {"base": "invalid_auth"}
        except ParkStartError:
            _LOGGER.debug("Could not connect to ParkStart", exc_info=True)
            return {"base": "cannot_connect"}
        return {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for municipality and credentials."""
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(
                f"{user_input[CONF_MUNICIPALITY]}_{user_input[CONF_EMAIL].strip().lower()}"
            )
            self._abort_if_unique_id_configured()
            if not (errors := await self._async_try_login(user_input)):
                return self.async_create_entry(title=user_input[CONF_EMAIL], data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start re-authentication after a failed login."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            if not (errors := await self._async_try_login(data)):
                return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): _PASSWORD_SELECTOR}),
            description_placeholders={"email": entry.data[CONF_EMAIL]},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ParkStartConfigEntry) -> OptionsFlow:
        """Create the options flow."""
        return ParkStartOptionsFlow()


class ParkStartOptionsFlow(OptionsFlow):
    """Poll interval and maximum session duration."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=int(DEFAULT_SCAN_INTERVAL.total_seconds() // 60),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=120,
                        step=1,
                        unit_of_measurement="min",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_MAX_DURATION,
                    default=int(DEFAULT_MAX_DURATION.total_seconds() // 3600),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1, max=168, step=1, unit_of_measurement="h", mode=NumberSelectorMode.BOX
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, self.config_entry.options),
        )
