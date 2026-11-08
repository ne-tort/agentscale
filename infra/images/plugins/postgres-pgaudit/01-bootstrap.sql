-- =============================================================================
-- Bootstrap ACL tenant-PostgreSQL (дизайн v4, §2.1, §14 п.2; задача 16).
--
-- Исполняется официальным docker-entrypoint.sh ОДИН РАЗ, при ПУСТОМ PGDATA,
-- из /docker-entrypoint-initdb.d/, от имени POSTGRES_USER (superuser кластера).
-- Префикс 01- гарантирует, что закрытый ACL будет у template1 ДО того, как
-- задача 05 создаст первую БД tenant_<company>.
--
-- ПОЧЕМУ ЭТО ВООБЩЕ НУЖНО. PostgreSQL по умолчанию выдаёт CONNECT и
-- TEMPORARY на ЛЮБУЮ БД роли PUBLIC (acldefault, копируется из template1 при
-- CREATE DATABASE). Поэтому `GRANT CONNECT ... TO <роль плагина>` САМ ПО СЕБЕ
-- НЕ является границей: роль всё равно сможет подключиться к чужой tenant_*
-- или к платформенным БД. Реальная граница =
--   REVOKE ... FROM PUBLIC (включая template1) + явный pg_hba на (db, user)
--   + NOLOGIN при revoke. pgaudit — НАБЛЮДЕНИЕ, а не граница (§11.5).
--
-- ПОРЯДОК КРИТИЧЕН (п.2 задачи). СНАЧАЛА выдаём CONNECT админской роли
-- `plugin_platform_admin` на КАЖДУЮ существующую БД, и ТОЛЬКО ПОТОМ снимаем
-- PUBLIC. Иначе платформа потеряет доступ для CREATE DATABASE / CREATE SCHEMA
-- при grant (ошибка 42501). Проверка в конце — fail-closed.
--
-- ЯВНО НЕ ТРОГАЕМ `prodavan`: это БД ПЛАТФОРМЕННОГО инстанса (другой сервис,
-- `agentscale-postgres`, as-built имя `agentscale`/`agentscale_userdb`). Этот
-- SQL исполняется на tenant-кластере и физически не имеет к тому инстансу
-- отношения. REVOKE ... FROM PUBLIC там делает ЗАДАЧА 14 своим bootstrap-Job'ом
-- (ключ `platform-revoke.sql` в ConfigMap `tenant-postgres-bootstrap`, см.
-- infra/k3s/base/plugins/tenant-postgres.yaml) — перебором по pg_database,
-- чтобы покрыть ЛЮБОЕ имя, включая `prodavan`, если оно появится. Мы здесь
-- НЕ дублируем и НЕ подключаемся к тому хосту: один инстанс = одна точка
-- применения ACL, иначе легко закрыть доступ самому API/миграциям платформы.
-- =============================================================================

\set ON_ERROR_STOP on

-- -----------------------------------------------------------------------------
-- ШАГ 1. Админская роль платформы (НЕ superuser). Создаём ДО любых REVOKE.
-- Пароль НЕ хранится в образе: его задаёт платформа при grant/ротации из
-- Secret `tenant-postgres-credentials` (PLATFORM_ADMIN_PASSWORD). Здесь роль
-- создаётся БЕЗ пароля и БЕЗ LOGIN — до явной выдачи пароля платформой она
-- НЕ может подключиться (fail-closed). Атрибуты повторены явно, чтобы
-- «дрейф» роли между синками не дал ей SUPERUSER/REPLICATION/BYPASSRLS.
-- -----------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'plugin_platform_admin') THEN
    CREATE ROLE plugin_platform_admin
      NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
  END IF;
END
$$;

ALTER ROLE plugin_platform_admin
  NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

-- ПРИМЕЧАНИЕ О СОВМЕСТИМОСТИ С ЗАДАЧЕЙ 14. Bootstrap-Job (Sync-хук, wave 1)
-- из ConfigMap `tenant-postgres-bootstrap` выполняется ПОЗЖЕ initdb и на
-- КАЖДОМ синке Argo применяет `ALTER ROLE ... PASSWORD` и
-- `LOGIN CREATEDB CREATEROLE NOSUPERUSER NOREPLICATION NOBYPASSRLS`.
-- Здесь роль намеренно создаётся БЕЗ пароля и БЕЗ LOGIN (безопасный дефолт
-- до первого синка); итоговые атрибуты задаёт тот Job, он и «побеждает».
-- Оба SQL идемпотентны и не конфликтуют: этот — один раз при initdb, тот —
-- на каждом sync. НЕ добавлять сюда пароль: секрет живёт только в Secret.

