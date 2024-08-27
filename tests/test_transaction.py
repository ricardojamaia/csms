from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from ocpp.v201.enums import Action, MeasurandType, TransactionEventType
from ocpp.v201.datatypes import TransactionType
from ocpp.messages import Call

from custom_components.csms.csms import (
    ChargingStation,
    ChargingStationManager,
    Measurand,
)


@pytest.fixture
def cs_manager():
    return ChargingStationManager()


@pytest.fixture
def charging_station(cs_manager: ChargingStationManager):
    # Mocking the websocket connection
    ws_connection = AsyncMock()
    return ChargingStation("CS001", ws_connection, cs_manager)


@pytest.mark.asyncio
async def test_on_transaction_event(
    charging_station: ChargingStation, transaction_fixture
):
    for message in transaction_fixture.messages:
        await charging_station.route_message(message.to_json())
        assert (
            charging_station._latest_sampled_values[
                Measurand.generate_key(
                    measurand=MeasurandType.energy_active_import_register,
                    location="Outlet",
                )
            ]
            == float(message.payload["meterValue"][0]["sampledValue"][0]["value"])
            / 1000
        )
        assert charging_station._latest_sampled_values[
            Measurand.generate_key(
                measurand=MeasurandType.voltage, location="Outlet", phase="L1-N"
            )
        ] == float(message.payload["meterValue"][0]["sampledValue"][1]["value"])
        assert charging_station._latest_sampled_values[
            Measurand.generate_key(
                measurand=MeasurandType.power_active_import, location="Outlet"
            )
        ] == float(message.payload["meterValue"][0]["sampledValue"][2]["value"])

    assert charging_station.current_session is not None
    assert (
        charging_station.current_session.start_time.isoformat()
        == transaction_fixture.start.isoformat()
    )
    assert charging_station.current_session.energy == transaction_fixture.energy
