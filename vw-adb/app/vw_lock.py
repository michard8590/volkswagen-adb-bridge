#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later

import argparse
import getpass
import json
import os
import sys
import time

from vw_ui import (
    UIError,
    start_app,
    adb,
    all_nodes,
    clickable_parent,
    density_scale,
    dump_ui,
    parse_bounds,
    swipe,
    tap,
    tap_node,
)
from vw_ui import dump_ui_checked
from vw_vehicle import ensure_vehicle_overview, select_vehicle_info


LOCKED_WORDS = (
    "verriegelt",
    "locked",
)

UNLOCKED_WORDS = (
    "entriegelt",
    "unlocked",
)

DETAIL_WORDS = (
    "details öffnen",
    "open details",
)

# VW SwitchStatusXL:
# Der Lock/Unlock-Switch ist vertikal.
SWITCH_WIDTH_DP = 104
SWITCH_HEIGHT_DP = 264

# ActionBar mit Statuszeile ("Verriegelt"/"Entriegelt")
ACTION_BAR_HEIGHT_DP = 84

WAIT_SHORT = 0.5
WAIT_SCREEN = 12.0
WAIT_RESULT = 60.0
WAIT_IDLE = 60.0
STABLE_CONFIRMATIONS = 2


def result(ok, vehicle=None, changed=False, state=None,
           pending=False, error=None, **extra):
    data = {
        "ok": ok,
        "vehicle": vehicle,
        "changed": changed,
        "state": state,
        "pending": pending,
        "error": error,
    }
    data.update(extra)
    return data


def node_text(node):
    return node.attrib.get("text", "").strip()


def node_desc(node):
    return node.attrib.get("content-desc", "").strip()


def node_id(node):
    return node.attrib.get("resource-id", "").strip()


def normalized(value):
    return value.strip().lower()


def state_from_string(value):
    value = normalized(value)

    if any(word in value for word in UNLOCKED_WORDS):
        return "unlocked"

    if any(word in value for word in LOCKED_WORDS):
        return "locked"

    return None




def transition_from_root(root):
    """
    Erkennt einen laufenden Lock/Unlock-Vorgang.

    Wichtig: Ein Zielzustand kann in der VW-App bereits angezeigt werden,
    obwohl der Remote-Vorgang noch mit "Entriegeln..."/"Verriegeln..."
    läuft. Dieser Zwischenzustand darf nicht als abgeschlossen gelten.
    """
    for item in all_nodes(root):
        values = (
            node_text(item["node"]),
            node_desc(item["node"]),
        )

        for value in values:
            low = normalized(value)
            if not low:
                continue

            if (
                low.startswith("entriegeln...")
                or low.startswith("entriegeln…")
                or "wird entriegelt" in low
            ):
                return "unlocking"

            if (
                low.startswith("verriegeln...")
                or low.startswith("verriegeln…")
                or "wird verriegelt" in low
            ):
                return "locking"

    return None


def wait_for_lock_idle(timeout=WAIT_IDLE):
    """Wartet nur auf Abschluss eines bereits laufenden Vorgangs; kein Retry."""
    deadline = time.time() + timeout
    last_transition = None

    while time.time() < deadline:
        root = dump_ui_checked()
        transition = transition_from_root(root)

        if transition is None:
            return root

        last_transition = transition
        time.sleep(1.0)

    raise RuntimeError(
        "Lock/Unlock-Vorgang ist nach "
        f"{int(timeout)} Sekunden noch aktiv ({last_transition})"
    )


def state_from_root(root):
    # Zuerst expliziter Status-Text, z.B. "Verriegelt".
    for item in all_nodes(root):
        node = item["node"]
        text = node_text(node)

        state = state_from_string(text)
        if state:
            return state

    # Danach die Feature-Kachel:
    # "Fahrzeug. Verriegelt. Details öffnen"
    for item in all_nodes(root):
        node = item["node"]
        desc = node_desc(node)

        if not desc:
            continue

        state = state_from_string(desc)
        if not state:
            continue

        low = normalized(desc)

        if any(word in low for word in DETAIL_WORDS):
            return state

    return None


def find_lock_feature(root):
    """
    Sucht die semantische Lock/Unlock-Kachel.
    Beispiele:
      Fahrzeug. Verriegelt. Details öffnen
      Fahrzeug. Entriegelt. Details öffnen
    """
    for item in all_nodes(root):
        node = item["node"]
        desc = node_desc(node)

        if not desc:
            continue

        low = normalized(desc)

        if state_from_string(desc) is None:
            continue

        if not any(word in low for word in DETAIL_WORDS):
            continue

        parent = clickable_parent(root, node)

        if parent is not None:
            return parent

    return None


