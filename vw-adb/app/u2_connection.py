# SPDX-License-Identifier: GPL-3.0-or-later
import time

import uiautomator2 as u2

from connection import (
    ConnectionError,
    select_device,
    verify_device,
)


class U2Connection:
    def __init__(self, options, log=None):
        self.options = options
        self.log = log or (lambda level, message: None)

        self.device = None
        self.u2 = None

    def connect(self):
        self.close()

        device = select_device(self.options)
        model = verify_device(device)

        self.log(
            "INFO",
            f"Verbinde uiautomator2 mit "
            f"{device.adb_serial} "
            f"({model or 'unbekannt'})",
        )

        try:
            u2_device = u2.connect(device.adb_serial)
        except Exception as exc:
            raise ConnectionError(
                f"uiautomator2-Verbindung zu "
                f"{device.adb_serial} fehlgeschlagen: {exc}"
            ) from exc

        self.device = device
        self.u2 = u2_device

        # Die komplette bestehende VW-UI-Schicht an genau diesen
        # ausgewählten ADB/uiautomator2-Transport binden.
        from vw_ui import configure_device

        configure_device(
            u2_device=self.u2,
            adb_serial=self.device.adb_serial,
        )

        # Healthcheck über einen echten UI-Dump.
        #
        # d.info/deviceInfo ist auf Android 17 derzeit problematisch:
        # androidx.test.uiautomator kann dabei in getDisplaySizeDp() mit
        # "ApplicationSharedMemory not initialized" scheitern.
        # Für unsere VW-Automation ist entscheidend, dass der
        # UIAutomator-Server Hierarchien lesen kann.
        try:
            xml = self.u2.dump_hierarchy()
            if not xml or "<hierarchy" not in xml:
                raise RuntimeError("Leerer oder ungültiger UI-Dump")
        except Exception as exc:
            self.close()
            raise ConnectionError(
                f"uiautomator2 verbunden, aber UIAutomator nicht "
                f"ansprechbar: {exc}"
            ) from exc

        self.log(
            "INFO",
            f"uiautomator2 bereit: "
            f"{device.adb_serial} "
            f"({model or 'unbekannt'})",
        )

        return self.u2

    def close(self):
        # Auch die VW-UI-Schicht freigeben. Die ADB-Serial bleibt dort
        # absichtlich nicht erhalten, weil sie sich bei Wireless
        # Debugging nach einem Reconnect geändert haben kann.
        try:
            from vw_ui import configure_device
            configure_device()
        except Exception:
            pass

        self.u2 = None
        self.device = None

    def is_alive(self):
        if self.u2 is None or self.device is None:
            return False

        try:
            verify_device(self.device)
            xml = self.u2.dump_hierarchy()
            return bool(xml and "<hierarchy" in xml)
        except Exception:
            return False

    def ensure_connected(self):
        if self.is_alive():
            return self.u2

        self.log(
            "WARNING",
            "uiautomator2-Verbindung nicht verfügbar, "
            "stelle Verbindung neu her.",
        )

        return self.connect()

    def reconnect(self, attempts=3, delay=2):
        last_error = None

        for attempt in range(1, attempts + 1):
            try:
                return self.connect()
            except Exception as exc:
                last_error = exc

                self.log(
                    "WARNING",
                    f"Reconnect {attempt}/{attempts} "
                    f"fehlgeschlagen: {exc}",
                )

                if attempt < attempts:
                    time.sleep(delay)

        raise ConnectionError(
            f"uiautomator2-Reconnect fehlgeschlagen: "
            f"{last_error}"
        )
