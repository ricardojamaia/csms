import pytest
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from ocpp.v201.enums import TransactionEventType
from custom_components.csms.csms import ChargingStation, ChargingStationManager
from custom_components.csms.measurand import Measurand
from ocpp.v201 import call_result


@pytest.fixture
def cs_manager():
    return ChargingStationManager()


@pytest.fixture
def charging_station(cs_manager):
    # Mocking the websocket connection
    ws_connection = AsyncMock()
    return ChargingStation("CS001", ws_connection, cs_manager)


@pytest.mark.asyncio
async def test_on_transaction_event_start(charging_station):
    # Prepare the transaction event payload
    timestamp = datetime.now(timezone.utc).isoformat()
    transaction_info = {"transaction_id": "1"}
    event_type = TransactionEventType.started

    # Call the method under test
    with patch("custom_components.csms.ChargingSession") as MockSession:
        mock_session = MockSession.return_value
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
                            "measurand": Measurand.energy_active_import_register,
                            "value": "5000",
                            "location": "Outlet",
                        }
                    ],
                }
            ],
        )

    # Validate that a new session was started
    mock_session.start_session.assert_called_once_with(
        datetime.fromisoformat(timestamp),
        5.0,  # 5000 divided by 1000 as per your logic
    )
    assert charging_station.current_session is not None
    assert charging_station.current_session.energy == 0


@pytest.mark.asyncio
async def test_on_transaction_event_update(charging_station):
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
                        "measurand": Measurand.energy_active_import_register,
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
async def test_on_transaction_event_end(charging_station):
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
                        "measurand": Measurand.energy_active_import_register,
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