def overview_end_reached(root):
    """
    Sobald die Einstellungen-Kachel sichtbar ist, haben wir den
    Feature-Bereich bis zum Ende durchsucht.

    Die Lock/Unlock-Kachel befindet sich – falls vom Fahrzeug
    unterstützt – vor der Einstellungen-Kachel. Ist Einstellungen
    sichtbar und Lock/Unlock wurde nicht gefunden, ist das Feature
    für dieses Fahrzeug nicht verfügbar.
    """
    for item in all_nodes(root):
        node = item["node"]

        desc = node_desc(node).lower()
        text = node_text(node).lower()

        if (
            desc.startswith("einstellungen.")
            or text == "einstellungen"
        ):
            return True

    return False


def scroll_overview_once(root):
    """Scroll relatively inside the largest live scrollable container."""
    candidates = []

    for item in all_nodes(root):
        node = item["node"]
        if node.attrib.get("scrollable") != "true":
            continue

        try:
            bounds = parse_bounds(node.attrib.get("bounds", ""))
        except UIError:
            continue

        candidates.append((node, bounds))

    if not candidates:
        return False

    _, bounds = max(
        candidates,
        key=lambda x: (x[1][2] - x[1][0]) * (x[1][3] - x[1][1]),
    )

    x1, y1, x2, y2 = bounds
    x = (x1 + x2) // 2
    start_y = y1 + int((y2 - y1) * 0.75)
    end_y = y1 + int((y2 - y1) * 0.30)

    swipe(x, start_y, x, end_y, 350)
    time.sleep(WAIT_SHORT)
    return True

def find_lock_feature_with_scroll():
    root = ensure_vehicle_overview()

    if root is None:
        return None, None

    # Mehr als wenige Scrolls sollten niemals nötig sein.
    # Vor allem wird sofort beendet, sobald "Einstellungen"
    # erreicht ist.
    for _ in range(4):
        feature = find_lock_feature(root)

        if feature is not None:
            return root, feature

        # Wenn wir Einstellungen sehen, sind wir am Ende der
        # Feature-Liste. Dann kann Lock/Unlock nicht mehr kommen.
        if overview_end_reached(root):
            return root, None

        if not scroll_overview_once(root):
            return root, None

        root = dump_ui_checked()

    return root, None


def open_lock_sheet():
    root, feature = find_lock_feature_with_scroll()

    if feature is None:
        return None, "Lock/Unlock-Funktion nicht gefunden"

    tap_node(feature)

    deadline = time.time() + WAIT_SCREEN

    while time.time() < deadline:
        root = dump_ui_checked()

        compose = None
        sheet = None

        for item in all_nodes(root):
            node = item["node"]
            rid = node_id(node)

            if rid.endswith(":id/compose_view"):
                compose = node

            if rid.endswith(":id/design_bottom_sheet"):
                sheet = node

        state = state_from_root(root)

        if compose is not None and sheet is not None and state:
            return root, None

        time.sleep(0.35)

    return None, "Lock/Unlock-Bottom-Sheet wurde nicht geöffnet"


def find_compose_bounds(root):
    for item in all_nodes(root):
        node = item["node"]
        if node_id(node).endswith(":id/compose_view"):
            try:
                return parse_bounds(node.attrib.get("bounds", ""))
            except UIError:
                return None
    return None

def tap_switch_target(root, target):
    """
    Target:
      locked   -> obere Hälfte
      unlocked -> untere Hälfte

    Es werden keine absoluten Pixelkoordinaten gespeichert.
    """
    compose = find_compose_bounds(root)

    if not compose:
        raise RuntimeError(
            "compose_view des Lock/Unlock-Dialogs nicht gefunden"
        )

    scale = density_scale()

    switch_width = round(
        SWITCH_WIDTH_DP * scale
    )
    switch_height = round(
        SWITCH_HEIGHT_DP * scale
    )
    action_height = round(
        ACTION_BAR_HEIGHT_DP * scale
    )

    x1, y1, x2, y2 = compose

    center_x = (x1 + x2) // 2

    switch_top = y1 + action_height
    switch_bottom = switch_top + switch_height

    # Sicherheitsprüfung: berechneter Switch muss im ComposeView liegen.
    if (
        switch_top < y1
        or switch_bottom > y2
        or switch_width <= 0
        or switch_height <= 0
    ):
        raise RuntimeError(
            "Berechnete Switch-Position liegt außerhalb des ComposeViews"
        )

    if target == "locked":
        # Mitte der oberen Hälfte.
        y = switch_top + switch_height // 4

    elif target == "unlocked":
        # Mitte der unteren Hälfte.
        y = switch_top + (switch_height * 3) // 4

    else:
        raise ValueError(target)

    tap(center_x, y)


