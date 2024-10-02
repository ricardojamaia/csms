import asyncio
import json
import pytest
import uuid

from pathlib import Path
from unittest.mock import AsyncMock

from ocpp.v201.enums import Action
from ocpp.messages import Call
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
async def test_status_notification(
    cs_manager: ChargingStationManager, charging_station: ChargingStation
):
    path = Path("tests/data/status_notification/status_notification_available.json")
    with open(path) as file:
        message = Call(
            unique_id=str(uuid.uuid4()),
            action=Action.StatusNotification,
            payload=json.load(file),
        )

        # Route the message and trigger the custom loop
        await charging_station.route_message(message.to_json())

        # Directly invoke the custom loop task
        await cs_manager.loop()

        # Assertions
        assert len(cs_manager.cs_component.evses) == 1
        assert (
            cs_manager.cs_component.evses[1]
            .connectors[1]
            .get_variable_actual_value("AvailabilityState")
            == "Available"
        )
