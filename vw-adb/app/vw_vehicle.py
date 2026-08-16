#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later

import argparse
import json
import os
import re
import time
from pathlib import Path

from job_queue import BackgroundCancelled, check_cancel

from vw_ui import (
    UIError,
    all_nodes,
    clickable_parent,
    dump_ui,
    detect_remote_unavailable,
    find_by_description,
    find_by_resource_id,
    parse_bounds,
    press_back,
    swipe,
    tap_node,
)


POLL_INTERVAL = 1.0
SWITCH_TIMEOUT = 15.0
SETTINGS_TIMEOUT = 10.0
SYNC_POLL_INTERVAL = 0.5

SYNC_CTA_ID = "subtitle_cta"

CACHE_FILE = Path(
    os.getenv(
        "VW_VEHICLE_CACHE",
        "/data/vehicles.json",
    )
)

VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")


def subtree_texts(node):
    values = []

    for child in node.iter("node"):
        text = child.attrib.get("text", "").strip()

        if text:
            values.append(text)

    return values


def vehicle_cards(root):
    cards = []
    seen = set()

    for node in root.iter("node"):
        text = node.attrib.get("text", "").strip()

        if not VIN_RE.fullmatch(text):
            continue

        card = clickable_parent(root, node)

        if card is None:
            continue

        key = id(card)

        if key in seen:
            continue

        seen.add(key)

        values = subtree_texts(card)

        vin = next(
            (
                value
                for value in values
                if VIN_RE.fullmatch(value)
            ),
            None,
        )

        if vin is None:
            continue

        vin_index = values.index(vin)

        candidates = [
            value
            for value in values[:vin_index]
            if not VIN_RE.fullmatch(value)
        ]

        if not candidates:
            continue

        cards.append(
            {
                "name": candidates[0],
                "vin": vin,
                "node": card,
            }
        )

    return cards


