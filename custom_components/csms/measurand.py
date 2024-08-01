from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Measurand:
    name: str  # Human-readable name for Home Assistant
    measurand: str  # OCPP measurand
    unit_of_measurement: str  # Unit of measurement
    device_class: str  # Device class for the sensor
    phase: Optional[str] = field(default=None)  # OCPP phase
    location: Optional[str] = field(default="Outlet")  # OCPP location
    unique_key: str = field(init=False)  # Unique key for the sensor, initialized later

    def __post_init__(self):
        self.unique_key = self.generate_key(self.measurand, self.phase, self.location)

    @staticmethod
    def generate_key(measurand: str, phase: str = None, location: str = None) -> str:
        """Generate a unique key based on measurand, phase, and location."""
        key_parts = [measurand]
        if phase:
            key_parts.append(phase)
        if location:
            key_parts.append(location)
        return "_".join(key_parts)

    def to_dict(self):
        """Convert the Measurand instance to a dictionary."""
        return {
            "name": self.name,
            "measurand": self.measurand,
            "unit_of_measurement": self.unit_of_measurement,
            "device_class": self.device_class,
            "phase": self.phase,
            "location": self.location,
            "unique_key": self.unique_key,
        }

    @classmethod
    def from_dict(cls, data: dict):
        """Create a Measurand instance from a dictionary."""
        try:
            return cls(
                name=data["name"],
                measurand=data["measurand"],
                unit_of_measurement=data["unit_of_measurement"],
                device_class=data["device_class"],
                phase=data.get("phase"),
                location=data.get("location", "Outlet"),
            )
        except KeyError:
            return None


default_measurands = [
    Measurand(
        name="Voltage L1-N",
        measurand="Voltage",
        unit_of_measurement="V",
        device_class="voltage",
        phase="L1-N",
    ),
    Measurand(
        name="Voltage L2-N",
        measurand="Voltage",
        unit_of_measurement="V",
        device_class="voltage",
        phase="L2-N",
    ),
    Measurand(
        name="Voltage L3-N",
        measurand="Voltage",
        unit_of_measurement="V",
        device_class="voltage",
        phase="L3-N",
    ),
    Measurand(
        name="Current L1-N",
        measurand="Current.Import",
        unit_of_measurement="A",
        device_class="current",
        phase="L1-N",
    ),
    Measurand(
        name="Current L2-N",
        measurand="Current.Import",
        unit_of_measurement="A",
        device_class="current",
        phase="L2-N",
    ),
    Measurand(
        name="Current L3-N",
        measurand="Current.Import",
        unit_of_measurement="A",
        device_class="current",
        phase="L3-N",
    ),
    Measurand(
        name="Energy Active Import Register",
        measurand="Energy.Active.Import.Register",
        unit_of_measurement="kWh",
        device_class="energy",
    ),
    Measurand(
        name="Power Active Import",
        measurand="Power.Active.Import",
        unit_of_measurement="W",
        device_class="power",
    ),
]
