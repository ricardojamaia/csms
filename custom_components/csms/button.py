"""Implement button entities of the CSMS."""

from ocpp.v201.enums import OperationalStatusType

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .csms import ChargingStationManagementSystem, ChargingStationManager


class StartStopTransactionButton(ButtonEntity):
    """Button to start/stop a charging session."""

    def __init__(self, cs_id, cs_manager) -> None:
        """Initialize the selector."""
        self._cs_id: str = cs_id
        self._cs_manager: ChargingStationManager = cs_manager
        self._attr_name = "Start/Stop transaction"
        self._attr_unique_id = f"{self._cs_id}_transaction_button"

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

    async def async_press(self):
        """Handle the button press."""

        if (
            self._cs_manager.current_session is None
            or not self._cs_manager.current_session.is_active()
        ):
            await self._cs_manager.start_transaction()
        else:
            await self._cs_manager.stop_current_transaction()


class ChangeAvailabilityButton(ButtonEntity):
    """Button to start/stop a charging session."""

    def __init__(self, cs_id, cs_manager) -> None:
        """Initialize the selector."""
        self._cs_id: str = cs_id
        self._cs_manager: ChargingStationManager = cs_manager
        self._attr_name = "Change availability"
        self._attr_unique_id = f"{self._cs_id}_change_availability"

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

    async def async_press(self):
        """Handle the button press."""

        if self._cs_manager.is_operational():
            await self._cs_manager.change_availability(
                OperationalStatusType.inoperative
            )
        else:
            await self._cs_manager.change_availability(OperationalStatusType.operative)


async def async_setup_platform(
    hass: HomeAssistant, config, async_add_entities, discovery_info=None
):
    """Initialise CSMS integration button entities."""
    csms: ChargingStationManagementSystem = hass.data[DOMAIN]

    buttons = []
    if discovery_info is not None:
        # Extract charging station data or any other needed values
        charging_stations = discovery_info.get("charging_station", [])

        for cs in charging_stations:
            cs_id = cs.get("id")
            cs_manager = csms.cs_managers.get(cs_id)

            if cs_manager is not None:
                buttons.append(StartStopTransactionButton(cs_id, cs_manager))
                buttons.append(ChangeAvailabilityButton(cs_id, cs_manager))

    async_add_entities(buttons)
