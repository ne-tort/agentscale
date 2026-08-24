# Flutter web → nginx (build context = prodavan/ repo root)
#
# Targets:
#   runtime   — full in-image Flutter SDK build (default)
#   from-host — nginx + host `flutter build web` output
#
# Flutter SDK: official Linux tarball (storage.googleapis.com), git clone fallback.

# --- fast local: prebuilt assets on host ---------------------------------
FROM nginx:1.27-alpine AS from-host
COPY apps/flutter/nginx.conf /etc/nginx/conf.d/default.conf
COPY apps/flutter/build/web /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
  CMD wget -qO- http://127.0.0.1/ || exit 1

# --- Flutter SDK download (heavy; keep as its own cached layer) ----------
FROM debian:bookworm-slim AS flutter-sdk

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
       ca-certificates curl git unzip xz-utils \
  && rm -rf /var/lib/apt/lists/*

ENV FLUTTER_HOME=/opt/flutter \
    PUB_CACHE=/root/.pub-cache \
    PATH="/opt/flutter/bin:/opt/flutter/bin/cache/dart-sdk/bin:${PATH}" \
    FLUTTER_STORAGE_BASE_URL=https://storage.googleapis.com \
    # Quiet root warning in containers
    FLUTTER_ROOT=/opt/flutter

# Match apps/flutter Dart SDK (^3.13 → Flutter 3.47.x)
ARG FLUTTER_VERSION=3.47.1

# Layer A: download + extract only (~1.5GB) — survives later RUN fixes
RUN set -eux; \
  url="https://storage.googleapis.com/flutter_infra_release/releases/stable/linux/flutter_linux_${FLUTTER_VERSION}-stable.tar.xz"; \
  mkdir -p /opt /tmp; \
  if curl -fL --retry 8 --retry-all-errors --retry-delay 5 --connect-timeout 30 \
       -o /tmp/flutter.tar.xz "$url"; then \
    tar -xJf /tmp/flutter.tar.xz -C /opt; \
    rm -f /tmp/flutter.tar.xz; \
  else \
    git clone --depth 1 --branch stable https://github.com/flutter/flutter.git "${FLUTTER_HOME}"; \
  fi; \
  test -x "${FLUTTER_HOME}/bin/flutter"

# Layer B: configure (cheap; re-run without re-download)
RUN set -eux; \
  git config --global --add safe.directory "${FLUTTER_HOME}"; \
  git config --global --add safe.directory '*'; \
  flutter config --no-analytics --enable-web; \
  flutter precache --web; \
  flutter --version

# --- app build ------------------------------------------------------------
FROM flutter-sdk AS build

WORKDIR /app

# Copy the whole Flutter package (avoids incomplete `lib/` trees on some
# Windows → Linux build-context transfers of deep paths).
COPY apps/flutter/pubspec.yaml apps/flutter/pubspec.lock apps/flutter/analysis_options.yaml ./

RUN --mount=type=cache,target=/root/.pub-cache \
    flutter pub get

COPY apps/flutter/ ./

ARG API_BASE=http://localhost:8088/api/v1
ARG BUILD_ID=dev
ARG BUILD_NAME=1.0.0
ARG BUILD_NUMBER=0
RUN --mount=type=cache,target=/root/.pub-cache \
    test -f lib/main.dart \
    && flutter build web --release \
         --build-name=${BUILD_NAME} \
         --build-number=${BUILD_NUMBER} \
         --dart-define=API_BASE=${API_BASE} \
         --dart-define=BUILD_ID=${BUILD_ID} \
         --dart-define=BUILD_NAME=${BUILD_NAME} \
         --dart-define=BUILD_NUMBER=${BUILD_NUMBER}

# --- runtime nginx --------------------------------------------------------
FROM nginx:1.27-alpine AS runtime
COPY apps/flutter/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/build/web /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
  CMD wget -qO- http://127.0.0.1/ || exit 1
