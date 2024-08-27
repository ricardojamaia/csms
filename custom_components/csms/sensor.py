from datetime import datetime, timedelta

import uuid

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import Entity

from .cs_components import ComponentInstance, VariableInstance
from .const import DOMAIN
from .csms import (
    ChargingSession,
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


class CurrentChargingSessionSensor(Entity):
    """Representation of the current charging session sensor."""

    def __init__(
        self, charging_station: ChargingStation, cs_manager: ChargingStationManager
    ):
        self._charging_station = charging_station
        self._cs_manager = cs_manager
        self._attr_name = "Current Charging Session"
        self._attr_unique_id = "current_charging_session"
        self._attr_unit_of_measurement = "kWh"
        self._attr_device_class = "energy"

    @property
    def state(self):
        if (
            not self._cs_manager.charging_station
            or not self._cs_manager.charging_station.current_session
        ):
            return None

        return self._cs_manager.charging_station.current_session.energy

    @property
    def extra_state_attributes(self):
        if (
            not self._cs_manager.charging_station
            or not self._cs_manager.charging_station.current_session
        ):
            return {}

        session: ChargingSession = self._cs_manager.charging_station.current_session

        return {
            "session_id": session.session_id,
            "start_time": session.start_time.astimezone(),
            "duration": str(timedelta(seconds=session.duration)),
            "cost": session.cost,
        }

    async def async_added_to_hass(self):
        """Run when this Entity has been added to HA."""
        self._cs_manager.register_callback(
            self._attr_unique_id, self.async_write_ha_state
        )

    async def async_will_remove_from_hass(self):
        """Entity being removed from hass."""
        self._cs_manager.unregister_callback(
            self._attr_unique_id, self.async_write_ha_state
        )


class ComponentVariableSensor(Entity):
    """Representation of a Home Assistant sensor for a specific component variable."""

    UNIT_TO_DEVICE_CLASS = {
        "%": "battery",
        "W": "power",
        "Watt": "power",
        "kW": "power",
        "Watthours": "energy",
        "kWh": "energy",
        "A": "current",
        "Ampere": "current",
    }

    def __init__(
        self,
        cs_manager: ChargingStationManager,
        component: ComponentInstance,
        variable_name: str,
        instance: str | None = None,
    ):
        """Initialize the sensor."""
        self._cs_manager = cs_manager
        self._component = component
        self._variable_name = variable_name
        self._instance = instance
        self._name = f"{component.name}_{variable_name}"  # Sensor name
        self._state = None
        self._attributes = {}

        self._attr_unique_id = uuid.uuid4()

        # Register a callback to update the state whenever the variable is updated
        self._component.register_variable_update_callback(self._variable_updated)

    @property
    def device_info(self):
        """Return information to link this entity with the correct device."""
        return self._cs_manager.device_info

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self) -> str | None:
        """Return the current state of the sensor."""
        return self._state

    @property
    def extra_state_attributes(self) -> dict:
        """Return the state attributes."""
        return self._attributes

    @property
    def unit_of_measurement(self) -> str | None:
        """Return the unit of measurement for the sensor."""

        variable = self._component.variables.get((self._variable_name, self._instance))
        if variable and variable.characteristics:
            return variable.characteristics.unit
        return None

    @property
    def device_class(self) -> str | None:
        """Return the device class of the sensor."""

        variable = self._component.variables.get((self._variable_name, self._instance))
        if variable and variable.characteristics:
            return self.UNIT_TO_DEVICE_CLASS.get(variable.characteristics.unit)
        return None

    async def _variable_updated(self, variable: VariableInstance):
        """Update sensor state when a variable is updated."""

        if variable.name == self._variable_name and variable.instance == self._instance:
            self._state = self._component.get_variable_actual_value(
                self._variable_name, self._instance
            )
            self._attributes = {attr.type: attr.value for attr in variable.attributes}
            self.async_write_ha_state()

    async def async_update(self):
        """Fetch new state data for the sensor."""
        # This method is optional and can be used to refresh the sensor state periodically
        self._state = self._component.get_variable_actual_value(
            self._variable_name, self._instance
        )
        variable = self._component.variables.get((self._variable_name, self._instance))
        if variable:
            self._attributes = {attr.type: attr.value for attr in variable.attributes}


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
    cs_manager._new_measurands_callback = create_and_add_devices

    # Get stored discovered measurands from config_entry and convert them back to Measurand instances
    stored_measurands = config_entry.data.get("discovered_measurands", [])
    measurands = [Measurand.from_dict(m) for m in stored_measurands if m is not None]

    # Create and add sensors based on stored discovered measurands
    devices = [
        ChargingStationSensor(cs_manager.charging_station, cs_manager, m)
        for m in measurands
        if m is not None
    ]

    devices.append(
        CurrentChargingSessionSensor(cs_manager.charging_station, cs_manager)
    )

    for variable in cs_manager.cs_component.get_variable_names():
        devices.append(
            ComponentVariableSensor(cs_manager, cs_manager.cs_component, variable)
        )

    async_add_entities(devices, update_before_add=True)
