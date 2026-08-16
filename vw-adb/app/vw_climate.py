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
    all_nodes,
    parse_bounds,
    swipe,
    clickable_parent,
    dump_ui,
    find_by_resource_id,
    tap_node,
)

from vw_ui import RemoteUnavailableError, dump_ui_checked
from vw_vehicle import (
    ensure_vehicle_overview,
    select_vehicle_info,
)


POLL_INTERVAL = 1.0
DIALOG_TIMEOUT = 10.0
COMMAND_TIMEOUT = 30.0

TEMP_STEP = 0.5
TEMP_SWIPE_DELAY = 0.25
TEMP_VERIFY_RETRIES = 3

CTA_START = "cta_start"
CTA_STOP = "cta_stop"


def get_climate_tile_status(root):
    tile = find_by_resource_id(
        root,
        "climateTile",
    )

    if tile is None:
        return None

    # Bei Compose liegt die eigentliche
    # Accessibility-Beschreibung häufig
    # auf einem Kind-Node.
    descriptions = []

    own_desc = tile.attrib.get(
        "content-desc",
        "",
    ).strip()

    if own_desc:
        descriptions.append(own_desc)

    for node in tile.iter("node"):
        desc = node.attrib.get(
            "content-desc",
            "",
        ).strip()

        if desc:
            descriptions.append(desc)

    for desc in descriptions:
        lower = desc.lower()

        if any(
            phrase in lower
            for phrase in (
                "vorklimatisierung",
                "klimatisierung",
                "air conditioning",
                "climate control",
                "climatisation",
                "pre-conditioning",
                "preconditioning",
            )
        ):
            if (
                ". an." in lower
                or ". on." in lower
            ):
                return "running"

            if (
                ". aus." in lower
                or ". off." in lower
            ):
                return "stopped"

    return None


def read_overview_status():
    root = dump_ui_checked()

    state = get_climate_tile_status(
        root
    )

    if state:
        return state

    return "unknown"


def is_climate_dialog(root):
    if find_by_resource_id(
        root,
        "clima_compose_view",
    ) is not None:
        return True

    if find_by_resource_id(
        root,
        CTA_START,
    ) is not None:
        return True

    if find_by_resource_id(
        root,
        CTA_STOP,
    ) is not None:
        return True

    return False


def open_climate_dialog_once():
    root = dump_ui_checked()

    if is_climate_dialog(root):
        return root

    tile = find_by_resource_id(
        root,
        "climateTile",
    )

    if tile is None:
        raise UIError(
            "climateTile nicht gefunden"
        )

    target = clickable_parent(
        root,
        tile,
    )

    tap_node(
        target
        if target is not None
        else tile
    )

    deadline = (
        time.monotonic()
        + DIALOG_TIMEOUT
    )

    while time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL)

        root = dump_ui_checked()

        if is_climate_dialog(root):
            return root

    raise UIError(
        "Klimaansicht konnte nicht geöffnet werden"
    )


def ensure_climate_dialog():
    try:
        return open_climate_dialog_once()
    except RemoteUnavailableError:
        # VW hat die Funktion ausdrücklich abgelehnt.
        # Nicht nochmals auf die Klimakachel tippen.
        raise
    except UIError:
        # Safe navigation retry only; no climate command has been sent.
        ensure_vehicle_overview()
        return open_climate_dialog_once()

def find_temperature_scroll_area(root):
    compose = find_by_resource_id(
        root,
        "clima_compose_view",
    )

    search_root = (
        compose
        if compose is not None
        else root
    )

    candidates = []

    for node in search_root.iter("node"):
        if (
            node.attrib.get("scrollable")
            != "true"
        ):
            continue

        bounds = node.attrib.get(
            "bounds",
            "",
        )

        try:
            x1, y1, x2, y2 = (
                parse_bounds(bounds)
            )
        except UIError:
            continue

        width = x2 - x1
        height = y2 - y1

        if (
            width > 600
            and 120 < height < 450
        ):
            candidates.append(
                (
                    width,
                    node,
                )
            )

    if not candidates:
        raise UIError(
            "Scrollbarer Temperaturbereich "
            "nicht gefunden"
        )

    candidates.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    return candidates[0][1]


