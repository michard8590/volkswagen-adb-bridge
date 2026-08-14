#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import os
import queue
import sys
import time

from command_service import submit_command
from connection import (
    ConnectionError,
    device_to_dict,
    load_options,
    maybe_pair_wifi,
)
from job_queue import (
    BackgroundCancelled,
    UIJobQueue,
    PRIORITY_BACKGROUND,
    PRIORITY_POLL,
)
from mqtt_bridge import MQTTBridge
from mqtt_discovery import (
    publish_vehicle_discovery,
    publish_vehicle_state,
)
from u2_connection import U2Connection
from vw_poll import poll_once


RETRY_SECONDS = 15
HEALTHCHECK_SECONDS = 30
LOOP_SLEEP_SECONDS = 1


def log(level, message):
    print(
        f"[{level}] {message}",
        flush=True,
    )


def print_adb_version():
    try:
        import subprocess

        adb_version = subprocess.run(
            ["adb", "version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            check=False,
        ).stdout.strip()

        log(
            "INFO",
            "ADB-Version:\n" + adb_version,
        )

    except Exception as exc:
        log(
            "ERROR",
            f"ADB-Version konnte nicht gelesen werden: {exc}",
        )


def connection_healthcheck(connection):
    """
    Läuft im einzigen UI-Worker.

    Dadurch findet auch der uiautomator2-Dump des Healthchecks niemals
    gleichzeitig mit einem VW-Poll oder Benutzerkommando statt.
    """
    if connection.is_alive():
        return {
            "ok": True,
            "reconnected": False,
            "device": device_to_dict(connection.device),
        }

    log(
        "WARNING",
        "Android-Verbindung verloren. "
        "Starte AutoDiscovery/Reconnect.",
    )

    connection.reconnect(
        attempts=3,
        delay=2,
    )

    return {
        "ok": True,
        "reconnected": True,
        "device": device_to_dict(connection.device),
    }


_vehicle_state_cache = {}


def publish_vehicle_update(
    mqtt_bridge,
    vehicle_data,
    poll_type,
):
    vehicle = vehicle_data.get("vehicle") or {}
    vin = str(vehicle.get("vin") or "").strip()

    if not vin:
        raise ValueError("Fahrzeugstatus ohne VIN")

    previous = _vehicle_state_cache.get(vin)

    merged = dict(vehicle_data)
    merged["charge"] = dict(
        vehicle_data.get("charge") or {}
    )

    if previous is not None:
        previous_charge = previous.get("charge") or {}

        if merged["charge"].get("target_soc") is None:
            merged["charge"]["target_soc"] = (
                previous_charge.get("target_soc")
            )

        if merged.get("odometer_km") is None:
            merged["odometer_km"] = (
                previous.get("odometer_km")
            )

    _vehicle_state_cache[vin] = merged

    publish_vehicle_discovery(
        mqtt_bridge,
        merged,
    )

    publish_vehicle_state(
        mqtt_bridge,
        merged,
    )

    name = vehicle.get("name") or vin

    log(
        "INFO",
        f"Fahrzeugstatus veröffentlicht: "
        f"{name} ({poll_type})",
    )


def run_basic_poll(
    options,
    mqtt_bridge,
    cancel_event=None,
):
    return poll_once(
        sync_if_older_than=options.get(
            "sync_if_older_than",
            900,
        ),
        sync_wait_timeout=options.get(
            "sync_wait_timeout",
            180,
        ),
        stop_after=options.get(
            "stop_app_after_poll",
            True,
        ),
        include_details=False,
        cancel_event=cancel_event,
        on_vehicle=lambda vehicle_data: publish_vehicle_update(
            mqtt_bridge,
            vehicle_data,
            "basic",
        ),
    )


def run_detail_poll(
    options,
    mqtt_bridge,
    cancel_event=None,
):
    return poll_once(
        sync_if_older_than=options.get(
            "sync_if_older_than",
            900,
        ),
        sync_wait_timeout=options.get(
            "sync_wait_timeout",
            180,
        ),
        stop_after=options.get(
            "stop_app_after_poll",
            True,
        ),
        include_details=True,
        cancel_event=cancel_event,
        on_vehicle=lambda vehicle_data: publish_vehicle_update(
            mqtt_bridge,
            vehicle_data,
            "detail",
        ),
    )


def main():
    log(
        "INFO",
        "Volkswagen ADB Bridge startet",
    )

    print_adb_version()

    connection = None
    jobs = None
    mqtt_bridge = None

    while True:
        try:
            options = load_options()

            mode = options.get(
                "connection_mode",
                "auto",
            )

            log(
                "INFO",
                f"ADB-Verbindungsmodus: {mode}",
            )

            connection = U2Connection(
                options,
                log=log,
            )

            # Zuerst immer die bereits vorhandene ADB-Kopplung verwenden.
            # Ein eventuell noch gespeicherter Pairing-Code darf einen
            # bereits gekoppelten Start nicht erneut ins Pairing zwingen.
            try:
                connection.connect()

            except ConnectionError as first_error:
                log(
                    "WARNING",
                    "Bestehende ADB-Verbindung konnte nicht hergestellt "
                    "werden. Prüfe, ob ein Wireless-Debugging-Pairing "
                    "konfiguriert ist.",
                )

                if not maybe_pair_wifi(options):
                    raise first_error

                log(
                    "INFO",
                    "WLAN-ADB-Pairing erfolgreich.",
                )

                # Nach erfolgreichem Pairing den normalen Connect-Port
                # erneut per mDNS suchen und verbinden.
                connection.connect()

            log(
                "INFO",
                "Android-Verbindung bereit: "
                + json.dumps(
                    device_to_dict(connection.device),
                    ensure_ascii=False,
                ),
            )

            # Genau ein Worker für alle späteren VW-UI-Aktionen.
            jobs = UIJobQueue(
                log=log,
            )
            jobs.start()

            log(
                "INFO",
                "VW UI-Worker bereit.",
            )

            # MQTT läuft in einem eigenen Netzwerk-Thread. Eingehende
            # Commands werden ausschließlich in den UI-Worker eingereiht.
            submitted_commands = queue.Queue()
            pending_commands = []

            def on_mqtt_command(payload):
                command = str(
                    payload.get("command", "")
                ).strip()

                vin = str(
                    payload.get("vin", "")
                ).strip()

                request_id = payload.get(
                    "request_id"
                )

                if not command:
                    raise ValueError(
                        "MQTT-Command enthält keinen 'command'"
                    )

                if not vin:
                    raise ValueError(
                        "MQTT-Command enthält keine 'vin'"
                    )

                job = submit_command(
                    jobs,
                    command,
                    vin,
                    value=payload.get("value"),
                    # Die S-PIN kommt ausschließlich aus der
                    # lokalen Add-on-Konfiguration, niemals aus MQTT.
                    spin_file=(
                        os.getenv("VW_SPIN_FILE")
                        or None
                    ),
                )

                submitted_commands.put(
                    (job, request_id)
                )

                log(
                    "INFO",
                    f"MQTT-Command eingereiht: "
                    f"{command} für {vin}",
                )

            mqtt_bridge = MQTTBridge(
                on_command=on_mqtt_command,
                log=log,
            )

            mqtt_bridge.start()

            log(
                "INFO",
                "MQTT-Bridge gestartet.",
            )

            now = time.monotonic()

            next_healthcheck = (
                now + HEALTHCHECK_SECONDS
            )

            # Ersten Basic-Poll sofort ausführen.
            next_poll = now

            # Detailwerte etwas später lesen, damit nach dem Start
            # zunächst schnell die normalen Fahrzeugwerte verfügbar sind.
            next_detail_poll = now + 60

            active_poll = None
            active_detail_poll = None
            active_healthcheck = None

            while True:
                now = time.monotonic()

                # --------------------------------------------------
                # MQTT Command-Ergebnisse
                # --------------------------------------------------

                while True:
                    try:
                        pending_commands.append(
                            submitted_commands.get_nowait()
                        )
                    except queue.Empty:
                        break

                remaining_commands = []

                for command_job, request_id in pending_commands:
                    if not command_job.done_event.is_set():
                        remaining_commands.append(
                            (command_job, request_id)
                        )
                        continue

                    try:
                        result = command_job.wait()

                        if not isinstance(result, dict):
                            result = {
                                "ok": True,
                                "result": result,
                            }

                    except Exception as exc:
                        result = {
                            "ok": False,
                            "error": str(exc),
                        }

                    if request_id is not None:
                        result = {
                            **result,
                            "request_id": request_id,
                        }

                    mqtt_bridge.publish_result(
                        result
                    )

                    # Nach einem Benutzerkommando den tatsächlichen
                    # Fahrzeugzustand möglichst schnell erneut lesen.
                    next_poll = time.monotonic()

                    log(
                        "INFO",
                        "MQTT-Command abgeschlossen: "
                        + json.dumps(
                            result,
                            ensure_ascii=False,
                        ),
                    )

                pending_commands = remaining_commands

                # --------------------------------------------------
                # Basic poll
                # --------------------------------------------------

                if (
                    active_poll is None
                    and now >= next_poll
                ):
                    active_poll = jobs.submit(
                        "basic-poll",
                        run_basic_poll,
                        options,
                        mqtt_bridge,
                        priority=PRIORITY_POLL,
                        cancellable=True,
                    )

                    next_poll = (
                        now
                        + int(
                            options.get(
                                "poll_interval",
                                300,
                            )
                        )
                    )

                if (
                    active_poll is not None
                    and active_poll.done_event.is_set()
                ):
                    try:
                        result = active_poll.wait()

                        mqtt_bridge.publish_state(
                            result
                        )

                        # Zusätzlich pro Fahrzeug einen eigenen,
                        # retained MQTT-State sowie Home-Assistant
                        # Device Discovery veröffentlichen.
                        log(
                            "INFO",
                            "Fahrzeug-Poll abgeschlossen: "
                            + json.dumps(
                                result,
                                ensure_ascii=False,
                            ),
                        )

                    except BackgroundCancelled:
                        log(
                            "INFO",
                            "Fahrzeug-Poll zugunsten eines "
                            "Benutzerkommandos abgebrochen.",
                        )

                    except Exception as exc:
                        log(
                            "ERROR",
                            f"Fahrzeug-Poll fehlgeschlagen: {exc}",
                        )

                    active_poll = None

                # --------------------------------------------------
                # Detail poll
                # --------------------------------------------------

                if (
                    active_detail_poll is None
                    and active_poll is None
                    and now >= next_detail_poll
                ):
                    active_detail_poll = jobs.submit(
                        "detail-poll",
                        run_detail_poll,
                        options,
                        mqtt_bridge,
                        priority=PRIORITY_BACKGROUND,
                        cancellable=True,
                    )

                    next_detail_poll = (
                        now
                        + int(
                            options.get(
                                "detail_poll_interval",
                                900,
                            )
                        )
                    )

                if (
                    active_detail_poll is not None
                    and active_detail_poll.done_event.is_set()
                ):
                    try:
                        result = active_detail_poll.wait()

                        mqtt_bridge.publish_state(
                            result
                        )

                        log(
                            "INFO",
                            "Fahrzeug-Detail-Poll abgeschlossen: "
                            + json.dumps(
                                result,
                                ensure_ascii=False,
                            ),
                        )

                    except BackgroundCancelled:
                        log(
                            "INFO",
                            "Fahrzeug-Detail-Poll zugunsten eines "
                            "Benutzerkommandos abgebrochen.",
                        )

                    except Exception as exc:
                        log(
                            "ERROR",
                            f"Fahrzeug-Detail-Poll fehlgeschlagen: {exc}",
                        )

                    active_detail_poll = None

                # --------------------------------------------------
                # Connection healthcheck
                # --------------------------------------------------

                if (
                    active_healthcheck is None
                    and now >= next_healthcheck
                ):
                    active_healthcheck = jobs.submit(
                        "connection-healthcheck",
                        connection_healthcheck,
                        connection,
                        priority=PRIORITY_BACKGROUND,
                        cancellable=False,
                    )


                if (
                    active_healthcheck is not None
                    and active_healthcheck.done_event.is_set()
                ):
                    result = active_healthcheck.wait()

                    device = result["device"]

                    if result["reconnected"]:
                        log(
                            "INFO",
                            "Android-Verbindung wiederhergestellt: "
                            + json.dumps(
                                device,
                                ensure_ascii=False,
                            ),
                        )
                    else:
                        log(
                            "INFO",
                            f"Android-Verbindung OK: "
                            f"{device['adb_serial']}",
                        )

                    active_healthcheck = None
                    next_healthcheck = (
                        time.monotonic()
                        + HEALTHCHECK_SECONDS
                    )

                time.sleep(LOOP_SLEEP_SECONDS)

        except KeyboardInterrupt:
            log(
                "INFO",
                "Beendet",
            )

            if mqtt_bridge is not None:
                mqtt_bridge.stop()

            if jobs is not None:
                jobs.stop()

            if connection is not None:
                connection.close()

            return 0

        except (
            ConnectionError,
            OSError,
            ValueError,
            TimeoutError,
        ) as exc:
            log(
                "ERROR",
                str(exc),
            )

            if mqtt_bridge is not None:
                try:
                    mqtt_bridge.stop()
                except Exception:
                    pass
                mqtt_bridge = None

            if jobs is not None:
                jobs.stop()
                jobs = None

            if connection is not None:
                connection.close()
                connection = None

            log(
                "INFO",
                f"Neuer Versuch in "
                f"{RETRY_SECONDS} Sekunden",
            )

            time.sleep(RETRY_SECONDS)

        except Exception as exc:
            log(
                "ERROR",
                f"Unerwarteter Fehler: {exc}",
            )

            if mqtt_bridge is not None:
                try:
                    mqtt_bridge.stop()
                except Exception:
                    pass
                mqtt_bridge = None

            if jobs is not None:
                jobs.stop()
                jobs = None

            if connection is not None:
                connection.close()
                connection = None

            time.sleep(RETRY_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
