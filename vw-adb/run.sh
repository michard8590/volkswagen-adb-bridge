#!/usr/bin/with-contenv bashio
set -e

export HOME=/data
export ANDROID_USER_HOME=/data/.android
export ADB_MDNS=1
export ADB_MDNS_OPENSCREEN=0

mkdir -p /data/.android

bashio::log.info "Starting Volkswagen ADB Bridge..."

# ------------------------------------------------------------
# Home Assistant MQTT service
# ------------------------------------------------------------

export MQTT_HOST="$(bashio::services mqtt 'host')"
export MQTT_PORT="$(bashio::services mqtt 'port')"
export MQTT_USERNAME="$(bashio::services mqtt 'username')"
export MQTT_PASSWORD="$(bashio::services mqtt 'password')"
export MQTT_SSL="$(bashio::services mqtt 'ssl')"

if bashio::var.is_empty "${MQTT_HOST}"; then
    bashio::log.fatal "Home Assistant MQTT service is not available."
    exit 1
fi

bashio::log.info "MQTT service found: ${MQTT_HOST}:${MQTT_PORT}"

# ------------------------------------------------------------
# Volkswagen S-PIN
# ------------------------------------------------------------

SPIN="$(bashio::config 'spin')"
SPIN_FILE="/data/.vw_spin"

if bashio::var.is_empty "${SPIN}"; then
    rm -f "${SPIN_FILE}"
    export VW_SPIN_FILE=""
else
    printf '%s\n' "${SPIN}" > "${SPIN_FILE}"
    chmod 600 "${SPIN_FILE}"
    export VW_SPIN_FILE="${SPIN_FILE}"
fi

unset SPIN

exec python3 /app/broker.py
