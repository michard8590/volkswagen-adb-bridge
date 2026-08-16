#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later

import argparse
import json
import re
import sys
import time

from job_queue import check_cancel

from vw_ui import (
    UIError,
    all_nodes,
    clickable_parent,
    dump_ui,
    find_by_resource_id,
    parse_bounds,
    press_back,
    start_app,
    tap,
    tap_node,
)
from vw_vehicle import (
    ensure_vehicle_overview,
    select_vehicle_info,
)


# Nach "Find vehicle" liegt der Fahrzeugmarker bei der getesteten VW-App
# relativ zur Kartenflaeche an dieser Position. Bewusst keine absoluten
# Display-Koordinaten. Die Werte koennen spaeter in der Add-on-Konfiguration
# pro Geraet kalibriert werden.
DEFAULT_MARKER_X = 0.533
DEFAULT_MARKER_Y = 0.454

WAIT_SHORT = 0.4
WAIT_SCREEN = 12.0
WAIT_SHARE = 8.0

NAV_TAB_ID = "cat_nav_map_tab_navigation"
VEHICLE_TAB_ID = "vehicle_tab_navigation"
MAP_ID = "catNavMapFragment"
SHARE_PREVIEW_ID = "android:id/content_preview_text"

LOCATION_RE = re.compile(
    r"(?:https?://(?:www\.)?google\.com/maps/place/)?"
    r"(-?\d{1,2}(?:\.\d+)?),\s*"
    r"(-?\d{1,3}(?:\.\d+)?)"
)


def emit(data, pretty=False):
    print(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2 if pretty else None,
        )
    )


def wait_for_resource_id(resource_id, timeout=WAIT_SCREEN):
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        root = dump_ui()
        node = find_by_resource_id(root, resource_id)
        if node is not None:
            return root, node
        time.sleep(WAIT_SHORT)

    return None, None


def open_navigation_tab():
    root = ensure_vehicle_overview()
    if root is None:
        root = dump_ui()

    node = find_by_resource_id(root, NAV_TAB_ID)
    if node is None:
        raise UIError("Navigation-Tab nicht gefunden")

    target = clickable_parent(root, node)
    tap_node(target if target is not None else node)

    root, map_node = wait_for_resource_id(MAP_ID)
    if map_node is None:
        raise UIError("Kartenansicht wurde nicht geoeffnet")

    return root


def find_vehicle_button(root):
    for item in all_nodes(root):
        desc = item["desc"].strip().lower()
        if desc in ("find vehicle", "fahrzeug finden"):
            target = clickable_parent(root, item["node"])
            return target if target is not None else item["node"]

    return None


def center_on_vehicle():
    deadline = time.monotonic() + WAIT_SCREEN

    while time.monotonic() < deadline:
        root = dump_ui()
        node = find_vehicle_button(root)
        if node is not None:
            tap_node(node)
            time.sleep(0.8)
            return
        time.sleep(WAIT_SHORT)

    raise UIError("'Find vehicle' wurde nicht gefunden")


LOCATION_UNAVAILABLE_MARKERS = (
    "wir können den standort ihres fahrzeugs nur anzeigen",
    "wir koennen den standort ihres fahrzeugs nur anzeigen",
    "motor ausgeschaltet",
    "gps verfügbar",
    "gps verfuegbar",
    "we can only show your vehicle",
    "engine is switched off",
    "gps is available",
)


def location_unavailable(root):
    values = []

    for item in all_nodes(root):
        if item["text"]:
            values.append(item["text"])
        if item["desc"]:
            values.append(item["desc"])

    combined = " ".join(values).lower()

    german = (
        (
            "standort ihres fahrzeugs" in combined
            or "standort des fahrzeugs" in combined
        )
        and "motor" in combined
        and "gps" in combined
    )

    english = (
        "vehicle" in combined
        and "location" in combined
        and "engine" in combined
        and "gps" in combined
    )

    return german or english


def dismiss_location_unavailable(root):
    if not location_unavailable(root):
        return False

    for item in all_nodes(root):
        text = item["text"].strip().lower()
        desc = item["desc"].strip().lower()

        if text in ("ok", "okay") or desc in ("ok", "okay"):
            target = clickable_parent(
                root,
                item["node"],
            )

            tap_node(
                target
                if target is not None
                else item["node"]
            )

            time.sleep(WAIT_SHORT)
            break

    return True


def vehicle_details_open(root):
    has_share = False
    has_route = False

    for item in all_nodes(root):
        text = item["text"].strip().lower()
        if text in ("teilen", "share"):
            has_share = True
        elif text in ("route", "route planen"):
            has_route = True

    return has_share or has_route


def open_vehicle_details(marker_x, marker_y):
    if not 0.0 <= marker_x <= 1.0 or not 0.0 <= marker_y <= 1.0:
        raise ValueError("Marker-Position muss zwischen 0.0 und 1.0 liegen")

    root, map_node = wait_for_resource_id(MAP_ID)
    if map_node is None:
        raise UIError("Karten-Container nicht gefunden")

    bounds = parse_bounds(map_node.attrib.get("bounds", ""))
    if not bounds:
        raise UIError("Ungueltige Karten-Bounds")

    x1, y1, x2, y2 = bounds
    x = x1 + round((x2 - x1) * marker_x)
    y = y1 + round((y2 - y1) * marker_y)

    tap(x, y)

    deadline = time.monotonic() + WAIT_SCREEN
    while time.monotonic() < deadline:
        time.sleep(WAIT_SHORT)
        root = dump_ui()

        if dismiss_location_unavailable(root):
            raise UIError(
                "Fahrzeugstandort aktuell nicht verfügbar: "
                "Motor muss ausgeschaltet und GPS verfügbar sein. "
                "Letzter bekannter Standort bleibt erhalten."
            )

        if vehicle_details_open(root):
            return root

    raise UIError(
        "Fahrzeugmarker wurde nicht geoeffnet. "
        "Marker-Kalibrierung pruefen."
    )


