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

Wireless Debugging requires a one-time pairing between the Android phone and
Volkswagen ADB Bridge.

### Step 1: Prepare Android

On the Android phone:

1. Open **Settings → Developer options → Wireless debugging**.
2. Enable **Wireless debugging**.
3. Open **Pair device with pairing code**.
4. Keep this dialog open.

Android now displays a temporary six-digit pairing code.

### Step 2: Configure Volkswagen ADB Bridge

In the Home Assistant app configuration:

- set `connection_mode` to `auto` or `wifi`
- enable `wifi.autodiscovery`
- enter the six-digit Android code into `wifi.pairing.code`
- normally leave `wifi.pairing.host` empty
- normally leave `wifi.pairing.port` at `0`

With Auto Discovery enabled, Volkswagen ADB Bridge discovers the temporary
Android pairing IP address and port automatically using mDNS.

### Step 3: Start the app

Start Volkswagen ADB Bridge while the Android **Pair device with pairing
code** dialog is still open.

A successful first connection contains a log entry similar to:

    WLAN-ADB-Pairing erfolgreich.

After pairing, the bridge stores its ADB key persistently in
`/data/.android`.

### Later starts

Pairing is normally required only once.

On later starts Volkswagen ADB Bridge first tries the existing ADB
authorization. It does not pair again when the existing authorization still
works.

The Android Wireless Debugging connect port may change. The bridge discovers
the current port automatically using mDNS.

### Pairing again

Repeat the pairing process if:

- Android's Wireless Debugging authorization was revoked,
- the paired device was removed in Android,
- the Home Assistant app data was deleted,
- or the persistent ADB key was otherwise lost.

The pairing code is temporary. Never reuse an old Android pairing code.


## Volkswagen S-PIN

`spin` is optional and required only for vehicle functions which request the
Volkswagen S-PIN, such as remote lock/unlock on supported vehicles.

The PIN is not sent through MQTT.

## Polling

- `poll_interval`: normal status poll
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
