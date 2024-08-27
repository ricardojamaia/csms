import json
import pytest
import uuid

from pathlib import Path
from unittest.mock import AsyncMock

from ocpp.charge_point import camel_to_snake_case

from custom_components.csms.cs_components import ComponentInstance, VariableInstance


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
