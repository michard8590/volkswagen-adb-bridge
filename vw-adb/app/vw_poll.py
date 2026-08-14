#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later

import argparse
import json
import sys
import time
from datetime import datetime, timedelta

from job_queue import BackgroundCancelled, check_cancel
from vw_charge import read_poll_charge_status, read_target_soc_setting
from vw_climate import get_climate_tile_status
from vw_odometer import read_odometer_km
from vw_ui import start_app, stop_app
from vw_vehicle import (
    ensure_vehicle_overview,
    read_overview_header_info,
    parse_lock_state_from_header,
    read_overview_lock_state,
    load_cache,
    list_vehicles,
    select_vehicle_info,
    sync_if_stale,
)


DEFAULT_SYNC_IF_OLDER_THAN = 15 * 60
DEFAULT_SYNC_WAIT_TIMEOUT = 180


def format_sync_age(age_seconds):
    if age_seconds is None:
        return None

    seconds = max(0, int(age_seconds))

    if seconds < 60:
        if seconds == 0:
            return "gerade eben"
        return f"{seconds} Sekunde" if seconds == 1 else f"{seconds} Sekunden"

    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} Minute" if minutes == 1 else f"{minutes} Minuten"

    hours = minutes // 60
    if hours < 24:
        return f"{hours} Stunde" if hours == 1 else f"{hours} Stunden"

    days = hours // 24
    return f"{days} Tag" if days == 1 else f"{days} Tage"


def sync_timestamp(age_seconds):
    if age_seconds is None:
        return None

    timestamp = datetime.now().astimezone() - timedelta(seconds=max(0, int(age_seconds)))
    return timestamp.isoformat(timespec="seconds")


def enrich_sync_info(sync):
    age = sync.get("last_sync_age_seconds")
    sync["last_sync_age"] = format_sync_age(age)
    sync["last_sync_at"] = sync_timestamp(age)
    return sync


def poll_vehicle(
    vin,
    sync_if_older_than,
    sync_wait_timeout,
    include_details=False,
    cancel_event=None,
):
    check_cancel(cancel_event)

    vehicle = select_vehicle_info(vin)

    check_cancel(cancel_event)

    sync = enrich_sync_info(sync_if_stale(
        sync_if_older_than,
        wait_timeout=sync_wait_timeout,
        cancel_event=cancel_event,
    ))

    check_cancel(cancel_event)

    # Sync navigation always finishes on the overview (or times out there).
    root = ensure_vehicle_overview()

    charge = None
    try:
        # read_poll_charge_status() kann bei fehlendem SoC die
        # Lade-Detailansicht öffnen. Unabhängig vom Pfad danach immer
        # wieder einen definierten Ausgangszustand herstellen.
        check_cancel(cancel_event)

        charge = read_poll_charge_status(root)

        check_cancel(cancel_event)

        odometer_km = None
        if include_details:
            charge["target_soc"] = read_target_soc_setting()

            check_cancel(cancel_event)

            ensure_vehicle_overview()

            check_cancel(cancel_event)

            odometer_km = read_odometer_km()
    finally:
        root = ensure_vehicle_overview()

    # Header/Climate erst nach dem Cleanup lesen, damit die Werte sicher
    # aus der Fahrzeugübersicht stammen.
    header = read_overview_header_info(root)
    climate_state = get_climate_tile_status(root) or "unknown"

    # Lock state priority:
    # 1. explicit wording in the current overview header
    # 2. explicit wording from the header captured during sync
    # 3. dedicated lock/status tile as fallback
    # This avoids unrelated "locked" nodes overriding an explicit
    # "Fahrzeug ist entriegelt" header captured moments earlier.
    current_description = header.get("description")
    lock_state = parse_lock_state_from_header(current_description)

    if lock_state is None:
        lock_state = parse_lock_state_from_header(
            sync.get("last_sync_description")
        )

    if lock_state is None:
        lock_state = read_overview_lock_state(root, None)

    return {
        "vehicle": vehicle,
        "sync": sync,
        "charge": charge,
        "lock": {
            "state": lock_state,
        },
        "climate": {
            "state": climate_state,
        },
        "odometer_km": odometer_km,
    }


def poll_once(
    sync_if_older_than=DEFAULT_SYNC_IF_OLDER_THAN,
    sync_wait_timeout=DEFAULT_SYNC_WAIT_TIMEOUT,
    stop_after=True,
    cancel_event=None,
    on_vehicle=None,
):
    started = time.time()
    results = []
    errors = []

    try:
        start_app()

        vehicles = load_cache()
        if not vehicles:
            vehicles = list_vehicles()

        # Use the cached/discovered VINs only; no model-specific assumptions.
        for item in vehicles:
            check_cancel(cancel_event)

            vin = item.get("vin")
            if not vin:
                continue

            try:
                vehicle_result = poll_vehicle(
                    vin,
                    sync_if_older_than,
                    sync_wait_timeout,
                    include_details=True,
                    cancel_event=cancel_event,
                )

                results.append(
                    vehicle_result
                )

                # Ein fertiges Fahrzeug sofort weiterreichen.
                # Bei mehreren Fahrzeugen muss dadurch nicht auf den
                # vollständigen Poll gewartet werden.
                if on_vehicle is not None:
                    on_vehicle(
                        vehicle_result
                    )

            except BackgroundCancelled:
                raise

            except Exception as exc:
                errors.append({
                    "vin": vin,
                    "error": str(exc),
                })

        return {
            "ok": not errors,
            "duration_seconds": round(time.time() - started, 3),
            "vehicles": results,
            "errors": errors,
        }

    finally:
        if stop_after:
            stop_app()


def main():
    parser = argparse.ArgumentParser(
        description="VW-App: einen kompletten read-only Status-Poll durchführen"
    )
    parser.add_argument(
        "--sync-if-older-than",
        type=int,
        default=DEFAULT_SYNC_IF_OLDER_THAN,
        metavar="SECONDS",
        help="Manuell synchronisieren, wenn letzter Sync älter ist (0=aus)",
    )
    parser.add_argument(
        "--sync-wait-timeout",
        type=int,
        default=DEFAULT_SYNC_WAIT_TIMEOUT,
        metavar="SECONDS",
        help="Maximal auf Bestätigung einer einmal ausgelösten Synchronisierung warten",
    )
    parser.add_argument(
        "--details",
        action="store_true",
        help="Langsamere Detailwerte wie den konfigurierten Zielladestand mitlesen",
    )
    parser.add_argument(
        "--keep-app-running",
        action="store_true",
        help="VW-App nach dem Poll nicht force-stoppen",
    )
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    try:
        payload = poll_once(
            sync_if_older_than=args.sync_if_older_than,
            sync_wait_timeout=args.sync_wait_timeout,
            stop_after=not args.keep_app_running,
            include_details=args.details,
        )
    except Exception as exc:
        payload = {
            "ok": False,
            "vehicles": [],
            "errors": [{"error": str(exc)}],
        }

    print(json.dumps(
        payload,
        ensure_ascii=False,
        indent=2 if args.pretty else None,
    ))

    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
