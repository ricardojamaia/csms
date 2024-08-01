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
