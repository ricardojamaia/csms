"""Config flow for OCPP Charging Station Management System integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PORT): str,
        vol.Required("measurands", default=["Voltage", "Current"]): cv.multi_select(
            {
                "Voltage": "Voltage",
                "Current": "Current",
                "Power.Active.Import": "Power.Active.Import",
                "Energy.Active.Import.Register": "Energy.Active.Import.Register",
            }
        ),
    }
)  # vol.Schema({vol.Required(CONF_PORT): str})


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """

    # Validate the port number
    try:
        port = int(data.get(CONF_PORT))
        if not (1 <= port <= 65535):
            raise InvalidPort
    except ValueError:
        raise InvalidPort from None

    # Return info that you want to store in the config entry.
    return {"title": "Charging station"}


class ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for OCPP Charging Station Management System."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except InvalidPort:
                errors["base"] = "invalid_port"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )


class InvalidPort(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
