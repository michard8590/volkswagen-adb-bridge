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
):
    vehicle = vehicle_data.get("vehicle") or {}
    vin = str(vehicle.get("vin") or "").strip()

    if not vin:
        raise ValueError("Fahrzeugstatus ohne VIN")

    # Letzten vollständig gelesenen Zustand merken. Er kann nach einem
    # bereits verifizierten Benutzerkommando sofort aktualisiert werden,
    # ohne auf den nächsten Fahrzeug-Poll warten zu müssen.
    _vehicle_state_cache[vin] = vehicle_data

    publish_vehicle_discovery(
        mqtt_bridge,
        vehicle_data,
    )

    publish_vehicle_state(
        mqtt_bridge,
        vehicle_data,
    )

    name = vehicle.get("name") or vin

    log(
        "INFO",
        f"Fahrzeugstatus veröffentlicht: {name}",
    )


def publish_confirmed_command_state(
    mqtt_bridge,
    result,
):
    """
    Bereits durch die VW-App bestätigte Command-Werte sofort in den
    zuletzt bekannten Fahrzeugzustand übernehmen.

    Der anschließende vollständige Poll bleibt die endgültige Kontrolle.
    """
    if not result.get("ok"):
        return False

    vehicle = result.get("vehicle") or {}
    vin = str(vehicle.get("vin") or "").strip()

    if not vin:
        return False

    previous = _vehicle_state_cache.get(vin)

    # Ohne vorherigen vollständigen Fahrzeugstatus keinen Teilzustand
    # veröffentlichen, da sonst andere MQTT-Entities auf unknown springen.
    if previous is None:
        return False

    command = result.get("command")

    updated = dict(previous)
    updated["charge"] = dict(
        previous.get("charge") or {}
    )
    updated["climate"] = dict(
        previous.get("climate") or {}
    )

    message = None

    # --------------------------------------------------------
    # Zielladestand
    # --------------------------------------------------------
    if command == "target_soc":
        target_soc = result.get("target_soc")

        if target_soc is None:
            return False

        updated["charge"]["target_soc"] = int(target_soc)

        message = (
            f"Bestätigten Zielladestand sofort veröffentlicht: "
            f"{target_soc} %"
        )

    # --------------------------------------------------------
    # Laden Start / Stop
    # --------------------------------------------------------
    elif command in (
        "charge_start",
        "charge_stop",
    ):
        charge_result = result.get("charge")

        # set_charge_state kann einen vollständigen Charge-Status
        # zurückliefern. Bekannte Werte direkt übernehmen.
        if isinstance(charge_result, dict):
            for key, value in charge_result.items():
                if value is not None:
                    updated["charge"][key] = value

            verified_state = charge_result.get("state")

        else:
            verified_state = charge_result

        # Falls nur der Zustand selbst zurückgegeben wurde.
        if isinstance(verified_state, str):
            verified_state = verified_state.strip().lower()

            state_map = {
                "charging": "charging",
                "stopped": "stopped",
                "start": "charging",
                "stop": "stopped",
            }

            verified_state = state_map.get(
                verified_state,
                verified_state,
            )

        if verified_state not in (
            "charging",
            "stopped",
        ):
            # Nichts erfinden. Wenn kein eindeutig bestätigter Zustand
            # vorliegt, übernimmt der anschließende Full-Poll.
            return False

        updated["charge"]["state"] = verified_state

        message = (
            f"Bestätigten Ladestatus sofort veröffentlicht: "
            f"{verified_state}"
        )

    # --------------------------------------------------------
    # Klimatisierung Start / Stop
    # --------------------------------------------------------
    elif command in (
        "climate_start",
        "climate_stop",
    ):
        climate_state = result.get("state")

        if climate_state not in (
            "running",
            "stopped",
        ):
            return False

        updated["climate"]["state"] = climate_state

        message = (
            f"Bestätigten Klimastatus sofort veröffentlicht: "
            f"{climate_state}"
        )

    # --------------------------------------------------------
    # Klima-Zieltemperatur
    # --------------------------------------------------------
    elif command == "temperature":
        temperature = result.get("temperature")

        if temperature is None:
            return False

        updated["climate"]["target_temperature"] = float(
            temperature
        )

        message = (
            f"Bestätigte Klima-Zieltemperatur sofort veröffentlicht: "
            f"{float(temperature):.1f} °C"
        )

    else:
        return False

    _vehicle_state_cache[vin] = updated

    publish_vehicle_state(
        mqtt_bridge,
        updated,
    )

    name = vehicle.get("name") or vin

    log(
        "INFO",
        f"{message} ({name})",
    )

    return True


def run_vehicle_poll(
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
        cancel_event=cancel_event,
        on_vehicle=lambda vehicle_data: publish_vehicle_update(
            mqtt_bridge,
            vehicle_data,
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

            # Ersten vollständigen Fahrzeug-Poll sofort ausführen.
            next_poll = now

            active_poll = None
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

                    # Bereits durch die VW-App verifizierte Änderungen
                    # sofort in Home Assistant sichtbar machen.
                    publish_confirmed_command_state(
                        mqtt_bridge,
                        result,
                    )

                    # Danach trotzdem den vollständigen Fahrzeugzustand
                    # erneut lesen und damit das Ergebnis kontrollieren.
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
                # Vehicle poll
                # --------------------------------------------------

                if (
                    active_poll is None
                    and now >= next_poll
                ):
                    active_poll = jobs.submit(
                        "vehicle-poll",
                        run_vehicle_poll,
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
