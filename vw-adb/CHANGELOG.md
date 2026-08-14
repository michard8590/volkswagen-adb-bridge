# Changelog

## 0.1.24

- Clean up stale Wireless ADB endpoints after a successful reconnect.
- Disconnect only an old endpoint belonging to the same physical Android device.
- Perform stale-endpoint cleanup only after the new ADB and uiautomator2 connection is fully verified.

## 0.1.23

- Add German and English Volkswagen app support for charging controls and status parsing.
- Parse charging details, range, charging state, power and remaining time in both languages.
- Add German and English climate overview status detection.

## 0.1.22

- Handle stale and current Wireless ADB mDNS endpoints for the same Android device.
- Try all matching Wireless ADB endpoints when Android changes its dynamic debugging port.
- Distinguish multiple physical Android devices from multiple ports advertised for one device.

## 0.1.21

- Ensure the Volkswagen app is stopped after a location poll even when the poll is cancelled or fails.
- Parse synchronization timing options defensively as integers.
- Accept both decimal point and decimal comma for temperature MQTT commands.

## 0.1.20

- Keep the Volkswagen app open when a location poll immediately follows a vehicle poll.
- Read the currently active vehicle location first to avoid an unnecessary vehicle switch.
- Stop the Volkswagen app after the combined status and location polling sequence.

## 0.1.19

- Recreate existing MQTT location trackers to remove stale inherited state topics.
- Fix Home Assistant vehicle trackers remaining in unknown state despite valid GPS coordinates.
- Poll the currently active Volkswagen vehicle location first to avoid unnecessary vehicle switching.

## 0.1.18

- Fix MQTT GPS device trackers remaining in unknown state.
- Stop sharing the vehicle state topic with every Home Assistant component.
- Assign vehicle state topics explicitly only to components that require them.
- Allow location trackers to derive their state directly from GPS coordinates and Home Assistant zones.

## 0.1.17

- Retry normal Wireless ADB mDNS discovery during add-on startup.
- Improve reconnection when Android changes the Wireless Debugging port.
- Do not treat a stored pairing code as a request to pair again when no pairing service is active.
- Fix cancellation handling in the location poll.

## 0.1.16

- Run the first vehicle location poll immediately after add-on startup.
- Keep subsequent location updates on the configured location polling interval.

## 0.1.15

- Add native Home Assistant MQTT device trackers for vehicle locations.
- Publish Volkswagen vehicle latitude and longitude through retained MQTT topics.
- Use Home Assistant zone matching for GPS vehicle locations.
- Keep location polling separate from normal vehicle status polling.
- Make location polling cancellable by higher-priority user commands.
- Isolate location errors so one vehicle cannot stop location updates for other vehicles.
- Keep GPS accuracy unset when the Volkswagen app does not provide a real value.

## 0.1.14

- Remove redundant read-only entities replaced by native Home Assistant controls.
- Remove the separate charging-state sensor in favor of the charging switch.
- Remove the separate climate-state sensor in favor of the climate entity.
- Remove the separate target-SOC sensor in favor of the target-charge number.
- Remove the separate lock-state sensor when native remote Lock/Unlock is supported.
- Keep the read-only lock-state sensor for vehicles without remote Lock/Unlock support.
- Add a Home Assistant MQTT Device Discovery migration for obsolete entities.

## 0.1.13

- Detect remote Lock/Unlock capability separately for every vehicle.
- Add a native Home Assistant lock entity only for supported vehicles.
- Keep lock state available for vehicles without remote Lock/Unlock support.
- Publish verified lock and unlock changes to Home Assistant immediately.
- Do not publish an unconfirmed final state while a remote lock command is pending.
- Keep the S-PIN local to the add-on and out of MQTT command payloads.

## 0.1.12

- Fix climate target temperature missing from published vehicle state.
- Retry opening the Volkswagen vehicle list once after a transient UI failure.
- Improve robustness when polling multiple vehicles.

## 0.1.11

- Add a native Home Assistant climate entity for every vehicle.
- Add remote climate start and stop control from Home Assistant.
- Add climate target-temperature control in 0.5 °C steps.
- Include climate target temperature in full vehicle polling.
- Publish verified climate state changes to Home Assistant immediately.
- Publish verified climate temperature changes to Home Assistant immediately.
- Keep the full vehicle poll as final command-state verification.

## 0.1.10

- Publish verified charging start/stop changes to Home Assistant immediately.
- Merge confirmed charging data into the latest MQTT vehicle state.
- Keep the full vehicle poll as final command-state verification.

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