def find_current_temperature(root):
    area = find_temperature_scroll_area(
        root
    )

    ax1, ay1, ax2, ay2 = parse_bounds(
        area.attrib["bounds"]
    )

    area_center_x = (
        ax1 + ax2
    ) / 2

    candidates = []

    for node in area.iter("node"):
        text = node.attrib.get(
            "text",
            "",
        ).strip()

        if not re.fullmatch(
            r"\d{1,2}(?:[.,]\d)?",
            text,
        ):
            continue

        value = float(
            text.replace(",", ".")
        )

        # Plausibilitätsfilter:
        # Fahrzeug-Klimatemperatur,
        # keine sonstigen Zahlen.
        if not 10.0 <= value <= 35.0:
            continue

        bounds = node.attrib.get(
            "bounds",
            "",
        )

        try:
            x1, y1, x2, y2 = (
                parse_bounds(bounds)
            )
        except UIError:
            continue

        node_center_x = (
            x1 + x2
        ) / 2

        distance = abs(
            node_center_x
            - area_center_x
        )

        candidates.append(
            (
                distance,
                value,
            )
        )

    if not candidates:
        raise UIError(
            "Aktuelle Solltemperatur "
            "nicht erkannt"
        )

    candidates.sort(
        key=lambda item: item[0]
    )

    return (
        candidates[0][1],
        area,
    )


def swipe_temperature_step(area, increase):
    x1, y1, x2, y2 = parse_bounds(area.attrib["bounds"])
    width = x2 - x1
    y = (y1 + y2) // 2

    if increase:
        start_x = x1 + int(width * 0.65)
        end_x = x1 + int(width * 0.35)
    else:
        start_x = x1 + int(width * 0.35)
        end_x = x1 + int(width * 0.65)

    swipe(start_x, y, end_x, y, 220)

def normalize_temperature(value):
    # Nur halbe Grad erlauben.
    rounded = round(
        float(value) * 2
    ) / 2

    if abs(
        rounded - float(value)
    ) > 0.001:
        raise ValueError(
            "Temperatur muss in "
            "0,5-°C-Schritten angegeben werden"
        )

    return rounded


def set_temperature(target):
    target = normalize_temperature(
        target
    )

    # VW-Grenzen können je nach Fahrzeug
    # abweichen. Hier nur ein grober
    # Plausibilitätscheck.
    if not 10.0 <= target <= 35.0:
        raise ValueError(
            "Unplausible Zieltemperatur"
        )

    root = ensure_climate_dialog()

    current, area = (
        find_current_temperature(
            root
        )
    )

    if abs(
        current - target
    ) < 0.01:
        return {
            "changed": False,
            "temperature": current,
        }

    delta = target - current

    steps = round(
        abs(delta) / TEMP_STEP
    )

    increase = delta > 0

    # Schneller Block:
    # keine UI-Dumps zwischen jedem Schritt.
    for _ in range(steps):
        swipe_temperature_step(
            area,
            increase,
        )

        time.sleep(
            TEMP_SWIPE_DELAY
        )

    # Danach verifizieren.
    for _ in range(
        TEMP_VERIFY_RETRIES
    ):
        time.sleep(0.6)

        root = dump_ui_checked()

        actual, area = (
            find_current_temperature(
                root
            )
        )

        if abs(
            actual - target
        ) < 0.01:
            return {
                "changed": True,
                "temperature": actual,
            }

        # Falls einzelne Swipe-Events
        # nicht angekommen sind:
        correction = (
            target - actual
        )

        correction_steps = round(
            abs(correction)
            / TEMP_STEP
        )

        if correction_steps == 0:
            break

        correction_increase = (
            correction > 0
        )

        # Verifikation/Korrektur bewusst
        # begrenzen.
        correction_steps = min(
            correction_steps,
            6,
        )

        for _ in range(
            correction_steps
        ):
            swipe_temperature_step(
                area,
                correction_increase,
            )

            time.sleep(
                TEMP_SWIPE_DELAY
            )

    root = dump_ui_checked()

    actual, _ = (
        find_current_temperature(
            root
        )
    )

    raise RuntimeError(
        "Zieltemperatur konnte nicht "
        f"gesetzt werden: Ziel={target:.1f}, "
        f"Ist={actual:.1f}"
    )


def get_cta(root):
    stop = find_by_resource_id(
        root,
        CTA_STOP,
    )

    if stop is not None:
        return (
            stop,
            "running",
        )

    start = find_by_resource_id(
        root,
        CTA_START,
    )

    if start is not None:
        return (
            start,
            "stopped",
        )

    return (
        None,
        "unknown",
    )


