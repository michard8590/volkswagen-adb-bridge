#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later

import argparse
import json
import re
import subprocess
import sys
import time

from vw_ui import (
    UIError,
    start_app,
    parent_of,
    parse_bounds,
    tap,
    all_nodes,
    clickable_parent,
    dump_ui,
    find_by_resource_id,
    tap_node,
)

from vw_ui import dump_ui_checked
from vw_vehicle import (
    ensure_vehicle_overview,
    open_settings,
    select_vehicle_info,
)


POLL_INTERVAL = 1.0
DIALOG_TIMEOUT = 10.0
COMMAND_TIMEOUT = 30.0
DETAIL_TIMEOUT = 8.0
TARGET_SOC_WAIT_TIMEOUT = 8.0

# Accessibility-Texte der deutschen und englischen VW-App.
# str.startswith() akzeptiert direkt ein Tuple von Präfixen.
CHARGE_START = (
    "Laden starten",
    "Start charging",
)
CHARGE_STOP = (
    "Laden stoppen",
    "Stop charging",
)

SOC_POSITIONS = {
    50: 0.10,
    60: 0.20,
    70: 0.30,
    80: 0.50,
    90: 0.70,
    100: 0.80,
}


def get_charge_button(root):
    for item in all_nodes(root):
        desc = item["desc"]

        if desc.startswith(CHARGE_START):
            return (
                item["node"],
                CHARGE_START,
            )

        if desc.startswith(CHARGE_STOP):
            return (
                item["node"],
                CHARGE_STOP,
            )

    return None, None


def parse_first_int(text):
    match = re.search(
        r"(\d+)",
        text,
    )

    if not match:
        return None

    return int(match.group(1))


def parse_remaining_minutes(text):
    text = text.lower()

    hours = 0
    minutes = 0
    found = False

    match = re.search(
        r"(\d+)\s*(?:stunden?|hours?)",
        text,
    )

    if match:
        hours = int(
            match.group(1)
        )
        found = True

    match = re.search(
        r"(\d+)\s*(?:minuten?|minutes?)",
        text,
    )

    if match:
        minutes = int(
            match.group(1)
        )
        found = True

    if not found:
        return None

    return (
        hours * 60
        + minutes
    )


def parse_dialog(root):
    result = {
        "state": "unknown",
        "soc": None,
        "target_soc": None,
        "remaining_minutes": None,
        "charge_power_kw": None,
        "charge_rate_kmh": None,
    }

    _, button_desc = get_charge_button(
        root
    )

    if button_desc == CHARGE_STOP:
        result["state"] = "charging"

    elif button_desc == CHARGE_START:
        result["state"] = "stopped"

    for item in all_nodes(root):
        text = item["text"]
        desc = item["desc"]
        rid = item["id"]

        if rid.endswith(
            "rangeArcBatterySoc"
        ):
            value = parse_first_int(
                text
            )

            if value is not None:
                result["soc"] = value

        desc_lower = desc.lower()

        if not desc_lower.startswith(
            (
                "ladedetails",
                "charging details",
            )
        ):
            continue

        match = re.search(
            r"(?:Zielladestand|Target state of charge):\s*"
            r"(\d+)\s*(?:Prozent|Percent|%)",
            desc,
            re.IGNORECASE,
        )

        if match:
            result["target_soc"] = int(
                match.group(1)
            )

        match = re.search(
            r"(?:Ladegeschwindigkeit|Charging speed):\s*"
            r"(\d+(?:[.,]\d+)?)\s*"
            r"(?:Kilometer pro Stunde|Kilometres per hour|"
            r"Kilometers per hour|km/h)",
            desc,
            re.IGNORECASE,
        )

        if match:
            result[
                "charge_rate_kmh"
            ] = float(
                match.group(1).replace(
                    ",",
                    ".",
                )
            )

        match = re.search(
            r"(?:Ladeleistung|Charging power):\s*"
            r"(\d+(?:[.,]\d+)?)\s*"
            r"(?:Kilowatt|Kilowatts|kW)",
            desc,
            re.IGNORECASE,
        )

        if match:
            result[
                "charge_power_kw"
            ] = float(
                match.group(1).replace(
                    ",",
                    ".",
                )
            )

        result[
            "remaining_minutes"
        ] = parse_remaining_minutes(
            desc
        )

    return result


