from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .csms import (
    ChargingStation,
    ChargingStationManagementSystem,
    ChargingStationManager,
)
from .measurand import Measurand


class ChargingStationSensor(Entity):
    """Representation of a Charging Station Sensor."""

    should_poll = False

    def __init__(
        self,
        charging_station: ChargingStation,
        cs_manager: ChargingStationManager,
        measurand: Measurand,
    ) -> None:
        """Initialize the sensor."""
        self._charging_station = charging_station
        self._cs_manager = cs_manager
        self._measurand = measurand
        self._attr_unique_id = f"{cs_manager.id}_{measurand.unique_key}"
        self._attr_name = measurand.name
        self._attr_unit_of_measurement = measurand.unit_of_measurement
        self._attr_device_class = measurand.device_class
        self._state = "Unavailable"

    @property
    def device_info(self):
        """Return information to link this entity with the correct device."""
        return self._cs_manager.device_info

    @property
    def state(self):
        """Return the state of the sensor."""
        self._state = self._cs_manager.get_latest_measurand_value(
            self._measurand.unique_key
        )
        return self._state

    @property
    def available(self) -> bool:
        """Return True if the sensor is available."""
        return True

    async def async_added_to_hass(self):
        """Run when this Entity has been added to HA."""
        self._cs_manager.register_callback(
            self._measurand.unique_key, self.async_write_ha_state
        )

    async def async_will_remove_from_hass(self):
        """Entity being removed from hass."""
        self._cs_manager.unregister_callback(
            self._measurand.unique_key, self.async_write_ha_state
        )


async def async_setup_entry(hass, config_entry, async_add_entities) -> None:
    """Add sensors for passed config_entry in HA."""
    csms: ChargingStationManagementSystem = config_entry.runtime_data
    cs_manager = csms.cs_manager

    async def create_and_add_devices(measurands):
        """Create ChargingStationSensor devices from the given measurands and add them to HA."""
        # Get existing entities in Home Assistant
        existing_entities = config_entry.data.get("discovered_measurands", [])
        existing_measurands = [
            Measurand.from_dict(m) for m in existing_entities if m is not None
        ]

        # Filter out measurands that already have corresponding entities
        new_measurands = [m for m in measurands if m not in existing_measurands]

        devices = [
            ChargingStationSensor(cs_manager.charging_station, cs_manager, measurand)
            for measurand in new_measurands
        ]

        if devices:
            async_add_entities(devices, update_before_add=True)

        # Update the config_entry with the discovered measurands
        data = {
            **config_entry.data,
            "discovered_measurands": [measurand.to_dict() for measurand in measurands],
        }
        hass.config_entries.async_update_entry(config_entry, data=data)

    # Register the create_and_add_devices function as the callback for new measurands
    cs_manager.new_measurands_callback = create_and_add_devices

    # Get stored discovered measurands from config_entry and convert them back to Measurand instances
    stored_measurands = config_entry.data.get("discovered_measurands", [])
    measurands = [Measurand.from_dict(m) for m in stored_measurands if m is not None]

    # Create and add sensors based on stored discovered measurands
    devices = [
        ChargingStationSensor(cs_manager.charging_station, cs_manager, m)
        for m in measurands
        if m is not None
    ]
    async_add_entities(devices, update_before_add=True)
