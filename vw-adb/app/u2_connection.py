# SPDX-License-Identifier: GPL-3.0-or-later
import time

import uiautomator2 as u2

from connection import (
    ConnectionError,
    run_adb,
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
        # Den bisherigen Transport merken. Bei Android Wireless Debugging
        # kann sich der dynamische TCP-Port bei einem Reconnect ändern.
        previous_device = self.device

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

        # Erst nachdem der neue Transport vollständig verifiziert wurde,
        # einen veralteten WLAN-ADB-Endpunkt desselben physischen Geräts
        # entfernen. Das Cleanup darf die neue Verbindung niemals gefährden.
        if (
            previous_device is not None
            and previous_device.transport == "wifi"
            and device.transport == "wifi"
            and previous_device.adb_serial != device.adb_serial
        ):
            old_physical = previous_device.physical_serial
            new_physical = device.physical_serial

            same_device = bool(
                old_physical
                and new_physical
                and old_physical == new_physical
            )

            # Falls mDNS keine physische Seriennummer geliefert hat,
            # ist derselbe Host ein vorsichtiger Fallback.
            if (
                not same_device
                and (not old_physical or not new_physical)
            ):
                same_device = bool(
                    previous_device.host
                    and device.host
                    and previous_device.host == device.host
                )

            if same_device:
                try:
                    output = run_adb(
                        "disconnect",
                        previous_device.adb_serial,
                        timeout=10,
                        check=False,
                    )

                    self.log(
                        "INFO",
                        "Alter WLAN-ADB-Endpunkt bereinigt: "
                        f"{previous_device.adb_serial}"
                        + (
                            f" ({output})"
                            if output
                            else ""
                        ),
                    )

                except Exception as exc:
                    # Reine Hygiene: Ein fehlgeschlagenes Cleanup darf
                    # die erfolgreich aufgebaute Verbindung nicht zerstören.
                    self.log(
                        "WARNING",
                        "Alter WLAN-ADB-Endpunkt konnte nicht "
                        f"bereinigt werden: {exc}",
                    )

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
