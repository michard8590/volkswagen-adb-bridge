# Volkswagen ADB Bridge

## Requirements

You need:

- an Android phone with the official Volkswagen app installed
- a logged-in Volkswagen account in the app
- Android USB debugging or Wireless Debugging
- MQTT configured in Home Assistant

MQTT connection details are obtained automatically from Home Assistant.

## Recommended setup

Wireless Debugging is the recommended setup when the Android phone remains
near the Home Assistant system.

Set:

- `connection_mode`: `wifi`
- `adb_device_serial`: physical Android device serial
- `wifi.autodiscovery`: `true`

The current Wireless Debugging connect port is discovered automatically using
mDNS.

## First Wi-Fi pairing

On Android:

1. Enable Developer options.
2. Enable Wireless debugging.
3. Open **Pair device with pairing code**.
4. Enter the displayed six-digit code into `wifi.pairing.code`.
5. Start the app.

With automatic discovery enabled, pairing IP address and port normally do not
need to be entered manually.

After successful pairing the stored ADB key is retained in `/data/.android`.

The pairing code is temporary and can be cleared from the configuration after
pairing.

## Volkswagen S-PIN

`spin` is optional and required only for vehicle functions which request the
Volkswagen S-PIN, such as remote lock/unlock on supported vehicles.

The PIN is not sent through MQTT.

## Polling

- `poll_interval`: normal status poll
- `detail_poll_interval`: detailed-data interval
- `location_poll_interval`: location interval
- `sync_if_older_than`: request Volkswagen synchronization when data is older
  than this number of seconds
- `sync_wait_timeout`: maximum synchronization confirmation wait

Background polling is lower priority than user commands. A user command can
interrupt a safe read/wait stage of a poll.

Once a remote vehicle command has actually been submitted, it is never
automatically sent a second time.

## Supported architecture

Version 0.1.x currently supports `amd64`.

## Development status

This software is experimental. Vehicle capabilities differ by model, account,
country and Volkswagen app version.
