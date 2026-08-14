# Changelog

## 0.1.9

- Simplify vehicle polling to one full poll cycle.
- Remove the separate basic and detail poll schedulers.
- Always include target state of charge and odometer in regular polling.
- Remove detail-value cache handling and the detail poll interval option.
- Refresh the complete vehicle state after user commands.
- Publish verified target-SOC changes to Home Assistant immediately.

## 0.1.8

- Add Home Assistant target charge control.
- Add Home Assistant charging start/stop switch.
- Use confirmed vehicle state instead of optimistic charging state.
- Trigger an immediate detail refresh after changing target SOC.

## 0.1.7

- Publish each vehicle to Home Assistant immediately after it has been polled.
- Avoid waiting for all vehicles before updating MQTT entities.
- Preserve detailed target-SOC and odometer values across basic polls.

## 0.1.6

- Add scheduled detailed vehicle polling.
- Publish target state of charge and odometer to Home Assistant.
- Trigger an immediate status refresh after MQTT commands.

## 0.1.5

- Add Home Assistant MQTT Device Discovery.
- Create one Home Assistant device per discovered Volkswagen vehicle.
- Add battery SOC, range, charging state, lock state, climate state,
  last synchronization and synchronization age entities.
- Publish retained MQTT state separately for every vehicle.

## 0.1.4

- Reuse an existing Wireless Debugging authorization before attempting pairing.
- Prevent a retained pairing-code option from forcing unnecessary re-pairing.
- Expand the Wireless Debugging pairing documentation.

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
