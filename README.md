# Volkswagen ADB Bridge for Home Assistant

Experimental Home Assistant app for reading and controlling supported
Volkswagen vehicles through the official Volkswagen Android app.

The bridge controls the normal Volkswagen Android app through ADB and
uiautomator2. It does not require direct access to Volkswagen private APIs.

## Status

Experimental / early development.

Currently tested with selected Volkswagen ID vehicles.

## Requirements

- Home Assistant with Supervisor / Apps support
- MQTT service available in Home Assistant
- Android phone with the official Volkswagen app installed and logged in
- USB debugging or Android Wireless Debugging
- amd64 Home Assistant host

## Connection

Supported Android connection modes:

- automatic
- Wi-Fi / Wireless Debugging with mDNS discovery
- USB

Wireless Debugging ports may change. The bridge discovers the current ADB
connect port automatically.

## Security

The Volkswagen S-PIN is stored in the Home Assistant app configuration and
written only to the app's persistent `/data` directory with restrictive file
permissions.

The S-PIN is never accepted through MQTT.

## Architecture

Version 0.1.x currently supports amd64 only because Google's official Linux
Android Platform Tools package used by this project is x86-64.

## Disclaimer

This project is not affiliated with or endorsed by Volkswagen AG.

Volkswagen and related trademarks belong to their respective owners.

## License

This project is licensed under the GNU General Public License v3.0 or later
(`GPL-3.0-or-later`).

See [LICENSE](LICENSE) for the full license text.
