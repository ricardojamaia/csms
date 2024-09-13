from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import json
import pytest
import uuid
import yaml

from pathlib import Path


from ocpp.v201.enums import Action
from ocpp.v201.call_result import GetVariables
from ocpp.messages import Call
from ocpp.charge_point import camel_to_snake_case

from custom_components.csms.csms import ChargingStation, ChargingStationManager


class TestChargingStationManager(ChargingStationManager):
    """Extended ChargingStationManager class with a custom loop for testing."""

    async def loop(self):
        """Custom loop function to process events from the _event_queue."""
        while not self._event_queue.empty():
            component, data = await self._event_queue.get()
            await component.update_variable(data)


@pytest.fixture
def cs_manager():
    return TestChargingStationManager()


@pytest.fixture
def charging_station(cs_manager: ChargingStationManager):
    # Mocking the websocket connection
    ws_connection = AsyncMock()
    return ChargingStation("CS001", ws_connection, cs_manager)


@pytest.mark.asyncio
async def test_get_variables(
    cs_manager: ChargingStationManager, charging_station: ChargingStation
):
    path = Path("tests/data/get_variables/get_variables.json")

    with open(path) as file:
        data = camel_to_snake_case(json.load(file))
        response = GetVariables(data)

        cs_manager.update_variables(data)

    # Directly invoke the custom loop task
    await cs_manager.loop()

    assert (
        cs_manager.cs_component.get_variable_actual_value("ACVoltage") == "235.000000"
    )