def find_spin_edit(root):
    for item in all_nodes(root):
        node = item["node"]

        if (
            node.attrib.get("class") == "android.widget.EditText"
            and node.attrib.get("enabled") == "true"
            and node.attrib.get("focusable") == "true"
        ):
            return node

    return None


def wait_for_spin_screen():
    deadline = time.time() + WAIT_SCREEN

    while time.time() < deadline:
        root = dump_ui_checked()

        edit = find_spin_edit(root)

        if edit is not None:
            return root, edit

        time.sleep(0.35)

    return None, None


def dismiss_autofill():
    """
    Android/KeePass Autofill-Speichern-Dialog schließen.
    Bevorzugt 'Nein danke'.
    """
    deadline = time.time() + 4.0

    while time.time() < deadline:
        root = dump_ui_checked()

        preferred = (
            "android:id/autofill_save_no",
            "android:id/closeButton",
        )

        for rid_wanted in preferred:
            for item in all_nodes(root):
                node = item["node"]

                if node_id(node) != rid_wanted:
                    continue

                if node.attrib.get("clickable") != "true":
                    continue

                tap_node(node)
                time.sleep(0.5)
                return True

        # Kein Autofill-Dialog sichtbar.
        if not any(
            "autofill_save_" in node_id(item["node"])
            for item in all_nodes(root)
        ):
            return False

        time.sleep(0.25)

    return False


def get_spin(args):
    if args.spin_file:
        path = os.path.expanduser(args.spin_file)

        st = os.stat(path)

        # Keine harte Abweisung wegen Windows/ungewöhnlicher FS,
        # aber auf Unix vor zu offenen Rechten schützen.
        if os.name == "posix":
            if st.st_mode & 0o077:
                raise RuntimeError(
                    "S-PIN-Datei ist für Gruppe/Andere lesbar. "
                    "Bitte z.B. chmod 600 verwenden."
                )

        with open(path, "r", encoding="utf-8") as f:
            pin = f.read().strip()

    else:
        pin = getpass.getpass("S-PIN: ")

    if not pin.isdigit():
        raise RuntimeError(
            "S-PIN muss ausschließlich aus Ziffern bestehen"
        )

    if not 4 <= len(pin) <= 12:
        raise RuntimeError(
            "Unplausible S-PIN-Länge"
        )

    return pin


def enter_spin(pin):
    root, edit = wait_for_spin_screen()

    if edit is None:
        raise RuntimeError(
            "S-PIN-Eingabefeld wurde nicht gefunden"
        )

    # Feld sollte normalerweise bereits fokussiert sein.
    # Falls nicht, semantischen EditText antippen.
    if edit.attrib.get("focused") != "true":
        tap_node(edit)
        time.sleep(0.2)

    # Nur Ziffern -> adb input text benötigt kein Sonderzeichen-Escaping.
    adb(
        "shell",
        "input",
        "text",
        pin,
    )


def wait_for_result(target):
    """
    Sendet niemals einen zweiten Fahrzeugbefehl.
    Beobachtet ausschließlich den Zustand.

    Der Zielzustand muss stabil sein und es darf kein laufender
    "Entriegeln..."/"Verriegeln..."-Zwischenzustand mehr sichtbar sein.
    """
    deadline = time.time() + WAIT_RESULT
    stable = 0
    last_state = None

    while time.time() < deadline:
        root = dump_ui_checked()

        state = state_from_root(root)
        transition = transition_from_root(root)
        last_state = state

        if state == target and transition is None:
            stable += 1
            if stable >= STABLE_CONFIRMATIONS:
                return True, state
        else:
            stable = 0

        time.sleep(1.0)

    return False, last_state


def do_status(vin):
    vehicle = select_vehicle_info(vin)

    if not vehicle:
        return result(
            False,
            error="Fahrzeug konnte nicht ausgewählt werden",
        )

    root, feature = find_lock_feature_with_scroll()

    if feature is None:
        return result(
            False,
            vehicle=vehicle,
            error="Lock/Unlock-Funktion für dieses Fahrzeug nicht gefunden",
            supported=False,
        )

    state = state_from_root(root)

    return result(
        True,
        vehicle=vehicle,
        state=state,
        supported=True,
    )


