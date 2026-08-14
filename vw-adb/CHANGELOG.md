# Changelog

## 0.1.3

- Improve Wireless Debugging pairing diagnostics.
- Retry mDNS pairing-service discovery while a pairing code is configured.
- Report a clear error when the Android pairing dialog is not discoverable.

## 0.1.2

- Add glibc compatibility for the official Android Platform Tools on Alpine.
- Verify ADB during the container build.
- Remove hardcoded version number from startup log.

## 0.1.1

- Fix Home Assistant startup with s6-overlay v3 by disabling Docker init.

## 0.1.0

- Initial experimental Home Assistant release.
- Android ADB connection via USB or Wi-Fi.
- Wireless Debugging mDNS discovery and pairing support.
- Persistent uiautomator2 connection.
- Automatic Volkswagen vehicle discovery.
- Basic vehicle polling.
- Volkswagen synchronization handling.
- Charging start/stop support.
- Target state-of-charge support.
- Climate start/stop and target-temperature support.
- Lock/unlock support where offered by the Volkswagen app.
- Vehicle location and odometer readers.
- Priority UI worker with interruptible background polling.
- Home Assistant MQTT service integration.
