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
async def test_on_transaction_event_raw(
    charging_station: ChargingStation, transaction_fixture
):
    for message in transaction_fixture.messages:
        await charging_station.route_message(message.to_json())
        assert (
            charging_station.latest_sampled_values[
                Measurand.generate_key(
                    measurand=MeasurandType.energy_active_import_register,
                    location="Outlet",
                )
            ]
            == float(message.payload["meterValue"][0]["sampledValue"][0]["value"])
            / 1000
        )
        assert charging_station.latest_sampled_values[
            Measurand.generate_key(
                measurand=MeasurandType.voltage, location="Outlet", phase="L1-N"
            )
        ] == float(message.payload["meterValue"][0]["sampledValue"][1]["value"])
        assert charging_station.latest_sampled_values[
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


@pytest.mark.asyncio
async def test_on_transaction_event_start(charging_station: ChargingStation):
    # Prepare the transaction event payload
    timestamp = datetime.now(timezone.utc).isoformat()
    transaction_info = {"transaction_id": "1"}
    event_type = TransactionEventType.started

    # Call the method under test
    await charging_station.on_transaction_event(
        event_type=event_type,
        timestamp=timestamp,
        trigger_reason="RemoteStart",
        seq_no=1,
        transaction_info=transaction_info,
        meter_value=[
            {
                "timestamp": timestamp,
                "sampled_value": [
                    {
                        "measurand": MeasurandType.energy_active_import_register,
                        "value": "5000",
                        "location": "Outlet",
                    }
                ],
            }
        ],
    )

    # Validate that a new session was started
    assert charging_station.current_session is not None
    assert charging_station.current_session.start_time.isoformat() == timestamp
    assert charging_station.current_session.energy == 0


@pytest.mark.asyncio
async def test_on_transaction_event_update(charging_station: ChargingStation):
    # Prepare the transaction event payload
    timestamp = datetime.now(timezone.utc).isoformat()
    transaction_info = {"transaction_id": "1"}
    event_type = TransactionEventType.updated

    # Simulate a session already in progress
    charging_station.current_session = (
        charging_station._cs_manager.charging_station.current_session
    ) = AsyncMock()

    await charging_station.on_transaction_event(
        event_type=event_type,
        timestamp=timestamp,
        trigger_reason="MeterValuePeriodic",
        seq_no=2,
        transaction_info=transaction_info,
        meter_value=[
            {
                "timestamp": timestamp,
                "sampled_value": [
                    {
                        "measurand": MeasurandType.energy_active_import_register,
                        "value": "6000",
                        "location": "Outlet",
                    }
                ],
            }
        ],
    )

    # Validate that the session was updated
    charging_station.current_session.update_session.assert_called_once_with(
        datetime.fromisoformat(timestamp), 6.0
    )


@pytest.mark.asyncio
async def test_on_transaction_event_end(charging_station: ChargingStation):
    # Prepare the transaction event payload
    timestamp = datetime.now(timezone.utc).isoformat()
    transaction_info = {"transaction_id": "1"}
    event_type = TransactionEventType.ended

    # Simulate a session already in progress
    charging_station.current_session = (
        charging_station._cs_manager.charging_station.current_session
    ) = AsyncMock()

    await charging_station.on_transaction_event(
        event_type=event_type,
        timestamp=timestamp,
        trigger_reason="EVDeparted",
        seq_no=3,
        transaction_info=transaction_info,
        meter_value=[
            {
                "timestamp": timestamp,
                "sampled_value": [
                    {
                        "measurand": MeasurandType.energy_active_import_register,
                        "value": "7000",
                        "location": "Outlet",
                    }
                ],
            }
        ],
    )

    # Validate that the session was ended
    charging_station.current_session.end_session.assert_called_once_with(
        datetime.fromisoformat(timestamp), 7.0
    )
