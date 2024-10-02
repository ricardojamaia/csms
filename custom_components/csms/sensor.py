"""Implement sensor entities of the CSMS."""

from datetime import timedelta

from dateutil import parser

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import DOMAIN
from .cs_components import ComponentInstance, VariableInstance
from .csms import (
    ChargingSession,
    ChargingStationManagementSystem,
    ChargingStationManager,
)
from .measurand import Measurand


class ChargingStationSensor(Entity):
    """Representation of a Charging Station Sensor."""

    should_poll = False

    def __init__(
        self,
        cs_id: str,
        cs_manager: ChargingStationManager,
        measurand: Measurand,
    ) -> None:
        """Initialize the sensor."""
        self._cs_id = cs_id
        self._cs_manager = cs_manager
        self._measurand = measurand
        self._attr_unique_id = f"{self._cs_id}_{measurand.unique_key}"
        self._attr_name = measurand.name
        self._attr_unit_of_measurement = measurand.unit_of_measurement
        self._attr_device_class = measurand.device_class
        self._state = "Unavailable"

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


class CurrentChargingSessionSensor(SensorEntity, RestoreEntity):
    """Representation of the current charging session sensor."""

    should_poll = False

    def __init__(self, cs_id: str, cs_manager: ChargingStationManager) -> None:
        """Initialise the sensor entity."""
        self._cs_id = cs_id
        self._cs_manager = cs_manager
        self._attr_name = "Current Charging Session"
        self._attr_unique_id = f"{self._cs_id}_current_charging_session"
        self._attr_unit_of_measurement = "kWh"
        self._attr_device_class = "energy"
        self._state = None
        self._attr_extra_state_attributes = {}

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

    @property
    def state(self):
        """Return energy currently consumed by the charging session."""
        return self._state

    @property
    def extra_state_attributes(self):
        """Return extra attributes."""
        return self._attr_extra_state_attributes

    async def async_added_to_hass(self):
        """Run when this Entity has been added to HA."""
        await super().async_added_to_hass()

        # Restore the last known state and attributes
        last_state = await self.async_get_last_state()
        if last_state and last_state.state != "unknown":
            self._state = last_state.state
            self._attr_extra_state_attributes = last_state.attributes or {}

            start_time = parser.isoparse(last_state.attributes.get("start_time"))
            start_energy_registry = float(
                last_state.attributes.get("start_energy_registry")
            )
            transaction_id = last_state.attributes.get("transaction_id")
            charging_state = last_state.attributes.get("charging_state")
            if start_time is not None and start_energy_registry is not None:
                self._cs_manager.current_session = ChargingSession(
                    start_time, start_energy_registry, transaction_id, charging_state
                )

        # Register the callback
        self._cs_manager.register_callback(self._attr_unique_id, self._updated)

    async def async_will_remove_from_hass(self):
        """Entity being removed from hass."""
        self._cs_manager.unregister_callback(self._attr_unique_id, self._updated)

    def _updated(self):
        """Handle updates to the charging session and write to HA state."""
        if not self._cs_manager.current_session:
            return

        session: ChargingSession = self._cs_manager.current_session

        # Update attributes with session details
        self._attr_extra_state_attributes = {
            "session_id": session.session_id,
            "start_time": session.start_time.astimezone(),
            "start_energy_registry": session.start_energy_registry,
            "duration": str(timedelta(seconds=session.duration)),
            "charging_state": session.charging_state,
            "is_active": session.is_active(),
        }

        # Update state with the current session energy
        self._state = session.energy

        # Write the updated state and attributes to Home Assistant
        self.async_write_ha_state()


class ComponentVariableSensor(Entity):
    """Representation of a Home Assistant sensor for a specific component variable."""

    should_poll = False

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
        cs_id: str,
        cs_manager: ChargingStationManager,
        component: ComponentInstance,
        variable_name: str,
        instance: str | None = None,
    ) -> None:
        """Initialize the sensor."""
        self._cs_id = cs_id
        self._cs_manager = cs_manager
        self._component = component
        self._variable_name = variable_name
        self._instance = instance
        self._name = f"{component.name} {variable_name}{" " + self._instance if self._instance is not None else ""}"
        self._state = None
        self._attributes = {}

        self._attr_unique_id = f"{self._cs_id}_{component.name}_{variable_name}{"_" + self._instance if self._instance is not None else ""}"

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

    async def async_added_to_hass(self):
        """Run when this Entity has been added to HA."""
        # Register a callback to update the state whenever the variable is updated
        self._component.register_variable_update_callback(self._variable_updated)

    async def async_will_remove_from_hass(self):
        """Entity being removed from hass."""
        # Unregister the callback
        self._component.unregister_variable_update_callback(self._variable_updated)

    async def async_update(self):
        """Fetch new state data for the sensor."""
        # This method is optional and can be used to refresh the sensor state periodically
        self._state = self._component.get_variable_actual_value(
            self._variable_name, self._instance
        )
        variable = self._component.variables.get((self._variable_name, self._instance))
        if variable:
            self._attributes = {attr.type: attr.value for attr in variable.attributes}


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Add sensors for passed config_entry in HA."""
    csms: ChargingStationManagementSystem = hass.data[DOMAIN]

    if discovery_info is not None:
        # Extract charging station data or any other needed values
        charging_stations = discovery_info.get("charging_station", [])

        for cs in charging_stations:
            cs_id = cs.get("id")
            cs_manager = csms.cs_managers.get(cs_id)

            if cs_manager is not None:
                measurands_json = cs.get("measurands", [])
                cs_manager.measurands = [
                    Measurand.from_dict(m) for m in measurands_json
                ]

                variables_json = cs.get("variables", [])
                for item in variables_json:
                    await cs_manager.cs_component.update_variable(item)

                sensors = [
                    ChargingStationSensor(cs_id, cs_manager, m)
                    for m in cs_manager.measurands
                ]

                sensors.append(CurrentChargingSessionSensor(cs_id, cs_manager))

                sensors.extend(
                    [
                        ComponentVariableSensor(
                            cs_id,
                            cs_manager,
                            cs_manager.cs_component,
                            variable_name,
                            variable_instance,
                        )
                        for variable_name, variable_instance in cs_manager.cs_component.variables
                    ]
                )
                sensors.extend(
                    [
                        ComponentVariableSensor(
                            cs_id,
                            cs_manager,
                            component,
                            variable_name,
                            variable_instance,
                        )
                        for component in cs_manager.cs_component.components.values()
                        for variable_name, variable_instance in component.variables
                    ]
                )
                sensors.extend(
                    [
                        ComponentVariableSensor(
                            cs_id, cs_manager, evse, variable_name, variable_instance
                        )
                        for evse in cs_manager.cs_component.evses.values()
                        for variable_name, variable_instance in evse.variables
                    ]
                )

                sensors.extend(
                    [
                        ComponentVariableSensor(
                            cs_id,
                            cs_manager,
                            connector,
                            variable_name,
                            variable_instance,
                        )
                        for evse in cs_manager.cs_component.evses.values()
                        for connector in evse.connectors.values()
                        for variable_name, variable_instance in connector.variables
                    ]
                )

        async_add_entities(sensors, update_before_add=True)
