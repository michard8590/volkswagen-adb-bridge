#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


OPTIONS_FILE = Path("/data/options.json")


class ConnectionError(RuntimeError):
    pass


@dataclass
class AdbDevice:
    adb_serial: str
    transport: str
    physical_serial: str | None = None
    host: str | None = None
    port: int | None = None
    model: str | None = None
    name: str | None = None


def run_adb(*args, timeout=10, check=True):
    proc = subprocess.run(
        ["adb", *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )

    if check and proc.returncode != 0:
        raise ConnectionError(
            f"ADB {' '.join(args)} fehlgeschlagen: "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )

    return proc.stdout.strip()


def load_options():
    if not OPTIONS_FILE.exists():
        raise ConnectionError(
            f"Options-Datei nicht gefunden: {OPTIONS_FILE}"
        )

    return json.loads(
        OPTIONS_FILE.read_text(encoding="utf-8")
    )


def adb_devices():
    output = run_adb(
        "devices",
        "-l",
        timeout=10,
    )

    result = []

    for line in output.splitlines():
        line = line.strip()

        if (
            not line
            or line.startswith("List of devices")
        ):
            continue

        parts = line.split()

        if len(parts) < 2:
            continue

        serial = parts[0]
        state = parts[1]

        attrs = {}

        for part in parts[2:]:
            if ":" in part:
                key, value = part.split(":", 1)
                attrs[key] = value

        result.append(
            {
                "serial": serial,
                "state": state,
                "model": attrs.get("model"),
                "device": attrs.get("device"),
                "product": attrs.get("product"),
            }
        )

    return result


def discover_usb(preferred_serial=""):
    candidates = []

    for device in adb_devices():
        serial = device["serial"]

        # WLAN-ADB-Serials sehen normalerweise wie host:port aus.
        if ":" in serial:
            continue

        if device["state"] != "device":
            continue

        candidates.append(device)

    if preferred_serial:
        for device in candidates:
            if device["serial"] == preferred_serial:
                return AdbDevice(
                    adb_serial=device["serial"],
                    physical_serial=device["serial"],
                    transport="usb",
                    model=device.get("model"),
                )

        return None

    if len(candidates) == 1:
        device = candidates[0]

        return AdbDevice(
            adb_serial=device["serial"],
            physical_serial=device["serial"],
            transport="usb",
            model=device.get("model"),
        )

    if len(candidates) > 1:
        raise ConnectionError(
            "Mehrere USB-ADB-Geräte gefunden. "
            "Bitte usb.device_serial konfigurieren."
        )

    return None


def read_mdns_services(seconds=3.0):
    proc = subprocess.Popen(
        [
            "adb",
            "mdns",
            "track-services",
            "--proto-text",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        stdout, stderr = proc.communicate(
            timeout=seconds
        )
    except subprocess.TimeoutExpired:
        proc.terminate()

        try:
            stdout, stderr = proc.communicate(
                timeout=1
            )
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()

    return stdout or ""


def parse_mdns_services(
    text,
    service_type="_adb-tls-connect._tcp",
):
    services = []
    seen = set()

    # Sowohl "tls { service {...} }" als auch
    # "pair { service {...} }" enthalten denselben inneren
    # service-Block. Deshalb direkt diese Blöcke parsen.
    for block in re.findall(
        r"service\s*\{(.*?)\n\s*\}",
        text,
        flags=re.DOTALL,
    ):
        def field(name):
            match = re.search(
                rf'{re.escape(name)}:\s*"([^"]*)"',
                block,
            )
            return match.group(1) if match else None

        def int_field(name):
            match = re.search(
                rf"{re.escape(name)}:\s*(\d+)",
                block,
            )
            return int(match.group(1)) if match else None

        service = field("service")

        if service != service_type:
            continue

        ipv4 = field("ipv4")
        port = int_field("port")

        if not ipv4 or not port:
            continue

        item = {
            "service": service,
            "host": ipv4,
            "port": port,
            "serial": field("serial"),
            "model": field("product_model"),
            "name": field("given_name"),
            "hostname": field("hostname"),
        }

        # track-services kann denselben Snapshot mehrfach liefern.
        key = (
            item["service"],
            item["serial"],
            item["host"],
            item["port"],
        )

        if key in seen:
            continue

        seen.add(key)
        services.append(item)

    return services


def discover_pairing_service(preferred_serial=""):
    text = read_mdns_services()

    services = parse_mdns_services(
        text,
        "_adb-tls-pairing._tcp",
    )

    if preferred_serial:
        services = [
            service
            for service in services
            if service.get("serial") == preferred_serial
        ]

    if not services:
        return None

    if len(services) > 1 and not preferred_serial:
        details = ", ".join(
            (
                f"{service.get('name') or service.get('model') or '?'} "
                f"({service.get('serial') or '?'})"
            )
            for service in services
        )

        raise ConnectionError(
            "Mehrere WLAN-ADB-Pairing-Geräte gefunden: "
            f"{details}. Bitte adb_device_serial konfigurieren."
        )

    return services[0]



def pair_wifi(host, port, code):
    host = (host or "").strip()
    code = (code or "").strip()

    if not host or not port or not code:
        return False

    endpoint = f"{host}:{int(port)}"

    proc = subprocess.run(
        ["adb", "pair", endpoint, code],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=20,
    )

    output = proc.stdout.strip()
    lower = output.lower()

    if (
        proc.returncode == 0
        and (
            "successfully paired" in lower
            or "already paired" in lower
        )
    ):
        return True

    raise ConnectionError(
        "ADB-Pairing fehlgeschlagen für "
        f"{endpoint}: {output}"
    )


def maybe_pair_wifi(options):
    wifi_options = options.get("wifi", {})
    pairing = wifi_options.get("pairing", {})

    host = str(pairing.get("host", "") or "").strip()
    port = int(pairing.get("port", 0) or 0)
    code = str(pairing.get("code", "") or "").strip()

    # Ohne Pairing-Code gibt es nichts zu tun.
    if not code:
        return False

    # Explizit konfigurierte IP + Port haben Vorrang.
    if host and port > 0:
        return pair_wifi(
            host,
            port,
            code,
        )

    # Ansonsten Pairing-Endpunkt automatisch per mDNS suchen.
    if not wifi_options.get("autodiscovery", True):
        return False

    preferred_serial = str(
        options.get("adb_device_serial", "") or ""
    ).strip()

    service = discover_pairing_service(
        preferred_serial=preferred_serial,
    )

    # Kein Pairing-Dialog offen ist kein fataler Fehler.
    # Das Gerät könnte bereits gekoppelt sein.
    if not service:
        return False

    return pair_wifi(
        service["host"],
        service["port"],
        code,
    )




def connect_wifi_service(service):
    endpoint = (
        f"{service['host']}:{service['port']}"
    )

    output = run_adb(
        "connect",
        endpoint,
        timeout=15,
        check=False,
    )

    # adb connect kann sowohl "connected to"
    # als auch "already connected to" liefern.
    lower = output.lower()

    if (
        "connected to" not in lower
        and "already connected" not in lower
    ):
        raise ConnectionError(
            f"WLAN-ADB-Verbindung zu {endpoint} "
            f"fehlgeschlagen: {output}"
        )

    # Kurz warten bis adb devices den Transport kennt.
    deadline = time.monotonic() + 8

    while time.monotonic() < deadline:
        for device in adb_devices():
            if (
                device["serial"] == endpoint
                and device["state"] == "device"
            ):
                return AdbDevice(
                    adb_serial=endpoint,
                    physical_serial=service.get(
                        "serial"
                    ),
                    transport="wifi",
                    host=service["host"],
                    port=service["port"],
                    model=service.get("model"),
                    name=service.get("name"),
                )

        time.sleep(0.5)

    raise ConnectionError(
        f"{endpoint} verbunden, aber nicht als "
        "ADB-Gerät verfügbar."
    )


def discover_wifi(preferred_serial=""):
    text = read_mdns_services()
    services = parse_mdns_services(text)

    if preferred_serial:
        services = [
            service
            for service in services
            if service.get("serial")
            == preferred_serial
        ]

    if not services:
        return None

    if len(services) > 1 and not preferred_serial:
        details = ", ".join(
            (
                f"{s.get('name') or s.get('model') or '?'} "
                f"({s.get('serial') or '?'})"
            )
            for s in services
        )

        raise ConnectionError(
            "Mehrere WLAN-ADB-Geräte gefunden: "
            f"{details}. Bitte adb_device_serial "
            "konfigurieren."
        )

    return connect_wifi_service(
        services[0]
    )


def connect_wifi_manual(host, port):
    if not host or not port:
        raise ConnectionError(
            "Für manuelles WLAN-ADB müssen "
            "wifi.host und wifi.port gesetzt sein."
        )

    service = {
        "host": host,
        "port": int(port),
        "serial": None,
        "model": None,
        "name": None,
    }

    return connect_wifi_service(service)


def select_device(options):
    mode = options.get(
        "connection_mode",
        "auto",
    ).lower()

    global_serial = options.get(
        "adb_device_serial",
        "",
    ).strip()

    usb_options = options.get(
        "usb",
        {},
    )

    wifi_options = options.get(
        "wifi",
        {},
    )

    usb_serial = (
        usb_options.get(
            "device_serial",
            "",
        ).strip()
        or global_serial
    )

    wifi_serial = global_serial

    if mode not in (
        "auto",
        "usb",
        "wifi",
    ):
        raise ConnectionError(
            f"Ungültiger connection_mode: {mode}"
        )

    if mode in ("auto", "usb"):
        device = discover_usb(
            usb_serial
        )

        if device is not None:
            return device

        if mode == "usb":
            raise ConnectionError(
                "Kein passendes USB-ADB-Gerät gefunden."
            )

    if mode in ("auto", "wifi"):
        autodiscovery = bool(
            wifi_options.get(
                "autodiscovery",
                True,
            )
        )

        if autodiscovery:
            device = discover_wifi(
                wifi_serial
            )

            if device is not None:
                return device

            raise ConnectionError(
                "Kein passendes WLAN-ADB-Gerät "
                "per mDNS gefunden."
            )

        return connect_wifi_manual(
            wifi_options.get(
                "host",
                "",
            ).strip(),
            int(
                wifi_options.get(
                    "port",
                    0,
                )
            ),
        )

    raise ConnectionError(
        "Kein ADB-Gerät gefunden."
    )


def verify_device(device):
    result = run_adb(
        "-s",
        device.adb_serial,
        "shell",
        "getprop",
        "ro.product.model",
        timeout=10,
    )

    return result.strip()


def device_to_dict(device):
    return {
        "adb_serial": device.adb_serial,
        "physical_serial": device.physical_serial,
        "transport": device.transport,
        "host": device.host,
        "port": device.port,
        "model": device.model,
        "name": device.name,
    }
