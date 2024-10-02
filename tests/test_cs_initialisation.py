from unittest.mock import AsyncMock, patch, call
from pathlib import Path

import pytest
import yaml

from ocpp.v201.call import SetChargingProfile
from ocpp.v201.datatypes import (
    ChargingProfileType,
    ChargingScheduleType,
    ChargingSchedulePeriodType,
)

from custom_components.csms.csms import (
    ChargingStationManager,
    ChargingStation,
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
async def test_set_charging_profiles(
    cs_manager: ChargingStationManager, charging_station: ChargingStation
):
    cs_manager.charging_station = charging_station
    cs_manager.charging_station.call = AsyncMock()

    path = Path("tests/data/configuration/configuration.yaml")
    with open(path) as file:
        config = yaml.safe_load(file)
        cs_config = config["csms"]["charging_station"][0]

        await cs_manager.initialise(config=cs_config)
        assert cs_manager.config == cs_config

        calls = [
            call(
                SetChargingProfile(
                    evse_id=1,
                    charging_profile=ChargingProfileType(
                        id=1,
                        stack_level=0,
                        charging_profile_purpose="TxDefaultProfile",
                        charging_profile_kind="Recurring",
                        recurrency_kind="Daily",
                        charging_schedule=[
                            ChargingScheduleType(
                                id=1,
                                start_schedule="2024-07-29T00:00:00Z",
                                duration=86400,
                                charging_rate_unit="A",
                                charging_schedule_period=[
                                    ChargingSchedulePeriodType(
                                        start_period=0,
                                        limit=6,
                                    )
                                ],
                            )
                        ],
                    ),
                )
            )
        ]
        cs_manager.charging_station.call.assert_has_calls(calls)
