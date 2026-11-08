# =============================================================================
# Базовый ИНФРАСТРУКТУРНЫЙ образ tenant-PostgreSQL с pgaudit
# (дизайн v4, §2.1 / §14 п.2; миграция, задача 16).
#
# ЗАЧЕМ ОН НУЖЕН (находка задачи 14). ghcr.io/ne-tort/postgres:16.15 — зеркало
# ванильного official-образа, расширения pgaudit в нём НЕТ. В tenant-postgres.yaml явно
# задан `shared_preload_libraries=pgaudit`, поэтому без этого образа сервер
# упадёт в FATAL на старте. Это НАМЕРЕННЫЙ fail-closed: аудит DDL/DML НЕ
# отключается «тихо», если библиотека не установлена.
#
# ВАЖНО ПО ПОСТАВКЕ. Образ собирается и пушится НАШИМ workflow
# `.github/workflows/plugin-base-images.yml` в ghcr с provenance: true и
# sbom: true. В проде/k3s НЕ тянется напрямую с Docker Hub: нода тянет ТОЛЬКО
# наш ghcr-образ, запиненный по digest (таблица и обоснование — в README.md
# этого каталога). Docker Hub здесь используется лишь как ИСТОЧНИК БАЗЫ ПРИ
# СБОРКЕ на self-hosted раннере: ci-images.yml уже делает
# `docker run ... postgres:16.15` в smoke-тесте на той же VM, значит Hub
# оттуда доступен. Зеркало ghcr.io/ne-tort/postgres:16.15 намеренно НЕ
# перезаписываем — им пользуется платформенный инстанс
# (infra/k3s/overlays/dev/postgres.yaml, userdb-init.yaml).
# =============================================================================

# Точная версия + ЖЁСТКИЙ пин по digest (п.5/п.6 задачи). Проверено через
# Docker Hub API 2026-11-08: library/postgres:16.15, multi-arch manifest-list.
# НЕ `:latest`, НЕ плавающий `16`, НЕ `16-alpine` (у alpine другой dpkg/пути).
# Тег 16.15 может быть перезалит (last_pushed уже обновлялся), digest — нет.
ARG PG_BASE_IMAGE=postgres:16.15@sha256:f9a7b9ebf6f56201f949db745e47bc423c0f0f8294bafe991cc10bf74b029657
FROM ${PG_BASE_IMAGE}

# -----------------------------------------------------------------------------
# Версии пакетов (fail-closed).
# pgaudit 16.0 — релиз для ветки PostgreSQL 16 (с PG16 pgaudit перешёл на
# нумерацию по major-версии сервера). Из PGDG для Debian 12 (bookworm) ставим
# `postgresql-16-pgaudit` вида 16.0-N.pgdg120+M: точный Debian-revision
# узнаём из apt-cache и ЖЁСТКО подставляем в apt-get install. Если PGDG
# откатил/обновил сборку 16.0 — сборка образа ПАДАЕТ, а не молча ставит
# другую версию аудита. Точная строка версии попадает в SBOM/provenance.
# -----------------------------------------------------------------------------
# ТОЧНАЯ версия пакета из PGDG (не плавающий тег). 2026-11-08: база
# postgres:16.15 уже содержит репозиторий PGDG для trixie, где доступен
# `postgresql-16-pgaudit | 16.1-2.pgdg13+1`; версии `16.0-*` там нет
# (первый прогон CI честно упал с FATAL). Bump осознанный: пин остаётся
# жёстким, при смене ревизии PGDG сборка снова упадёт и потребует решения.
ARG PGAUDIT_VERSION=16.1
ARG PGDG_KEY_URL=https://www.postgresql.org/media/keys/ACCC4CF8.asc
ARG PGDG_REPO_URL=https://apt.postgresql.org/pub/repos/apt
ARG PGDG_SUITE=bookworm-pgdg

LABEL org.opencontainers.image.title="agentscale-plugin-base-postgres" \
      org.opencontainers.image.description="PostgreSQL 16.15 + pgaudit 16.0 (PGDG), non-root runtime" \
      org.opencontainers.image.source="https://github.com/ne-tort/agentscale" \
      org.opencontainers.image.base.name="postgres:16.15@sha256:f9a7b9ebf6f56201f949db745e47bc423c0f0f8294bafe991cc10bf74b029657"

# Сборка идёт от root (установка пакетов), в runtime ЯВНО уходим на postgres.
USER root

