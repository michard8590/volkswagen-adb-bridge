#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later

import argparse
import json
import re
import sys
import time

from vw_ui import (
    UIError,
    all_nodes,
    clickable_parent,
    dump_ui,
    parse_bounds,
    start_app,
    swipe,
    tap_node,
)
from vw_vehicle import ensure_vehicle_overview, select_vehicle_info


WAIT_SHORT = 0.4
OPEN_TIMEOUT = 10.0
REPORT_TEXTS = (
    "fahrzeugzustandsbericht",
    "vehicle status report",
    "vehicle health report",
)

ODOMETER_UNAVAILABLE_TEXTS = (
    "momentan keine daten verfügbar",
    "currently no data available",
    "no data available",
)


def _find_report_tile(root):
    for item in all_nodes(root):
        combined = f"{item['text']} {item['desc']}".strip().lower()
        if any(text in combined for text in REPORT_TEXTS):
            return item["node"]
    return None


def odometer_unavailable(root):
    for item in all_nodes(root):
        combined = (
            f"{item['text']} {item['desc']}"
            .strip()
            .lower()
        )

        if any(
            text in combined
            for text in ODOMETER_UNAVAILABLE_TEXTS
        ):
            return True

    return False


def _largest_scrollable(root):
    best = None
    best_area = -1

    for node in root.iter("node"):
        if node.attrib.get("scrollable") != "true":
            continue
        bounds = parse_bounds(node.attrib.get("bounds", ""))
        if not bounds:
            continue
        x1, y1, x2, y2 = bounds
        area = max(0, x2 - x1) * max(0, y2 - y1)
        if area > best_area:
            best = bounds
            best_area = area

    return best


def _scroll_down_once(root):
    bounds = _largest_scrollable(root)
    if bounds is None:
        return False

    x1, y1, x2, y2 = bounds
    x = (x1 + x2) // 2
    height = y2 - y1
    start_y = y1 + int(height * 0.75)
    end_y = y1 + int(height * 0.30)
    swipe(x, start_y, x, end_y, 350)
    time.sleep(WAIT_SHORT)
    return True


def open_vehicle_status_report(max_scrolls=8):
    root = ensure_vehicle_overview()

    for _ in range(max_scrolls + 1):
        node = _find_report_tile(root)
        if node is not None:
            target = clickable_parent(root, node)
            tap_node(target if target is not None else node)

            deadline = time.monotonic() + OPEN_TIMEOUT
            while time.monotonic() < deadline:
                time.sleep(WAIT_SHORT)
                root = dump_ui()

                if parse_odometer_km(root) is not None:
                    return root

                if odometer_unavailable(root):
                    return root

            raise UIError(
                "Fahrzeugzustandsbericht geöffnet, "
                "Kilometerstand aber nicht gefunden"
            )

        if not _scroll_down_once(root):
            break
        root = dump_ui()

    raise UIError("Fahrzeugzustandsbericht nicht gefunden")


def parse_odometer_value(text):
    if not text:
        return None

    # Examples: 49'216 km, 49.216 km, 49 216 km, 49216 km
    match = re.fullmatch(
        r"\s*([0-9][0-9'’ .\u00a0]*)\s*km\s*",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None

    digits = re.sub(r"\D", "", match.group(1))
    if not digits:
        return None

    value = int(digits)
    if value < 0:
        return None
    return value


def parse_odometer_km(root):
    candidates = []

    for item in all_nodes(root):
        for value in (item["text"], item["desc"]):
            km = parse_odometer_value(value.strip())
            if km is not None:
                candidates.append(km)

    if not candidates:
        return None

    # A vehicle status report can contain other km values. The odometer is
    # normally the largest absolute kilometre value on this screen.
    return max(candidates)


def read_odometer_km():
    root = open_vehicle_status_report()
    value = parse_odometer_km(root)

    if value is not None:
        return value

    # Ein explizites "keine Daten verfügbar" ist ein gültiger Zustand
    # der VW-App und kein Fehler des Pollers.
    if odometer_unavailable(root):
        return None

    raise UIError("Kilometerstand nicht gefunden")


def main():
    parser = argparse.ArgumentParser(description="VW Kilometerstand lesen")
    parser.add_argument("vin", help="VIN des Fahrzeugs")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    vehicle = {"vin": args.vin, "name": None}

    try:
        start_app()
        vehicle = select_vehicle_info(args.vin)
        odometer_km = read_odometer_km()
        data = {
            "ok": True,
            "vehicle": vehicle,
            "odometer_km": odometer_km,
            "error": None,
        }
    except Exception as exc:
        data = {
            "ok": False,
            "vehicle": vehicle,
            "odometer_km": None,
            "error": str(exc),
        }
    finally:
        try:
            ensure_vehicle_overview()
        except Exception:
            pass

    print(json.dumps(data, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if data["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
