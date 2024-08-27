"""Charging Stations Management System."""

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import os
import ssl
from typing import Callable, Coroutine
import uuid

from dateutil import parser

from ocpp.routing import create_route_map, on

from ocpp.v201 import ChargePoint as cp, call, call_result

from ocpp.v201.datatypes import (
    ChargingProfileType,
    ChargingSchedulePeriodType,
    ChargingScheduleType,
    ComponentType,
    ComponentVariableType,
    SetVariableDataType,
    VariableType,
)
from ocpp.v201.enums import (
    Action,
    AlignedDataCtrlrVariableName,
    ChargingProfileKindType,
    ChargingProfilePurposeType,
    ChargingRateUnitType,
    ControllerComponentName,
    MeasurandType,
    RecurrencyKindType,
    RegistrationStatusType,
    ReportBaseType,
    SampledDataCtrlrVariableName,
    TransactionEventType,
    TxCtrlrVariableName,
    TxStartStopPointType,
)
import websockets

from .const import DOMAIN
from .cs_components import ChargingStationComponent, EVSEComponent
from .measurand import Measurand, default_measurands


class ChargingSession:
    """Holds information regsarding a charging session initiated on a Charging Station."""

    def __init__(self):
        self.session_id = str(uuid.uuid4())
        self.start_time: datetime = None
        self.last_update: datetime = None
        self.end_time: datetime = None
        self.start_energy_registry = None
        self.energy = 0

    def start_session(self, start_time: datetime, energy_registry_value):
        """Starts the charging session."""
        self.start_time = start_time
        self.last_update = start_time
        self.start_energy_registry = energy_registry_value

    def update_session(self, update_time: datetime, energy_registry_value):
        """Updates the amount of energy consumed during the charging session."""
        self.last_update = update_time
        if self.start_energy_registry is not None and energy_registry_value is not None:
            self.energy = energy_registry_value - self.start_energy_registry

    def end_session(self, end_time: datetime, energy_registry_value):
        """Ends a charging session."""
        self.last_update = end_time
        self.end_time = end_time
        if self.start_energy_registry is not None and energy_registry_value is not None:
            self.energy = energy_registry_value - self.start_energy_registry

    @property
    def duration(self) -> timedelta:
        """Returns the amount of energy consumed on the charging session."""
        if self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return (self.last_update - self.start_time).total_seconds()

    @property
    def cost(self) -> int:
        return self.energy * 0.2