def save_cache(vehicles):
    CACHE_FILE.write_text(
        json.dumps(
            vehicles,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def load_cache():
    if not CACHE_FILE.exists():
        return []

    try:
        data = json.loads(
            CACHE_FILE.read_text(
                encoding="utf-8"
            )
        )

        if isinstance(data, list):
            return data

    except Exception:
        pass

    return []


def current_from_root(root, vehicles=None):
    if vehicles is None:
        vehicles = load_cache()

    descriptions = [
        item["desc"]
        for item in all_nodes(root)
        if item["desc"]
    ]

    # Bevorzugt: bekannte Fahrzeugnamen.
    # Längere Namen zuerst.
    for vehicle in sorted(
        vehicles,
        key=lambda x: len(x["name"]),
        reverse=True,
    ):
        name = vehicle["name"]

        for desc in descriptions:
            if (
                desc.startswith("Ihr Fahrzeug:")
                and name in desc
            ):
                return vehicle

    return None


def is_vehicle_overview_top(root):
    return (
        find_by_resource_id(root, "rangeTile") is not None
        or find_by_resource_id(root, "climateTile") is not None
        or _header_description(root) is not None
    )


def is_vehicle_overview(root):
    if is_vehicle_overview_top(root):
        return True

    # Auch eine nach unten gescrollte Übersicht erkennen. Die Einstellungen-
    # Kachel hat eine Accessibility-Beschreibung; der separate Settings-Screen
    # dagegen den Titel vwd_title und wird dadurch nicht verwechselt.
    if is_settings_screen(root):
        return False

    for item in all_nodes(root):
        desc = item["desc"]
        if desc.startswith("Einstellungen."):
            return True

    return False


def _scroll_up_once(root, duration_ms=350):
    _, bounds = _largest_scrollable(root)
    if bounds is None:
        return False

    x1, y1, x2, y2 = bounds
    x = (x1 + x2) // 2
    height = y2 - y1
    start_y = y1 + int(height * 0.30)
    end_y = y1 + int(height * 0.75)

    swipe(x, start_y, x, end_y, duration_ms)
    time.sleep(0.35)
    return True


def ensure_vehicle_overview(max_back=4):
    """
    Reach the vehicle overview at the top.

    Wenn der Benutzer die VW-App während einer read-only Navigation
    beendet oder verlässt, niemals BACK/Swipe in einer fremden App
    ausführen. Stattdessen die VW-App kontrolliert wieder öffnen.
    """
    for attempt in range(max_back + 1):
        root = dump_ui()

        from vw_ui import root_has_vw_app, start_app

        if not root_has_vw_app(root):
            root = start_app()

        if is_vehicle_overview_top(root):
            return root

        if is_vehicle_overview(root):
            # We are already on the overview, only scrolled down. Scroll to
            # the header instead of sending BACK and leaving the overview.
            for _ in range(8):
                if not _scroll_up_once(root):
                    break
                root = dump_ui()
                if is_vehicle_overview_top(root):
                    return root

            raise UIError("Fahrzeugübersicht erkannt, oberer Bereich aber nicht erreichbar")

        if attempt >= max_back:
            break

        press_back()
        time.sleep(0.35)

    raise UIError("Fahrzeugübersicht konnte nicht erreicht werden")


def _header_description(root):
    for item in all_nodes(root):
        desc = item["desc"]
        if desc.startswith("Ihr Fahrzeug:"):
            return desc
    return None


def parse_sync_age_seconds(description):
    """
    Parse the synchronization age from the vehicle overview header.

    Returns an age in seconds, or None when the current wording is not
    understood. Unknown formats are deliberately not treated as stale.
    """
    if not description:
        return None

    text = description.strip().lower()

    # VW uses this wording directly after a successful manual sync.
    if any(phrase in text for phrase in (
        "gerade synchronisiert",
        "gerade eben",
        "soeben",
        "weniger als einer minute",
        "just synchronized",
        "just synced",
    )):
        return 0

    if "synchronisiert" not in text and "synchronized" not in text and "synced" not in text:
        return None

    patterns = (
        (
            r"synchronisiert vor\s+(\d+)\s+"
            r"sek(?:unde[n]?)?\.?",
            1,
        ),
        (
            r"synchronisiert vor\s+(?:eine|einer|1)\s+"
            r"min(?:ute[n]?)?\.?",
            60,
        ),
        (
            r"synchronisiert vor\s+(\d+)\s+"
            r"min(?:ute[n]?)?\.?",
            60,
        ),
        (
            r"synchronisiert vor\s+(?:eine|einer|1)\s+"
            r"(?:std\.?|stunde[n]?)",
            3600,
        ),
        (
            r"synchronisiert vor\s+(\d+)\s+"
            r"(?:std\.?|stunde[n]?)",
            3600,
        ),
        (
            r"synchronisiert vor\s+(?:ein|einem|1)\s+"
            r"tag(?:en)?",
            86400,
        ),
        (
            r"synchronisiert vor\s+(\d+)\s+"
            r"tag(?:en)?",
            86400,
        ),
    )

    for pattern, multiplier in patterns:
        match = re.search(pattern, text)
        if match:
            value = 1 if not match.groups() else int(match.group(1))
            return value * multiplier

    if "gestern" in text:
        return 86400

    return None


def parse_lock_state_from_header(description):
    """Read lock state from the overview header, independent of command support."""
    if not description:
        return None

    text = description.strip().lower()

    unlocked_phrases = (
        "fahrzeug ist entriegelt",
        "vehicle is unlocked",
    )
    locked_phrases = (
        "fahrzeug ist verriegelt",
        "vehicle is locked",
    )

    if any(phrase in text for phrase in unlocked_phrases):
        return "unlocked"

    if any(phrase in text for phrase in locked_phrases):
        return "locked"

    return None


def read_overview_lock_state(root, header_description=None):
    """Read lock state from header or overview feature/status nodes.

    This is independent of whether remote Lock/Unlock control is supported.
    """
    state = parse_lock_state_from_header(header_description)
    if state is not None:
        return state

    for item in all_nodes(root):
        text = item["text"].strip().lower()
        desc = item["desc"].strip().lower()

        if desc:
            if "entriegelt" in desc or "unlocked" in desc:
                return "unlocked"
            if "verriegelt" in desc or "locked" in desc:
                return "locked"

        if text in ("entriegelt", "unlocked"):
            return "unlocked"
        if text in ("verriegelt", "locked"):
            return "locked"

    return None


def read_overview_header_info(root=None):
    if root is None:
        root = ensure_vehicle_overview()

    description = _header_description(root)

    return {
        "description": description,
        "sync_age_seconds": parse_sync_age_seconds(description),
        "lock_state": read_overview_lock_state(root, description),
    }


def read_sync_info(root=None):
    info = read_overview_header_info(root)

    return {
        "description": info["description"],
        "age_seconds": info["sync_age_seconds"],
    }


def is_settings_screen(root):
    title = find_by_resource_id(root, "vwd_title")
    return bool(
        title is not None
        and title.attrib.get("text", "").strip() == "Einstellungen"
    )


def _largest_scrollable(root):
    candidates = []

    for item in all_nodes(root):
        node = item["node"]
        if node.attrib.get("scrollable") != "true":
            continue

        try:
            bounds = parse_bounds(node.attrib.get("bounds", ""))
        except UIError:
            continue

        x1, y1, x2, y2 = bounds
        candidates.append(((x2 - x1) * (y2 - y1), node, bounds))

    if not candidates:
        return None, None

    _, node, bounds = max(candidates, key=lambda item: item[0])
    return node, bounds


def _scroll_down_once(root, duration_ms=350):
    _, bounds = _largest_scrollable(root)
    if bounds is None:
        return False

    x1, y1, x2, y2 = bounds
    x = (x1 + x2) // 2
    height = y2 - y1
    start_y = y1 + int(height * 0.75)
    end_y = y1 + int(height * 0.30)

    swipe(x, start_y, x, end_y, duration_ms)
    time.sleep(0.35)
    return True


def open_settings(max_scrolls=6):
    root = dump_ui()
    if is_settings_screen(root):
        return root

    root = ensure_vehicle_overview()

    for _ in range(max_scrolls + 1):
        settings = None

        for item in all_nodes(root):
            desc = item["desc"]
            text = item["text"]
            if desc.startswith("Einstellungen.") or text == "Einstellungen":
                settings = item["node"]
                break

        if settings is not None:
            target = clickable_parent(root, settings) or settings
            tap_node(target)

            deadline = time.monotonic() + SETTINGS_TIMEOUT
            while time.monotonic() < deadline:
                time.sleep(0.35)
                root = dump_ui()
                if is_settings_screen(root):
                    return root

            raise UIError("Einstellungen konnten nicht geöffnet werden")

        if not _scroll_down_once(root):
            break

        root = dump_ui()

    raise UIError("Einstellungen-Kachel nicht gefunden")


def find_sync_cta(root):
    node = find_by_resource_id(root, SYNC_CTA_ID)
    if node is None:
        return None

    if node.attrib.get("text", "").strip() != "Jetzt synchronisieren":
        return None

    return node


def open_settings_to_sync(max_scrolls=8):
    root = open_settings()

    for _ in range(max_scrolls + 1):
        node = find_sync_cta(root)
        if node is not None:
            return root, node

        if not _scroll_down_once(root):
            break

        root = dump_ui()

    raise UIError("'Jetzt synchronisieren' nicht gefunden")


def dismiss_sync_blocked_dialog(root):
    """
    Kompatibilitäts-Wrapper für den zentralen VW-Remote-Blocker.
    """
    return detect_remote_unavailable(
        root,
        dismiss=True,
    )

def wait_for_sync_confirmation(
    previous_age_seconds,
    timeout=60.0,
    cancel_event=None,
):
    """
    Wait for a fresher header timestamp after one manual sync request.

    This function never triggers another sync request.

    If a higher-priority user command arrives, only the waiting is
    cancelled. The already submitted VW synchronization continues
    independently and must never be submitted a second time here.
    """
    deadline = time.monotonic() + timeout
    last_info = None

    while time.monotonic() < deadline:
        # Safe cancellation point: the sync request has already been
        # sent, so cancelling here only stops our local waiting.
        if cancel_event is not None and cancel_event.is_set():
            return None, last_info

        try:
            root = ensure_vehicle_overview(max_back=2)
            info = read_sync_info(root)
            last_info = info
            age = info["age_seconds"]

            if age is not None:
                if age <= 120:
                    return True, info

                if (
                    previous_age_seconds is not None
                    and age < previous_age_seconds
                ):
                    return True, info

        except UIError:
            pass

        # Avoid one long sleep so commands become visible quickly.
        sleep_until = time.monotonic() + SYNC_POLL_INTERVAL
        while time.monotonic() < sleep_until:
            if cancel_event is not None and cancel_event.is_set():
                return None, last_info
            time.sleep(0.1)

    return False, last_info


def sync_if_stale(
    max_age_seconds,
    wait_timeout=60.0,
    cancel_event=None,
):
    """
    Request one manual VW sync when the overview timestamp is too old.

    Unknown timestamp formats are not treated as stale.

    Cancellation rules:
    - Before sending the sync request: abort immediately.
    - After sending it: never re-send; only stop waiting and report
      the request as pending.
    """
    check_cancel(cancel_event)

    root = ensure_vehicle_overview()

    check_cancel(cancel_event)

    before = read_sync_info(root)
    age = before["age_seconds"]

    stale = None if age is None else age > max_age_seconds

    result = {
        "last_sync_age_seconds": age,
        "last_sync_description": before["description"],
        "sync_stale": stale,
        "sync_requested": False,
        "sync_confirmed": None,
        "sync_pending": False,
        "sync_error": None,
    }

    if max_age_seconds is None or max_age_seconds <= 0:
        result["sync_stale"] = None
        return result

    if age is None or age <= max_age_seconds:
        return result

    check_cancel(cancel_event)

    root, node = open_settings_to_sync()

    # Last cancellation point before the remote request is submitted.
    check_cancel(cancel_event)

    # Manual sync is deliberately clicked exactly once per invocation.
    tap_node(node)
    result["sync_requested"] = True

    # From this point on we NEVER raise BackgroundCancelled for this
    # synchronization. A command may only stop our local confirmation
    # wait. The remote request has already been submitted.
    #
    # VW kann unmittelbar nach dem Klick einen Hinweis anzeigen, dass
    # Remote-Bedienung wegen einer schwachen 12-V-Batterie nicht möglich
    # ist. In diesem Fall nicht sinnlos bis zum Sync-Timeout warten.
    blocker = None
    blocker_deadline = time.monotonic() + 2.0

    while time.monotonic() < blocker_deadline:
        time.sleep(0.25)

        try:
            blocker_root = dump_ui()
        except UIError:
            continue

        blocker = dismiss_sync_blocked_dialog(
            blocker_root
        )

        if blocker is not None:
            break

        # Wenn der Dialog gar nicht erschienen ist, nicht die vollen
        # zwei Sekunden warten, sobald wieder normale VW-Navigation
        # sichtbar ist.
        if (
            is_settings_screen(blocker_root)
            or is_vehicle_overview(blocker_root)
        ):
            break

    if blocker is not None:
        result["sync_confirmed"] = False
        result["sync_pending"] = False
        result["sync_error"] = blocker

        try:
            ensure_vehicle_overview(max_back=2)
        except UIError:
            pass

        return result

    press_back()
    time.sleep(0.35)

    confirmed, after = wait_for_sync_confirmation(
        age,
        timeout=wait_timeout,
        cancel_event=cancel_event,
    )

    result["sync_confirmed"] = confirmed

    if confirmed is None:
        result["sync_pending"] = True

    if after:
        result["last_sync_age_seconds"] = after["age_seconds"]
        result["last_sync_description"] = after["description"]

        after_age = after["age_seconds"]
        if after_age is not None:
            result["sync_stale"] = (
                after_age > max_age_seconds
            )

    return result


def open_vehicle_list():
    # Wir versuchen zuerst, die Fahrzeugliste
    # ohne App-Neustart zu erreichen.
    #
    # Falls gerade z. B. der Lade-Dialog offen
    # ist, gehen wir kontrolliert per BACK zur
    # Fahrzeugübersicht zurück.
    for navigation_attempt in range(3):
        root = dump_ui()

        # Schon in "Meine Fahrzeuge".
        for item in all_nodes(root):
            if item["desc"] == "Meine Fahrzeuge":
                return root

        node = find_by_resource_id(
            root,
            "vehicleCarPlus",
        )

        if node is not None:
            target = clickable_parent(
                root,
                node,
            )

            tap_node(
                target
                if target is not None
                else node
            )

            break

        node, _ = find_by_description(
            root,
            (
                "Fahrzeuge verwalten",
                "Meine Fahrzeuge",
            ),
        )

        if node is not None:
            target = clickable_parent(
                root,
                node,
            )

            tap_node(
                target
                if target is not None
                else node
            )

            break

        # Keine Fahrzeugauswahl sichtbar:
        # vermutlich Unterseite/Dialog offen.
        # Ein Schritt zurück, dann erneut prüfen.
        press_back()

        time.sleep(POLL_INTERVAL)

    else:
        raise UIError(
            "Fahrzeugauswahl konnte "
            "nicht erreicht werden"
        )

    deadline = (
        time.monotonic()
        + SWITCH_TIMEOUT
    )

    while time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL)

        root = dump_ui()

        for item in all_nodes(root):
            if item["desc"] == "Meine Fahrzeuge":
                return root

    raise UIError(
        "Fahrzeugliste wurde nicht "
        "rechtzeitig geöffnet"
    )

def discover_vehicles():
    root = open_vehicle_list()

    vehicles = [
        {
            "name": card["name"],
            "vin": card["vin"],
        }
        for card in vehicle_cards(root)
    ]

    if not vehicles:
        raise UIError(
            "Keine Fahrzeuge erkannt"
        )

    save_cache(vehicles)

    return vehicles


def list_vehicles():
    return discover_vehicles()


def current_vehicle():
    vehicles = load_cache()

    if not vehicles:
        vehicles = discover_vehicles()
        # Discovery leaves us in "Meine Fahrzeuge".
        root = ensure_vehicle_overview()
    else:
        root = dump_ui()

    current = current_from_root(root, vehicles)
    if current:
        return current

    # Subscreen/dialog may hide the header. Navigate back only.
    root = ensure_vehicle_overview()
    current = current_from_root(root, vehicles)

    if current:
        return current

    raise UIError("Aktuelles Fahrzeug konnte nicht erkannt werden")

def resolve_vehicle(identifier, vehicles):
    # VIN bevorzugt.
    for vehicle in vehicles:
        if vehicle["vin"] == identifier:
            return vehicle

    # Anzeigename als komfortabler Fallback.
    for vehicle in vehicles:
        if vehicle["name"] == identifier:
            return vehicle

    raise UIError(
        f"Fahrzeug {identifier!r} "
        "nicht gefunden"
    )


def select_vehicle(identifier):
    vehicles = load_cache()

    if not vehicles:
        vehicles = discover_vehicles()

    target_vehicle = resolve_vehicle(
        identifier,
        vehicles,
    )

    try:
        root = dump_ui()

        current = current_from_root(
            root,
            vehicles,
        )

    except Exception:
        current = None

    if (
        current
        and current["vin"]
        == target_vehicle["vin"]
    ):
        return {
            "changed": False,
            "vehicle": target_vehicle,
        }

    # Wenn der aktuelle Screen das Fahrzeug nicht erkennen lässt
    # (z.B. Lade-/Klima-/Lock-Unterseite), NICHT sofort die
    # Fahrzeugauswahl öffnen.
    #
    # Erst kontrolliert zur Fahrzeugübersicht zurückgehen und dort
    # den sichtbaren Fahrzeug-Header prüfen.
    if current is None:
        for _ in range(3):
            press_back()

            time.sleep(POLL_INTERVAL)

            root = dump_ui()

            current = current_from_root(
                root,
                vehicles,
            )

            if current:
                break

        if (
            current
            and current["vin"]
            == target_vehicle["vin"]
        ):
            return {
                "changed": False,
                "vehicle": target_vehicle,
            }

    # Nur wenn jetzt tatsächlich ein anderes Fahrzeug aktiv ist
    # oder der Zustand weiterhin nicht bestimmbar ist, muss die
    # Fahrzeugliste geöffnet werden.
    #
    # Die VW-App reagiert gelegentlich nicht auf den ersten Versuch,
    # die Fahrzeugliste zu öffnen. In diesem Fall einmal kontrolliert
    # zur Übersicht zurückkehren und genau einen zweiten Versuch machen.
    last_error = None

    for attempt in range(2):
        try:
            root = open_vehicle_list()
            break

        except UIError as exc:
            last_error = exc

            if attempt >= 1:
                raise

            try:
                ensure_vehicle_overview()
            except Exception:
                pass

            time.sleep(POLL_INTERVAL)

    else:
        raise last_error

    cards = vehicle_cards(root)

    target_card = next(
        (
            card
            for card in cards
            if card["vin"]
            == target_vehicle["vin"]
        ),
        None,
    )

    if target_card is None:
        # Fahrzeugliste könnte sich geändert haben.
        vehicles = [
            {
                "name": card["name"],
                "vin": card["vin"],
            }
            for card in cards
        ]

        save_cache(vehicles)

        raise UIError(
            f"VIN {target_vehicle['vin']} "
            "nicht mehr in der "
            "Fahrzeugliste gefunden"
        )

    tap_node(
        target_card["node"]
    )

    deadline = (
        time.monotonic()
        + SWITCH_TIMEOUT
    )

    while time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL)

        root = dump_ui()

        current = current_from_root(
            root,
            vehicles,
        )

        if (
            current
            and current["vin"]
            == target_vehicle["vin"]
        ):
            return {
                "changed": True,
                "vehicle": current,
            }

    raise UIError(
        f"Fahrzeugwechsel zu "
        f"{target_vehicle['name']!r} "
        "konnte nicht bestätigt werden"
    )


