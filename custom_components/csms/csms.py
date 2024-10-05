"""Charging Stations Management System."""

import asyncio
from datetime import datetime, timedelta, timezone
import os
import ssl
import uuid

from dateutil import parser
from ocpp.routing import create_route_map, on
from ocpp.v201 import ChargePoint, call, call_result


from ocpp.v201.datatypes import (
    ChargingProfileType,
    ChargingSchedulePeriodType,
    ChargingScheduleType,
    ComponentType,
    SetMonitoringDataType,
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
    OperationalStatusType,
    RecurrencyKindType,
    RegistrationStatusType,
    SampledDataCtrlrVariableName,
    TransactionEventType,
    TxCtrlrVariableName,
    TxStartStopPointType,
)
import websockets

from .const import _LOGGER
from .cs_components import ChargingStationComponent, ConnectorComponent, EVSEComponent
from .measurand import Measurand


class RequestId:
    """Generate ids for requests to charging station."""

    def __init__(self) -> None:
        """Initialise RequestId."""

        self._id = 0

    def next(self) -> int:
        """Return next request id."""
        self._id += 1
        return self._id


class ChargingSession:
    """Holds information regarding a charging session initiated on a Charging Station (EVSE)."""

    def __init__(
        self,
        start_time: datetime,
        energy_registry_value,
        transaction_id: str,
        charging_state: str | None = None,
    ) -> None:
        """Initialise and start a charging session."""
        self.session_id = str(start_time)
        self.start_time: start_time = start_time
        self.last_update: datetime = start_time
        self.end_time: datetime = None
        self.start_energy_registry = energy_registry_value
        self.energy = 0
        self.transaction_id: str = transaction_id
        self.charging_state: str = charging_state

    def update_session(
        self,
        update_time: datetime,
        energy_registry_value,
        charging_state: str | None = None,
    ):
        """Update the amount of energy consumed during the charging session."""
        self.last_update = update_time
        if charging_state is not None:
            self.charging_state = charging_state
        if self.start_energy_registry is not None and energy_registry_value is not None:
            self.energy = energy_registry_value - self.start_energy_registry

    def end_session(
        self,
        end_time: datetime,
        energy_registry_value,
        charging_state: str | None = None,
    ):
        """End a charging session."""
        self.last_update = end_time
        self.end_time = end_time
        if charging_state is not None:
            self.charging_state = charging_state
        if self.start_energy_registry is not None and energy_registry_value is not None:
            self.energy = energy_registry_value - self.start_energy_registry

    def is_active(self) -> bool:
        """Return true if charging session is active and fals eotherwise."""
        return self.end_time is None

    @property
    def duration(self) -> timedelta:
        """Returns the amount of energy consumed on the charging session."""
        if self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return (self.last_update - self.start_time).total_seconds()


