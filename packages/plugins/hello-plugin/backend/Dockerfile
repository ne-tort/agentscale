# syntax=docker/dockerfile:1
# Backend-образ пилотного плагина hello_plugin (эталон v4, §5.1).
#
# Контекст сборки — КОРЕНЬ пакета `packages/plugins/hello-plugin`
# (`docker build -f backend/Dockerfile .`), поэтому COPY путей ниже начинается
# с `backend/` и `migrations/`. Так один Dockerfile видит и backend, и changelog —
# ровно те слои, которые задача 16 упаковывает в единый OCI-артефакт.
#
# Собирается как СЛОЙ OCI-артефакта (backend image + frontend + migrations),
# подписывается cosign по digest, деплоится pin'ом по digest. `:latest` ЗАПРЕЩЁН.
# `imagePullPolicy: IfNotPresent` задаётся НЕ здесь, а в k8s Deployment (§7.4).
#
# Distroless-подобные практики: отдельный build-stage, в финальном слое только
# runtime, без компилятора/пакетного менеджера, non-root UID/GID, read-only rootfs
# совместим, явный HEALTHCHECK на /health.

# ---- Stage 1: сборка зависимостей в виртуальном окружении -------------------
FROM python:3.13-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
COPY backend/requirements.txt ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ---- Stage 2: runtime --------------------------------------------------------
FROM python:3.13-slim AS runtime

# Liquibase + JRE нужны ТОЛЬКО для `liquibase update` в entrypoint (§3).
# Версия зафиксирована, а не latest.
ARG LIQUIBASE_VERSION=4.24.0
ENV LIQUIBASE_HOME=/opt/liquibase \
    PATH="/opt/liquibase:/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LIQUIBASE_ALLOW_EXECUTE_COMMAND=false

RUN apt-get update \
    && apt-get install -y --no-install-recommends default-jre-headless ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /opt/liquibase \
    && curl -fsSL "https://github.com/liquibase/liquibase/releases/download/v${LIQUIBASE_VERSION}/liquibase-${LIQUIBASE_VERSION}.tar.gz" \
        | tar -xz -C /opt/liquibase \
    && chmod +x /opt/liquibase/liquibase \
    && groupadd --gid 10001 plugin \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin plugin

COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY backend/app.py backend/liquibase_url.py backend/entrypoint.sh ./
COPY migrations/changelog.xml /app/migrations/changelog.xml

# chown ДО переключения USER: у non-root нет прав менять владельца.
RUN chmod 0555 /app/entrypoint.sh \
    && chown -R plugin:plugin /app /opt/liquibase

USER 10001:10001
EXPOSE 8080

# /health = 200 только после успешного Liquibase update (entrypoint, §11 кр. 4).
HEALTHCHECK --interval=5s --timeout=3s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).status==200 else 1)"

# Не bash-логика инфраструктуры: контейнерный entrypoint, см. комментарий в файле.
ENTRYPOINT ["/app/entrypoint.sh"]