def select_vehicle_info(identifier):
    """Select a vehicle and return only its stable name/VIN data."""
    result = select_vehicle(identifier)
    vehicle = result.get("vehicle") if isinstance(result, dict) else None

    if not isinstance(vehicle, dict):
        raise UIError("Fahrzeugauswahl lieferte keine Fahrzeugdaten")

    requested = resolve_vehicle(identifier, load_cache())
    if vehicle.get("vin") != requested.get("vin"):
        raise UIError("Ausgewählte VIN stimmt nicht mit angeforderter VIN überein")

    return {"name": vehicle["name"], "vin": vehicle["vin"]}


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--pretty",
        action="store_true",
    )

    sub = parser.add_subparsers(
        dest="command",
        required=True,
    )

    sub.add_parser("current")
    sub.add_parser("list")

    select = sub.add_parser("select")

    select.add_argument(
        "vehicle",
        help="VIN oder Fahrzeugname",
    )

    args = parser.parse_args()

    try:
        if args.command == "current":
            result = {
                "ok": True,
                "vehicle": current_vehicle(),
                "error": None,
            }

        elif args.command == "list":
            result = {
                "ok": True,
                "vehicles": list_vehicles(),
                "error": None,
            }

        else:
            result = {
                "ok": True,
                **select_vehicle(
                    args.vehicle
                ),
                "error": None,
            }

    except Exception as exc:
        result = {
            "ok": False,
            "error": str(exc),
        }

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
        )
    )


if __name__ == "__main__":
    main()