def do_command(vin, target, args):
    vehicle = select_vehicle_info(vin)

    if not vehicle:
        return result(
            False,
            error="Fahrzeug konnte nicht ausgewählt werden",
        )

    # Falls ein vorheriger Lock/Unlock-Aufruf noch läuft, nicht sofort
    # den nächsten Befehl versuchen. Nur auf den Abschluss warten.
    wait_for_lock_idle()

    root, feature = find_lock_feature_with_scroll()

    if feature is None:
        return result(
            False,
            vehicle=vehicle,
            error="Lock/Unlock-Funktion für dieses Fahrzeug nicht gefunden",
            supported=False,
        )

    current = state_from_root(root)

    if current == target:
        return result(
            True,
            vehicle=vehicle,
            changed=False,
            state=current,
            supported=True,
        )

    root, error = open_lock_sheet()

    if error:
        return result(
            False,
            vehicle=vehicle,
            state=current,
            error=error,
            supported=True,
        )

    sheet_state = state_from_root(root)

    # Nochmal direkt vor dem physischen Befehl prüfen.
    if sheet_state == target:
        return result(
            True,
            vehicle=vehicle,
            changed=False,
            state=sheet_state,
            supported=True,
        )

    try:
        tap_switch_target(root, target)
    except Exception as exc:
        return result(
            False,
            vehicle=vehicle,
            state=sheet_state,
            error=str(exc),
            supported=True,
        )

    # Ab hier wurde der gewünschte Lock/Unlock-Flow ausgelöst.
    # NIEMALS automatisch nochmals auf den Switch tippen.

    root, edit = wait_for_spin_screen()

    if edit is None:
        return result(
            False,
            vehicle=vehicle,
            state=sheet_state,
            pending=False,
            command_sent=False,
            error=(
                "S-PIN-Maske nicht erschienen. "
                "Fahrzeugbefehl wurde nicht erneut versucht."
            ),
            supported=True,
        )

    try:
        pin = get_spin(args)

        enter_spin(pin)

    except Exception as exc:
        return result(
            False,
            vehicle=vehicle,
            state=sheet_state,
            error=str(exc),
            supported=True,
        )

    finally:
        # Python garantiert kein Memory-Wipe,
        # aber Referenz so früh wie möglich verwerfen.
        try:
            pin = None
        except Exception:
            pass

    time.sleep(1.0)

    dismiss_autofill()

    success, final_state = wait_for_result(target)

    if success:
        return result(
            True,
            vehicle=vehicle,
            changed=True,
            state=final_state,
            pending=False,
            supported=True,
        )

    # Kein Retry!
    # Ein Remote-Fahrzeugbefehl kann server-/fahrzeugseitig noch laufen.
    return result(
        True,
        vehicle=vehicle,
        changed=True,
        state=final_state,
        pending=True,
        error=None,
        supported=True,
        message=(
            "Befehl wurde über die VW-App abgeschickt, "
            "Zielzustand aber innerhalb des Prüfzeitraums "
            "noch nicht bestätigt. Kein automatischer Retry."
        ),
    )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "vin",
        help="VIN des Fahrzeugs",
    )

    parser.add_argument(
        "command",
        choices=("status", "lock", "unlock"),
    )

    parser.add_argument(
        "--spin-file",
        help=(
            "Datei mit S-PIN. Für unbeaufsichtigten Betrieb "
            "empfohlen; unter Linux chmod 600."
        ),
    )

    parser.add_argument(
        "--pretty",
        action="store_true",
    )

    args = parser.parse_args()

    try:
        # Standalone-Aufrufe müssen unabhängig vom App-Zustand funktionieren.
        # start_app() startet die VW-App bzw. holt sie in den Vordergrund.
        start_app()

        if args.command == "status":
            data = do_status(args.vin)

        elif args.command == "lock":
            data = do_command(
                args.vin,
                "locked",
                args,
            )

        else:
            data = do_command(
                args.vin,
                "unlocked",
                args,
            )

        # Standalone-CLI wie Charge/Climate immer sauber auf der
        # Fahrzeugübersicht beenden.
        ensure_vehicle_overview()

    except KeyboardInterrupt:
        data = result(
            False,
            error="Abgebrochen",
        )

    except Exception as exc:
        data = result(
            False,
            error=str(exc),
        )

    print(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
        )
    )

    return 0 if data.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
