from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import json
import pytest
import uuid

from pathlib import Path


from ocpp.v201.enums import Action, MeasurandType, TransactionEventType
from ocpp.v201.datatypes import TransactionType
from ocpp.messages import Call

from custom_components.csms.csms import ChargingStation, ChargingStationManager


@pytest.fixture
def cs_manager():
    return ChargingStationManager()


@pytest.fixture
def charging_station(cs_manager: ChargingStationManager):
    # Mocking the websocket connection
    ws_connection = AsyncMock()
    return ChargingStation("CS001", ws_connection, cs_manager)


@pytest.mark.asyncio
async def test_notify_report(
    cs_manager: ChargingStationManager, charging_station: ChargingStation
):
    pathlist = Path("tests/data/notify_report").glob("*.json")
    for path in pathlist:
        with open(path) as file:
            message = Call(
                unique_id=str(uuid.uuid4()),
                action=Action.NotifyReport,
                payload=json.load(file),
            )

            await charging_station.route_message(message.to_json())

    assert len(cs_manager.cs_component.evses) == 1

    evse = cs_manager.cs_component.evses[1]
    evse_variables = evse.get_variable_names()

    assert set(evse_variables) == set(
        [
            "Available",
            "AvailabilityState",
            "SupplyPhases",
            "Power",
            "ACVoltage",
            "ACCurrent",
        ]
    )

    assert evse.get_variable_actual_value("Available") is True
    assert evse.get_variable_actual_value("AvailabilityState") == "Available"
    assert evse.get_variable_actual_value("SupplyPhases") == 3