def wait_for_overview_state(
    target_state,
):
    deadline = (
        time.monotonic()
        + COMMAND_TIMEOUT
    )

    last_state = "unknown"

    while time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL)

        root = dump_ui_checked()

        # Dialog kann nach Start/Stop
        # weiterhin offen sein.
        _, dialog_state = get_cta(
            root
        )

        if dialog_state == target_state:
            return target_state

        overview_state = (
            get_climate_tile_status(
                root
            )
        )

        if overview_state:
            last_state = (
                overview_state
            )

            if (
                overview_state
                == target_state
            ):
                return target_state

    raise RuntimeError(
        "Timeout beim Warten auf "
        f"Klima-Zustand {target_state!r}; "
        f"letzter Status: {last_state}"
    )


def set_climate_state(target):
    root = ensure_climate_dialog()

    button, current_state = (
        get_cta(root)
    )

    if target == "start":
        if current_state == "running":
            return {
                "changed": False,
                "state": "running",
            }

        if current_state != "stopped":
            raise UIError(
                "Klima-Startbutton "
                "nicht erkannt"
            )

        target_state = "running"

    elif target == "stop":
        if current_state == "stopped":
            return {
                "changed": False,
                "state": "stopped",
            }

        if current_state != "running":
            raise UIError(
                "Klima-Stoppbutton "
                "nicht erkannt"
            )

        target_state = "stopped"

    else:
        raise ValueError(target)

    target_node = clickable_parent(
        root,
        button,
    )

    # Ab hier kein automatischer
    # zweiter Fahrzeugbefehl.
    tap_node(
        target_node
        if target_node is not None
        else button
    )

    state = wait_for_overview_state(
        target_state
    )

    return {
        "changed": True,
        "state": state,
    }


def read_status():
    root = dump_ui_checked()

    state = get_climate_tile_status(
        root
    )

    temperature = None

    if state is None:
        # Vielleicht ist der Dialog gerade
        # schon offen.
        _, state = get_cta(root)

    if state in (
        None,
        "unknown",
    ):
        state = "unknown"

    # Temperatur ist nur im Dialog sichtbar.
    # Für einen vollständigen Status öffnen
    # wir ihn bei Bedarf.
    try:
        root = ensure_climate_dialog()

        temperature, _ = (
            find_current_temperature(
                root
            )
        )

        _, dialog_state = get_cta(
            root
        )

        if (
            dialog_state
            != "unknown"
        ):
            state = dialog_state

    except UIError:
        # Status der Übersicht bleibt
        # trotzdem verwertbar.
        pass

    return {
        "state": state,
        "target_temperature": (
            temperature
        ),
    }


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
            "VW Klimatisierung lesen "
            "und steuern"
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
            "temperature",
        ),
    )

    parser.add_argument(
        "value",
        nargs="?",
        help=(
            "Zieltemperatur für "
            "'temperature'"
        ),
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

        vehicle = (
            select_vehicle_info(
                args.vin
            )
        )

        if args.command == "status":
            status = read_status()

            # Standalone-Aufrufe enden wieder auf der Fahrzeugübersicht.
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

        if args.command in (
            "start",
            "stop",
        ):
            result = (
                set_climate_state(
                    args.command
                )
            )

            # Nach Start/Stop den Klima-Dialog verlassen.
            ensure_vehicle_overview()

            emit(
                {
                    "ok": True,
                    "vehicle": vehicle,
                    **result,
                    "error": None,
                },
                args.pretty,
            )

            return

        if args.value is None:
            raise ValueError(
                "Bei 'temperature' "
                "muss ein Wert angegeben werden"
            )

        result = set_temperature(
            float(
                args.value.replace(
                    ",",
                    ".",
                )
            )
        )

        # Nach dem Verstellen wieder auf die Fahrzeugübersicht zurück.
        ensure_vehicle_overview()

        emit(
            {
                "ok": True,
                "vehicle": vehicle,
                "changed": (
                    result["changed"]
                ),
                "target_temperature": (
                    result["temperature"]
                ),
                "error": None,
            },
            args.pretty,
        )

    except subprocess.CalledProcessError as exc:
        emit(
            {
                "ok": False,
                "vehicle": vehicle,
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
                "error": str(exc),
            },
            args.pretty,
        )

        sys.exit(1)


if __name__ == "__main__":
    main()
