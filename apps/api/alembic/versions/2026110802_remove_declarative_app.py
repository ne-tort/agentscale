"""Финальный strangler-cutover: удаление declarative-app (§8 этап 6, §13.1).

ЯВНЫЙ КОНТРАКТ РЕВИЗИИ
=======================

ЧТО УДАЛЯЕТСЯ
-------------
Ровно 8 платформенных таблиц ``module_*`` declarative-app в platform-БД
``prodavan`` (полный реестр — ``application/plugins/strangler/contexts.py``,
``MODULE_TABLES_DROPPED_WITHOUT_DATA_TRANSFER``):

    modules, module_meta_documents, module_company_grants,
    module_cabinet_bindings, module_project_bindings, module_instances,
    module_instance_meta_documents, module_instance_data_rows.

Дроп идёт ``op.drop_table()`` в порядке, ОБРАТНОМ FK-зависимостям: сначала
зависимые (дети), последним — родитель ``modules``. Реальные FK проверены в
``infrastructure/persistence/models/modules.py`` и в более ранних ревизиях
``2026082703``, ``2026082705``, ``2026090503``:

    module_instance_data_rows        -> module_instances
    module_instance_meta_documents  -> module_instances
    module_instances                -> modules, module_instances (self, SET NULL)
    module_project_bindings         -> modules, projects
    module_cabinet_bindings         -> modules, cabinet_instances
    module_company_grants           -> modules, companies
    module_meta_documents           -> modules
    modules                         -> companies (owner_company_id, SET NULL)

PostgreSQL сам удаляет индексы, UNIQUE и FK-ограничения вместе с таблицей,
поэтому отдельные ``drop_index``/``drop_constraint`` не нужны. ``upgrade head``
на чистой БД проходит (проверено на локальном postgres:17-alpine) ТОЛЬКО при
выставленном флаге подтверждения (см. ниже).

ЗАЩИТА ОТ ПРЕЖДЕВРЕМЕННОГО ПРИМЕНЕНИЯ (обязательно)
----------------------------------------------------
Пилот в кластере ещё НЕ подтверждён, поэтому ревизия НЕ ИМЕЕТ ПРАВА
применяться автоматически: CI Gate и initContainer ``migrate.sh`` на dev
вызывают ``alembic upgrade head`` и иначе молча снесли бы working-контур.
``upgrade()`` требует ЯВНОГО подтверждения переменной окружения
``PLUGIN_CUTOVER_CONFIRMED=true`` (строгое строковое сравнение). Без флага
(или при любом другом значении) бросается ``RuntimeError`` с русским
сообщением ДО первого ``op.drop_table()``: ни одна таблица не дропается,
это НЕ «тихий pass». Ревизия остаётся ИСПОЛНЯЕМОЙ: с флагом дроп ровно
8 таблиц работает как описано выше. Флаг выставляется родителем вручную
только после подтверждения пилота, вместе с применением чек-листа
``strangler/removal.py``; в обычном деплое переменной нет.

ЧТО НЕ УДАЛЯЕТСЯ (жёстко)
--------------------------
* **НЕ трогаем** плагиновые таблицы ``plugin_*`` (``plugin_artifacts``,
  ``plugin_instances``, ``plugin_capability_grants``, ``plugin_audit_events``,
  ``plugin_event_deliveries``, ``plugin_feature_flags``) — состояние plugin-
  платформы из ревизии ``2026110801`` остаётся.
* **НЕ трогаем** tenant-БД компаний ``tenant_<company>`` и схемы плагинов
  ``module_<plugin_id>`` внутри них: это ДРУГИЕ таблицы, совпадающие только
  префиксом ``module_``. Плагин владеет своей схемой и мигрирует её сам (§2/§3).
* **НЕ трогаем** никакие существующие таблицы, не related к ``module_*``:
  ``companies``, ``cabinet_instances``, ``projects``, ``dynamic_app_*`` и т.д.
* **НЕ добавляем** RLS/политики/расширения/роли/схемы/БД (§13.1: RLS как
  граница отвергнута; доп. расширения PostgreSQL не требуются).
* ORM-модели в ``infrastructure/persistence/models/modules.py`` и реестр
  ``models/__init__.py`` намеренно ОСТАЮТСЯ до реального cutover'а: другие
  подсистемы ещё читают эти таблицы. Их удаление — отдельный шаг родителя
  ПОСЛЕ ``alembic upgrade head`` (чек-лист см. ``strangler/removal.py``).

ПОЧЕМУ БЕЗ ПЕРЕНОСА СОДЕРЖИМОГО
--------------------------------
**БЕЗ ПЕРЕНОСА СОДЕРЖИМОГО**: дроп выполняется без переноса содержимого.
Данные dev эфемерны (§8, §13.1, §10 «Сломанный cutover»). Строки
``module_instance_data_rows`` (JSONB declarative-app) не читаются,
не выгружаются, не бэкапируются, не перекладываются в таблицы плагина;
тестовые данные создаются заново сидом плагина. НЕТ параллельного
чтения/записи, снимков состояния, бэкапов, выгрузок утилитами дампа,
сверки контрольных сумм, метрик парности и «недель наблюдения».
Откат = выключить фича-флаг ``plugin_enabled_modules[]``, а НЕ downgrade.

DOWNGRADE
---------
ЯВНО запрещён (``raise NotImplementedError``): по дизайну §8 DOWN-миграции
платформы не требуются. Таблицы технически НЕ пересоздаются, данные НЕ
восстанавливаются (их нет и не было). Это не «тихий pass»: попытка
``alembic downgrade`` должна громко упасть, чтобы никто не подумал, будто
declarative-app можно вернуть ревизией.

Эту ревизию НЕЛЬЗЯ применять к общему dev-кластеру до подтверждения пилота:
код подготовлен и проверен только на локальном чистом Postgres. Более того,
``upgrade()`` ЗАЩИЩЁН ЯВНЫМ РУБИЛЬНИКОМ ``PLUGIN_CUTOVER_CONFIRMED=true``:
``scripts/migrate.sh`` / CI Gate вызывают ``alembic upgrade head`` при каждом
деплое, и без guard'а ревизия автоматически дропнула бы 8 таблиц под
работающим declarative-app. Без переменной окружения ``upgrade()`` бросает
``RuntimeError`` и НЕ трогает ни одной таблицы (fail-closed).

Revision ID: 2026110802
Revises: 2026110801
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from alembic import op

revision: str = "2026110802"
down_revision: str | Sequence[str] | None = "2026110801"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: Порядок DROP: дети перед родителями (обратный к порядку CREATE).
#: Проверен по FK в models/modules.py и ревизиях 2026082703/2026082705/2026090503.
#: НЕ переставлять: иначе PostgreSQL откажет ``cannot drop table ... because
#: other objects depend on it`` и ``alembic upgrade head`` на чистой БД упадёт.
DROP_ORDER: tuple[str, ...] = (
    # 1-2. Дети module_instances (CASCADE FK на module_instances.id).
    "module_instance_data_rows",
    "module_instance_meta_documents",
    # 3. Self-FK parent_instance_id + FK на modules.id. Дети уже удалены.
    "module_instances",
    # 4-6. Привязки: FK на modules.id и на внешние таблицы, которых НЕ трогаем.
    "module_project_bindings",
    "module_cabinet_bindings",
    "module_company_grants",
    # 7. Мета-документы модуля: FK на modules.id.
    "module_meta_documents",
    # 8. Родитель. FK на companies.id (SET NULL) снимается вместе с таблицей.
    "modules",
)


#: ЯВНЫЙ рубильник. Ревизия НЕ должна применяться автоматически: initContainer
#: `scripts/migrate.sh` и CI Gate вызывают `alembic upgrade head` на КАЖДОМ
#: деплое, а cutover §8 этап 6 допустим только ПОСЛЕ подтверждённого пилота и
#: переключения фича-флага. Без этого guard'а первый же Argo sync дропнул бы
#: 8 таблиц под работающим declarative-app и сломал бы dev-контур.
#: Включается ТОЛЬКО осознанно, отдельным заходом (export в окружении job'а/пода).
CUTOVER_ENV = "PLUGIN_CUTOVER_CONFIRMED"
CUTOVER_VALUE = "true"


def _assert_cutover_confirmed() -> None:
    """Fail-closed: без явного подтверждения НЕ трогаем ни одной таблицы."""
    value = (os.environ.get(CUTOVER_ENV) or "").strip().lower()
    if value != CUTOVER_VALUE:
        raise RuntimeError(
            f"2026110802: cutover НЕ подтверждён. Установите {CUTOVER_ENV}={CUTOVER_VALUE} "
            "ТОЛЬКО после подтверждённого пилота и переключения plugin_enabled_modules[]; "
            "иначе alembic upgrade head автоматически удалит 8 таблиц module_* на "
            "рабочем dev-контуре. Откат = выключить фича-флаг, а не downgrade."
        )


def upgrade() -> None:
    """Дроп ровно 8 таблиц ``module_*`` БЕЗ переноса содержимого."""

    _assert_cutover_confirmed()
    for table_name in DROP_ORDER:
        op.drop_table(table_name)


def downgrade() -> None:
    """DOWN-миграция платформы не требуется (§8) — откат = выключить фича-флаг.

    Русское пояснение: это dev-разработка, данные эфемерны и НЕ переносятся,
    поэтому восстанавливать нечего; таблицы технически НЕ пересоздаём (§8:
    «DOWN-миграции платформы не требуются»). Откат cutover'а делается не
    ``alembic downgrade``, а выключением ``plugin_enabled_modules[]`` (§10).
    Явный ``NotImplementedError`` вместо «тихого pass», чтобы попытка отката
    ревизией была заметна и не создавала ложную иллюзию восстановления.
    """

    raise NotImplementedError(
        "2026110802: DOWN-миграция платформы не требуется (§8). "
        "Данные dev эфемерны и не переносились; таблицы module_* не "
        "пересоздаются. Откат = выключить фича-флаг plugin_enabled_modules[], "
        "а не alembic downgrade."
    )
