FROM ghcr.io/home-assistant/base:latest

ARG BUILD_VERSION
ARG BUILD_ARCH

LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}"

RUN apk add --no-cache \
        python3 \
        py3-pip \
        curl \
        unzip \
        libstdc++ \
        gcompat \
    && curl -fsSL \
        https://dl.google.com/android/repository/platform-tools_r37.0.0-linux.zip \
        -o /tmp/platform-tools.zip \
    && unzip /tmp/platform-tools.zip -d /opt \
    && ln -s /opt/platform-tools/adb /usr/local/bin/adb \
    && rm -f /tmp/platform-tools.zip \
    && adb version

COPY requirements.txt /requirements.txt

RUN pip3 install \
        --no-cache-dir \
        --break-system-packages \
        -r /requirements.txt

COPY run.sh /run.sh
COPY app /app

RUN chmod a+x /run.sh

CMD ["/run.sh"]
