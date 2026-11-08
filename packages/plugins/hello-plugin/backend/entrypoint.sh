#!/bin/sh
# Контейнерный ENTRYPOINT пилотного плагина hello_plugin.
#
# ВАЖНО (AGENTS.md, «Принципы инфраструктуры», п.1–2): этот файл НЕ является
# хостовым .sh-скриптом для управления инфраструктурой и не делает ничего с k8s,
# БД платформы или GitOps-состоянием. Это обычный entrypoint ВНУТРИ образа
# плагина: он запускает self-managed миграции в СВОЕЙ БД компании и затем
# передаёт управление процессу приложения. Вся «логика» разбора DSN вынесена
# в Python-модуль liquibase_url.py (контейнерная Python-логика, не bash-парсинг);
# shell здесь только связывает две команды и обеспечивает fail-closed.
#
# Порядок (§3.1, §5.1 шаг 4, §11 критерий 4):
#   liquibase update  ->  DATABASECHANGELOG в схеме плагина  ->  /health Ready
# При любой ошибке миграции (в т.ч. checksum mismatch) `set -e` завершает
# контейнер: readinessProbe НЕ получит 200, приложение не запускается.
set -eu

# Fail-closed: все обязательные значения приходят из k8s Secret, без дефолтов.
: "${GATEWAY_URL:?GATEWAY_URL не задан в Secret}"
: "${PLUGIN_ID:?PLUGIN_ID не задан в Secret}"
: "${TENANT_DB_DSN:?TENANT_DB_DSN не задан в Secret}"
: "${PLUGIN_CLAIMS:?PLUGIN_CLAIMS не задан в Secret}"

# JDBC-URL считает Python-хелпер (секрет не попадает в аргументы процесса и логи:
# значение попадает в переменную окружения дочернего liquibase через export).
LIQUIBASE_COMMAND_URL="$(python -c 'from liquibase_url import jdbc_url; print(jdbc_url())')"
export LIQUIBASE_COMMAND_URL

export LIQUIBASE_HOME=/opt/liquibase
export LIQUIBASE_COMMAND_CHANGELOG_FILE=/app/migrations/changelog.xml
# §3.2: shell-команды из changelog запрещены.
export LIQUIBASE_ALLOW_EXECUTE_COMMAND=false
# Не/debug: Liquibase не должен печатать URL с credentials в логи пода.
export LIQUIBASE_LOG_LEVEL=WARNING

liquibase \
  --url="$LIQUIBASE_COMMAND_URL" \
  --changelog-file="$LIQUIBASE_COMMAND_CHANGELOG_FILE" \
  update

# Только после успешного update помечаем готовность для /health.
export LIQUIBASE_UPDATE_OK=1

# exec: PID 1 = uvicorn, корректная доставка SIGTERM от kubelet (graceful wake/idle).
# app.py — обычный модуль с FastAPI-приложением `app`; uvicorn грузит его как
# `app:app`. Порт 8080 из manifest.backend.port; секретов в командной строке нет.
exec python -m uvicorn app:app --host 0.0.0.0 --port "${PLUGIN_PORT:-8080}" --no-access-log
