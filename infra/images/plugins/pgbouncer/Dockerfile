# =============================================================================
# Базовый ИНФРАСТРУКТУРНЫЙ образ PgBouncer для tenant-кластера
# (дизайн v4, §2.1, §14 п.2; миграция, задача 16, п.4).
#
# ОДИН ПУЛ НА TENANT-КЛАСТЕР, не N пулов на компанию: single-node k3s
# (ResourceQuota pods: 45, requests.memory: 12Gi, три OOM-инцидента). N
# компаний = N БД `tenant_<company>` на ОДНОМ PostgreSQL, но ОДНА запись
# `[databases] *` и ОДИН под PgBouncer (см. pgbouncer.yaml задачи 14).
#
# ВАЖНО ПО ПОСТАВКЕ. Собирается и пушится НАШИМ workflow
# `.github/workflows/plugin-base-images.yml` в ghcr (provenance + SBOM).
# В k3s НЕ тянется напрямую с Docker Hub: нода тянет наш ghcr-образ,
# запиненный по digest. Docker Hub здесь — только источник базы при сборке на
# self-hosted раннере. Имя `ghcr.io/ne-tort/plugin-base-pgbouncer` выбрано,
# чтобы НЕ затереть существующее зеркало `ghcr.io/ne-tort/pgbouncer:1.24.1`.
# =============================================================================

# ЖЁСТКАЯ ФИКСАЦИЯ БАЗЫ ПО DIGEST (п.5/п.6 задачи, отсюда же берётся пин для
# остальных). `pgbouncer/pgbouncer` — ОФИЦИАЛЬНЫЙ Docker Official Image
# (НЕ чей-то форк): multi-arch manifest-list, проверен через Docker Hub API
# 2026-11-08, tag 1.24.1, last_pushed 2026-09-16. Тег один и тот же может
# быть перезалит, digest — нет. Без SCRAM (libpq) `auth_type=scram-sha-256`
# невозможен, поэтому базу НЕ заменяем на alpine/другую без проверки.
ARG PGBOUNCER_BASE_IMAGE=pgbouncer/pgbouncer:1.24.1@sha256:3d6fbf93c72009fc9ecb3e4b9957974ffca3e66cc2641fc85df8fbc2429bb2ba
FROM ${PGBOUNCER_BASE_IMAGE}

LABEL org.opencontainers.image.title="agentscale-plugin-base-pgbouncer" \
      org.opencontainers.image.description="PgBouncer 1.24.1, transaction mode, scram-sha-256, non-root" \
      org.opencontainers.image.source="https://github.com/ne-tort/agentscale" \
      org.opencontainers.image.base.name="pgbouncer/pgbouncer:1.24.1@sha256:3d6fbf93c72009fc9ecb3e4b9957974ffca3e66cc2641fc85df8fbc2429bb2ba"

# -----------------------------------------------------------------------------
# NON-ROOT. Официальный образ запускает процесс под непривилегированным
# пользователем `pgbouncer` (uid 70 / gid 70). Мы задаём USER ЯВНО, чтобы это
# было зафиксировано в НАШЕМ образе, а не унаследовано неявно. Если в базе
# пользователя нет — сборка УПАДЁТ на `id -u` (fail-closed), а не тихо уедет
# под root. Каталоги доступны на чтение всем: конфиг НЕ секретный, а
# userlist.txt в k3s монтируется из Secret только на чтение. Паролей в образе
# НЕТ (пустая заглушка auth_file = deny all).
# -----------------------------------------------------------------------------
USER root

RUN set -eux; \
    id -u pgbouncer >/dev/null; \
    id -g pgbouncer >/dev/null; \
    pgbouncer --version; \
    install -d -m 0755 /etc/pgbouncer /etc/pgbouncer/auth; \
    mkdir -p /var/log/pgbouncer /var/run/pgbouncer; \
    chown -R pgbouncer:pgbouncer /var/log/pgbouncer /var/run/pgbouncer /etc/pgbouncer/auth

# -----------------------------------------------------------------------------
# ДЕФОЛТНЫЙ конфиг образа. В k3s его ПЕРЕКРЫВАЕТ ConfigMap
# `pgbouncer-config` (ключ pgbouncer.ini, subPath): Deployment задачи 14 явно
# запускает `pgbouncer /etc/pgbouncer/config/pgbouncer.ini`. Конфиг в образе
# нужен как ЭТАЛОН и как fail-closed дефолт: даже без ConfigMap PgBouncer
# стартует в transaction mode с ЗАКРЫТЫМ пустым auth_file (ни одна роль не
# пройдёт аутентификацию), а не с trust/md5.
#
# `auth_file` ТОЛЬКО из Secret `pgbouncer-credentials` (ключ userlist.txt),
# смонтированного КАТАЛОГОМ БЕЗ subPath в /etc/pgbouncer/auth в k3s.
# `auth_query` / `auth_user` НАМЕРЕННО ОТСУТСТВУЮТ: они заставляли бы
# PgBouncer читать pg_shadow на backend (нужна суперпользовательская роль) и
# размывали бы границу. Известны ТОЛЬКО роли из userlist.txt.
# -----------------------------------------------------------------------------
COPY --chown=pgbouncer:pgbouncer pgbouncer.ini /etc/pgbouncer/pgbouncer.ini

RUN set -eux; \
    printf '# ПУСТОЙ auth_file по умолчанию = deny all (fail-closed).\n# В k3s монтируется Secret pgbouncer-credentials/userlist.txt.\n' \
      > /etc/pgbouncer/auth/userlist.txt; \
    chown pgbouncer:pgbouncer /etc/pgbouncer/auth/userlist.txt; \
    chmod 0640 /etc/pgbouncer/auth/userlist.txt

USER pgbouncer:pgbouncer

EXPOSE 6432

# HEALTHCHECK: проверяем, что процесс жив и слушает :6432. TCP-порт открыт
# даже при пустом allowlist: доступность ≠ авторизация. Проверка SHOW POOLS
# требует пароля из userlist и в образ его не вложить — она вынесена в
# чек-лист README.md (п.8 задачи).
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=5 \
    CMD ["CMD-SHELL", "set -eu; exec 3<>/dev/tcp/127.0.0.1/6432 || exit 1; exec 3<&-; exec 3>&-"]

ENTRYPOINT ["pgbouncer"]
CMD ["/etc/pgbouncer/pgbouncer.ini"]