def open_charging_dialog_once():
    root = dump_ui_checked()

    button, _ = get_charge_button(
        root
    )

    if button is not None:
        return root

    # Ladeansicht kann auch offen sein,
    # ohne dass ein Start-/Stop-Button
    # exakt erkannt wird.
    if find_by_resource_id(
        root,
        "rangeArcBatterySoc",
    ) is not None:
        return root

    range_tile = find_by_resource_id(
        root,
        "rangeTile",
    )

    if range_tile is None:
        raise UIError(
            "rangeTile nicht gefunden"
        )

    target = clickable_parent(
        root,
        range_tile,
    )

    tap_node(
        target
        if target is not None
        else range_tile
    )

    deadline = (
        time.monotonic()
        + DIALOG_TIMEOUT
    )

    while time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL)

        root = dump_ui_checked()

        button, _ = get_charge_button(
            root
        )

        if button is not None:
            return root

    raise UIError(
        "Ladeansicht konnte nicht "
        "geöffnet werden"
    )


def ensure_charging_dialog():
    try:
        return open_charging_dialog_once()
    except UIError:
        # Safe navigation retry only; no vehicle command has been sent.
        ensure_vehicle_overview()
        return open_charging_dialog_once()

def wait_for_state(target_state):
    deadline = (
        time.monotonic()
        + COMMAND_TIMEOUT
    )

    last_status = None

    while time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL)

        root = dump_ui_checked()

        status = parse_dialog(
            root
        )

        last_status = status

        if (
            status["state"]
            == target_state
        ):
            return status

    # Hier absichtlich KEIN Retry
    # des Start-/Stop-Klicks.
    raise RuntimeError(
        "Timeout beim Warten auf "
        f"{target_state!r}; "
        f"letzter Status: "
        f"{last_status}"
    )


def refresh_charging_details(
    initial_status,
):
    """
    Nach einem erfolgreichen Start kann
    VW einige Sekunden benötigen, bis
    Restdauer/Leistung verfügbar sind.

    Der Fahrzeugbefehl wird dabei NICHT
    erneut ausgelöst.
    """
    if (
        initial_status["state"]
        != "charging"
    ):
        return initial_status

    status = initial_status

    deadline = (
        time.monotonic()
        + DETAIL_TIMEOUT
    )

    while time.monotonic() < deadline:
        if (
            status["soc"] is not None
            and
            status["target_soc"]
            is not None
            and
            status["charge_power_kw"]
            is not None
        ):
            return status

        time.sleep(POLL_INTERVAL)

        root = dump_ui_checked()

        current = parse_dialog(
            root
        )

        if (
            current["state"]
            == "charging"
        ):
            status = current

    return status