class ChargingStationManager:
    """Charging station representation on the CSMS side."""

    def __init__(self) -> None:
        self.cs_component: ChargingStationComponent = ChargingStationComponent()
        self.charge_point: ChargePoint = None

        # Holds information of the ongoing charging session.
        self.current_session = None

        self.manufacturer = None
        self.model = None
        self.sw_version = None

        # Configuration information of the Charging Station such as measurands to collect
        # variable to monitor or charging profiles
        self.config = {}

        self._measurand_callbacks = {}
        self._latest_sampled_values = {}
        self._pending_reports = []
        self._futures = {}

        # Queue to defer handling of Charging Station messages to the main loop
        self._event_queue = asyncio.Queue()

        self._request_id = RequestId()
        self._remote_start_id = 1

        self._charging_profiles: dict[str, ChargingProfileType] = {}

    def get_latest_measurand_value(self, unique_key):
        """Return the latest value of the specified measurand."""
        if self.charge_point is not None:
            return self._latest_sampled_values.get(unique_key, "Unavailable")
        return "Unavailable"

    def register_callback(self, unique_key, callback):
        """Register a callback to be called when the measurand value changes."""
        if unique_key not in self._measurand_callbacks:
            self._measurand_callbacks[unique_key] = []
        self._measurand_callbacks[unique_key].append(callback)

    def unregister_callback(self, unique_key, callback):
        """Unregister a callback."""
        if unique_key in self._measurand_callbacks:
            self._measurand_callbacks[unique_key].remove(callback)
            if not self._measurand_callbacks[unique_key]:
                del self._measurand_callbacks[unique_key]

    def publish_updates(self):
        """Publishes measurands whenever they are updated."""
        for measurand_callbacks in self._measurand_callbacks.values():
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
        if self.charge_point is not None:
            await self.setup_tx_variables()

            variables = self.config.get("variables")

            if variables is not None:
                await self.get_variables(variables)

                await self.set_variables_monitoring(variables)

            await self.charge_point.call(
                call.GetMonitoringReport(self._request_id.next())
            )

    async def set_variables_monitoring(self, variables):
        request_set_variable_monitoring = call.SetVariableMonitoring(
            [
                SetMonitoringDataType(
                    value=variable["monitoring"]["value"],
                    type=variable["monitoring"]["type"],
                    component=ComponentType(
                        name=variable["component"]["name"],
                        instance=variable["component"].get("instance"),
                        evse=variable["component"].get("evse"),
                    ),
                    variable=VariableType(
                        name=variable["variable"]["name"],
                        instance=variable["variable"].get("instance"),
                    ),
                    severity=2,
                    transaction=False,
                )
                for variable in variables
            ]
        )
        response = await self.charge_point.call(request_set_variable_monitoring)
        _LOGGER.debug("Received response: %s", response)

    async def get_variables(self, variables):
        """Sends a GetVariables request to the charging station."""
        request_base_report = call.GetReport(
            self._request_id.next(),
            (
                [
                    {
                        "component": {
                            "name": variable["component"]["name"],
                            "instance": variable["component"].get("instance"),
                            "evse": {
                                "id": variable["component"]["evse"].get("id"),
                                "connector_id": variable["component"]["evse"].get(
                                    "connector_id"
                                ),
                            }
                            if variable["component"].get("evse") is not None
                            else None,
                        },
                        "variable": {
                            "name": variable["variable"]["name"],
                            "instance": variable["variable"].get("instance"),
                        },
                    }
                    for variable in variables
                ]
            ),
        )

        response = await self.charge_point.call(request_base_report)
        _LOGGER.debug("Received response: %s", response)

    async def setup_tx_variables(self):
        """Initialises charging station transaction related variables."""
        request_set_tx_ctrlr = call.SetVariables(
            set_variable_data=[
                SetVariableDataType(
                    component=ComponentType(name=ControllerComponentName.tx_ctrlr),
                    variable=VariableType(name=TxCtrlrVariableName.tx_start_point),
                    attribute_value=TxStartStopPointType.ev_connected,
                )
            ]
        )
        response = await self.charge_point.call(request_set_tx_ctrlr)
        _LOGGER.debug("Received response: %s", response)

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

        response = await self.charge_point.call(request_set_tx_interval)
        _LOGGER.debug("Received response: %s", response)

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
            response = await self.charge_point.call(request_set_tx_meassurands)
            _LOGGER.debug("Received response: %s", response)

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

            response = await self.charge_point.call(request_set_tx_meassurands)
            _LOGGER.debug("Received response: %s", response)

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

            response = await self.charge_point.call(request_set_tx_meassurands)
            _LOGGER.debug("Received response: %s", response)

    async def get_full_report(self):
        request_base_report = call.GetBaseReport(
            self._request_id.next(), "FullInventory"
        )

        try:
            response = await self.charge_point.call(request_base_report)
            _LOGGER.debug("Received response: %s", response)
        except TimeoutError as e:
            _LOGGER.error("Error sending payload: %s", e)

    async def start_transaction(self):
        """Starts a remote transaction."""
        if self.current_session is not None and self.current_session.is_active():
            _LOGGER.error("Attempt to start transaction but one is already on-going.")
            return

        request_start_transaction = call.RequestStartTransaction(
            id_token={"id_token": str(uuid.uuid4()), "type": "Central"},
            remote_start_id=self._remote_start_id,
            evse_id=1,
        )
        self._remote_start_id += 1

        try:
            response = await self.charge_point.call(request_start_transaction)
            _LOGGER.debug("RequestStopTransaction response: %s", response)
        except TimeoutError as e:
            _LOGGER.error("Error stopping transaction: %s", e)

    async def stop_current_transaction(self):
        """Stop current transaction."""
        if self.current_session is not None and self.current_session.is_active():
            await self.stop_transaction(self.current_session.transaction_id)

    async def stop_transaction(self, transaction_id: str):
        """Stop and ongoing transaction given a transacton Id."""

        if transaction_id is None:
            return

        request_stop_transaction = call.RequestStopTransaction(transaction_id)

        try:
            response = await self.charge_point.call(request_stop_transaction)
            _LOGGER.debug("RequestStopTransaction response: %s", response)
        except TimeoutError as e:
            _LOGGER.error("Error stopping transaction: %s", e)

    async def set_current_transaction_charging_profile(self, profile_name: str):
        """Set Charging Profile for the on-going transaction"""

        if (
            self.charge_point is None
            or self.current_session is None
            or not self.current_session.is_active()
            or self.current_session.transaction_id is None
        ):
            return

        await self.set_charging_profile_by_name(
            profile_name, self.current_session.transaction_id
        )

    async def set_charging_profile_by_name(
        self, profile_name: str, transaction_id: str = None
    ):
        """Set a charging profile."""

        if self.charge_point is None:
            return

        profile = self._charging_profiles.get(profile_name)

        if profile is not None:
            if (
                profile.charging_profile_purpose
                == ChargingProfilePurposeType.tx_profile
            ):
                await self.clear_charging_profile(
                    charging_profile_purpose=profile.charging_profile_purpose,
                    stack_level=profile.stack_level,
                )

                if transaction_id is not None:
                    profile.transaction_id = transaction_id

            await self.set_charging_profile(profile)

    async def set_charging_profile(self, profile):
        """Set a given charging profile.

        Raises exception in case of error.
        """

        request_set_profile = call.SetChargingProfile(
            evse_id=1, charging_profile=profile
        )

        response = await self.charge_point.call(request_set_profile)
        _LOGGER.debug("SetChargingProfile response: %s", response)

    async def clear_charging_profile(
        self,
        charging_profile_id: int | None = None,
        evse_id: int | None = None,
        charging_profile_purpose: ChargingProfilePurposeType | None = None,
        stack_level: int | None = None,
    ):
        """Clears charging profiles according to criteria."""
        clear_profile_request = call.ClearChargingProfile(
            charging_profile_id=charging_profile_id,
            charging_profile_criteria={
                "evse_id": evse_id,
                "charging_profile_purpose": charging_profile_purpose,
                "stack_level": stack_level,
            },
        )
        try:
            response = await self.charge_point.call(clear_profile_request)
            _LOGGER.debug("SetChargingProfile response: %s", response)
        except TimeoutError as e:
            _LOGGER.error("Error setting default charging profile: %s", e)

    async def change_availability(self, operational_status: OperationalStatusType):
        """Change the operational status of the charging station."""
        request_change_availability = call.ChangeAvailability(operational_status)

        try:
            response = await self.charge_point.call(request_change_availability)
            _LOGGER.debug("SetChargingProfile response: %s", response)

        except TimeoutError as e:
            _LOGGER.error("Error changing availability: %s", e)

    def is_connected(self):
        """Return true if charging station is connected."""

        return self.charge_point is not None

    def is_operational(self):
        """Return true if charging station is operational."""

        connectors_availability = [
            connector.get_variable_actual_value("AvailabilityState")
            for evse in self.cs_component.evses.values()
            for connector in evse.connectors.values()
        ]

        # According to G04.FR.07:
        # When the availability of the Charging Station becomes Inoperative (Unavailable, Faulted)
        # All operative EVSEs and connectors (i.e. not Faulted) SHALL become Unavailable.
        for state in connectors_availability:
            if state not in ("Unavailable", "Faulted"):
                return True

        return False

    async def get_charging_profiles(self, profile_purpose: ChargingProfilePurposeType):
        """Get charging profiles installed on the charging station."""

        # Create a new future for the response
        future = asyncio.get_running_loop().create_future()

        # Store the future with the request_id as key
        request_id = self._request_id.next()
        self._futures[request_id] = future

        get_profiles_request = call.GetChargingProfiles(
            request_id=request_id,
            charging_profile={"charging_profile_purpose": profile_purpose},
        )

        response: call_result.GetChargingProfiles
        try:
            response = await self.charge_point.call(get_profiles_request)
            _LOGGER.debug("SetChargingProfile response: %s", response)
        except TimeoutError as e:
            _LOGGER.error("Error setting default charging profile: %s", e)

        profiles = []
        if response.status == "Accepted":
            try:
                # Wait for the response (future to be resolved)
                profiles = await future
            except asyncio.CancelledError as e:
                raise ChargingStationRequestError(
                    f"Request to {self.charge_point.id} with request_id {request_id} was cancelled.",
                    self.charge_point.id,
                    request_id,
                ) from e
            finally:
                # Clean up future after completion
                del self._futures[request_id]
        elif response.status == "NoProfiles":
            profiles = []
        else:
            raise ChargingStationRequestError(
                f"{self.charge_point.id} responded to GetChargingProfiles with status: {response.status}.",
                self.charge_point.id,
                request_id,
            )

        return profiles

    async def set_default_charging_profiles(self):
        """Clear charging station profiles and set all default profiles."""

        await self.clear_charging_profile(
            charging_profile_purpose=ChargingProfilePurposeType.tx_default_profile
        )

        await self.get_charging_profiles(ChargingProfilePurposeType.tx_default_profile)

        default_charging_profiles = [
            name
            for name, profile in self._charging_profiles.items()
            if profile.charging_profile_purpose
            == ChargingProfilePurposeType.tx_default_profile
        ]

        if default_charging_profiles:
            for name in default_charging_profiles:
                await self.set_charging_profile_by_name(name)

    @on(Action.ReportChargingProfiles)
    async def on_get_charging_profiles(
        self, request_id, charging_limit_source, evse_id, charging_profile, tbc=False
    ):
        """Handle ReportChargingProfiles."""
        # Check if there's a pending future for this request_id
        if request_id in self._futures:
            future = self._futures[request_id]
            # Resolve the future with the received data
            if not future.done():
                future.set_result(charging_profile)

        # Send response
        return call_result.ReportChargingProfiles()

    @on(Action.SecurityEventNotification)
    async def on_security_event_notification(
        self, type: str, timestamp, techinfo: str | None = None
    ):
        """Handle SecurityEventNotification."""

        # Send response (if needed)
        return call_result.SecurityEventNotification()

    @on(Action.TransactionEvent)
    async def on_transaction_event(
        self, event_type, timestamp, trigger_reason, seq_no, transaction_info, **kwargs
    ) -> call_result.TransactionEvent:
        """Update measurand values and charing session informaiton based on transaction events."""
        _LOGGER.debug(
            "Transaction event received with event_type: %s, timestamp: %s, trigger_reason: %s, "
            "seq_no: %s and transaction_info:%s",
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
                _LOGGER.error("Missing expected field in transaction_info: %s", e)
            except TypeError as e:
                _LOGGER.error("Unexpected data structure in transaction_info: %s", e)

            _LOGGER.debug("Latest sampled values: %s", self._latest_sampled_values)

            self.publish_updates()

        energy_register_value = self._latest_sampled_values.get(
            Measurand.generate_key(
                measurand="Energy.Active.Import.Register", phase="", location="Outlet"
            )
        )
        t = parser.isoparse(timestamp)

        if event_type == TransactionEventType.started:
            self.current_session = ChargingSession(
                t,
                energy_register_value,
                transaction_info.get("transaction_id"),
                transaction_info.get("charging_state"),
            )
        elif event_type == TransactionEventType.updated:
            if self.current_session is None:
                self.current_session = ChargingSession(
                    t,
                    energy_register_value,
                    transaction_info.get("transaction_id"),
                    transaction_info.get("charging_state"),
                )
                _LOGGER.error(
                    "Transaction event received but not transaction is ongoing."
                )
            else:
                if transaction_info.get("transaction_id") is not None:
                    self.current_session.transaction_id = transaction_info.get(
                        "transaction_id"
                    )
                self.current_session.update_session(
                    t, energy_register_value, transaction_info.get("charging_state")
                )

        elif event_type == TransactionEventType.ended:
            self.current_session.end_session(
                t, energy_register_value, transaction_info.get("charging_state")
            )

        return call_result.TransactionEvent()

    @on(Action.NotifyReport)
    async def _on_notify_report(
        self,
        request_id: int,
        generated_at: str,
        seq_no: int,
        report_data: list,
        tbc: bool = False,
    ):
        _LOGGER.debug(
            "NotifyReport: request_id:%s, generated_at:%s, seq_no:%s",
            request_id,
            generated_at,
            seq_no,
        )
        # Store the report data
        self._pending_reports.extend(report_data)

        if not tbc:
            # If tbc is False, process all accumulated reports
            report_data = self._pending_reports
            self._pending_reports = []  # Reset the storage

            # Update the EVSE component state using the accumulated data
            for item in report_data:
                await self.cs_component.update_variable(item)

        return call_result.NotifyReport()

    @on(Action.NotifyEvent)
    async def on_notify_event(
        self, generated_at: str, seq_no: int, event_data: list, **kargs
    ):
        for item in event_data:
            component = item["component"]
            component_name = component["name"]
            component_instance = component.get("instance")
            variable = item["variable"]
            variable_name = variable["name"]
            variable_instance = variable.get("instance")
            actual_value = item["actual_value"]

            self._event_queue.put_nowait(
                (
                    self.cs_component,
                    {
                        "component": {
                            "name": component_name,
                            "instance": component_instance,
                        },
                        "variable": {
                            "name": variable_name,
                            "instance": variable_instance,
                        },
                        "variable_attribute": [
                            {"type": "Actual", "value": actual_value}
                        ],
                    },
                )
            )
            await self.cs_component.update_variable(item)

        return call_result.NotifyEvent()

    @on(Action.NotifyMonitoringReport)
    async def on_notify_monitoring_report(self, **kargs):
        return call_result.NotifyMonitoringReport()

    @on(Action.MeterValues)
    async def on_meter_values(self, evse_id, meter_value) -> call_result.MeterValues:
        """Handle MeterValues request."""
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
                    _LOGGER.debug("Key Error")
                _LOGGER.debug(
                    "Timestamp: %s, EVSE: %s, Value: %s, Context: %s, Unit: %s",
                    timestamp,
                    evse_id,
                    value,
                    context,
                    unit,
                )

        return call_result.MeterValues()

    @on(Action.BootNotification)
    async def on_boot_notification(self, charging_station, reason):
        """Update device informaiton based on BootNotification request."""
        _LOGGER.debug(
            "BootNotification received with charging_station: %s, reason: %s",
            charging_station,
            reason,
        )

        self.manufacturer = charging_station.get("vendor_name")
        self.model = charging_station.get("model")
        self.sw_version = charging_station.get("firmware_version")

        return call_result.BootNotification(
            current_time=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z",
            interval=10,
            status=RegistrationStatusType.accepted,
        )

    @on("Heartbeat")
    def on_heartbeat(self):
        """Handle heartbeat events sent by the charging station."""
        _LOGGER.debug("Received a Heartbeat!")
        return call_result.Heartbeat(
            current_time=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"
        )

    @on(Action.StatusNotification)
    def on_status_notification(
        self, timestamp, connector_status, evse_id, connector_id
    ):
        """Updates connectos status upon a StatusNotification event."""
        _LOGGER.debug(
            "StatusNotification timestamp: %s, evse: %s, connector_id: %s, connector_status: %s",
            timestamp,
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
                    "variable_attribute": [
                        {"type": "Actual", "value": connector_status}
                    ],
                },
            )
        )
        return call_result.StatusNotification()

    def update_variables(self, get_variables_result):
        """Updates Charging Stations variables from a GetVariables call result."""
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
                _LOGGER.error("Error getting variables: %s", key_error)
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
            response = await self.charge_point.call(request)
            _LOGGER.debug("SetChargingProfile response: %s", response)
        except TimeoutError as e:
            _LOGGER.error("Error setting default charging profile: %s", e)

    async def loop(self):
        """Initialises the charging station and loop handling responses to calls."""

        _LOGGER.debug("Starting Charging Station loop task.")

        await self.initialise()

        while True:
            component, data = await self._event_queue.get()
            await component.update_variable(data)


