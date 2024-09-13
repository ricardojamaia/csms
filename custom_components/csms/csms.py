"""Charging Stations Management System."""

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import os
import ssl

from dateutil import parser
from ocpp.routing import create_route_map, on
from ocpp.v201 import ChargePoint as cp, call, call_result
from ocpp.v201.datatypes import (
    ChargingProfileType,
    ChargingSchedulePeriodType,
    ChargingScheduleType,
    ComponentType,
    SetVariableDataType,
    VariableType,
)
from ocpp.v201.enums import (
    Action,
    ChargingProfileKindType,
    ChargingProfilePurposeType,
    ChargingRateUnitType,
    ControllerComponentName,
    MeasurandType,
    RecurrencyKindType,
    RegistrationStatusType,
    SampledDataCtrlrVariableName,
    TransactionEventType,
    TxCtrlrVariableName,
    TxStartStopPointType,
)
import websockets

from .cs_components import ChargingStationComponent, ConnectorComponent, EVSEComponent
from .measurand import Measurand


class ChargingSession:
    """Holds information regsarding a charging session initiated on a Charging Station."""

    def __init__(self, start_time: datetime, energy_registry_value):
        """Initialise and start a charging session."""
        self.session_id = str(start_time)
        self.start_time: start_time = start_time
        self.last_update: datetime = start_time
        self.end_time: datetime = None
        self.start_energy_registry = energy_registry_value
        self.energy = 0

    def update_session(self, update_time: datetime, energy_registry_value):
        """Update the amount of energy consumed during the charging session."""
        self.last_update = update_time
        if self.start_energy_registry is not None and energy_registry_value is not None:
            self.energy = energy_registry_value - self.start_energy_registry

    def end_session(self, end_time: datetime, energy_registry_value):
        """End a charging session."""
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