def parse_overview_charge_status(root):
    """Read fast charging/range information from the normal overview.

    SOC is not exposed in the range tile on every vehicle. In that case it
    stays None and the poller opens the charging detail view once.
    """
    tile = find_by_resource_id(root, "rangeTile")
    if tile is None:
        raise UIError("rangeTile nicht gefunden")

    texts = []
    for node in tile.iter("node"):
        text = node.attrib.get("text", "").strip()
        desc = node.attrib.get("content-desc", "").strip()
        if text:
            texts.append(text)
        if desc:
            texts.append(desc)

    own_text = tile.attrib.get("text", "").strip()
    own_desc = tile.attrib.get("content-desc", "").strip()
    if own_text:
        texts.append(own_text)
    if own_desc:
        texts.append(own_desc)

    # Compose may expose the semantic description as a sibling with identical
    # bounds instead of a child of rangeTile. Include matching live bounds.
    tile_bounds = tile.attrib.get("bounds", "")
    for item in all_nodes(root):
        if item["bounds"] != tile_bounds:
            continue
        if item["text"]:
            texts.append(item["text"])
        if item["desc"]:
            texts.append(item["desc"])

    combined = " | ".join(dict.fromkeys(texts))
    lower = combined.lower()

    soc = None
    match = re.search(r"\b(\d{1,3})\s*%", combined)
    if match:
        value = int(match.group(1))
        if 0 <= value <= 100:
            soc = value

    range_km = None
    match = re.search(
        r"(?:batteriereichweite|battery range):\s*"
        r"(\d+)\s*(?:kilometer|kilometres|kilometers|km)",
        combined,
        re.IGNORECASE,
    )
    if match:
        range_km = int(match.group(1))

    # Reihenfolge ist wichtig: negative/gestoppte Zustände müssen vor
    # einem möglichen isolierten "Laden" erkannt werden.
    if (
        "ladekabel verbinden" in lower
        or "nicht verbunden" in lower
        or "connect charging cable" in lower
        or "not connected" in lower
    ):
        state = "stopped"
        cable_connected = False
    elif any(
        phrase in lower
        for phrase in (
            "laden gestoppt",
            "laden beendet",
            "laden pausiert",
            "ladevorgang gestoppt",
            "ladevorgang beendet",
            "ladevorgang pausiert",
            "nicht laden",
            "nicht ladend",
            "abgebrochen",
            "ladebereit",
            "charging stopped",
            "charge stopped",
            "charging finished",
            "charging completed",
            "charge completed",
            "charging paused",
            "charge paused",
            "not charging",
            "ready to charge",
            "cancelled",
            "canceled",
        )
    ):
        state = "stopped"
        cable_connected = True
    elif (
        "lädt gerade" in lower
        or "wird geladen" in lower
        or "is charging" in lower
        or re.search(
            r"(?:^|[•.\s])lädt(?:[•.\s]|$)",
            lower,
        )
    ):
        state = "charging"
        cable_connected = True
    elif re.search(
        # Nur isoliertes "Laden", z.B. "• Laden •" oder "Laden.".
        # "Laden gestoppt" darf hier ausdrücklich nicht matchen.
        r"(?:^|[•.\s])(?:laden|charging)(?=\s*(?:[•.]|$))",
        lower,
    ):
        state = "charging"
        cable_connected = True
    else:
        # Unbekannte VW-Formulierungen nicht als "stopped" erfinden.
        state = "unknown"
        cable_connected = (
            True
            if (
                "verbunden" in lower
                or "connected" in lower
            )
            else None
        )

    return {
        "state": state,
        "soc": soc,
        "range_km": range_km,
        "cable_connected": cable_connected,
    }


def read_poll_charge_status(root):
    """Reliable poll status; open charging details only when SOC is missing."""
    overview = parse_overview_charge_status(root)

    if overview["soc"] is not None:
        return overview

    detail_root = ensure_charging_dialog()
    detail = parse_dialog(detail_root)

    state = detail.get("state")
    if state in (None, "unknown"):
        state = overview["state"]

    cable_connected = overview["cable_connected"]
    if state == "charging":
        cable_connected = True

    return {
        "state": state,
        "soc": detail.get("soc"),
        "range_km": overview["range_km"],
        "cable_connected": cable_connected,
        "target_soc": detail.get("target_soc"),
        "remaining_minutes": detail.get("remaining_minutes"),
        "charge_power_kw": detail.get("charge_power_kw"),
        "charge_rate_kmh": detail.get("charge_rate_kmh"),
    }

def read_overview_status(vin):
    vehicle = select_vehicle_info(vin)
    root = ensure_vehicle_overview()
    status = parse_overview_charge_status(root)

    if status["soc"] is None:
        raise UIError("SOC konnte auf der Fahrzeugübersicht nicht erkannt werden")

    return vehicle, status


def read_status(vin):
    vehicle = (
        select_vehicle_info(
            vin
        )
    )

    root = (
        ensure_charging_dialog()
    )

    status = parse_dialog(root)

    if status["state"] == "unknown":
        raise UIError(
            "Ladezustand konnte "
            "nicht erkannt werden"
        )

    return vehicle, status


