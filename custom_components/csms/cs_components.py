"""Implementation of the Device Model."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from dateutil import parser


@dataclass
class VariableAttribute:
    """Attribute data of a variable."""

    type: str | None = None
    value: str = None
    mutability: str | None = None
    persistent: bool | None = None
    constant: bool | None = None

    def to_dict(self):
        """Convert the VariableAttribute to a dict, excluding attributes with None values."""
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class VariableCharacteristics:
    """Fixed read-only parameters of a variable."""

    data_type: str
    supports_monitoring: bool
    unit: str | None = None
    min_limit: float | None = None
    max_limit: float | None = None
    values_list: str | None = None

    def to_dict(self):
        """Convert the VariableCharacteristics to a dict, excluding attributes with None values."""
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class VariableInstance:
    """A         Variable instance of the device model."""

    name: str
    instance: str | None
    attributes: list[VariableAttribute] = field(default_factory=list)
    characteristics: VariableCharacteristics | None = None

    def to_dict(self):
        """Convert the VariableInstance to a dict, excluding 'instance' if it's None."""
        data = {"name": self.name}

        # Only include 'instance' if it's not None
        if self.instance is not None:
            data["instance"] = self.instance

        return data


class ComponentInstance:
    """A component of the device model."""

    def __init__(self, name: str, instance: str | None = None) -> None:
        """Initialise Component Instance."""
        self.name = name
        self.instance = instance
        self.variables: dict[tuple[str, str | None], VariableInstance] = {}
        self._callbacks: list[Callable[[VariableInstance], Awaitable[None]]] = []

    async def update_variable(self, data: dict):
        """Update a given component variable based on the JSON received from the CS."""

        component_name = data.get("component", {}).get("name")
        component_instance = data.get("component", {}).get("instance")
        if component_name != self.name or component_instance != self.instance:
            return

        variable_name = data.get("variable", {}).get("name")
        variable_instance = data.get("variable", {}).get("instance")
        variable_attributes_json = data.get("variable_attribute", [])
        variable_characteristics_json = data.get("variable_characteristics", {})

        if (variable_name, variable_instance) not in self.variables:
            # Create a new Variable if it doesn't exist
            self.variables[(variable_name, variable_instance)] = VariableInstance(
                name=variable_name,
                instance=variable_instance,
                characteristics=self._build_characteristics(
                    variable_characteristics_json
                ),
            )

        # Update or add the attributes
        variable = self.variables[(variable_name, variable_instance)]
        for attribute_json in variable_attributes_json:
            self._update_or_add_attribute(variable, attribute_json)

        for callback in self._callbacks:
            await callback(self.variables[(variable_name, variable_instance)])

    def _update_or_add_attribute(
        self, variable: VariableInstance, attribute_json: dict
    ):
        """Update an existing attribute or add a new one."""
        attr_type = attribute_json.get("type")
        value = attribute_json.get("value")
        mutability = attribute_json.get("mutability")
        persistent = attribute_json.get("persistent")
        constant = attribute_json.get("constant")

        for attr in variable.attributes:
            if attr.type == attr_type:
                # Update the existing attribute
                attr.value = value
                attr.mutability = mutability
                attr.persistent = persistent
                attr.constant = constant
                break
        else:
            # If no attribute with the same type is found, add a new one
            variable.attributes.append(
                VariableAttribute(
                    type=attr_type,
                    value=value,
                    mutability=mutability,
                    persistent=persistent,
                    constant=constant,
                )
            )

    def _build_characteristics(
        self, characteristics_json: dict
    ) -> VariableCharacteristics:
        """Build VariableCharacteristics from JSON."""
        return VariableCharacteristics(
            data_type=characteristics_json.get("data_type"),
            supports_monitoring=characteristics_json.get("supports_monitoring", False),
            unit=characteristics_json.get("unit"),
            min_limit=characteristics_json.get("min_limit"),
            max_limit=characteristics_json.get("max_limit"),
            values_list=characteristics_json.get("values_list"),
        )

    def _convert_value(self, value: str, data_type: str) -> any:
        """Convert the value to the appropriate data type."""

        if data_type == "integer":
            return int(value)

        if data_type == "decimal":
            return float(value)

        if data_type == "dateTime":
            parser.isoparse(value)

        if data_type == "boolean":
            return value.lower() == "true"

        # Default to returning the value as a string
        return str(value)

    def get_variable_names(self) -> list[str]:
        """Return a list of unique variable names known to this component."""
        return list({name for name, _ in self.variables})

    def get_variable_actual_value(self, name: str, instance: str | None = None):
        """Return actual value of a component variable."""
        variable = self.variables.get((name, instance))
        if variable is not None:
            for attribute in variable.attributes:
                if attribute.type == "Actual":
                    return self._convert_value(
                        attribute.value, variable.characteristics.data_type
                    )

        return None

    def set_variable_actual_value(self, name: str, value, instance: str | None = None):
        """Set the actual vlue of a variable."""
        variable = self.variables.get((name, instance))

        if variable is not None:
            for attribute in variable.attributes:
                if attribute.type == "Actual":
                    attribute.value = value
                    return True

        return False

    def to_dict(self) -> dict:
        """Convert ComponentInstance to a dictionary format using OCPP models."""
        data = []

        for variable in self.variables.values():
            entry = {
                "component": {"name": self.name},
                "variable": variable.to_dict(),
                "variable_attribute": [
                    attribute.to_dict() for attribute in variable.attributes
                ],
                "variable_characteristics": variable.characteristics.to_dict(),
            }

            if self.instance is not None:
                entry["component"]["instance"] = self.instance

            data.append(entry)

        return data

    def register_variable_update_callback(
        self, callback: Callable[[VariableInstance], Awaitable[None]]
    ):
        """Register a callback to be called when one of the component variiables is updated.

        The callback will receive the variable instances updated.
        """
        if callback not in self._callbacks:
            self._callbacks.append(callback)

    def unregister_variable_update_callback(
        self, callback: Callable[[VariableInstance], Awaitable[None]]
    ):
        """Unegister a callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)


class ConnectorComponent(ComponentInstance):
    """A Connector of the Charing Station."""

    def __init__(self, evse_id: int, connector_id: int) -> None:
        """Initialise ConnectorComponent."""

        super().__init__(name="Connector")
        self.evse_id: int = evse_id
        self.connector_id: int = connector_id
        self.components: dict[tuple[str, str | None], ComponentInstance] = {}

    async def update_variable(self, data: dict):
        """Update a Connector variable based on the data received from the Charging Station."""

        component_name = data.get("component", {}).get("name")
        component_instance = data.get("component", {}).get("instance")
        evse_id = data.get("component", {}).get("evse", {}).get("id")
        connector_id = data.get("component", {}).get("evse", {}).get("connector_id")

        if evse_id == self.evse_id and connector_id == self.connector_id:
            if component_name == self.name:
                # Variable of the "Connector" component itself.
                await super().update_variable(data)
            else:
                if (component_name, component_instance) not in self.components:
                    self.components[(component_name, component_instance)] = (
                        ComponentInstance(
                            name=component_name, instance=component_instance
                        )
                    )
                await self.components[
                    (component_name, component_instance)
                ].update_variable(data)

    def to_dict(self) -> dict:
        """Convert ConnectorComponent to a dictionary format using OCPP models."""
        data = []

        data = super().to_dict()
        for variable in data:
            variable["component"]["evse"] = {}
            variable["component"]["evse"]["id"] = self.evse_id
            variable["component"]["evse"]["connector_id"] = self.connector_id

        for component in self.components.values():
            d = component.to_dict()
            for variable in d:
                variable["component"]["evse"] = {}
                variable["component"]["evse"]["id"] = self.evse_id
                variable["component"]["evse"]["connector_id"] = self.connector_id
            data += d

        return data


class EVSEComponent(ComponentInstance):
    """EVSE of a Charging Station."""

    def __init__(self, evse_id: int) -> None:
        """Initialise EVSE component."""

        super().__init__(name="EVSE")
        self.evse_id: int = evse_id
        self.connectors: dict[int, ConnectorComponent] = {}
        self.components: dict[tuple[str, str | None], ComponentInstance] = {}

    async def update_variable(self, data: dict):
        """Update a EVSE variable based on the data received from the Charging Station."""

        component_name = data.get("component", {}).get("name")
        component_instance = data.get("component", {}).get("instance")
        evse_id = data.get("component", {}).get("evse", {}).get("id")
        connector_id = data.get("component", {}).get("evse", {}).get("connector_id")

        if evse_id == self.evse_id:
            if connector_id is not None:
                # Variable of one of the connectors of this EVSE
                if connector_id not in self.connectors:
                    self.connectors[connector_id] = ConnectorComponent(
                        evse_id=self.evse_id, connector_id=connector_id
                    )
                await self.connectors[connector_id].update_variable(data)
            elif component_name == self.name:
                # Variable of the "EVSE" component itself.
                await super().update_variable(data)
            else:
                # Variable of a component of the EVSE
                if (component_name, component_instance) not in self.components:
                    self.components[(component_name, component_instance)] = (
                        ComponentInstance(
                            name=component_name, instance=component_instance
                        )
                    )
                await self.components[
                    (component_name, component_instance)
                ].update_variable(data)

    def to_dict(self) -> dict:
        """Convert EVSEComponent to a dictionary format using OCPP models."""
        data = []

        evse_variables = super().to_dict()
        for variable in evse_variables:
            variable["component"]["evse"] = {}
            variable["component"]["evse"]["id"] = self.evse_id

        data += evse_variables

        for connector in self.connectors.values():
            connector_variables = connector.to_dict()
            data += connector_variables

        for component in self.components.values():
            component_variables = component.to_dict()
            for variable in component_variables:
                variable["component"]["evse"] = {}
                variable["component"]["evse"]["id"] = self.evse_id
            data += component_variables

        return data


class ChargingStationComponent(ComponentInstance):
    """Charging Station device model."""

    def __init__(self) -> None:
        """Initialise ChargingStationComponent."""

        super().__init__("ChargingStation")

        self.evses: dict[int, EVSEComponent] = {}
        self.components: dict[tuple[str, str | None], ComponentInstance] = {}

    async def update_variable(self, data: dict):
        """Update a ChargingStation variable based on the data received from the Charging Station."""

        component_name = data.get("component", {}).get("name")
        component_instance = data.get("component", {}).get("instance")
        evse_id = data.get("component", {}).get("evse", {}).get("id")

        if evse_id is not None:
            # Variable of one of the EVSEs of this ChargingStation
            if evse_id not in self.evses:
                self.evses[evse_id] = EVSEComponent(evse_id=evse_id)
            await self.evses[evse_id].update_variable(data)
        elif component_name == self.name:
            # Variable of the "ChargingStation" component itself.
            await super().update_variable(data)
        else:
            # Variable of one of the components of the ChargingStaton
            if (component_name, component_instance) not in self.components:
                self.components[(component_name, component_instance)] = (
                    ComponentInstance(name=component_name, instance=component_instance)
                )
            await self.components[(component_name, component_instance)].update_variable(
                data
            )

    def to_dict(self) -> dict:
        """Convert ChargingComponent to a dictionary format using OCPP models."""
        data = []

        data += super().to_dict()

        for evse in self.evses.values():
            data += evse.to_dict()

        for component in self.components.values():
            data += component.to_dict()

        return data
