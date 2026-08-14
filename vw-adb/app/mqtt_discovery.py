# SPDX-License-Identifier: GPL-3.0-or-later

import re


DISCOVERY_PREFIX = "homeassistant"
BRIDGE_NAME = "Volkswagen ADB Bridge"
BRIDGE_VERSION = "0.1.23"

# Bereits migrierte Discovery-Geräte dieses Add-on-Laufs.
_discovery_cleanup_done = set()


def _safe_id(value):
    value = re.sub(
        r"[^A-Za-z0-9_-]+",
        "_",
        str(value),
    )
    return value.strip("_").lower()


def vehicle_object_id(vin):
    return f"vw_adb_{_safe_id(vin)}"


def vehicle_state_topic(vin):
    return f"vw_adb/vehicle/{vin}/state"


def vehicle_location_topic(vin):
    return f"vw_adb/vehicle/{vin}/location"


def vehicle_discovery_topic(vin):
    return (
        f"{DISCOVERY_PREFIX}/device/"
        f"{vehicle_object_id(vin)}/config"
    )


def build_vehicle_discovery(vehicle_data):
    vehicle = vehicle_data.get("vehicle") or {}

    vin = str(vehicle.get("vin") or "").strip()
    name = str(vehicle.get("name") or vin).strip()

    if not vin:
        raise ValueError(
            "Fahrzeug ohne VIN kann nicht per MQTT Discovery "
            "veröffentlicht werden"
        )

    prefix = vehicle_object_id(vin)

    payload = {
        "dev": {
            "ids": [prefix],
            "name": name,
            "mf": "Volkswagen",
            "mdl": name,
            "sn": vin,
            "sw": BRIDGE_VERSION,
        },
        "o": {
            "name": BRIDGE_NAME,
            "sw": BRIDGE_VERSION,
        },
        "availability_topic": "vw_adb/availability",
        "payload_available": "online",
        "payload_not_available": "offline",
        "qos": 1,
        "cmps": {
            "soc": {
                "state_topic": vehicle_state_topic(vin),
                "p": "sensor",
                "name": "Battery",
                "unique_id": f"{prefix}_soc",
                "device_class": "battery",
                "state_class": "measurement",
                "unit_of_measurement": "%",
                "value_template": (
                    "{{ value_json.charge.soc "
                    "if value_json.charge.soc is not none "
                    "else none }}"
                ),
            },
            "range": {
                "state_topic": vehicle_state_topic(vin),
                "p": "sensor",
                "name": "Range",
                "unique_id": f"{prefix}_range",
                "device_class": "distance",
                "state_class": "measurement",
                "unit_of_measurement": "km",
                "value_template": (
                    "{{ value_json.charge.range_km "
                    "if value_json.charge.range_km is not none "
                    "else none }}"
                ),
            },
            "climate_control": {
                "p": "climate",
                "name": "Climate",
                "unique_id": f"{prefix}_climate_control",
                "icon": "mdi:car-defrost-front",
                "modes": [
                    "off",
                    "auto",
                ],
                "mode_command_topic": "vw_adb/command",
                "mode_command_template": (
                    '{"command":"climate_'
                    '{{ "stop" if value == "off" else "start" }}'
                    '","vin":"'
                    + vin
                    + '"}'
                ),
                "mode_state_topic": vehicle_state_topic(vin),
                "mode_state_template": (
                    '{{ "auto" if value_json.climate.state == '
                    '"running" else "off" }}'
                ),
                "temperature_command_topic": "vw_adb/command",
                "temperature_command_template": (
                    '{"command":"temperature","vin":"'
                    + vin
                    + '","value":{{ value | float }}}'
                ),
                "temperature_state_topic": vehicle_state_topic(vin),
                "temperature_state_template": (
                    "{{ value_json.climate.target_temperature "
                    "if value_json.climate.target_temperature "
                    "is not none else none }}"
                ),
                "temperature_unit": "C",
                "min_temp": 10.0,
                "max_temp": 35.0,
                "temp_step": 0.5,
                "precision": 0.5,
                "optimistic": False,
            },
            "last_sync": {
                "state_topic": vehicle_state_topic(vin),
                "p": "sensor",
                "name": "Last synchronization",
                "unique_id": f"{prefix}_last_sync",
                "device_class": "timestamp",
                "value_template": (
                    "{{ value_json.sync.last_sync_at }}"
                ),
            },
            "odometer": {
                "state_topic": vehicle_state_topic(vin),
                "p": "sensor",
                "name": "Odometer",
                "unique_id": f"{prefix}_odometer",
                "device_class": "distance",
                "state_class": "total_increasing",
                "unit_of_measurement": "km",
                "icon": "mdi:counter",
                "value_template": (
                    "{{ value_json.odometer_km "
                    "if value_json.odometer_km is not none "
                    "else none }}"
                ),
            },
            "target_soc_control": {
                "state_topic": vehicle_state_topic(vin),
                "p": "number",
                "name": "Target charge",
                "unique_id": f"{prefix}_target_soc_control",
                "icon": "mdi:battery-charging",
                "unit_of_measurement": "%",
                "min": 50,
                "max": 100,
                "step": 10,
                "mode": "slider",
                "command_topic": "vw_adb/command",
                "command_template": (
                    '{"command":"target_soc","vin":"'
                    + vin
                    + '","value":{{ value | int }}}'
                ),
                "value_template": (
                    "{{ value_json.charge.target_soc "
                    "if value_json.charge.target_soc is not none "
                    "else none }}"
                ),
            },
            "charging_control": {
                "state_topic": vehicle_state_topic(vin),
                "p": "switch",
                "name": "Charging",
                "unique_id": f"{prefix}_charging_control",
                "icon": "mdi:ev-station",
                "command_topic": "vw_adb/command",
                "command_template": (
                    '{"command":"charge_{{ value }}","vin":"'
                    + vin
                    + '"}'
                ),
                "payload_on": "start",
                "payload_off": "stop",
                "state_on": "charging",
                "state_off": "stopped",
                "value_template": (
                    "{{ value_json.charge.state }}"
                ),
                "optimistic": False,
            },
            "sync_age": {
                "state_topic": vehicle_state_topic(vin),
                "p": "sensor",
                "name": "Synchronization age",
                "unique_id": f"{prefix}_sync_age",
                "device_class": "duration",
                "state_class": "measurement",
                "unit_of_measurement": "s",
                "entity_category": "diagnostic",
                "value_template": (
                    "{{ value_json.sync.last_sync_age_seconds "
                    "if value_json.sync.last_sync_age_seconds "
                    "is not none else none }}"
                ),
            },
        },
    }


    payload["cmps"]["location"] = {
        "p": "device_tracker",
        "name": "Location",
        "unique_id": f"{prefix}_location",
        "icon": "mdi:car-marker",
        "json_attributes_topic": vehicle_location_topic(vin),
        "source_type": "gps",
    }

    # Remote Lock/Unlock nur anbieten, wenn die VW-App diese Funktion
    # für genau dieses Fahrzeug tatsächlich bereitstellt.
    lock = vehicle_data.get("lock") or {}

    # Fahrzeuge ohne Remote Lock/Unlock behalten den read-only
    # Lock-State. Bei unterstützten Fahrzeugen übernimmt die native
    # Lock-Entität sowohl Anzeige als auch Steuerung.
    if lock.get("supported") is not True:
        payload["cmps"]["lock_state"] = {
            "state_topic": vehicle_state_topic(vin),
            "p": "sensor",
            "name": "Lock state",
            "unique_id": f"{prefix}_lock_state",
            "icon": "mdi:car-door-lock",
            "value_template": (
                "{{ value_json.lock.state }}"
            ),
        }

    if lock.get("supported") is True:
        payload["cmps"]["lock_control"] = {
            "state_topic": vehicle_state_topic(vin),
            "p": "lock",
            "name": "Lock",
            "unique_id": f"{prefix}_lock_control",
            "icon": "mdi:car-door-lock",
            "command_topic": "vw_adb/command",
            "command_template": (
                '{"command":"{{ value }}","vin":"'
                + vin
                + '"}'
            ),
            "payload_lock": "lock",
            "payload_unlock": "unlock",
            "state_locked": "locked",
            "state_unlocked": "unlocked",
            "value_template": (
                "{{ value_json.lock.state }}"
            ),
            "optimistic": False,
        }

    return payload