def set_charge_state(
    vin,
    target,
):
    vehicle = (
        select_vehicle_info(
            vin
        )
    )

    root = (
        ensure_charging_dialog()
    )

    button, desc = get_charge_button(
        root
    )

    if button is None:
        raise UIError(
            "Ladebutton nicht gefunden"
        )

    if target == "start":
        if desc == CHARGE_STOP:
            status = parse_dialog(
                root
            )

            status = (
                refresh_charging_details(
                    status
                )
            )

            return (
                vehicle,
                False,
                status,
            )

        if desc != CHARGE_START:
            raise UIError(
                "Unerwarteter Ladebutton: "
                f"{desc!r}"
            )

        target_state = "charging"

    elif target == "stop":
        if desc == CHARGE_START:
            return (
                vehicle,
                False,
                parse_dialog(root),
            )

        if desc != CHARGE_STOP:
            raise UIError(
                "Unerwarteter Ladebutton: "
                f"{desc!r}"
            )

        target_state = "stopped"

    else:
        raise ValueError(target)

    # Ab diesem Punkt wurde ein echter
    # Fahrzeugbefehl ausgelöst.
    # Dieser Klick wird NICHT automatisch
    # wiederholt.
    target_node = clickable_parent(
        root,
        button,
    )

    tap_node(
        target_node
        if target_node is not None
        else button
    )

    status = wait_for_state(
        target_state
    )

    status = (
        refresh_charging_details(
            status
        )
    )

    return (
        vehicle,
        True,
        status,
    )


def read_target_soc_setting():
    """Read the configured target SOC from vehicle settings without changing it."""
    open_settings()
    _, value = wait_for_target_soc_setting()
    return value


def read_target_soc_from_settings(root):
    node = find_by_resource_id(
        root,
        "value",
    )

    if node is None:
        raise UIError(
            "Zielladestand-Wert nicht gefunden"
        )

    value = node.attrib.get(
        "text",
        "",
    ).strip()

    match = re.fullmatch(
        r"(\d+)%",
        value,
    )

    if not match:
        raise UIError(
            f"Ungültiger Zielladestand: {value!r}"
        )

    return int(match.group(1))


def wait_for_target_soc_setting(timeout=TARGET_SOC_WAIT_TIMEOUT):
    """Wait until the settings screen has finished rendering the target-SOC value."""
    deadline = time.monotonic() + timeout
    last_error = None

    while time.monotonic() < deadline:
        root = dump_ui_checked()
        try:
            value = read_target_soc_from_settings(root)
            return root, value
        except UIError as exc:
            last_error = exc

        time.sleep(0.35)

    raise UIError(
        "Zielladestand-Wert wurde in den Einstellungen "
        f"nicht rechtzeitig sichtbar: {last_error}"
    )


def find_soc_container(root):
    value = find_by_resource_id(root, "value")
    if value is None:
        raise UIError("Zielladestand-Wert nicht gefunden")

    container = parent_of(root, value)
    if container is None:
        raise UIError("Zielladestand-Container nicht gefunden")

    return container

def handle_battery_care_warning():
    root = dump_ui_checked()

    confirm = None

    for item in all_nodes(root):
        if item["desc"] == "Alles klar":
            confirm = item["node"]
            break

    if confirm is None:
        return False

    target = clickable_parent(
        root,
        confirm,
    )

    tap_node(
        target
        if target is not None
        else confirm
    )

    time.sleep(1.0)

    return True