class ChargingStationManagementSystem:
    """Placeholder class to make tests pass."""

    def __init__(self) -> None:
        """Initialize."""
        self.port = 9520
        self.host = "0.0.0.0"
        self.cert_path = "/ssl/cert.pem"
        self.key_path = "/ssl/key.pem"
        self.cs_managers: dict[str, ChargingStationManager] = {}

    async def run_server(self) -> None:
        """Run web socket server and wait for Charging Stations connections."""
        _LOGGER.info("Current dir: %s", os.getcwd())

        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(certfile=self.cert_path, keyfile=self.key_path)

        server = await websockets.serve(
            self.on_connect,
            self.host,
            self.port,
            subprotocols=["ocpp2.0.1"],
            ssl=ssl_context,
        )
        _LOGGER.info("WebSocket Server Started")
        await server.wait_closed()

    async def on_connect(self, websocket, path):
        """Link the charging point with the corresponding ChargingStationManager."""
        try:
            requested_protocols = websocket.request_headers["Sec-WebSocket-Protocol"]
        except KeyError:
            _LOGGER.error("Client hasn't requested any Subprotocol. Closing Connection")
            return await websocket.close()

        if websocket.subprotocol:
            _LOGGER.debug("Protocols Matched: %s", websocket.subprotocol)
        else:
            _LOGGER.error(
                "Protocols Mismatched | Expected Subprotocols: %s,"
                " but client supports %s | Closing connection",
                websocket.available_subprotocols,
                requested_protocols,
            )
            return await websocket.close()

        charge_point_id = path.strip("/")

        cs_manager = self.cs_managers.get(charge_point_id)
        if cs_manager is not None:
            _LOGGER.info("Charge point %s connected", charge_point_id)
            cs_manager.charge_point = ChargePoint(charge_point_id, websocket)
            cs_manager.charge_point.route_map = create_route_map(cs_manager)
        else:
            _LOGGER.error("Unexpected charge point: %s.", charge_point_id)
            return await websocket.close()

        await asyncio.gather(
            asyncio.create_task(cs_manager.charge_point.start()),
            asyncio.create_task(cs_manager.loop()),
        )


class ChargingStationRequestError(Exception):
    """Exception raised for errors in charging station requests."""

    def __init__(self, message, charging_station_id, request_id):
        """Initialise ChargingStationRequestError exception."""
        super().__init__(message)
        self.charging_station_id = charging_station_id
        self.request_id = request_id
