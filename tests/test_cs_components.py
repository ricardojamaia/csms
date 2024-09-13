import json
import pytest
import uuid

from pathlib import Path
from unittest.mock import AsyncMock

from ocpp.charge_point import camel_to_snake_case

from custom_components.csms.cs_components import (
    ComponentInstance,
    ConnectorComponent,
    EVSEComponent,
    ChargingStationComponent,
    VariableInstance,
)


@pytest.mark.asyncio
async def test_update_component():
    callback = AsyncMock()

    component = ComponentInstance("SampledDataCtrlr")
    component.register_variable_update_callback(callback)

    path = Path("tests/data/component_update/component_update.json")
    with open(path) as file:
        data = camel_to_snake_case(json.load(file))

        for variable in data:
            await component.update_variable(variable)

    assert callback.call_count == 5
    assert set(component.get_variable_names()) == set(
        [
            "TxEndedMeasurands",
            "TxEndedInterval",
            "TxStartedMeasurands",
            "TxUpdatedMeasurands",
            "TxUpdatedInterval",
        ]
    )


@pytest.mark.asyncio
async def test_from_to_dict():
    callback = AsyncMock()

    component = ComponentInstance("SampledDataCtrlr")
    component.register_variable_update_callback(callback)

    path = Path("tests/data/component_update/component_update.json")
    with open(path) as file:
        data = camel_to_snake_case(json.load(file))

        for variable in data:
            await component.update_variable(variable)

    d = component.to_dict()
    assert d == data


@pytest.mark.asyncio
async def test_connector_component_to_dict():
    callback = AsyncMock()

    component = ConnectorComponent(evse_id=1, connector_id=1)
    component.register_variable_update_callback(callback)

    path = Path("tests/data/component_update/connector_component.json")
    with open(path) as file:
        data = camel_to_snake_case(json.load(file))

        for variable in data:
            await component.update_variable(variable)

    d = component.to_dict()
    assert d == data


@pytest.mark.asyncio
async def test_evse_component_to_dict():
    callback = AsyncMock()

    evse_component = EVSEComponent(evse_id=1)
    evse_component.register_variable_update_callback(callback)

    path = Path("tests/data/component_update/evse_component.json")
    with open(path) as file:
        data = camel_to_snake_case(json.load(file))

        for variable in data:
            await evse_component.update_variable(variable)

    d = evse_component.to_dict()
    assert d == data


@pytest.mark.asyncio
async def test_cs_component_to_dict():
    callback = AsyncMock()

    cs_component = ChargingStationComponent()
    cs_component.register_variable_update_callback(callback)

    path = Path("tests/data/component_update/cs_component.json")
    with open(path) as file:
        data = camel_to_snake_case(json.load(file))

        for variable in data:
            await cs_component.update_variable(variable)

    d = cs_component.to_dict()
    assert d == data