def build_obsolete_component_cleanup(vehicle_data):
    """
    Entfernt Discovery-Komponenten, die seit 0.1.14 durch native
    steuerbare Entities ersetzt wurden.

    Home Assistant verlangt bei Device Discovery für das Entfernen
    einer einzelnen Komponente weiterhin mindestens deren Plattform.
    """
    normal = build_vehicle_discovery(
        vehicle_data
    )

    lock = vehicle_data.get("lock") or {}

    components = {
        "charging_state": {
            "p": "sensor",
        },
        "climate_state": {
            "p": "sensor",
        },
        "target_soc": {
            "p": "sensor",
        },

        # Vor 0.1.18 hat der Location-Tracker das gemeinsame
        # vehicle state_topic geerbt. Die alte Komponente muss einmal
        # explizit entfernt werden, damit Home Assistant diese alte
        # Konfiguration nicht weiter verwendet.
        "location": {
            "p": "device_tracker",
        },
    }

    # Nur beim Remote-Lock-fähigen Fahrzeug verschwindet der separate
    # Lock-State-Sensor. Ohne Remote Lock bleibt er bestehen.
    if lock.get("supported") is True:
        components["lock_state"] = {
            "p": "sensor",
        }

    cleanup = {
        **normal,
        "cmps": components,
    }

    return cleanup


def publish_vehicle_discovery(mqtt_bridge, vehicle_data):
    vin = vehicle_data["vehicle"]["vin"]
    topic = vehicle_discovery_topic(vin)

    if vin not in _discovery_cleanup_done:
        cleanup = build_obsolete_component_cleanup(
            vehicle_data
        )

        mqtt_bridge.publish_json(
            topic,
            cleanup,
            retain=True,
            qos=1,
        )

        _discovery_cleanup_done.add(vin)

    payload = build_vehicle_discovery(
        vehicle_data
    )

    return mqtt_bridge.publish_json(
        topic,
        payload,
        retain=True,
        qos=1,
    )


def publish_vehicle_location(
    mqtt_bridge,
    vin,
    latitude,
    longitude,
):
    return mqtt_bridge.publish_json(
        vehicle_location_topic(vin),
        {
            "latitude": float(latitude),
            "longitude": float(longitude),
        },
        retain=True,
        qos=1,
    )


def publish_vehicle_state(mqtt_bridge, vehicle_data):
    vin = vehicle_data["vehicle"]["vin"]

    return mqtt_bridge.publish_json(
        vehicle_state_topic(vin),
        vehicle_data,
        retain=True,
        qos=1,
    )
