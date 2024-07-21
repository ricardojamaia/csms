### Initialisation

__init__.py:
    async_setup_entry:
        1. Creates CSMS
        2. Adds the CSMS to the configuration entry
        3. Adds a event listner to execute CSMS startup after home asistant started
        4. Forwards entry setups to platforms e.g. sensor.py

    csms_async_startup:
        1. Creates a background task to run CSMS

sensor.py:
    async_setup_entry:
        1. Creates ChargingPointSensor for each known Charging Point and adds them as a device.
           Charging sensors have a refence to the CSMS that can be used to retrieve the corresponding Charing Point attributes