class ChargingStationManager:
    def __init__(self) -> None:
        self.charging_station: ChargingStation = None
        self._callbacks = {}
        self._new_measurands_callback: Callable[[list[Measurand]], Coroutine[None]] = (
            None
        )
        self.id = "DE*BMW*EDAKG4234502990WE"

        self._latest_sampled_values = {}
        self._pending_reports = []
        self.current_session = None
        self.evse_components: dict[int, EVSEComponent] = {}
        self.cs_component: ChargingStationComponent = ChargingStationComponent()

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self.id)},
            "name": f"Charging Station {self.id}",
            "manufacturer": "BMW",
            "model": "Gen 4",
            "sw_version": "1.0",
        }

    def get_latest_measurand_value(self, unique_key):
        """Return the latest value of the specified measurand."""
        if self.charging_station is not None:
            return self.charging_station._latest_sampled_values.get(
                unique_key, "Unavailable"
            )
        return "Unavailable"

    def register_callback(self, unique_key, callback):
        """Register a callback to be called when the measurand value changes."""
        if unique_key not in self._callbacks:
            self._callbacks[unique_key] = []
        self._callbacks[unique_key].append(callback)

    def unregister_callback(self, unique_key, callback):
        """Unregister a callback."""
        if unique_key in self._callbacks:
            self._callbacks[unique_key].remove(callback)
            if not self._callbacks[unique_key]:
                del self._callbacks[unique_key]

    def publish_updates(self):
        for measurand_callbacks in self._callbacks.values():
            for callback in measurand_callbacks:
                callback()

    async def initialise(self, config):
        # Discover available measurands
        supported_measurands = default_measurands
        logging.info(f"Discovered measurands: {supported_measurands}")

        # Call the callback to add devices to Home Assistant
        if self._new_measurands_callback:
            await self._new_measurands_callback(supported_measurands)

        # Initialize the charging station
        for item in config:
            await self.cs_component.update_variable(item)

    async def on_transaction_event(
        self, event_type, timestamp, trigger_reason, seq_no, transaction_info, **kwargs
    ):
        logging.info(
            "Transaction event received with event_type: %s, timestamp: %s, trigger_reason: %s, seq_no: %s and transaction_info:%s",
            event_type,
            timestamp,
            trigger_reason,
            seq_no,
            transaction_info,
        )

        if "meter_value" in kwargs:
            meter_values = kwargs["meter_value"]
            try:
                for mv in meter_values:
                    sampled_values = mv["sampled_value"]
                    for sampled_value in sampled_values:
                        # Option fields
                        measurand = sampled_value.get("measurand")
                        phase = sampled_value.get("phase")
                        location = sampled_value.get("location")

                        # Value is mandatory
                        value = (
                            sampled_value["value"]
                            if measurand != MeasurandType.energy_active_import_register
                            else int(sampled_value["value"]) / 1000
                        )

                        # Update the dictionary with the latest value for each measurand
                        self._latest_sampled_values[
                            Measurand.generate_key(
                                measurand=measurand, phase=phase, location=location
                            )
                        ] = value

            except KeyError as e:
                logging.error("Missing expected field in transaction_info: %s", e)
            except TypeError as e:
                logging.error("Unexpected data structure in transaction_info: %s", e)

            logging.info("Latest sampled values: %s", self._latest_sampled_values)

            self._cs_manager.publish_updates()

        energy_register_value = self._latest_sampled_values.get(
            Measurand.generate_key(
                measurand="Energy.Active.Import.Register", phase="", location="Outlet"
            )
        )
        t = parser.isoparse(timestamp)

        if event_type == TransactionEventType.started:
            self.current_session = ChargingSession()
            self.current_session.start_session(t, energy_register_value)
        elif event_type == TransactionEventType.updated:
            if self.current_session is None:
                self.current_session = ChargingSession()
                self.current_session.start_session(t, energy_register_value)

                logging.error(
                    "Transaction event received but not transaction is ongoing."
                )
            else:
                self.current_session.update_session(t, energy_register_value)

        elif event_type == TransactionEventType.ended:
            self.current_session.end_session(t, energy_register_value)

        return call_result.TransactionEventPayload()

    @on(Action.NotifyReport)
    async def _on_notify_report(
        self,
        request_id: int,
        generated_at: str,
        seq_no: int,
        report_data: list,
        tbc: bool = False,
        **kwargs,
    ):
        logging.info("NotifyReport.")
        # Store the report data
        self._pending_reports.extend(report_data)

        if not tbc:
            # If tbc is False, process all accumulated reports
            report_data = self._pending_reports
            self._pending_reports = []  # Reset the storage

            # Update the EVSE component state using the accumulated data
            for item in report_data:
                await self.cs_component.update_variable(item)

        return call_result.NotifyReportPayload()

    @on(Action.MeterValues)
    async def on_meter_values(self, evse_id, meter_value):
        # Process meter values
        for m_value in meter_value:
            timestamp = m_value["timestamp"]
            for sampled_value in m_value["sampled_value"]:
                try:
                    value = None
                    context = None
                    unit = None
                    value = sampled_value["value"]
                    context = sampled_value["context"]
                    unit = sampled_value["unit_of_measure"]["unit"]
                except KeyError:
                    logging.info("Key Error")
                logging.info(
                    "Timestamp: %s, Value: %s, Context: %s, Unit: %s",
                    logging.info,
                    value,
                    context,
                    unit,
                )

        return call_result.MeterValues()

    @on(Action.BootNotification)
    async def on_boot_notification(self, charging_station, reason, **kwargs):
        logging.debug(
            "BootNotification received with charging_station: %s, reason: %s",
            charging_station,
            reason,
        )
        return call_result.BootNotification(
            current_time=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z",  # noqa: UP017
            interval=10,
            status=RegistrationStatusType.accepted,
        )

    @on("Heartbeat")
    def on_heartbeat(self):
        logging.debug("Received a Heartbeat!")
        return call_result.Heartbeat(
            current_time=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"  # noqa: UP017
        )

    @on(Action.StatusNotification)
    def on_status_notification(
        self, timestamp, connector_status, evse_id, connector_id
    ):
        logging.debug(
            "Received a StatusNotification with evse: %s, connector_id: %s, and connector_status: %s",
            evse_id,
            connector_id,
            connector_status,
        )
        return call_result.StatusNotificationPayload()


class ChargingStation(cp):
    def __init__(self, id, connection, cs_manager: ChargingStationManager):
        super().__init__(id, connection)
        self._cs_manager: ChargingStationManager = cs_manager

        self.route_map = create_route_map(cs_manager)

    async def set_tx_default_profile(self, max_current: int):
        """Send a Smart Charging command to the charging station to set the maximum current."""
        profile = ChargingProfileType(
            id=1,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurposeType.tx_default_profile,
            charging_profile_kind=ChargingProfileKindType.recurring,
            charging_schedule=[
                ChargingScheduleType(
                    id=1,
                    charging_rate_unit=ChargingRateUnitType.amps,
                    charging_schedule_period=[
                        ChargingSchedulePeriodType(
                            start_period=0, limit=max_current
                        ),  # Limit max current
                    ],
                    start_schedule="2024-07-29T07:55:00Z",  # Start time
                    duration=64800,  # Duration of 8:55 to 18:30 in seconds (9 hours 35 minutes)
                )
            ],
            recurrency_kind=RecurrencyKindType.daily,
        )

        request = call.SetChargingProfile(evse_id=1, charging_profile=profile)

        try:
            response = await self.call(request)
            logging.debug("SetChargingProfile response: %s", response)
        except Exception as e:
            logging.error("Error setting default charging profile: %s", e)


