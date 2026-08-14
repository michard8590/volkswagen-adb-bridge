# SPDX-License-Identifier: GPL-3.0-or-later
from types import SimpleNamespace

from vw_charge import (
    set_charge_state,
    set_target_soc,
)
from vw_climate import (
    set_climate_state,
    set_temperature,
)
from vw_lock import do_command as do_lock_command
from vw_ui import start_app
from vw_vehicle import (
    ensure_vehicle_overview,
    select_vehicle_info,
)


class CommandError(RuntimeError):
    pass


def _cleanup_overview():
    """
    Nach einem Command möglichst wieder einen definierten UI-Zustand
    herstellen. Ein Cleanup-Fehler darf das eigentliche Command-Ergebnis
    nicht überschreiben.
    """
    try:
        ensure_vehicle_overview()
    except Exception:
        pass


def _charge_state(vin, target):
    vehicle, changed, status = set_charge_state(
        vin,
        target,
    )

    return {
        "ok": True,
        "command": f"charge_{target}",
        "vehicle": vehicle,
        "changed": changed,
        "charge": status,
    }


def _target_soc(vin, value):
    target = int(value)

    vehicle, changed, verified = set_target_soc(
        vin,
        target,
    )

    return {
        "ok": True,
        "command": "target_soc",
        "vehicle": vehicle,
        "changed": changed,
        "target_soc": verified,
    }


def _climate_state(vin, target):
    # Climate-Funktionen selbst kennen keine VIN.
    # Deshalb hier IMMER explizit das Fahrzeug auswählen.
    vehicle = select_vehicle_info(vin)

    result = set_climate_state(
        target,
    )

    return {
        "ok": True,
        "command": f"climate_{target}",
        "vehicle": vehicle,
        **result,
    }


def _temperature(vin, value):
    # Auch hier niemals vom aktuell ausgewählten Fahrzeug ausgehen.
    vehicle = select_vehicle_info(vin)

    result = set_temperature(
        float(value),
    )

    return {
        "ok": True,
        "command": "temperature",
        "vehicle": vehicle,
        **result,
    }


def _lock(vin, target, spin_file):
    if not spin_file:
        raise CommandError(
            "Für Lock/Unlock ist eine S-PIN-Datei erforderlich"
        )

    args = SimpleNamespace(
        spin_file=spin_file,
    )

    result = do_lock_command(
        vin,
        target,
        args,
    )

    return {
        "command": (
            "lock"
            if target == "locked"
            else "unlock"
        ),
        **result,
    }


def dispatch_command(
    command,
    vin,
    value=None,
    spin_file=None,
):
    """
    Zentraler Einstiegspunkt für alle schreibenden VW-Kommandos.

    Diese Funktion darf nur innerhalb des einzigen UI-Workers
    ausgeführt werden.
    """
    command = str(command or "").strip().lower()
    vin = str(vin or "").strip()

    if not vin:
        raise CommandError("VIN fehlt")

    valid_commands = {
        "charge_start",
        "charge_stop",
        "target_soc",
        "climate_start",
        "climate_stop",
        "temperature",
        "lock",
        "unlock",
    }

    if command not in valid_commands:
        raise CommandError(
            f"Unbekannter Command: {command!r}"
        )

    start_app()

    try:
        if command == "charge_start":
            return _charge_state(
                vin,
                "start",
            )

        if command == "charge_stop":
            return _charge_state(
                vin,
                "stop",
            )

        if command == "target_soc":
            if value is None:
                raise CommandError(
                    "Zielladestand fehlt"
                )

            return _target_soc(
                vin,
                value,
            )

        if command == "climate_start":
            return _climate_state(
                vin,
                "start",
            )

        if command == "climate_stop":
            return _climate_state(
                vin,
                "stop",
            )

        if command == "temperature":
            if value is None:
                raise CommandError(
                    "Zieltemperatur fehlt"
                )

            return _temperature(
                vin,
                value,
            )

        if command == "lock":
            return _lock(
                vin,
                "locked",
                spin_file,
            )

        if command == "unlock":
            return _lock(
                vin,
                "unlocked",
                spin_file,
            )

        raise CommandError(
            f"Nicht implementierter Command: {command!r}"
        )

    finally:
        _cleanup_overview()
