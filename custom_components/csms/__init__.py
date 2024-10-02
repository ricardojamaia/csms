"""The OCPP Charging Station Management System integration."""

from __future__ import annotations

import logging
import uuid

from ocpp.exceptions import FormatViolationError, ProtocolError
from ocpp.v201.enums import ChargingProfilePurposeType
import voluptuous as vol

from homeassistant.const import CONF_PORT, Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .csms import ChargingStationManagementSystem, ChargingStationManager

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
                        vol.Optional("instance"): cv.string,
                    },
                    vol.Required("variable"): {
                        vol.Required("name"): cv.string,
                        vol.Optional("instance"): cv.string,
                    },
                    vol.Required("monitoring"): {
                        vol.Required("type"): cv.string,
                        vol.Required("value"): cv.positive_float,
                    },
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

GET_CHARGING_PROFILES_SCHEMA = vol.Schema(
    {
        vol.Required("charging_station"): cv.string,  # Charging station ID
        vol.Required("profile_purpose"): vol.In(
            ["tx_default_profile", "tx_profile"]
        ),  # Profile types
    }
)

SET_CHARGING_PROFILE_SCHEMA = vol.Schema(
    {
        vol.Required("charging_station"): cv.string,  # Charging station ID
        vol.Required("charging_profile"): dict,  # JSON object with the charging profile
    }
)

CLEAR_CHARGING_PROFILES_SCHEMA = vol.Schema(
    {
        vol.Required("charging_station"): cv.string,  # Charging station ID
        vol.Required("profile_purpose"): vol.In(
            ["tx_default_profile", "tx_profile"]
        ),  # Profile types
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration from YAML."""

    async def handle_get_charging_profiles(call: ServiceCall):
        profile_purpose = call.data.get("profile_purpose")
        charging_station = call.data.get("charging_station")

        # Get the ChargingStationManager instance
        cs_manager = csms.cs_managers.get(charging_station)

        purpose_type = get_profile_purpose_type(profile_purpose)

        # Retrieve the charging profiles
        profiles = {}
        if cs_manager is not None:
            if not cs_manager.is_connected():
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="not_connected",
                    translation_placeholders={
                        "charging_station": charging_station,
                    },
                )
            profiles = {
                "charging_profiles": await cs_manager.get_charging_profiles(
                    purpose_type
                )
            }
        else:
            logging.error("Unrecognized charging station.")
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unrecognised_charging_station",
                translation_placeholders={
                    "charging_station": charging_station,
                },
            )

        return profiles

    async def handle_set_charging_profile(call: ServiceCall):
        charging_profile = call.data.get("charging_profile")
        charging_station = call.data.get("charging_station")

        # # Get the ChargingStationManager instance
        cs_manager = csms.cs_managers.get(charging_station)

        # # Set the charging profile
        if cs_manager is not None:
            if not cs_manager.is_connected():
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="not_connected",
                    translation_placeholders={
                        "charging_station": charging_station,
                    },
                )
            try:
                await cs_manager.set_charging_profile(charging_profile)
            except Exception as e:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="request_error",
                    translation_placeholders={
                        "log_message": str(e),
                    },
                ) from e
        else:
            logging.error("Unrecognized charging station.")
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unrecognised_charging_station",
                translation_placeholders={
                    "charging_station": charging_station,
                },
            )

    async def handle_clear_charging_profiles(call: ServiceCall):
        profile_purpose = call.data.get("profile_purpose")
        charging_station = call.data.get("charging_station")

        # # Get the ChargingStationManager instance
        cs_manager = csms.cs_managers.get(charging_station)

        purpose_type = get_profile_purpose_type(profile_purpose)

        # # Set the maximum current for the charging station using smart charging features
        if cs_manager is not None:
            if not cs_manager.is_connected():
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="not_connected",
                    translation_placeholders={
                        "charging_station": charging_station,
                    },
                )
            await cs_manager.clear_charging_profile(
                charging_profile_purpose=purpose_type
            )
        else:
            logging.error("Unrecognized charging station.")
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unrecognised_charging_station",
                translation_placeholders={
                    "charging_station": charging_station,
                },
            )

    def get_profile_purpose_type(profile_purpose):
        if profile_purpose == "tx_default_profile":
            purpose_type = ChargingProfilePurposeType.tx_default_profile
        elif profile_purpose == "tx_profile":
            purpose_type = ChargingProfilePurposeType.tx_profile

        return purpose_type

    csms_config = config.get(DOMAIN)

    if csms_config is None:
        return True

    hass.data[DOMAIN] = csms
    csms.port = csms_config[CONF_PORT]
    csms.cert_path = csms_config["ssl_cert_path"]
    csms.key_path = csms_config["sss_key_path"]

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
        service="get_charging_profiles",
        service_func=handle_get_charging_profiles,
        schema=GET_CHARGING_PROFILES_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        domain=DOMAIN,
        service="set_charging_profile",
        service_func=handle_set_charging_profile,
        schema=SET_CHARGING_PROFILE_SCHEMA,
    )

    hass.services.async_register(
        domain=DOMAIN,
        service="clear_charging_profiles",
        service_func=handle_clear_charging_profiles,
        schema=CLEAR_CHARGING_PROFILES_SCHEMA,
    )

    hass.async_create_background_task(csms.run_server(), name="CSMS Server Task")
    logging.info("Created server task.")

    return True
