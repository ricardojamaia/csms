"""The OCPP Charging Station Management System integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.start import async_at_start
from homeassistant.helpers.device_registry import async_get

from .csms import ChargingStationManagementSystem
from .const import DOMAIN

PLATFORMS: list[Platform] = [Platform.SENSOR]

type CsmsConfigEntry = ConfigEntry[ChargingStationManagementSystem]

csms = ChargingStationManagementSystem()
ha: HomeAssistant

config = [
    {
        "component": {"name": "ChargingStation"},
        "variable": {"name": "Available"},
        "variableAttribute": [
            {"type": "Actual", "value": "true", "mutability": "ReadOnly"}
        ],
        "variableCharacteristics": {
            "dataType": "boolean",
            "supportsMonitoring": False,
        },
    },
    {
        "component": {"name": "ChargingStation"},
        "variable": {"name": "AvailabilityState"},
        "variableAttribute": [
            {"type": "Actual", "value": "Available", "mutability": "ReadOnly"}
        ],
        "variableCharacteristics": {
            "dataType": "OptionList",
            "valuesList": "Available,Occupied,Reserved,Unavailable,Faulted",
            "supportsMonitoring": False,
        },
    },
    {
        "component": {"name": "ChargingStation"},
        "variable": {"name": "SupplyPhases"},
        "variableAttribute": [
            {"type": "Actual", "value": "1", "mutability": "ReadOnly"}
        ],
        "variableCharacteristics": {
            "dataType": "integer",
            "supportsMonitoring": False,
        },
    },
    {
        "component": {"name": "ChargingStation"},
        "variable": {"name": "Power"},
        "variableAttribute": [
            {"type": "Actual", "value": "0.000000", "mutability": "ReadOnly"}
        ],
        "variableCharacteristics": {
            "unit": "W",
            "dataType": "decimal",
            "maxLimit": 27840.0,
            "supportsMonitoring": False,
        },
    },
    {
        "component": {"name": "ChargingStation"},
        "variable": {"name": "ACCurrent"},
        "variableAttribute": [
            {"type": "Actual", "value": "0.000000", "mutability": "ReadOnly"}
        ],
        "variableCharacteristics": {
            "unit": "A",
            "dataType": "decimal",
            "maxLimit": 96.0,
            "supportsMonitoring": False,
        },
    },
    {
        "component": {"name": "ChargingStation"},
        "variable": {"name": "ACVoltage"},
        "variableAttribute": [
            {"type": "Actual", "value": "235.000000", "mutability": "ReadOnly"}
        ],
        "variableCharacteristics": {
            "unit": "V",
            "dataType": "decimal",
            "maxLimit": 230.0,
            "supportsMonitoring": False,
        },
    },
    {
        "component": {"name": "ChargingStation"},
        "variable": {"name": "FirmwareVersion"},
        "variableAttribute": [
            {"type": "Actual", "value": "01.04.16.00", "mutability": "ReadOnly"}
        ],
        "variableCharacteristics": {
            "dataType": "string",
            "supportsMonitoring": False,
        },
    },
    {
        "component": {"name": "ChargingStation"},
        "variable": {"name": "GridType"},
        "variableAttribute": [
            {"type": "Actual", "value": "TN", "mutability": "ReadOnly"}
        ],
        "variableCharacteristics": {
            "dataType": "OptionList",
            "valuesList": "TN,TT,IT",
            "supportsMonitoring": False,
        },
    },
]


async def async_setup_entry(hass: HomeAssistant, entry: CsmsConfigEntry) -> bool:
    """Set up OCPP Charging Station Management System from a config entry."""

    async def handle_set_tx_default_profile(call: ServiceCall):
        max_current = call.data.get("max_current")

        # # Get the ChargingStationManager instance
        csms: ChargingStationManagementSystem = entry.runtime_data
        charging_station = csms.charging_station

        # # Set the maximum current for the charging station using smart charging features
        await charging_station.set_tx_default_profile(max_current)

    entry.runtime_data = csms
    csms.port = entry.data["port"]
    csms.hass = hass

    await csms.initialise(config)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    hass.services.async_register(
        domain=DOMAIN,
        service="set_tx_default_profile",
        service_func=handle_set_tx_default_profile,
    )

    async_at_start(hass, csms_startup)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: CsmsConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def csms_startup(hass):
    hass.async_create_background_task(csms.run_server(), name="CSMS Server Task")

    logging.info("Created server task.")
