#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later

import re
import subprocess
import time
import xml.etree.ElementTree as ET

import uiautomator2 as u2


PACKAGE = "com.volkswagen.weconnect"

_u2_device = None
_adb_serial = None

POLL_INTERVAL = 1.0
APP_START_TIMEOUT = 15.0
FEEDBACK_DISMISS_DELAY = 0.4

FEEDBACK_DIALOG_MARKERS = (
    "danke, dass sie volkswagen app nutzen",
    "danke, dass sie die volkswagen app nutzen",
)


class UIError(RuntimeError):
    pass


def configure_device(u2_device=None, adb_serial=None):
    """
    Bindet die VW-UI-Schicht an das vom Broker ausgewählte Android-Gerät.

    Standalone-Skripte funktionieren weiterhin ohne diese Funktion:
    Dann verwendet uiautomator2/ADB wie bisher das Standardgerät.
    """
    global _u2_device, _adb_serial

    _u2_device = u2_device
    _adb_serial = str(adb_serial or "").strip() or None


def adb(*args, capture=False):
    cmd = ["adb"]

    if _adb_serial:
        cmd.extend(["-s", _adb_serial])

    cmd.extend(args)

    if capture:
        return subprocess.run(
            cmd,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout

    subprocess.run(
        cmd,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _get_u2_device():
    global _u2_device

    if _u2_device is None:
        if _adb_serial:
            _u2_device = u2.connect(_adb_serial)
        else:
            _u2_device = u2.connect()

    return _u2_device


def _dismiss_feedback_dialog_from_root(root):
    """
    Schließt den sporadischen VW-Feedbackdialog durch einen neutralen
    Tap außerhalb seines tatsächlichen Dialog-Containers.

    Die Tap-Position wird ausschließlich aus den aktuellen Bounds des
    Dialogs abgeleitet; es werden keine festen Display-Koordinaten
    verwendet.
    """
    title = None

    for node in root.iter("node"):
        rid = node.attrib.get("resource-id", "").strip()
        text = node.attrib.get("text", "").strip().lower()

        if not (
            rid == "title"
            or rid.endswith("/title")
            or rid.endswith(":id/title")
        ):
            continue

        if any(marker in text for marker in FEEDBACK_DIALOG_MARKERS):
            title = node
            break

    if title is None:
        return False

    parents = build_parent_map(root)
    dialog = parents.get(title)

    if dialog is None:
        return False

    # Beim VW-Feedbackdialog ist der direkte Parent des Titels der
    # eigentliche Dialog-Container (android.view.ViewGroup).
    # Nur diesen Container verwenden, nicht die Titel-Bounds.
    if dialog.attrib.get("class", "") != "android.view.ViewGroup":
        return False

    try:
        x1, y1, x2, _ = parse_bounds(
            dialog.attrib.get("bounds", "")
        )
    except UIError:
        return False

    if y1 <= 1:
        return False

    # Neutral mittig oberhalb des Dialogs tippen. Genau diese
    # Vorgehensweise wurde am realen Dialog erfolgreich getestet.
    tap_x = (x1 + x2) // 2
    tap_y = max(1, y1 // 2)

    tap(tap_x, tap_y)
    return True


def dump_ui():
    """
    Liefert den aktuellen Android-UI-Baum als ElementTree-Root.

    Der Dump wird über den persistenten uiautomator2-Dienst direkt
    im Speicher abgefragt. Es werden keine XML-Dateien mehr über
    /sdcard geschrieben oder per adb pull übertragen.

    Bei einem verlorenen RPC-Dienst wird einmal neu verbunden.
    """
    global _u2_device

    last_error = None

    for attempt in range(2):
        try:
            device = _get_u2_device()

            xml_data = device.dump_hierarchy(
                compressed=False,
                pretty=False,
            )

            if not xml_data:
                raise UIError(
                    "Leerer UI-Dump von uiautomator2"
                )

            root = ET.fromstring(xml_data)

            if _dismiss_feedback_dialog_from_root(root):
                time.sleep(FEEDBACK_DISMISS_DELAY)

                xml_data = device.dump_hierarchy(
                    compressed=False,
                    pretty=False,
                )

                if not xml_data:
                    raise UIError(
                        "Leerer UI-Dump nach Feedback-Dialog"
                    )

                root = ET.fromstring(xml_data)

            return root

        except Exception as exc:
            last_error = exc
            _u2_device = None

            if attempt == 0:
                continue

    raise UIError(
        f"UI-Dump über uiautomator2 fehlgeschlagen: {last_error}"
    )



def parse_bounds(bounds):
    """Parse Android bounds '[x1,y1][x2,y2]'."""
    match = re.fullmatch(
        r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]",
        bounds or "",
    )

    if not match:
        raise UIError(f"Ungültige Bounds: {bounds}")

    return tuple(map(int, match.groups()))


def tap(x, y):
    adb(
        "shell", "input", "tap",
        str(round(x)), str(round(y)),
    )


def swipe(x1, y1, x2, y2, duration_ms=350):
    adb(
        "shell", "input", "swipe",
        str(round(x1)), str(round(y1)),
        str(round(x2)), str(round(y2)),
        str(int(duration_ms)),
    )


def press_back():
    adb("shell", "input", "keyevent", "KEYCODE_BACK")


def density_scale():
    output = adb("shell", "wm", "density", capture=True)

    override = re.search(r"Override density:\s*(\d+)", output)
    physical = re.search(r"Physical density:\s*(\d+)", output)
    match = override or physical

    if not match:
        raise UIError("Android Display-Density konnte nicht ermittelt werden")

    return int(match.group(1)) / 160.0


def parent_of(root, node):
    return build_parent_map(root).get(node)

def center(bounds):
    x1, y1, x2, y2 = parse_bounds(bounds)
    return ((x1 + x2) // 2, (y1 + y2) // 2)

def tap_node(node):
    bounds = node.attrib.get("bounds", "")
    x, y = center(bounds)
    tap(x, y)

def build_parent_map(root):
    parents = {}

    for parent in root.iter():
        for child in parent:
            parents[child] = parent

    return parents


def clickable_parent(
    root,
    node,
):
    parents = build_parent_map(root)

    current = node

    while current is not None:
        if (
            current.attrib.get("clickable")
            == "true"
        ):
            return current

        current = parents.get(current)

    return None


def find_by_resource_id(
    root,
    resource_id,
):
    for node in root.iter("node"):
        rid = node.attrib.get(
            "resource-id",
            "",
        )

        if (
            rid == resource_id
            or rid.endswith(
                f":id/{resource_id}"
            )
        ):
            return node

    return None


def find_by_text(
    root,
    text,
):
    wanted = text.strip()

    for node in root.iter("node"):
        if (
            node.attrib.get(
                "text",
                "",
            ).strip()
            == wanted
        ):
            return node

    return None


def find_by_description(
    root,
    descriptions,
):
    if isinstance(
        descriptions,
        str,
    ):
        descriptions = (
            descriptions,
        )

    for node in root.iter("node"):
        desc = node.attrib.get(
            "content-desc",
            "",
        ).strip()

        if desc in descriptions:
            return node, desc

    return None, None


def all_nodes(root):
    for node in root.iter("node"):
        yield {
            "node": node,
            "text": node.attrib.get(
                "text",
                "",
            ).strip(),
            "desc": node.attrib.get(
                "content-desc",
                "",
            ).strip(),
            "id": node.attrib.get(
                "resource-id",
                "",
            ).strip(),
            "clickable": (
                node.attrib.get(
                    "clickable"
                )
                == "true"
            ),
            "enabled": (
                node.attrib.get(
                    "enabled"
                )
                == "true"
            ),
            "bounds": node.attrib.get(
                "bounds",
                "",
            ),
        }


def root_has_vw_app(root):
    """True wenn im aktuellen UI-Baum eine View der Volkswagen-App liegt."""
    if root is None:
        return False

    return any(
        node.attrib.get("package", "") == PACKAGE
        for node in root.iter("node")
    )


def prepare_device():
    """
    Bildschirm für die UI-Automation vorbereiten.

    KEYCODE_WAKEUP schaltet einen bereits aktiven Bildschirm nicht aus.
    wm dismiss-keyguard kann einen ungesicherten Sperrbildschirm schließen,
    umgeht aber bewusst keinen PIN, kein Passwort und kein Muster.
    """
    try:
        adb(
            "shell",
            "input",
            "keyevent",
            "KEYCODE_WAKEUP",
        )
    except Exception:
        pass

    time.sleep(0.15)

    try:
        adb(
            "shell",
            "wm",
            "dismiss-keyguard",
        )
    except Exception:
        # Auf Geräten/Android-Versionen ohne unterstützten Befehl
        # übernimmt start_app() anschließend die normale Fehlerbehandlung.
        pass

    time.sleep(0.15)


def stop_app():
    """VW-App vollständig beenden, uiautomator2 selbst bleibt aktiv."""
    adb(
        "shell",
        "am",
        "force-stop",
        PACKAGE,
    )


def start_app():
    """VW-App starten und warten, bis eine VW-UI sichtbar ist."""
    prepare_device()

    adb(
        "shell",
        "monkey",
        "-p",
        PACKAGE,
        "-c",
        "android.intent.category.LAUNCHER",
        "1",
    )

    deadline = time.monotonic() + APP_START_TIMEOUT

    while time.monotonic() < deadline:
        time.sleep(0.35)

        try:
            root = dump_ui()
        except Exception:
            continue

        # Normaler Fahrzeug-Overview oder Header. Falls Android einen
        # Unterdialog wiederherstellt, wird die aufrufende Navigation
        # kontrolliert per BACK zur Übersicht zurückkehren.
        if find_by_resource_id(root, "rangeTile") is not None:
            return root

        if find_current_vehicle_name(root):
            return root

        # Auch andere eindeutig zur VW-App gehörende Views akzeptieren.
        if any(
            item["id"].startswith(PACKAGE + ":id/")
            for item in all_nodes(root)
        ):
            return root

    raise UIError(
        "VW-App wurde gestartet, aber keine VW-UI war rechtzeitig sichtbar"
    )


def restart_app():
    stop_app()
    time.sleep(0.5)
    return start_app()


def find_current_vehicle_name(root):
    for item in all_nodes(root):
        desc = item["desc"]

        m = re.search(
            r"Ihr Fahrzeug:\s*"
            r"(.+?)"
            r"(?:\.|$)",
            desc,
        )

        if m:
            return m.group(1).strip()

    return None