# Официальный postgres:16.15 УЖЕ содержит репозиторий PGDG (из него установлен
# сам сервер). Повторно добавлять тот же deb-источник с другим signed-by НЕЛЬЗЯ:
# apt ответит "Conflicting values set for option Signed-By". Поэтому сначала
# проверяем наличие PGDG и добавляем его только если база его не содержит.
RUN set -eux; \
    export DEBIAN_FRONTEND=noninteractive; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg; \
    PGDG_CONFIGURED=0; \
    for f in /etc/apt/sources.list.d/pgdg.list /etc/apt/sources.list.d/pgdg.sources /etc/apt/sources.list; do \
      if [ -f "$f" ] && grep -q 'apt.postgresql.org' "$f"; then PGDG_CONFIGURED=1; fi; \
    done; \
    if [ "$PGDG_CONFIGURED" -eq 0 ]; then \
      install -d -m 0755 /usr/share/postgresql-common/pgdg; \
      curl -fsSL "${PGDG_KEY_URL}" -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc; \
      echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] ${PGDG_REPO_URL} ${PGDG_SUITE} main" > /etc/apt/sources.list.d/pgdg.list; \
    fi; \
    apt-get update; \
    PKG_VER="$(apt-cache madison postgresql-16-pgaudit | awk '{print $3}' | grep -F "${PGAUDIT_VERSION}-" | head -n 1)"; \
    if [ -z "${PKG_VER}" ]; then \
      echo "FATAL: PGDG не отдаёт postgresql-16-pgaudit версии ${PGAUDIT_VERSION}*" >&2; \
      apt-cache madison postgresql-16-pgaudit >&2 || true; \
      exit 1; \
    fi; \
    echo "PIN: postgresql-16-pgaudit=${PKG_VER}"; \
    apt-get install -y --no-install-recommends "postgresql-16-pgaudit=${PKG_VER}"; \
    test -f /usr/lib/postgresql/16/lib/pgaudit.so; \
    dpkg-query -W postgresql-16-pgaudit; \
    apt-get purge -y --auto-remove curl gnupg; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# Bootstrap ACL (п.2 задачи). Официальный docker-entrypoint.sh исполняет *.sql
# из этого каталога ОДИН РАЗ при пустом PGDATA, от имени POSTGRES_USER.
# Префикс 01- гарантирует, что REVOKE ... FROM PUBLIC (включая template1)
# произойдёт ДО появления первых tenant_* БД.
# -----------------------------------------------------------------------------
COPY --chown=postgres:postgres 01-bootstrap.sql /docker-entrypoint-initdb.d/01-bootstrap.sql

# -----------------------------------------------------------------------------
# Эталоны сетевой политики (п.3 задачи). В образ НЕ кладётся активный
# /etc/postgresql/pg_hba.conf: в k3s его приносит ConfigMap
# `tenant-postgres-config` (задача 14), смонтированный КАТАЛОГОМ в
# /etc/postgresql, а Deployment передаёт
#   -c hba_file=/etc/postgresql/pg_hba.conf
#   -c ident_file=/etc/postgresql/pg_ident.conf
# Если ConfigMap не смонтирован и hba_file указан — postgres НЕ стартует
# (fail-closed). pg_ident.conf и tenant_hba.conf — ПУСТЫЕ заглушки: ни одного
# разрешения до первого grant, пары задаются полями DATABASE/USER.
# -----------------------------------------------------------------------------
COPY --chown=postgres:postgres pg_hba.conf.template /etc/postgresql/pg_hba.conf.template
COPY --chown=postgres:postgres pg_ident.conf         /etc/postgresql/pg_ident.conf
COPY --chown=postgres:postgres tenant_hba.conf       /etc/postgresql/tenant_hba.conf

# -----------------------------------------------------------------------------
# pgaudit НЕ прописывается в postgresql.conf образа: shared_preload_libraries
# и pgaudit.log=* передаёт Deployment через `-c` (задача 14). Причина:
# bootstrap-сервер официального entrypoint стартует ДО загрузки pgaudit, и
# ALTER SYSTEM SET pgaudit.* на нём был бы неразрешённым параметром.
# -----------------------------------------------------------------------------

# NON-ROOT RUNTIME. Официальный образ сам не задаёт USER (его entrypoint
# дропает привилегии через gosu, будучи root). Мы задаём USER ЯВНО: при
# старте entrypoint увидит не-root и НЕ будет делать chown PGDATA. В k3s это
# работает потому, что securityContext.fsGroup: 999 делает PVC (и PGDATA
# /var/lib/postgresql/data/pgdata) доступным для uid/gid 999 пользователя
# postgres. См. infra/k3s/base/plugins/tenant-postgres.yaml.
USER postgres:postgres

EXPOSE 5432

# ЯВНЫЙ HEALTHCHECK (shell-форма Dockerfile, без CMD-SHELL — это синтаксис
# Compose, а не Dockerfile). pg_isready ходит по unix-сокету (в pg_hba для
# local разрешён scram-sha-256), поэтому не зависит от pod-сетей и reject-all;
# пароль не нужен: сам факт ответа сервера = «принимает соединения». Вторая
# проверка — наличие pgaudit.so: если библиотеку выкинули из образа, контейнер
# станет unhealthy СРАЗУ, а не упадёт в FATAL при первом же
# `-c shared_preload_libraries=pgaudit` в k3s. Факт ЗАГРУЗКИ pgaudit
# проверяется отдельным SQL-чеком из README.md.
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=5 \
    CMD pg_isready -q && test -f /usr/lib/postgresql/16/lib/pgaudit.so

# ENTRYPOINT/CMD наследуются от official-образа (docker-entrypoint.sh +
# postgres). Deployment задачи 14 переопределяет command, добавляя `-c ...`.