def set_target_soc(vin, target_soc):
    if target_soc not in SOC_POSITIONS:
        raise ValueError(
            "Zielladestand muss 50, 60, 70, 80, 90 oder 100 sein"
        )

    vehicle = select_vehicle_info(
        vin
    )

    open_settings()

    root, current = wait_for_target_soc_setting()

    if current == target_soc:
        return (
            vehicle,
            False,
            current,
        )

    container = find_soc_container(
        root
    )

    bounds = container.attrib.get(
        "bounds",
        "",
    )

    x1, y1, x2, y2 = parse_bounds(bounds)

    position = SOC_POSITIONS[
        target_soc
    ]

    x = (
        x1
        + round(
            (x2 - x1)
            * position
        )
    )

    # Vertikale Sliderposition relativ
    # zum live gefundenen SOC-Container.
    y = (
        y1
        + round(
            (y2 - y1)
            * 0.40
        )
    )

    tap(x, y)

    time.sleep(1.0)

    handle_battery_care_warning()

    root, selected = wait_for_target_soc_setting()

    if selected != target_soc:
        raise RuntimeError(
            "Zielladestand konnte nicht ausgewählt werden: "
            f"Ziel={target_soc}, Ist={selected}"
        )

    save = find_by_resource_id(
        root,
        "vwd_save_button",
    )

    if save is None:
        raise UIError(
            "Speichern-Button nicht gefunden"
        )

    target = clickable_parent(
        root,
        save,
    )

    tap_node(
        target
        if target is not None
        else save
    )

    # Nach dem Speichern etwas Zeit geben.
    time.sleep(2.0)

    # Verifikation:
    # Einstellungen erneut öffnen und Wert prüfen.
    root = open_settings()

    verified = read_target_soc_from_settings(
        root
    )

    if verified != target_soc:
        raise RuntimeError(
            "Zielladestand nach Speichern nicht bestätigt: "
            f"Ziel={target_soc}, Ist={verified}"
        )

    return (
        vehicle,
        True,
        verified,
    )

def emit(
    payload,
    pretty=False,
):
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=(
                2
                if pretty
                else None
            ),
            separators=(
                None
                if pretty
                else (",", ":")
            ),
        )
    )


def main():
    parser = argparse.ArgumentParser(
        description=(
            "VW Ladezustand lesen "
            "und Ladefunktion steuern"
        )
    )

    parser.add_argument(
        "vin",
        help="17-stellige Fahrzeug-VIN",
    )

    parser.add_argument(
        "command",
        choices=(
            "status",
            "start",
            "stop",
            "target",
        ),
    )

    parser.add_argument(
        "value",
        nargs="?",
        help="Zielladestand für 'target'",
    )

    parser.add_argument(
        "--pretty",
        action="store_true",
    )

    args = parser.parse_args()

    vehicle = {
        "vin": args.vin,
        "name": None,
    }

    try:
        # Standalone-Aufrufe müssen unabhängig vom App-Zustand funktionieren.
        # start_app() startet die VW-App bzw. holt sie in den Vordergrund.
        start_app()

        if args.command == "status":
            (
                vehicle,
                status,
            ) = read_status(
                args.vin
            )

            # Standalone-CLI immer sauber auf der Fahrzeugübersicht beenden.
            ensure_vehicle_overview()

            emit(
                {
                    "ok": True,
                    "vehicle": vehicle,
                    **status,
                    "error": None,
                },
                args.pretty,
            )

            return

        if args.command == "target":
            if args.value is None:
                raise ValueError(
                    "Bei 'target' muss ein Zielladestand angegeben werden"
                )

            (
                vehicle,
                changed,
                target_soc,
            ) = set_target_soc(
                args.vin,
                int(args.value),
            )

            # Nach Einstellungen/Zielladestand wieder zur Startseite.
            ensure_vehicle_overview()

            emit(
                {
                    "ok": True,
                    "vehicle": vehicle,
                    "changed": changed,
                    "target_soc": target_soc,
                    "error": None,
                },
                args.pretty,
            )

            return

        (
            vehicle,
            changed,
            status,
        ) = set_charge_state(
            args.vin,
            args.command,
        )

        # Auch Start/Stop nicht im Lade-Dialog stehen lassen.
        ensure_vehicle_overview()

        emit(
            {
                "ok": True,
                "vehicle": vehicle,
                "changed": changed,
                **status,
                "error": None,
            },
            args.pretty,
        )

    except subprocess.CalledProcessError as exc:
        emit(
            {
                "ok": False,
                "vehicle": vehicle,
                "state": "unknown",
                "error": (
                    f"ADB-Fehler: {exc}"
                ),
            },
            args.pretty,
        )

        sys.exit(2)

    except Exception as exc:
        emit(
            {
                "ok": False,
                "vehicle": vehicle,
                "state": "unknown",
                "error": str(exc),
            },
            args.pretty,
        )

        sys.exit(1)


if __name__ == "__main__":
    main()
