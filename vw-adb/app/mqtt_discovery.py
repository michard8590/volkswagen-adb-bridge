# SPDX-License-Identifier: GPL-3.0-or-later

import re


DISCOVERY_PREFIX = "homeassistant"
BRIDGE_NAME = "Volkswagen ADB Bridge"
BRIDGE_VERSION = "0.1.12"


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

    return {
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
        "state_topic": vehicle_state_topic(vin),
        "availability_topic": "vw_adb/availability",
        "payload_available": "online",
        "payload_not_available": "offline",
        "qos": 1,
        "cmps": {
            "soc": {
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
            "charging_state": {
                "p": "sensor",
                "name": "Charging state",
                "unique_id": f"{prefix}_charging_state",
                "icon": "mdi:ev-station",
                "value_template": (
                    "{{ value_json.charge.state }}"
                ),
            },
            "lock_state": {
                "p": "sensor",
                "name": "Lock state",
                "unique_id": f"{prefix}_lock_state",
                "icon": "mdi:car-door-lock",
                "value_template": (
                    "{{ value_json.lock.state }}"
                ),
            },
            "climate_state": {
                "p": "sensor",
                "name": "Climate state",
                "unique_id": f"{prefix}_climate_state",
                "icon": "mdi:car-defrost-front",
                "value_template": (
                    "{{ value_json.climate.state }}"
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
                "p": "sensor",
                "name": "Last synchronization",
                "unique_id": f"{prefix}_last_sync",
                "device_class": "timestamp",
                "value_template": (
                    "{{ value_json.sync.last_sync_at }}"
                ),
            },
            "target_soc": {
                "p": "sensor",
                "name": "Target charge",
                "unique_id": f"{prefix}_target_soc",
                "device_class": "battery",
                "unit_of_measurement": "%",
                "entity_category": "diagnostic",
                "value_template": (
                    "{{ value_json.charge.target_soc "
                    "if value_json.charge.target_soc is not none "
                    "else none }}"
                ),
            },
            "odometer": {
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


def publish_vehicle_discovery(mqtt_bridge, vehicle_data):
    vin = vehicle_data["vehicle"]["vin"]

    payload = build_vehicle_discovery(
        vehicle_data
    )

    return mqtt_bridge.publish_json(
        vehicle_discovery_topic(vin),
        payload,
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