class ChargingStationManagementSystem:
    """Placeholder class to make tests pass."""

    def __init__(self) -> None:
        """Initialize."""
        self.port = 9520
        self.cert_path = "/ssl/cert.pem"
        self.key_path = "/ssl/key.pem"
        self.charging_station: ChargingStation = None
        self.cs_manager: ChargingStationManager = ChargingStationManager()
        self.hass = None

    async def initialise(self, config):
        await self.cs_manager.initialise(config)

    async def run_server(self):
        logging.info("Current dir: %s", os.getcwd())

        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(certfile=self.cert_path, keyfile=self.key_path)

        server = await websockets.serve(
            self.on_connect,
            "0.0.0.0",
            self.port,
            subprotocols=["ocpp2.0.1"],
            ssl=ssl_context,
        )
        logging.info("WebSocket Server Started")
        await server.wait_closed()

    async def on_connect(self, websocket, path):
        try:
            requested_protocols = websocket.request_headers["Sec-WebSocket-Protocol"]
        except KeyError:
            logging.info("Client hasn't requested any Subprotocol. Closing Connection")
            return await websocket.close()

        if websocket.subprotocol:
            logging.info("Protocols Matched: %s", websocket.subprotocol)
        else:
            logging.warning(
                "Protocols Mismatched | Expected Subprotocols: %s,"
                " but client supports %s | Closing connection",
                websocket.available_subprotocols,
                requested_protocols,
            )
            return await websocket.close()

        charge_point_id = path.strip("/")
        self.charging_station = ChargingStation(
            charge_point_id, websocket, self.cs_manager
        )
        self.cs_manager.charging_station = self.charging_station

        logging.info("Charge point %s connected", charge_point_id)
        logging.info("Charger connected")

        await asyncio.gather(
            asyncio.create_task(self.charging_station.start()),
            asyncio.create_task(self.cs_manager.initialise(config)),
            asyncio.create_task(self.request_meter_values()),
        )

    async def configure_charging_station(self):
        logging.info("Setting transaction start ...")

        request = call.SetVariables(
            set_variable_data=[
                SetVariableDataType(
                    component=ComponentType(name=ControllerComponentName.tx_ctrlr),
                    variable=VariableType(name=TxCtrlrVariableName.tx_start_point),
                    attribute_value=TxStartStopPointType.ev_connected,
                )
            ]
        )

        request_tx_interval = call.SetVariables(
            set_variable_data=[
                SetVariableDataType(
                    component=ComponentType(
                        name=ControllerComponentName.sampled_data_ctrlr
                    ),
                    variable=VariableType(
                        name=SampledDataCtrlrVariableName.tx_updated_interval
                    ),
                    attribute_value="60",
                )
            ]
        )

        request_tx_meassurands = call.SetVariables(
            set_variable_data=[
                SetVariableDataType(
                    component=ComponentType(
                        name=ControllerComponentName.sampled_data_ctrlr
                    ),
                    variable=VariableType(
                        name=SampledDataCtrlrVariableName.tx_updated_measurands
                    ),
                    attribute_value="Energy.Active.Import.Register,Power.Active.Import,Current.Import,Voltage",
                )
            ]
        )

        request_supported_measurands = call.GetVariables(
            get_variable_data=[
                {
                    "component": ComponentType(name="AlignedDataCtrlr"),
                    "variable": VariableType(
                        name=AlignedDataCtrlrVariableName.measurands
                    ),
                }
            ]
        )

        set_aligned_data = call.SetVariables(
            set_variable_data=[
                SetVariableDataType(
                    component=ComponentType(
                        name=ControllerComponentName.aligned_data_ctrlr
                    ),
                    variable=VariableType(name=AlignedDataCtrlrVariableName.enabled),
                    attribute_value="true",
                )
            ]
        )

        get_variable = call.GetVariables(
            get_variable_data=[
                {
                    "component": ComponentType(name="AlignedDataCtrlr"),
                    "variable": VariableType(
                        name=AlignedDataCtrlrVariableName.send_during_idle
                    ),
                }
            ]
        )

        request_base_report = call.GetBaseReport(542332, ReportBaseType.full_inventory)

        try:
            # response = await self.charging_point.call(request)
            # logging.debug("Received response: %s", response)
            # response = await self.charging_point.call(request_tx_interval)
            # logging.debug("Received response: %s", response)
            response = await self.charging_station.call(get_variable)
            logging.debug("Received response: %s", response)
        except Exception as e:
            logging.error("Error sending payload: %s", e)

    async def request_meter_values(self):
        logging.info("Requesting meter values")
        payload = call.TriggerMessage("MeterValues")
        try:
            response = await self.charging_station.call(payload)
            logging.debug("Received response: %s", response)
        except Exception as e:
            logging.error("Error sending payload: %s", e)

    async def start_charging_point(self):
        await self.hass.async_add_executor_job(self.charging_station.start)
