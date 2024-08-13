from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import List

import uuid
import pytest
from ocpp.v201.enums import (
    Action,
    ChargingStateType,
    LocationType,
    ReadingContextType,
    MeasurandType,
    PhaseType,
    IdTokenType,
    TransactionEventType,
    TriggerReasonType,
)
from ocpp.messages import Call


@dataclass
class TransactionFixture:
    start: datetime
    duration: int
    energy: int
    messages: List


@dataclass
class MeterSample:
    energy: float
    voltage: float
    power: float


TRANSACTION_MESSAGES_INTERVAL_SEC = 60


@pytest.fixture
def transaction_fixture() -> TransactionFixture:
    """Fixture to create a list of JSON messages for TransactionEvent with varying energy values."""

    meter_samples = [
        MeterSample(energy=286000, voltage=230.0, power=1350),
        MeterSample(energy=287000, voltage=231.5, power=1382),
        MeterSample(energy=288000, voltage=229.0, power=4734),
        MeterSample(energy=289000, voltage=230.8, power=7200),
        MeterSample(energy=290000, voltage=230.8, power=7200),
        MeterSample(energy=291000, voltage=230.8, power=7200),
    ]
    start_time = datetime.now(timezone.utc)
    transaction_messages = []

    for i, sample in enumerate(meter_samples):
        timestamp = start_time + timedelta(
            seconds=TRANSACTION_MESSAGES_INTERVAL_SEC * i
        )
        seq_no = i

        if i == 0:
            transaction_event = TransactionEventType.started
        elif i == len(meter_samples) - 1:
            transaction_event = TransactionEventType.ended
        else:
            transaction_event = TransactionEventType.updated

        message = Call(
            unique_id=str(uuid.uuid4()),
            action=Action.TransactionEvent,
            payload={
                "eventType": transaction_event,
                "timestamp": timestamp.isoformat(),
                "triggerReason": TriggerReasonType.meter_value_periodic,
                "seqNo": seq_no,
                "transactionInfo": {
                    "transactionId": str(uuid.uuid4()),
                    "chargingState": ChargingStateType.charging,
                },
                "offline": False,
                "idToken": {"idToken": "", "type": IdTokenType.no_authorization},
                "evse": {"id": 1, "connectorId": 1},
                "meterValue": [
                    {
                        "timestamp": timestamp.isoformat(),
                        "sampledValue": [
                            {
                                "value": sample.energy,
                                "context": ReadingContextType.sample_periodic,
                                "measurand": MeasurandType.energy_active_import_register,
                                "location": LocationType.outlet,
                            },
                            {
                                "value": sample.voltage,
                                "context": ReadingContextType.sample_periodic,
                                "measurand": MeasurandType.voltage,
                                "phase": PhaseType.l1_n,
                                "location": LocationType.outlet,
                            },
                            {
                                "value": sample.power,
                                "context": ReadingContextType.sample_periodic,
                                "measurand": MeasurandType.power_active_import,
                                "location": LocationType.outlet,
                            },
                        ],
                    }
                ],
            },
        )
        transaction_messages.append(message)

    energy = (meter_samples[-1].energy - meter_samples[0].energy) / 1000
    data = TransactionFixture(
        start=start_time,
        duration=len(transaction_messages) * TRANSACTION_MESSAGES_INTERVAL_SEC,
        energy=energy,
        messages=transaction_messages,
    )
    return data
