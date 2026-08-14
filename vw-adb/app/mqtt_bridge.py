# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import ssl
import threading

import paho.mqtt.client as mqtt


DEFAULT_PREFIX = "vw_adb"


class MQTTBridge:
    def __init__(
        self,
        on_command=None,
        log=None,
        topic_prefix=DEFAULT_PREFIX,
    ):
        self.log = log or (lambda level, message: None)
        self.on_command_callback = on_command

        self.topic_prefix = (
            str(topic_prefix)
            .strip()
            .strip("/")
        )

        self.host = os.getenv("MQTT_HOST", "").strip()
        self.port = int(os.getenv("MQTT_PORT", "1883"))

        self.username = os.getenv("MQTT_USERNAME", "")
        self.password = os.getenv("MQTT_PASSWORD", "")

        self.use_ssl = (
            os.getenv("MQTT_SSL", "false")
            .strip()
            .lower()
            in ("1", "true", "yes", "on")
        )

        self.connected = threading.Event()

        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="vw-adb-bridge",
            protocol=mqtt.MQTTv311,
        )

        if self.username:
            self.client.username_pw_set(
                self.username,
                self.password,
            )

        if self.use_ssl:
            self.client.tls_set(
                cert_reqs=ssl.CERT_REQUIRED,
            )

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    @property
    def command_topic(self):
        return f"{self.topic_prefix}/command"

    @property
    def result_topic(self):
        return f"{self.topic_prefix}/result"

    @property
    def state_topic(self):
        return f"{self.topic_prefix}/state"

    @property
    def availability_topic(self):
        return f"{self.topic_prefix}/availability"

    def start(self):
        if not self.host:
            raise RuntimeError("MQTT_HOST ist nicht gesetzt")

        self.client.will_set(
            self.availability_topic,
            payload="offline",
            qos=1,
            retain=True,
        )

        self.log(
            "INFO",
            f"Verbinde MQTT mit {self.host}:{self.port}",
        )

        self.client.connect(
            self.host,
            self.port,
            keepalive=60,
        )

        self.client.loop_start()

    def stop(self):
        try:
            self.publish_availability("offline")
        except Exception:
            pass

        try:
            self.client.disconnect()
        finally:
            self.client.loop_stop()

    def _on_connect(
        self,
        client,
        userdata,
        flags,
        reason_code,
        properties,
    ):
        if reason_code.is_failure:
            self.log(
                "ERROR",
                f"MQTT-Verbindung fehlgeschlagen: {reason_code}",
            )
            return

        self.connected.set()

        self.log(
            "INFO",
            "MQTT verbunden.",
        )

        client.subscribe(
            self.command_topic,
            qos=1,
        )

        self.publish_availability("online")

    def _on_disconnect(
        self,
        client,
        userdata,
        disconnect_flags,
        reason_code,
        properties,
    ):
        self.connected.clear()

        if reason_code != 0:
            self.log(
                "WARNING",
                f"MQTT-Verbindung getrennt: {reason_code}",
            )

    def _on_message(
        self,
        client,
        userdata,
        message,
    ):
        if message.topic != self.command_topic:
            return

        try:
            payload = json.loads(
                message.payload.decode("utf-8")
            )

            if not isinstance(payload, dict):
                raise ValueError(
                    "Command-Payload muss ein JSON-Objekt sein"
                )

        except Exception as exc:
            self.publish_result({
                "ok": False,
                "error": f"Ungültiger MQTT-Command: {exc}",
            })
            return

        if self.on_command_callback is None:
            self.publish_result({
                "ok": False,
                "error": "Kein Command-Handler registriert",
            })
            return

        try:
            self.on_command_callback(payload)

        except Exception as exc:
            self.publish_result({
                "ok": False,
                "error": str(exc),
            })

    def publish_json(
        self,
        topic,
        payload,
        *,
        retain=False,
        qos=1,
    ):
        data = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )

        return self.client.publish(
            topic,
            data,
            qos=qos,
            retain=retain,
        )

    def publish_state(self, payload):
        return self.publish_json(
            self.state_topic,
            payload,
            retain=True,
        )

    def publish_result(self, payload):
        return self.publish_json(
            self.result_topic,
            payload,
            retain=False,
        )

    def publish_availability(self, state):
        return self.client.publish(
            self.availability_topic,
            str(state),
            qos=1,
            retain=True,
        )