def find_share_button(root):
    for item in all_nodes(root):
        if item["text"].strip().lower() in ("teilen", "share"):
            target = clickable_parent(root, item["node"])
            return target if target is not None else item["node"]

    return None


def open_share_sheet(root):
    node = find_share_button(root)
    if node is None:
        raise UIError("Teilen-Button nicht gefunden")

    tap_node(node)

    deadline = time.monotonic() + WAIT_SHARE
    while time.monotonic() < deadline:
        time.sleep(WAIT_SHORT)
        root = dump_ui()
        node = find_by_resource_id(root, SHARE_PREVIEW_ID)
        if node is not None:
            return root, node

    raise UIError("Android-Share-Sheet mit Standort wurde nicht geoeffnet")


def parse_location(text):
    match = LOCATION_RE.search(text or "")
    if not match:
        raise UIError(f"Koordinaten konnten nicht gelesen werden: {text!r}")

    latitude = float(match.group(1))
    longitude = float(match.group(2))

    if not -90.0 <= latitude <= 90.0:
        raise UIError(f"Ungueltiger Breitengrad: {latitude}")
    if not -180.0 <= longitude <= 180.0:
        raise UIError(f"Ungueltiger Laengengrad: {longitude}")

    return latitude, longitude


def close_share_sheet():
    # Android Sharesheet neutral schliessen; kein Ziel auswaehlen.
    press_back()
    time.sleep(WAIT_SHORT)


CLOSE_DETAILS_DESCRIPTIONS = (
    "close details view",
    "detailansicht schließen",
    "detailansicht schliessen",
    "details schließen",
    "details schliessen",
    "ansicht schließen",
    "ansicht schliessen",
)


def return_to_vehicle_overview():
    # Falls noch ein Map-Detail offen ist, zuerst normal schliessen.
    # Exakte bekannte Accessibility-Texte verwenden, damit nicht
    # versehentlich irgendein anderer "Schließen"-Button getroffen wird.
    root = dump_ui()
    for item in all_nodes(root):
        desc = item["desc"].strip().lower()
        if desc in CLOSE_DETAILS_DESCRIPTIONS:
            target = clickable_parent(root, item["node"])
            tap_node(target if target is not None else item["node"])
            time.sleep(WAIT_SHORT)
            break

    root = dump_ui()
    node = find_by_resource_id(root, VEHICLE_TAB_ID)
    if node is not None:
        target = clickable_parent(root, node)
        tap_node(target if target is not None else node)
        time.sleep(WAIT_SHORT)

    ensure_vehicle_overview()


def read_location(
    vin,
    marker_x=DEFAULT_MARKER_X,
    marker_y=DEFAULT_MARKER_Y,
    cancel_event=None,
):
    check_cancel(cancel_event)

    start_app()

    check_cancel(cancel_event)
    vehicle = select_vehicle_info(vin)

    share_open = False
    try:
        check_cancel(cancel_event)
        open_navigation_tab()

        check_cancel(cancel_event)
        center_on_vehicle()

        check_cancel(cancel_event)
        root = open_vehicle_details(marker_x, marker_y)

        check_cancel(cancel_event)
        _, preview = open_share_sheet(root)
        share_open = True

        check_cancel(cancel_event)

        text = preview.attrib.get("text", "").strip()
        latitude, longitude = parse_location(text)

        return {
            "ok": True,
            "vehicle": vehicle,
            "location": {
                "latitude": latitude,
                "longitude": longitude,
            },
            "source": "volkswagen_app_share",
            "error": None,
        }
    finally:
        try:
            if share_open:
                close_share_sheet()
            return_to_vehicle_overview()
        except Exception:
            # Der eigentliche Standortwert/Fehler soll nicht durch Cleanup
            # ueberschrieben werden. Der naechste Aufruf kann die App ueber
            # start_app()/Fahrzeugauswahl wieder in einen definierten Zustand
            # bringen.
            pass


def main():
    parser = argparse.ArgumentParser(
        description="Fahrzeugstandort aus der Volkswagen App lesen"
    )
    parser.add_argument("vin", help="VIN des Fahrzeugs")
    parser.add_argument(
        "--marker-x",
        type=float,
        default=DEFAULT_MARKER_X,
        help="Relative X-Position des Markers nach 'Find vehicle' (Default: 0.533)",
    )
    parser.add_argument(
        "--marker-y",
        type=float,
        default=DEFAULT_MARKER_Y,
        help="Relative Y-Position des Markers nach 'Find vehicle' (Default: 0.454)",
    )
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    try:
        data = read_location(
            args.vin,
            marker_x=args.marker_x,
            marker_y=args.marker_y,
        )
    except KeyboardInterrupt:
        data = {
            "ok": False,
            "vehicle": {"vin": args.vin, "name": None},
            "location": None,
            "error": "Abgebrochen",
        }
    except Exception as exc:
        data = {
            "ok": False,
            "vehicle": {"vin": args.vin, "name": None},
            "location": None,
            "error": str(exc),
        }

    emit(data, args.pretty)
    return 0 if data.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