-- -----------------------------------------------------------------------------
-- ШАГ 2. ПЕРЕД revoke — CONNECT админской роли на КАЖДУЮ СУЩЕСТВУЮЩУЮ БД
-- (по ТЗ: «template1 и существующие БД»), без сужения по имени: на
-- tenant-кластере это `template1`, `postgres`, `tenant_admin` (POSTGRES_DB)
-- и уже созданные `tenant_*`. template0 исключён явным условием.
-- Перебор через DO-блок по pg_database: format('%I', ...) экранирует имя,
-- поэтому БД с дефисами/заглавными буквами не ломают SQL и не дают инъекции.
--
-- template1 ОБЯЗАТЕЛЕН: CREATE DATABASE по умолчанию копирует именно его
-- ACL. Снятый там PUBLIC CONNECT наследуют ВСЕ будущие tenant_* БД, которые
-- задача 05 создаст между синками Argo. Идемпотентно: GRANT повторен без
-- ошибки на каждом initdb/синке. Порядок совпадает с bootstrap.sql задачи 14.
-- -----------------------------------------------------------------------------
DO $$
DECLARE
  db record;
BEGIN
  FOR db IN
    SELECT datname FROM pg_database
    WHERE datallowconn AND datname <> 'template0'
    ORDER BY datname
  LOOP
    EXECUTE format(
      'GRANT CONNECT, TEMPORARY ON DATABASE %I TO plugin_platform_admin',
      db.datname
    );
  END LOOP;
END
$$;

-- -----------------------------------------------------------------------------
-- ШАГ 3. Снимаем дефолтные привилегии PUBLIC. ТОТ ЖЕ ЯВНЫЙ список + все
-- tenant_* через DO-блок по pg_database. ПОСЛЕ шага 2 — иначе админская
-- роль потеряет CONNECT и CREATE SCHEMA при grant упадёт с 42501.
--
-- Владелец БД (POSTGRES_USER / tenant_admin) сохраняет CONNECT: ACL владельца
-- — отдельная запись, REVOKE ... FROM PUBLIC её НЕ трогает. API/bootstrap
-- платформы продолжают работать.
--
-- НАМЕРЕННО НЕ ДЕЛАЕМ здесь `GRANT CONNECT ... TO <роль плагина>`: роли и
-- пары (db, user) создаёт задача 05 при grant, а pg_hba-пары она же пишет в
-- `tenant_hba.conf`. Пустой список пар = ни один плагин не подключится.
-- -----------------------------------------------------------------------------
DO $$
DECLARE
  db record;
BEGIN
  FOR db IN
    SELECT datname FROM pg_database
    WHERE datallowconn
      AND datname <> 'template0'
      AND (
            datname IN ('template1', 'postgres', 'agentscale', 'agentscale_userdb')
            OR datname LIKE 'tenant\_%' ESCAPE '\'
          )
    ORDER BY datname
  LOOP
    EXECUTE format(
      'REVOKE CONNECT, TEMPORARY ON DATABASE %I FROM PUBLIC',
      db.datname
    );
  END LOOP;
END
$$;

-- -----------------------------------------------------------------------------
-- ШАГ 4. Проверки (fail-closed). НЕ «тихо продолжить», а RAISE EXCEPTION:
-- если граница не построилась, контейнер/initdb обязан упасть, а не стартовать
-- с открытым PUBLIC CONNECT.
-- -----------------------------------------------------------------------------
DO $$
DECLARE
  db record;
  bad int := 0;
BEGIN
  -- 4a. У админской роли остался CONNECT на каждую существующую БД.
  FOR db IN
    SELECT datname FROM pg_database
    WHERE datallowconn AND datname <> 'template0'
  LOOP
    IF NOT has_database_privilege('plugin_platform_admin', db.datname, 'CONNECT') THEN
      RAISE WARNING 'plugin_platform_admin потеряла CONNECT на БД %', db.datname;
      bad := bad + 1;
    END IF;
  END LOOP;
  IF bad > 0 THEN
    RAISE EXCEPTION 'bootstrap ACL сломан: % БД без CONNECT у plugin_platform_admin', bad;
  END IF;

  -- 4b. PUBLIC больше НЕ имеет CONNECT/TEMPORARY ни на одной целевой БД.
  --     `has_database_privilege('public', ...)` здесь НЕ годится: 'public'
  --     это ключевое слово ACL, а не строка-имя роли для этой функции.
  --     Поэтому смотрим прямо в pg_database.datacl через aclexplode:
  --     grantee = 0 и есть privilege_type CONNECT или TEMPORARY → дыра.
  IF EXISTS (
    SELECT 1
    FROM pg_database d
    CROSS JOIN LATERAL aclexplode(COALESCE(d.datacl, acldefault('d', d.datdba))) a
    WHERE d.datallowconn
      AND d.datname <> 'template0'
      AND (
            d.datname IN ('template1', 'postgres', 'agentscale', 'agentscale_userdb')
            OR d.datname LIKE 'tenant\_%' ESCAPE '\'
          )
      AND a.grantee = 0
      AND a.privilege_type IN ('CONNECT', 'TEMPORARY')
  ) THEN
    RAISE EXCEPTION 'REVOKE ... FROM PUBLIC не применён: в datacl остались привилегии PUBLIC';
  END IF;
END
$$;
