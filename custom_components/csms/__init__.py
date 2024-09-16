"""The OCPP Charging Station Management System integration."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PORT, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .csms import ChargingStationManagementSystem, ChargingStationManager

type CsmsConfigEntry = ConfigEntry[ChargingStationManagementSystem]

csms = ChargingStationManagementSystem()
ha: HomeAssistant

CHARGING_STATION_SCHEMA = vol.Schema(
    {
        vol.Required("id"): cv.string,
        vol.Optional("measurands", default=[]): vol.All(
            cv.ensure_list,
            [
                {
                    vol.Required("name"): cv.string,
                    vol.Required("measurand"): cv.string,
                    vol.Required("unit_of_measurement"): cv.string,
                    vol.Required("device_class"): cv.string,
                    vol.Optional("phase"): cv.string,
                    vol.Optional("location"): cv.string,
                }
            ],
        ),
        vol.Optional("variables", default=[]): vol.All(
            cv.ensure_list,
            [
                {
                    vol.Required("component"): {
                        vol.Required("name"): cv.string,
                        vol.Optional("evse"): {
                            vol.Required("id"): cv.positive_int,
                            vol.Required("connector_id"): cv.positive_int,
                        },
                    },
                    vol.Required("variable"): {vol.Required("name"): cv.string},
                }
            ],
        ),
        vol.Optional("charging_profile", default=[]): vol.All(
            cv.ensure_list,
            [
                {
                    vol.Required("id"): cv.positive_int,
                    vol.Required("name"): cv.string,
                    vol.Required("stack_level"): cv.positive_int,
                    vol.Required("charging_profile_purpose"): cv.string,
                    vol.Required("charging_profile_kind"): cv.string,
                    vol.Required("recurrency_kind"): cv.string,
                    vol.Required("charging_schedule"): vol.All(
                        cv.ensure_list,
                        [
                            {
                                vol.Required("id"): cv.positive_int,
                                vol.Required("start_schedule"): cv.string,
                                vol.Optional("duration", default=0): cv.positive_int,
                                vol.Required("charging_rate_unit"): cv.string,
                                vol.Required("charging_schedule_period"): vol.All(
                                    cv.ensure_list,
                                    [
                                        {
                                            vol.Required(
                                                "start_period"
                                            ): cv.positive_int,
                                            vol.Required("limit"): vol.Coerce(float),
                                        }
                                    ],
                                ),
                            }
                        ],
                    ),
                }
            ],
        ),
    }
)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_PORT): cv.port,
                vol.Required("ssl_cert_path"): cv.path,
                vol.Required("sss_key_path"): cv.path,
                vol.Required("charging_station"): vol.All(
                    cv.ensure_list, [CHARGING_STATION_SCHEMA]
                ),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration from YAML."""

    async def handle_set_tx_default_profile(call: ServiceCall):
        max_current = call.data.get("max_current")

        # # Get the ChargingStationManager instance
        charging_station = csms.charging_station

        # # Set the maximum current for the charging station using smart charging features
        await charging_station.set_tx_default_profile(max_current)

    csms_config = config.get(DOMAIN)

    if csms_config is None:
        return True

    hass.data[DOMAIN] = csms
    csms.port = csms_config[CONF_PORT]
    csms.cert_path = csms_config["ssl_cert_path"]
    csms.key_path = csms_config["sss_key_path"]
    csms.hass = hass

    charging_stations = csms_config.get("charging_station", [])

    for cs in charging_stations:
        cs_id = cs.get("id")
        csms.cs_managers[cs_id] = ChargingStationManager()
        for profile in cs.get("charging_profiles", []):
            profile.pop("name", None)  # Remove the 'name' field of the charging profile
        await csms.cs_managers[cs_id].initialise(cs)

    await async_load_platform(hass, Platform.SENSOR, DOMAIN, csms_config, config)
    await async_load_platform(hass, Platform.SELECT, DOMAIN, csms_config, config)
    await async_load_platform(hass, Platform.BUTTON, DOMAIN, csms_config, config)

    hass.services.async_register(
        domain=DOMAIN,
        service="set_tx_default_profile",
        service_func=handle_set_tx_default_profile,
    )

    hass.async_create_background_task(csms.run_server(), name="CSMS Server Task")
    logging.info("Created server task.")

    return True
