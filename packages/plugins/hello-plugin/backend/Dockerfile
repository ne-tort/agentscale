# Backend-образ пилотного плагина hello_plugin.
# Контекст сборки: packages/plugins/hello-plugin.
#
# Обязательные supply-chain inputs (без небезопасных defaults):
#   PYTHON_BASE_IMAGE=python:3.13-slim@sha256:<проверенный digest>
#   LIQUIBASE_SHA256=<SHA256 liquibase-4.24.0.tar.gz>
#   backend/requirements.lock — полный direct+transitive lock с хешами.
# В репозитории нет проверенных digest/SHA256 и полного lock, поэтому образ
# намеренно НЕ собирается до передачи этих inputs платформенным владельцем CI.
# Mutable syntax directive и tag-only base здесь запрещены.

ARG PYTHON_BASE_IMAGE
FROM ${PYTHON_BASE_IMAGE} AS python-base
ARG PYTHON_BASE_IMAGE
RUN set -eu; \
    if ! printf '%s\n' "${PYTHON_BASE_IMAGE:-}" | grep -Eq '^(python|ghcr.io/ne-tort/python):3\.13-slim@sha256:[0-9a-f]{64}$'; then \
        printf '%s\n' 'FATAL: PYTHON_BASE_IMAGE must be python or ghcr.io/ne-tort/python:3.13-slim@sha256:<64 lowercase hex>.' >&2; \
        exit 1; \
    fi; \
    # The CI VM answers AAAA but has no usable IPv6 egress. BuildKit RUN steps
    # read THIS image's /etc/gai.conf, not the host's, so set the same IPv4
    # preference here, before the first networked RUN. getaddrinfo(3) ordering
    # only; IPv6 stays enabled. Guarded append: an existing rule is kept as-is.
    if ! grep -Eq '^[[:space:]]*precedence[[:space:]]+::ffff:0:0/96[[:space:]]+100' /etc/gai.conf 2>/dev/null; then \
        printf '%s\n' 'precedence ::ffff:0:0/96 100' >> /etc/gai.conf; \
    fi

# ---- Python dependencies: только полный воспроизводимый lock -----------------
FROM python-base AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /build
# requirements.txt — direct manifest; requirements.lock появится в этом COPY,
# когда будет получен из проверенного полного resolution. Не создавать его вручную.
COPY backend/requirements* ./
RUN set -eu; \
    if [ ! -f requirements.lock ]; then \
        printf '%s\n' 'FATAL: backend/requirements.lock is required; installing from requirements.txt is forbidden.' >&2; \
        printf '%s\n' 'Provide a reviewed direct+transitive CPython 3.13 lock with an exact version and verified SHA256 for every artifact.' >&2; \
        exit 1; \
    fi; \
    python -m venv /opt/venv; \
    /opt/venv/bin/pip install --no-cache-dir --require-hashes --no-deps -r requirements.lock

# ---- Liquibase: download и SHA256 проверка ДО распаковки ---------------------
FROM python-base AS liquibase
ARG LIQUIBASE_VERSION=4.24.0
ARG LIQUIBASE_SHA256
RUN set -eu; \
    if [ "${LIQUIBASE_VERSION}" != '4.24.0' ]; then \
        printf '%s\n' 'FATAL: LIQUIBASE_VERSION is pinned to 4.24.0; bump only with a newly verified SHA256.' >&2; \
        exit 1; \
    fi; \
    if ! printf '%s\n' "${LIQUIBASE_SHA256:-}" | grep -Eq '^[0-9a-f]{64}$'; then \
        printf '%s\n' 'FATAL: LIQUIBASE_SHA256 must contain the verified lowercase SHA256 of liquibase-4.24.0.tar.gz.' >&2; \
        exit 1; \
    fi; \
    # The VM can reach the Debian CDN over HTTPS but not plain HTTP.
    # Convert debian.org URLs before any package operation instead of failing
    # on port 80 with 'connection timed out'.
    for file in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do \
        if [ -f "$file" ]; then \
            sed -i 's|http://deb.debian.org|https://deb.debian.org|g' "$file"; \
        fi; \
    done; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl; \
    rm -rf /var/lib/apt/lists/*; \
    curl -fsSL --proto '=https' --tlsv1.2 \
      "https://github.com/liquibase/liquibase/releases/download/v${LIQUIBASE_VERSION}/liquibase-${LIQUIBASE_VERSION}.tar.gz" \
      -o /tmp/liquibase.tar.gz; \
    printf '%s  %s\n' "${LIQUIBASE_SHA256}" /tmp/liquibase.tar.gz | sha256sum -c -; \
    mkdir -p /opt/liquibase; \
    tar -xzf /tmp/liquibase.tar.gz -C /opt/liquibase; \
    rm -f /tmp/liquibase.tar.gz; \
    chmod 0555 /opt/liquibase/liquibase

# ---- Runtime: non-root, без curl, root-owned read-only application files ------
FROM python-base AS runtime
ENV LIQUIBASE_HOME=/opt/liquibase \
    PATH="/opt/liquibase:/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LIQUIBASE_ALLOW_EXECUTE_COMMAND=false \
    HOME=/tmp \
    TMPDIR=/tmp

# JRE нужен только Liquibase. /bin/sh необходим контейнерному entrypoint;
# curl и другие download tools в runtime не копируются и не устанавливаются.
RUN set -eu; \
    for file in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do \
        if [ -f "$file" ]; then \
            sed -i 's|http://deb.debian.org|https://deb.debian.org|g' "$file"; \
        fi; \
    done; \
    apt-get update; \
    apt-get install -y --no-install-recommends default-jre-headless ca-certificates; \
    rm -rf /var/lib/apt/lists/*; \
    groupadd --gid 10001 plugin; \
    useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp --shell /usr/sbin/nologin plugin; \
    chmod 1777 /tmp

COPY --from=builder /opt/venv /opt/venv
COPY --from=liquibase /opt/liquibase /opt/liquibase
WORKDIR /app
COPY backend/app.py backend/runtime_api.py backend/liquibase_url.py backend/entrypoint.sh ./
COPY migrations/changelog.xml /app/migrations/changelog.xml

# Файлы принадлежат root и недоступны на запись UID 10001 даже без readOnlyRootFilesystem.
RUN chmod 0555 /app/entrypoint.sh; \
    chmod -R a-w /app /opt/liquibase /opt/venv

USER 10001:10001
EXPOSE 8080

# Совместимость: readOnlyRootFilesystem=true + /tmp как emptyDir. /health=200
# только после успешного Liquibase update в entrypoint.
HEALTHCHECK --interval=5s --timeout=3s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).status==200 else 1)"]

ENTRYPOINT ["/app/entrypoint.sh"]