class ChargingStationManager:
    """Charging station representation on the CSMS side."""

    def __init__(self) -> None:
        self.charging_station: ChargingStation = None
        self._callbacks = {}

        self._latest_sampled_values = {}
        self._pending_reports = []
        self.current_session = None
        self.cs_component: ChargingStationComponent = ChargingStationComponent()
        self.manufacturer = None
        self.model = None
        self.sw_version = None
        self._event_queue = asyncio.Queue()
        self.config = {}
        self.request_id = 1
        self._charging_profiles: dict[str, ChargingProfileType] = {}
        self.current_transaction_id = None

    def get_latest_measurand_value(self, unique_key):
        """Return the latest value of the specified measurand."""
        if self.charging_station is not None:
            return self._latest_sampled_values.get(unique_key, "Unavailable")
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
        """Publishes measurands whenever they are updated."""
        for measurand_callbacks in self._callbacks.values():
            for callback in measurand_callbacks:
                callback()

    def _parse_charging_profiles(self):
        """Get charging profiles from config."""

        charging_profiles = self.config.get("charging_profile")

        if charging_profiles is not None:
            for p in charging_profiles:
                self._charging_profiles[p["name"]] = ChargingProfileType(
                    id=p["id"],
                    stack_level=p["stack_level"],
                    charging_profile_purpose=p["charging_profile_purpose"],
                    charging_profile_kind=p.get("charging_profile_kind"),
                    recurrency_kind=p.get("recurrency_kind"),
                    charging_schedule=[
                        ChargingScheduleType(
                            id=schedule["id"],
                            start_schedule=schedule.get("start_schedule"),
                            duration=schedule.get("duration"),
                            charging_rate_unit=schedule["charging_rate_unit"],
                            charging_schedule_period=[
                                ChargingSchedulePeriodType(
                                    start_period=period["start_period"],
                                    limit=period["limit"],
                                )
                                for period in schedule.get("charging_schedule_period")
                            ],
                        )
                        for schedule in p.get("charging_schedule")
                    ],
                )

    async def initialise(self, config=None):
        """Initialises the charging station."""

        if config is not None:
            self.config = config

        self._parse_charging_profiles()

        # Initialize the charging station if it is connected.
        if self.charging_station is not None:
            request_set_tx_ctrlr = call.SetVariables(
                set_variable_data=[
                    SetVariableDataType(
                        component=ComponentType(name=ControllerComponentName.tx_ctrlr),
                        variable=VariableType(name=TxCtrlrVariableName.tx_start_point),
                        attribute_value=TxStartStopPointType.ev_connected,
                    )
                ]
            )
            try:
                response = await self.charging_station.call(request_set_tx_ctrlr)
                logging.debug("Received response: %s", response)
            except TimeoutError as e:
                logging.error("Error sending payload: %s", e)

            request_set_tx_interval = call.SetVariables(
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
            try:
                response = await self.charging_station.call(request_set_tx_interval)
                logging.debug("Received response: %s", response)
            except TimeoutError as e:
                logging.error("Error sending payload: %s", e)

            measurands = self.config.get("measurands")
            if measurands is not None:
                measurand_names = [m.get("measurand") for m in measurands]
                sample_data_ctrlr = ComponentType(
                    name=ControllerComponentName.sampled_data_ctrlr
                )
                request_set_tx_meassurands = call.SetVariables(
                    set_variable_data=[
                        SetVariableDataType(
                            component=sample_data_ctrlr,
                            variable=VariableType(
                                name=SampledDataCtrlrVariableName.tx_started_measurands
                            ),
                            attribute_value=",".join(measurand_names),
                        )
                    ]
                )
                try:
                    response = await self.charging_station.call(
                        request_set_tx_meassurands
                    )
                    logging.debug("Received response: %s", response)
                except TimeoutError as e:
                    logging.error("Error sending payload: %s", e)

                request_set_tx_meassurands = call.SetVariables(
                    set_variable_data=[
                        SetVariableDataType(
                            component=sample_data_ctrlr,
                            variable=VariableType(
                                name=SampledDataCtrlrVariableName.tx_updated_measurands
                            ),
                            attribute_value=",".join(measurand_names),
                        )
                    ]
                )
                try:
                    response = await self.charging_station.call(
                        request_set_tx_meassurands
                    )
                    logging.debug("Received response: %s", response)
                except TimeoutError as e:
                    logging.error("Error sending payload: %s", e)

                request_set_tx_meassurands = call.SetVariables(
                    set_variable_data=[
                        SetVariableDataType(
                            component=sample_data_ctrlr,
                            variable=VariableType(
                                name=SampledDataCtrlrVariableName.tx_ended_measurands
                            ),
                            attribute_value=",".join(measurand_names),
                        )
                    ]
                )
                try:
                    response = await self.charging_station.call(
                        request_set_tx_meassurands
                    )
                    logging.debug("Received response: %s", response)
                except TimeoutError as e:
                    logging.error("Error sending payload: %s", e)

            variables = self.config.get("variables")

            if variables is not None:
                request_base_report = call.GetReport(
                    self.request_id,
                    variables,
                )
                self.request_id += 1

                try:
                    response = await self.charging_station.call(request_base_report)
                    logging.debug("Received response: %s", response)
                except TimeoutError as e:
                    logging.error("Error sending payload: %s", e)

            await self.set_default_charging_profiles()

    async def set_current_transaction_charging_profile(self, profile_name: str):
        """Set Charging Profile for the on-going transaction"""

        if self.charging_station is None or self.current_transaction_id is None:
            return

        await self.set_charging_profile(profile_name, self.current_transaction_id)

    async def set_charging_profile(self, profile_name: str, transaction_id: str = None):
        """Set a charging profile."""

        if self.charging_station is None:
            return

        profile = self._charging_profiles.get(profile_name)

        if profile is not None:
            await self.clear_charging_profile(
                charging_profile_purpose=profile.charging_profile_purpose,
                stack_level=profile.stack_level,
            )

            if transaction_id is not None:
                profile.transaction_id = transaction_id
            request_set_profile = call.SetChargingProfile(
                evse_id=1, charging_profile=profile
            )

            try:
                response = await self.charging_station.call(request_set_profile)
                logging.debug("SetChargingProfile response: %s", response)
            except TimeoutError as e:
                logging.error("Error setting default charging profile: %s", e)

    async def clear_charging_profile(
        self,
        charging_profile_id: int | None = None,
        evse_id: int | None = None,
        charging_profile_purpose: ChargingProfilePurposeType | None = None,
        stack_level: int | None = None,
    ):
        clear_profile_request = call.ClearChargingProfile(
            charging_profile_id=charging_profile_id,
            charging_profile_criteria={
                "evse_id": evse_id,
                "charging_profile_purpose": charging_profile_purpose,
                "stack_level": stack_level,
            },
        )
        try:
            response = await self.charging_station.call(clear_profile_request)
            logging.debug("SetChargingProfile response: %s", response)
        except TimeoutError as e:
            logging.error("Error setting default charging profile: %s", e)

    async def set_default_charging_profiles(self):
        """Clear charging station profiles and set all default profiles."""

        await self.clear_charging_profile(
            charging_profile_purpose=ChargingProfilePurposeType.tx_default_profile
        )

        default_charging_profiles = [
            name
            for name, profile in self._charging_profiles.items()
            if profile.charging_profile_purpose
            == ChargingProfilePurposeType.tx_default_profile
        ]

        if default_charging_profiles:
            for name in default_charging_profiles:
                await self.set_charging_profile(name)

    @on(Action.SecurityEventNotification)
    async def on_security_event_notification(self, **kwargs):
        """Handle SecurityEventNotification."""

        # Send response (if needed)
        return call_result.SecurityEventNotification()

    @on(Action.TransactionEvent)
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

            self.publish_updates()

        energy_register_value = self._latest_sampled_values.get(
            Measurand.generate_key(
                measurand="Energy.Active.Import.Register", phase="", location="Outlet"
            )
        )
        t = parser.isoparse(timestamp)

        if event_type == TransactionEventType.started:
            self.current_session = ChargingSession(t, energy_register_value)
            self.current_transaction_id = transaction_info.get("transaction_id")
        elif event_type == TransactionEventType.updated:
            self.current_transaction_id = transaction_info.get("transaction_id")
            if self.current_session is None:
                self.current_session = ChargingSession(t, energy_register_value)

                logging.error(
                    "Transaction event received but not transaction is ongoing."
                )
            else:
                self.current_session.update_session(t, energy_register_value)

        elif event_type == TransactionEventType.ended:
            self.current_transaction_id = None
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

            # TODO: Defer update to main loop
            # Update the EVSE component state using the accumulated data
            for item in report_data:
                await self.cs_component.update_variable(item)

            # await self._new_charging_station_callback()

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

        self.manufacturer = charging_station.get("vendor_name")
        self.model = charging_station.get("model")
        self.sw_version = charging_station.get("firmware_version")

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
            "Received a StatusNotification: evse: %s, connector_id: %s, and connector_status: %s",
            evse_id,
            connector_id,
            connector_status,
        )

        evse = self.cs_component.evses.get(evse_id)
        if evse is None:
            self.cs_component.evses[evse_id] = EVSEComponent(evse_id=evse_id)
            evse = self.cs_component.evses.get(evse_id)

        connector = self.cs_component.evses[evse_id].connectors.get(connector_id)

        if connector is None:
            self.cs_component.evses[evse_id].connectors[connector_id] = (
                ConnectorComponent(evse_id=evse_id, connector_id=connector_id)
            )
            connector = self.cs_component.evses[evse_id].connectors.get(connector_id)

        self._event_queue.put_nowait(
            (
                connector,
                {
                    "component": {
                        "name": "Connector",
                        "evse": {
                            "id": evse_id,
                            "connector_id": connector_id,
                        },
                    },
                    "variable": {"name": "AvailabilityState"},
                    "variable_attribute": [{"type": "Actual", "value": "Available"}],
                },
            )
        )
        return call_result.StatusNotificationPayload()

    def update_variables(self, get_variables_result):
        evse_id = None
        connector = None

        for item in get_variables_result:
            try:
                attribute_status = item["attribute_status"]
                attribute_type = item["attribute_type"]
                attribute_value = item["attribute_value"]
                variable_name = item["variable"]["name"]
                component_name = item["component"]["name"]

                evse = item["component"].get("evse")
                if evse is not None:
                    evse_id = evse["id"]
                    connector = evse.get("connector")
            except KeyError as key_error:
                logging.error("Error getting variables: %s", key_error)
                return

            if attribute_status == "Accepted":
                self._event_queue.put_nowait(
                    (
                        self.cs_component,
                        {
                            "component": {
                                "name": component_name,
                                "evse": {
                                    "id": evse_id,
                                    "connector_id": connector,
                                },
                            },
                            "variable": {"name": variable_name},
                            "variable_attribute": [
                                {"type": attribute_type, "value": attribute_value}
                            ],
                        },
                    )
                )

    async def loop(self):
        """Initialises the charging station and loop handling responses to calls."""

        logging.debug("Starting Charging Station loop task.")

        await self.initialise()

        while True:
            component, data = await self._event_queue.get()
            await component.update_variable(data)


class ChargingStation(cp):
    def __init__(self, cs_id, connection, cs_manager: ChargingStationManager):
        super().__init__(cs_id, connection)
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
        except TimeoutError as e:
            logging.error("Error setting default charging profile: %s", e)


class ChargingStationManagementSystem:
    """Placeholder class to make tests pass."""

    def __init__(self) -> None:
        """Initialize."""
        self.port = 9520
        self.cert_path = "/ssl/cert.pem"
        self.key_path = "/ssl/key.pem"
        self.cs_managers: dict[str, ChargingStationManager] = {}
        self.hass = None

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

        cs_manager = self.cs_managers.get(charge_point_id)
        if cs_manager is not None:
            logging.info("Charge point %s connected", charge_point_id)
            cs_manager.charging_station = ChargingStation(
                charge_point_id, websocket, cs_manager
            )
        else:
            logging.warning("Unexpected charging point: %s.", charge_point_id)
            return await websocket.close()

        await asyncio.gather(
            asyncio.create_task(cs_manager.charging_station.start()),
            asyncio.create_task(cs_manager.loop()),
        )
