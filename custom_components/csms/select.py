"""Implements select entities of the CSMS integration."""

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import DOMAIN
from .csms import ChargingStationManagementSystem, ChargingStationManager


class ChargingProfileSelector(SelectEntity):
    """Entity to select a charging profile for ongoing transactions."""

    def __init__(self, cs_id, cs_manager, options) -> None:
        """Initialize the selector."""
        self._cs_id: str = cs_id
        self._cs_manager: ChargingStationManager = cs_manager
        self._attr_unique_id = f"{self._cs_id}_profile_selector"
        self._attr_name = "Charging Profile"
        self._attr_options = options
        self._attr_current_option = "Default"

    @property
    def device_info(self):
        """Return information to link this entity with the correct device."""

        return {
            "identifiers": {(DOMAIN, self._cs_id)},
            "name": f"Charging Station {self._cs_id}",
            "manufacturer": self._cs_manager.manufacturer,
            "model": self._cs_manager.model,
            "sw_version": self._cs_manager.sw_version,
        }

    async def async_select_option(self, option: str):
        """Handle the selection of a charging profile."""
        self._attr_current_option = option
        await self._cs_manager.set_current_transaction_charging_profile(option)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Initialise CSMS integration selector entities."""
    csms: ChargingStationManagementSystem = hass.data[DOMAIN]

    selectors = []
    if discovery_info is not None:
        # Extract charging station data or any other needed values
        charging_stations = discovery_info.get("charging_station", [])

        for cs in charging_stations:
            cs_id = cs.get("id")
            cs_manager = csms.cs_managers.get(cs_id)

            if cs_manager is not None:
                profiles_json = cs.get("charging_profile", [])
                profiles_names = [p.get("name") for p in profiles_json]
                selectors.append(
                    ChargingProfileSelector(cs_id, cs_manager, profiles_names)
                )

    async_add_entities(selectors, update_before_add=True)
