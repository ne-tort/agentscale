"""WAVE7 equipment pipeline: agent picks part-number groups, automation owns offers.

Responsibility split (docs/WAVE7-ARCHITECTURE.md):

- agent (MCP ``found_groups_upsert``) — «Позиции заказчика» (request_lines) и
  группы кандидатов found_groups: партномер + алиасы (другие написания P/N,
  хэши позиций src_hash) + категория точности (exact | analog | doubt);
- этот сервис — материализация found_offers из OpenSearch по ключам групп,
  актуализация цен / is_stale, best-оффер и «лицо» группы, авто-регистрация
  поставщиков, снапшот бюджетирования и таблица «Закупка».

Все записи пайплайна идут с ``run_actions=False`` (через ModuleRowIO), поэтому
пайплайн не самотриггерится. Идемпотентен: ключ оффера — (group_id, src_hash).
"""

from __future__ import annotations

import contextvars
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.modules.equipment_catalog_search import parse_price
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

logger = logging.getLogger(__name__)

# Re-entrancy guard: каскадное удаление пишет через ModuleRowIO → сервисные
# delete_data_row снова зовут хук; флаг останавливает рекурсию (каскад
# итеративно покрывает связанные таблицы сам).
_IN_CASCADE_DELETE: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "equipment_cascade_delete", default=False
)

MODULE_ID = "mod_equipment"
GROUPS_TABLE = "found_groups"
OFFERS_TABLE = "found_offers"
LINES_TABLE = "request_lines"
SELLERS_TABLE = "trusted_sellers"
BUDGET_TABLE = "budget_lines"
PROCUREMENT_TABLE = "procurement"
CATALOGS_TABLE = "catalogs"
BUILDS_TABLE = "equipment_builds"
ITEMS_TABLE = "equipment_items"

MATCH_EXACT = "exact"
MATCH_ANALOG = "analog"
MATCH_DOUBT = "doubt"
MATCH_KINDS = frozenset({MATCH_EXACT, MATCH_ANALOG, MATCH_DOUBT})
MATCH_ORDER = {MATCH_EXACT: 0, MATCH_ANALOG: 1, MATCH_DOUBT: 2, None: 3}

DEFAULT_MARKUP = 0.1
DEFAULT_MARGIN_PCT = 10.0

# Поля оффера, синхронизируемые пайплайном; ручные правки хранятся в body.manual
# (dict «поле → true») и поверх синхронизации не перезаписываются.
# Только «каталожные» поля: связующие (line_id/group_id), ключ сверки (src_hash),
# точность (match_kind) и признак приоритета (priority) вычисляются пайплайном —
# ручное переопределение рвёт связность и потому не поддерживается.
SYNCED_OFFER_FIELDS = (
    "title",
    "brand",
    "seller",
    "part_number",
    "price",
    "price_orig",
    "currency",
    "lead_time",
    "in_stock",
)

# Валюты, которые умеет FX и схема колонки found_offers.currency.
SUPPORTED_CURRENCIES = frozenset({"RUB", "USD", "EUR"})


def alias_tokens(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str):
        return [
            t.strip()
            for t in raw.replace(";", ",").replace("\n", ",").split(",")
            if t.strip()
        ]
    return []


def _num_or(raw: Any, default: float | None = None) -> float | None:
    if raw is None or isinstance(raw, bool):
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _float_or_none(raw: Any) -> float | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def group_pn_keys(body: dict[str, Any]) -> list[str]:
    """Part-number термы группы (партномер + pn-алиасы) для OS terms-поиска."""
    tokens = [str(body.get("part_number") or "").strip()]
    tokens += alias_tokens(body.get("aliases_pn"))
    keys: list[str] = []
    for tok in tokens:
        if not tok:
            continue
        for variant in (tok, tok.upper(), tok.lower()):
            if variant and variant not in keys:
                keys.append(variant)
    return keys


def group_hash_keys(body: dict[str, Any]) -> list[str]:
    return [t for t in alias_tokens(body.get("aliases_hash")) if t]


class ModuleRowIO:
    """Row IO для пайплайна: маршрутизация cabinet / project contour.

    Обёртки CabinetModuleService / ProjectRuntimeModuleService дают ACL,
    chat-scope и валидацию колонок; пайплайн пишет без запуска row-actions.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        cabinet_id: str,
        project_id: str | None,
        principal: Principal,
        employee: EmployeeRow | None,
        session_id: str | None,
    ) -> None:
        self._session = session
        self._cabinet_id = cabinet_id
        self._project_id = (project_id or "").strip() or None
        self._principal = principal
        self._employee = employee
        self._session_id = (session_id or "").strip() or None

    async def list(self, table_slug: str) -> list[dict[str, Any]]:
        if self._project_id:
            from prodavan.application.projects.project_runtime_module_service import (
                ProjectRuntimeModuleService,
            )

            return await ProjectRuntimeModuleService(self._session).list_data_rows(
                project_id=self._project_id,
                module_id=MODULE_ID,
                table_slug=table_slug,
                principal=self._principal,
                employee=self._employee,
                session_id=self._session_id,
            )
        from prodavan.application.cabinets.cabinet_module_service import (
            CabinetModuleService,
        )

        return await CabinetModuleService(self._session).list_data_rows(
            cabinet_id=self._cabinet_id,
            module_id=MODULE_ID,
            table_slug=table_slug,
            principal=self._principal,
            employee=self._employee,
            session_id=self._session_id,
        )

    async def list_project_wide(self, table_slug: str) -> list[dict[str, Any]]:
        """Rows across ALL chat sessions of the project (Закупка semantics).

        Закупка belongs to the project, not to a single chat: it aggregates
        data from every chat of the project (budget snapshots = effective
        selections, offers for counts). Without a project context the call
        degrades to the plain (session-scoped) [list].
        """
        from sqlalchemy import select as sa_select

        from prodavan.application.modules.module_instance_service import (
            OWNER_PROJECT,
            ModuleInstanceService,
        )
        from prodavan.infrastructure.persistence.models.agent import AgentSessionRow

        if not self._project_id:
            return await self.list(table_slug)
        session_ids: list[str] = []
        if self._session_id:
            session_ids.append(self._session_id)
        q = await self._session.execute(
            sa_select(AgentSessionRow.id).where(
                AgentSessionRow.project_id == self._project_id
            )
        )
        for row in q.scalars().all():
            sid = str(row)
            if sid not in session_ids:
                session_ids.append(sid)
        # Синтетический бакет «main» (записи вне активного чата) — тоже проект.
        from prodavan.application.modules.chat_scope import DEFAULT_CHAT_SESSION_ID

        if DEFAULT_CHAT_SESSION_ID not in session_ids:
            session_ids.append(DEFAULT_CHAT_SESSION_ID)
        if not session_ids:
            return []
        # Same SoT resolution as the project read path (resolve_sot_instance
        # walks global binds up: local bind → project leaf, global → cabinet).
        instances = ModuleInstanceService(self._session)
        instance = await instances.resolve_sot_instance(
            module_id=MODULE_ID,
            owner_kind=OWNER_PROJECT,
            owner_id=self._project_id,
        )
        if instance is None:
            return []
        return await instances.list_data_rows(
            instance_id=instance.id,
            table_slug=table_slug,
            session_ids=session_ids,
        )

    async def create(self, table_slug: str, body: dict[str, Any]) -> dict[str, Any]:
        if self._project_id:
            from prodavan.application.projects.project_runtime_module_service import (
                ProjectRuntimeModuleService,
            )

            return await ProjectRuntimeModuleService(self._session).create_data_row(
                project_id=self._project_id,
                module_id=MODULE_ID,
                table_slug=table_slug,
                body=body,
                principal=self._principal,
                employee=self._employee,
                session_id=self._session_id,
                run_actions=False,
            )
        from prodavan.application.cabinets.cabinet_module_service import (
            CabinetModuleService,
        )

        return await CabinetModuleService(self._session).create_data_row(
            cabinet_id=self._cabinet_id,
            module_id=MODULE_ID,
            table_slug=table_slug,
            body=body,
            principal=self._principal,
            employee=self._employee,
            session_id=self._session_id,
            run_actions=False,
        )

    async def update(
        self, table_slug: str, row_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        if self._project_id:
            from prodavan.application.projects.project_runtime_module_service import (
                ProjectRuntimeModuleService,
            )

            return await ProjectRuntimeModuleService(self._session).update_data_row(
                project_id=self._project_id,
                module_id=MODULE_ID,
                table_slug=table_slug,
                row_id=row_id,
                body=body,
                principal=self._principal,
                employee=self._employee,
                run_actions=False,
                session_id=self._session_id,
            )
        from prodavan.application.cabinets.cabinet_module_service import (
            CabinetModuleService,
        )

        return await CabinetModuleService(self._session).update_data_row(
            cabinet_id=self._cabinet_id,
            module_id=MODULE_ID,
            table_slug=table_slug,
            row_id=row_id,
            body=body,
            principal=self._principal,
            employee=self._employee,
            run_actions=False,
            session_id=self._session_id,
        )

    async def delete(self, table_slug: str, row_id: str) -> bool:
        try:
            if self._project_id:
                from prodavan.application.projects.project_runtime_module_service import (
                    ProjectRuntimeModuleService,
                )

                await ProjectRuntimeModuleService(self._session).delete_data_row(
                    project_id=self._project_id,
                    module_id=MODULE_ID,
                    table_slug=table_slug,
                    row_id=row_id,
                    principal=self._principal,
                    employee=self._employee,
                )
            else:
                from prodavan.application.cabinets.cabinet_module_service import (
                    CabinetModuleService,
                )

                await CabinetModuleService(self._session).delete_data_row(
                    cabinet_id=self._cabinet_id,
                    module_id=MODULE_ID,
                    table_slug=table_slug,
                    row_id=row_id,
                    principal=self._principal,
                    employee=self._employee,
                    session_id=self._session_id,
                )
        except AppError as exc:
            if exc.status == 404:
                return False
            raise
        return True


class EquipmentPipelineService:
    """Полный пайплайн модуля «Подбор товаров» (WAVE7)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ run

    async def run(
        self,
        io: ModuleRowIO,
        *,
        materialize: bool = True,
    ) -> dict[str, Any]:
        stats: dict[str, Any] = {
            "kind": "equipment.pipeline",
            "materialize": bool(materialize),
            "groups": 0,
            "groups_deleted": 0,
            "offers_created": 0,
            "offers_updated": 0,
            "offers_stale": 0,
            "offers_deleted": 0,
            "offers_fx_repriced": 0,
            "sellers_created": 0,
            "budget_created": 0,
            "budget_updated": 0,
            "builds_updated": 0,
            "builds_deleted": 0,
            "build_slot_groups_updated": 0,
            "build_lines_updated": 0,
            "procurement_rows": 0,
            "checked": 0,
        }

        lines = [r for r in await io.list(LINES_TABLE) if isinstance(r, dict)]
        groups = [r for r in await io.list(GROUPS_TABLE) if isinstance(r, dict)]
        offers = [r for r in await io.list(OFFERS_TABLE) if isinstance(r, dict)]
        sellers = [r for r in await io.list(SELLERS_TABLE) if isinstance(r, dict)]

        # Группы-сироты (владелец удалён) — чистим вместе с офферами, иначе
        # мусорные группы продолжают материализоваться и висеть в UI.
        # WAVE10: группа принадлежит ЛИБО позиции (line_id), ЛИБО слоту сборки
        # (build_id + slot_type_id) — «живая», если её владелец существует.
        line_ids = {str(r.get("row_id") or "") for r in lines}
        build_rows = [r for r in await io.list(BUILDS_TABLE) if isinstance(r, dict)]
        # Сборки-сироты (позиция удалена в обход каскада, например через MCP) —
        # удаляем ДО групп: иначе их слот-группы считаются «живыми» вечно.
        live_build_rows: list[dict[str, Any]] = []
        for b in build_rows:
            bid = str(b.get("row_id") or "")
            blid = str((b.get("body") or {}).get("line_id") or "")
            if not blid or blid in line_ids:
                live_build_rows.append(b)
                continue
            if await io.delete(BUILDS_TABLE, bid):
                stats["builds_deleted"] += 1
        build_rows = live_build_rows
        build_ids = {str(r.get("row_id") or "") for r in build_rows}
        live_groups: list[dict[str, Any]] = []
        for g in groups:
            gid = str(g.get("row_id") or "")
            gbody = g.get("body") or {}
            line_id = str(gbody.get("line_id") or "")
            build_id = str(gbody.get("build_id") or "")
            owner_alive = (
                build_id in build_ids
                if build_id
                else (bool(line_id) and line_id in line_ids)
            )
            if owner_alive:
                live_groups.append(g)
                continue
            if await io.delete(GROUPS_TABLE, gid):
                stats["groups_deleted"] += 1
                orphan_ids = {
                    str(o.get("row_id") or "")
                    for o in offers
                    if str((o.get("body") or {}).get("group_id") or "") == gid
                }
                for oid in orphan_ids:
                    if await io.delete(OFFERS_TABLE, oid):
                        stats["offers_deleted"] += 1
                offers = [
                    o for o in offers if str(o.get("row_id") or "") not in orphan_ids
                ]
        groups = live_groups
        stats["groups"] = len(groups)

        registry = _SellerRegistry(sellers)

        if materialize:
            offers_stats = await self._materialize_offers(
                io, groups=groups, offers=offers, registry=registry
            )
            stats.update(offers_stats)
            # перечитываем офферы после материализации
            offers = [r for r in await io.list(OFFERS_TABLE) if isinstance(r, dict)]

        # ₽-цены валютных офферов следуют за курсом ЦБ: дрейф курса без смены
        # price_orig тоже переоценивает оффер (stale/ручные цены заморожены).
        stats["offers_fx_repriced"] = await self._reprice_fx(io, offers=offers)
        if stats["offers_fx_repriced"]:
            offers = [r for r in await io.list(OFFERS_TABLE) if isinstance(r, dict)]

        # авто-регистрация поставщиков из офферов (до best/закупки)
        stats["sellers_created"] = await self._register_sellers(io, offers, registry)

        best_stats = await self._recompute_groups_and_lines(io, groups=groups, offers=offers)
        stats.update(best_stats)

        # groups/lines могли обновиться — перечитываем для бюджета/закупки
        groups = [r for r in await io.list(GROUPS_TABLE) if isinstance(r, dict)]
        lines = [r for r in await io.list(LINES_TABLE) if isinstance(r, dict)]
        offers = [r for r in await io.list(OFFERS_TABLE) if isinstance(r, dict)]
        stats["checked"] = len(offers)

        # «Сборка» ДО бюджета: бюджет позиции-сборки читает уже посчитанную
        # price_total сборки. Состав/цена из слотов (группы-кандидаты + легаси
        # item-слоты), best-сборка позиции, альтернативы и выгода.
        build_stats = await self._sync_builds(
            io, groups=groups, offers=offers
        )
        stats.update(build_stats)

        budget_stats = await self._sync_budget(
            io, lines=lines, groups=groups, offers=offers, registry=registry
        )
        stats.update(budget_stats)

        proc_stats = await self._sync_procurement(io, registry=registry)
        stats.update(proc_stats)
        return stats

    # --------------------------------------------------------- материализация

    async def _materialize_offers(
        self,
        io: ModuleRowIO,
        *,
        groups: list[dict[str, Any]],
        offers: list[dict[str, Any]],
        registry: _SellerRegistry,
    ) -> dict[str, Any]:
        """Синк found_offers с каталогом OpenSearch по ключам групп.

        Позиции отключённых поставщиков не материализуются (не участвуют в
        поиске, как и в поиске агента).
        """
        stats = {
            "offers_created": 0,
            "offers_updated": 0,
            "offers_stale": 0,
            "offers_deleted": 0,
        }
        if not groups:
            # Нет групп — чистим легаси-офферы (старая архитектура без group_id).
            for offer in offers:
                gid = str((offer.get("body") or {}).get("group_id") or "")
                if not gid:
                    if await io.delete(OFFERS_TABLE, str(offer.get("row_id") or "")):
                        stats["offers_deleted"] += 1
            return stats

        catalogs = [r for r in await io.list(CATALOGS_TABLE) if isinstance(r, dict)]
        ready = [
            str(c.get("row_id"))
            for c in catalogs
            if str((c.get("body") or {}).get("status") or "").strip().lower() == "ready"
        ]
        if not ready:
            return {**stats, "reason": "no_ready_catalogs"}

        lines = [r for r in await io.list(LINES_TABLE) if isinstance(r, dict)]
        line_title = {
            str(r.get("row_id") or ""): str((r.get("body") or {}).get("title") or "")
            for r in lines
        }

        docs = await self._search_docs(ready_ids=ready, groups=groups)

        # назначение доков группам
        group_pn_sets: dict[str, set[str]] = {}
        group_hash_sets: dict[str, set[str]] = {}
        for g in groups:
            gid = str(g.get("row_id") or "")
            body = g.get("body") or {}
            group_pn_sets[gid] = {p.casefold() for p in group_pn_keys(body)}
            group_hash_sets[gid] = set(group_hash_keys(body))

        doc_group_pairs: dict[str, set[str]] = {}
        for d in docs:
            h = str(d.get("src_hash") or "")
            pn_cf = str(d.get("part_number") or "").casefold()
            if registry.is_disabled(str(d.get("supplier") or "")):
                continue
            for g in groups:
                gid = str(g.get("row_id") or "")
                if pn_cf and pn_cf in group_pn_sets.get(gid, set()):
                    doc_group_pairs.setdefault(h, set()).add(gid)
                elif h and h in group_hash_sets.get(gid, set()):
                    doc_group_pairs.setdefault(h, set()).add(gid)

        offers_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        group_ids = set(group_pn_sets)
        deleted_ids: set[str] = set()
        for offer in offers:
            body = offer.get("body") or {}
            gid = str(body.get("group_id") or "")
            h = str(body.get("src_hash") or "")
            offers_by_key[(gid, h)] = offer
            if gid not in group_ids or not h:
                # группа удалена / легаси-оффер без ключа — чистим
                rid = str(offer.get("row_id") or "")
                if await io.delete(OFFERS_TABLE, rid):
                    stats["offers_deleted"] += 1
                    deleted_ids.add(rid)

        seen_keys: set[tuple[str, str]] = set()
        for g in groups:
            gid = str(g.get("row_id") or "")
            gbody = dict(g.get("body") or {})
            line_id = str(gbody.get("line_id") or "")
            match_kind = _valid_match_kind(gbody.get("match_kind"))
            for d in docs:
                dh = str(d.get("src_hash") or "")
                if not dh or gid not in doc_group_pairs.get(dh, set()):
                    continue
                key = (gid, dh)
                seen_keys.add(key)
                existing = offers_by_key.get(key)
                source_title = line_title.get(line_id, "")
                if existing is not None and str(existing.get("row_id") or "") in deleted_ids:
                    existing = None
                if existing is None:
                    body = await self._offer_body_from_doc(
                        d,
                        group_id=gid,
                        line_id=line_id,
                        match_kind=match_kind,
                        source_title=source_title,
                    )
                    if body is None:
                        continue
                    await io.create(OFFERS_TABLE, body)
                    stats["offers_created"] += 1
                else:
                    changed = await self._merge_doc_into_offer(
                        d,
                        offer=existing,
                        group_id=gid,
                        line_id=line_id,
                        match_kind=match_kind,
                        source_title=source_title,
                    )
                    if changed:
                        await io.update(
                            OFFERS_TABLE, str(existing.get("row_id") or ""), existing["body"]
                        )
                        stats["offers_updated"] += 1

        # позиция исчезла из каталога → is_stale (цена не трогается).
        # Офферы отключённых поставщиков — НЕ stale: позиция в каталоге есть,
        # она просто исключена из поиска; строка замирает без warning'а.
        for (gid, h), offer in offers_by_key.items():
            if (gid, h) in seen_keys or not h:
                continue
            rid = str(offer.get("row_id") or "")
            if rid in deleted_ids:
                continue
            body = dict(offer.get("body") or {})
            if registry.is_disabled(str(body.get("seller") or "")):
                continue
            if body.get("is_stale") is not True:
                body["is_stale"] = True
                await io.update(OFFERS_TABLE, rid, body)
                stats["offers_stale"] += 1
        return stats

    async def _search_docs(
        self, *, ready_ids: list[str], groups: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        from prodavan.application.modules.equipment_catalog_opensearch import (
            OS_NAMESPACE,
            catalog_os_index_name,
        )
        from prodavan.core.infra.opensearch_manager import get_search_index_service

        pn_terms: list[str] = []
        hash_terms: list[str] = []
        for g in groups:
            body = g.get("body") or {}
            for key in group_pn_keys(body):
                if key not in pn_terms:
                    pn_terms.append(key)
            for key in group_hash_keys(body):
                if key not in hash_terms:
                    hash_terms.append(key)
        if not pn_terms and not hash_terms:
            return []

        pn_set = set(pn_terms)
        svc = get_search_index_service()
        docs: dict[str, dict[str, Any]] = {}
        all_terms = pn_terms + hash_terms
        # чанки терминов; MAX_SEARCH_SIZE=200 на запрос → пагинируем до 5 страниц
        for start in range(0, len(all_terms), 20):
            chunk = all_terms[start : start + 20]
            chunk_pn = [t for t in chunk if t in pn_set]
            chunk_hash = [t for t in chunk if t not in pn_set]
            chunk_should: list[dict[str, Any]] = []
            if chunk_pn:
                chunk_should.append({"terms": {"part_number": chunk_pn}})
            if chunk_hash:
                chunk_should.append({"terms": {"src_hash": chunk_hash}})
            chunk_query = {
                "bool": {
                    "filter": [
                        {"bool": {"should": chunk_should, "minimum_should_match": 1}}
                    ]
                }
            }
            for catalog_row_id in ready_ids:
                page = 0
                while page < 5:
                    try:
                        result = await svc.search(
                            namespace=OS_NAMESPACE,
                            index=catalog_os_index_name(catalog_row_id),
                            query=chunk_query,
                            from_=page * 200,
                            size=200,
                            company_id="platform",
                            cabinet_id=None,
                            project_id=None,
                            apply_tenant_filter=False,
                            session=self._session,
                        )
                    except Exception:
                        logger.exception(
                            "equipment pipeline: os search failed catalog=%s", catalog_row_id
                        )
                        break
                    page_hits = result.hits or []
                    for hit in page_hits:
                        doc = hit.source if isinstance(hit.source, dict) else {}
                        h = str(doc.get("src_hash") or "")
                        if h and h not in docs:
                            docs[h] = doc
                    if len(page_hits) < 200:
                        break
                    page += 1
        return list(docs.values())

    async def _offer_body_from_doc(
        self,
        doc: dict[str, Any],
        *,
        group_id: str,
        line_id: str,
        match_kind: str,
        source_title: str = "",
    ) -> dict[str, Any] | None:
        """Тело оффера из OS-дока; None — док пропущен (неподдерживаемая валюта)."""
        from prodavan.application.modules.equipment_fx import convert_offer_price

        currency = str(doc.get("currency") or "RUB").upper() or "RUB"
        if currency not in SUPPORTED_CURRENCIES:
            logger.warning(
                "equipment pipeline: skip doc src_hash=%s — unsupported currency %r",
                doc.get("src_hash"),
                currency,
            )
            return None
        price_num = _float_or_none(doc.get("price_num"))
        if price_num is None:
            price_num = parse_price(str(doc.get("price") or ""))
        rub = price_num
        if price_num is not None and currency != "RUB":
            rub, _ = await convert_offer_price(price=price_num, currency=currency)
        return {
            "group_id": group_id,
            "line_id": line_id,
            "title": str(doc.get("title") or ""),
            "brand": str(doc.get("brand") or ""),
            "seller": str(doc.get("supplier") or ""),
            "part_number": str(doc.get("part_number") or ""),
            "src_hash": str(doc.get("src_hash") or ""),
            "catalog_id": str(doc.get("catalog_id") or ""),
            "lead_time": str(doc.get("lead_time") or ""),
            "in_stock": bool(doc.get("in_stock")),
            "currency": currency,
            "price_orig": price_num,
            "price": rub,
            "match_kind": match_kind,
            "source_title": source_title,
            "priority": False,
            "is_selected": False,
            "is_stale": False,
            "is_best": False,
            "score": _score_for_match(match_kind),
        }

    async def _merge_doc_into_offer(
        self,
        doc: dict[str, Any],
        *,
        offer: dict[str, Any],
        group_id: str,
        line_id: str,
        match_kind: str,
        source_title: str = "",
    ) -> bool:
        """Обновляет тело оффера данными дока. Возвращает True если были изменения."""
        from prodavan.application.modules.equipment_fx import convert_offer_price

        body = dict(offer.get("body") or {})
        manual = body.get("manual") if isinstance(body.get("manual"), dict) else {}
        changed = False

        def _set(field: str, value: Any) -> None:
            nonlocal changed
            if field in manual:
                return
            if body.get(field) != value:
                body[field] = value
                changed = True

        currency = str(doc.get("currency") or "RUB").upper() or "RUB"
        price_num = _float_or_none(doc.get("price_num"))
        if price_num is None:
            price_num = parse_price(str(doc.get("price") or ""))

        _set("title", str(doc.get("title") or ""))
        _set("brand", str(doc.get("brand") or ""))
        _set("seller", str(doc.get("supplier") or ""))
        _set("part_number", str(doc.get("part_number") or ""))
        _set("catalog_id", str(doc.get("catalog_id") or ""))
        _set("lead_time", str(doc.get("lead_time") or ""))
        _set("in_stock", bool(doc.get("in_stock")))
        _set("group_id", group_id)
        _set("line_id", line_id)
        _set("match_kind", match_kind)
        _set("source_title", source_title)

        if body.get("is_stale") is True:
            body["is_stale"] = False
            changed = True

        # сравнение цены — в исходной валюте каталога. Любое ручное
        # переопределение ценовой связки (price/price_orig/currency)
        # защищает её целиком — синхронизация не актуализирует её.
        price_manual = any(k in manual for k in ("price", "price_orig", "currency"))
        if currency not in SUPPORTED_CURRENCIES:
            # Неподдерживаемая валюта: цену не синкаем (конвертировать нечем),
            # остальные поля уже обновлены выше.
            logger.warning(
                "equipment pipeline: skip price sync src_hash=%s — unsupported currency %r",
                doc.get("src_hash"),
                currency,
            )
        elif price_num is not None and not price_manual:
            cur = str(body.get("currency") or "RUB").upper()
            if cur != currency:
                body["currency"] = currency
                changed = True
                cur = currency
            orig = _float_or_none(body.get("price_orig"))
            if orig is None or abs(price_num - orig) > 0.005:
                body["price_orig"] = price_num
                if cur == "RUB":
                    body["price"] = round(price_num, 2)
                else:
                    rub, _ = await convert_offer_price(price=price_num, currency=cur)
                    body["price"] = rub
                changed = True
        offer["body"] = body
        return changed

    # ------------------------------------------------------------------ best

    async def _reprice_fx(
        self,
        io: ModuleRowIO,
        *,
        offers: list[dict[str, Any]],
    ) -> int:
        """Переоценка ₽-цены валютных офферов по текущему курсу ЦБ.

        Срабатывает и на дрейф курса при неизменном price_orig (merge по доку
        такой случай не трогает — сравнение идёт в исходной валюте). Заморожены:
        stale-офферы (позиция пропала из каталога — цену не меняем) и цены с
        ручным переопределением (manual price/price_orig/currency).
        """
        from prodavan.application.modules.equipment_fx import convert_offer_price

        updated = 0
        for offer in offers:
            body = dict(offer.get("body") or {})
            currency = str(body.get("currency") or "RUB").upper()
            if currency == "RUB" or currency not in SUPPORTED_CURRENCIES:
                continue
            if body.get("is_stale") is True:
                continue
            manual = body.get("manual") if isinstance(body.get("manual"), dict) else {}
            if any(k in manual for k in ("price", "price_orig", "currency")):
                continue
            orig = _float_or_none(body.get("price_orig"))
            if orig is None:
                continue
            rub, _ = await convert_offer_price(price=orig, currency=currency)
            current = _float_or_none(body.get("price"))
            if current is None or abs(rub - current) > 0.005:
                body["price"] = rub
                await io.update(OFFERS_TABLE, str(offer.get("row_id") or ""), body)
                updated += 1
        return updated

    # ------------------------------------------------------------------ best

    async def _recompute_groups_and_lines(
        self,
        io: ModuleRowIO,
        *,
        groups: list[dict[str, Any]],
        offers: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """best-оффер группы, «лицо» группы, best-группа позиции, found_count."""
        stats = {"groups_updated": 0, "lines_updated": 0}

        sellers = [r for r in await io.list(SELLERS_TABLE) if isinstance(r, dict)]
        registry = _SellerRegistry(sellers)

        offers_by_group: dict[str, list[dict[str, Any]]] = {}
        for o in offers:
            body = o.get("body") or {}
            gid = str(body.get("group_id") or "")
            if gid:
                offers_by_group.setdefault(gid, []).append(o)

        now = datetime.now(UTC).isoformat(timespec="seconds")
        # Партномера позиций заказчика: display-фолбэк для групп без P/N.
        lines_rows = [r for r in await io.list(LINES_TABLE) if isinstance(r, dict)]
        line_pn_by_id = {
            str(r.get("row_id") or ""): str((r.get("body") or {}).get("part_number") or "")
            for r in lines_rows
        }
        groups_by_line: dict[str, list[dict[str, Any]]] = {}
        best_group_by_line: dict[str, str] = {}

        for g in groups:
            gid = str(g.get("row_id") or "")
            body = dict(g.get("body") or {})
            line_id = str(body.get("line_id") or "")
            # WAVE10: группы-кандидаты слота сборки (build_id) не участвуют в
            # выборе best-группы позиции — у них своя логика в _sync_builds.
            if line_id and not str(body.get("build_id") or ""):
                groups_by_line.setdefault(line_id, []).append(g)

            group_offers = offers_by_group.get(gid) or []

            # приоритетность поставщика — сначала (влияет на best), зелёный текст в UI
            for o in group_offers:
                obody = dict(o.get("body") or {})
                seller = str(obody.get("seller") or "")
                want_priority = registry.is_priority(seller)
                if bool(obody.get("priority")) is not want_priority:
                    obody["priority"] = want_priority
                    await io.update(OFFERS_TABLE, str(o.get("row_id") or ""), obody)
                    o["body"] = obody

            selected = next(
                (o for o in group_offers if (o.get("body") or {}).get("is_selected") is True),
                None,
            )
            best_offer = self._best_offer(group_offers, registry)
            face_offer = selected if selected is not None else best_offer

            best_id = ""
            if best_offer is not None:
                best_id = str(best_offer.get("row_id") or "")
            face = (face_offer or {}).get("body") or {}
            # приоритетный поставщик лица группы — бьёт цену внутри одной
            # точности (match_kind) при выборе лучшей группы позиции
            face_priority = bool(face) and registry.is_priority(str(face.get("seller") or ""))
            face_in_stock = bool(face) and face.get("in_stock") is True
            want_match_label = _match_label(
                _valid_match_kind(body.get("match_kind")),
                face_in_stock,
                has_offers=bool(face),
            )

            updates: dict[str, Any] = {}
            # WAVE10: владелец группы — позиция («line») или слот сборки
            # («build»). Глобальный список «Найденные товары» показывает только
            # позиционные группы; кандидаты слота видны внутри сборки.
            want_owner = "build" if str(body.get("build_id") or "") else "line"
            if str(body.get("owner_kind") or "") != want_owner:
                updates["owner_kind"] = want_owner
            want_rank = MATCH_ORDER.get(_valid_match_kind(body.get("match_kind")), 3)
            if body.get("rank") != want_rank:
                updates["rank"] = want_rank
            if bool(body.get("face_priority")) is not face_priority:
                updates["face_priority"] = face_priority
            if bool(body.get("face_in_stock")) is not face_in_stock:
                updates["face_in_stock"] = face_in_stock
            # Партномер позиции заказчика — фолбэк для колонки «PN» списка,
            # когда у группы своего партномера нет (display-only снапшот).
            want_line_pn = str(line_pn_by_id.get(line_id) or "")
            if str(body.get("line_part_number") or "") != want_line_pn:
                updates["line_part_number"] = want_line_pn
            if str(body.get("match_label") or "") != want_match_label:
                updates["match_label"] = want_match_label
            if body.get("best_offer_id") != best_id:
                updates["best_offer_id"] = best_id
            # Лицо = товар лучшего оффера; без офферов лицо пустое — UI
            # показывает «Нет оффера» warning (партномер НЕ подставляем).
            want_face_title = str(face.get("title") or "") if face else ""
            if body.get("face_title") != want_face_title:
                updates["face_title"] = want_face_title
            if body.get("face_price") != _num_or(face.get("price"), None):
                updates["face_price"] = _num_or(face.get("price"), None)
            if body.get("face_seller") != face.get("seller"):
                updates["face_seller"] = face.get("seller") or ""
            if body.get("face_brand") != (face.get("brand") or ""):
                updates["face_brand"] = face.get("brand") or ""
            # лицо группы устарело (позиция пропала из каталога) → warning в UI
            want_face_stale = bool(face) and face.get("is_stale") is True
            if bool(body.get("face_stale")) is not want_face_stale:
                updates["face_stale"] = want_face_stale
            if body.get("offers_count") != len(group_offers):
                updates["offers_count"] = len(group_offers)
            # synced_at — метка сверки с OS; пишем только вместе с реальными
            # изменениями, иначе каждый прогон переписывает все группы
            # (write-amplification → ложный «Проект требует обновления»).
            if updates:
                updates["synced_at"] = now
            if updates:
                body.update(updates)
                await io.update(GROUPS_TABLE, gid, body)
                stats["groups_updated"] += 1
                g["body"] = body

            # is_best на офферах группы
            for o in group_offers:
                obody = dict(o.get("body") or {})
                want_best = best_id != "" and str(o.get("row_id") or "") == best_id
                if obody.get("is_best") is not want_best:
                    obody["is_best"] = want_best
                    await io.update(OFFERS_TABLE, str(o.get("row_id") or ""), obody)
                    o["body"] = obody

        # лучшая группа позиции: точность → приоритетный поставщик → цена лица.
        # Приоритет бьёт цену ТОЛЬКО внутри одной точности: аналог приоритетного
        # поставщика никогда не вытесняет exact-группу.
        for line_id, line_groups in groups_by_line.items():
            ranked = sorted(
                line_groups,
                key=lambda g: (
                    MATCH_ORDER.get(_valid_match_kind((g.get("body") or {}).get("match_kind")), 3),
                    0 if (g.get("body") or {}).get("face_priority") is True else 1,
                    _num_or((g.get("body") or {}).get("face_price"), float("inf"))
                    if _num_or((g.get("body") or {}).get("face_price"), None) is not None
                    else float("inf"),
                    str(g.get("row_id") or ""),
                ),
            )
            with_offers = [
                g for g in ranked if _num_or((g.get("body") or {}).get("face_price"), None) is not None
            ]
            winner = str((with_offers[0].get("row_id") if with_offers else "") or "")
            # «Альтернативы»: другие группы позиции с живыми офферами.
            alternatives = max(0, len(with_offers) - 1)
            for g in ranked:
                gid = str(g.get("row_id") or "")
                want = gid != "" and gid == winner
                body = dict(g.get("body") or {})
                want_alternatives = alternatives if body.get("offers_count") else 0
                updates: dict[str, Any] = {}
                if bool(body.get("is_best")) is not want:
                    updates["is_best"] = want
                if _num_or(body.get("alternatives_count"), 0) != want_alternatives:
                    updates["alternatives_count"] = want_alternatives
                if updates:
                    body.update(updates)
                    await io.update(GROUPS_TABLE, gid, body)
                    g["body"] = body
                    stats["groups_updated"] += 1
            if winner:
                best_group_by_line[line_id] = winner

        # found_count / status на позициях заказчика
        lines = [r for r in await io.list(LINES_TABLE) if isinstance(r, dict)]
        for line in lines:
            line_id = str(line.get("row_id") or "")
            body = dict(line.get("body") or {})
            line_groups = groups_by_line.get(line_id) or []
            found = sum(
                1
                for g in line_groups
                if int(_num_or((g.get("body") or {}).get("offers_count"), 0) or 0) > 0
            )
            updates: dict[str, Any] = {}
            if body.get("found_count") != found:
                updates["found_count"] = found
            selected_ok = str(body.get("selected_offer_id") or "") in {
                str(o.get("row_id") or "")
                for o in offers
                if str((o.get("body") or {}).get("line_id") or "") == line_id
            }
            # Статус позиции полностью вычислим: валидный выбор → selected,
            # есть группы с офферами → matched, иначе → open. Откатывает и
            # протухший selected (оффер удалён/снят), и matched без находок.
            want_status = "selected" if selected_ok else ("matched" if found > 0 else "open")
            if str(body.get("status") or "") != want_status:
                updates["status"] = want_status
            if not selected_ok and body.get("selected_offer_id"):
                # битая ссылка на выбранный оффер — очищаем
                updates["selected_offer_id"] = None
            if updates:
                body.update(updates)
                await io.update(LINES_TABLE, line_id, body)
                line["body"] = body
                stats["lines_updated"] += 1

        # Per-оффер аннотации для «Товаров поставщика»: «Выгода» (против
        # эффективного выбора позиции), «Альтернативы» (офферы других
        # поставщиков той же позиции), «Совпадение» с «(под заказ)».
        stats["offers_annotated"] = await self._annotate_offers(
            io, lines=lines, groups=groups, offers=offers, best_group_by_line=best_group_by_line
        )
        return stats

    async def _annotate_offers(
        self,
        io: ModuleRowIO,
        *,
        lines: list[dict[str, Any]],
        groups: list[dict[str, Any]],
        offers: list[dict[str, Any]],
        best_group_by_line: dict[str, str],
    ) -> int:
        offers_by_line: dict[str, list[dict[str, Any]]] = {}
        for o in offers:
            lid = str((o.get("body") or {}).get("line_id") or "")
            if lid:
                offers_by_line.setdefault(lid, []).append(o)
        groups_by_id = {str(g.get("row_id") or ""): g for g in groups}
        annotated = 0
        for line in lines:
            line_id = str(line.get("row_id") or "")
            line_offers = offers_by_line.get(line_id) or []
            if not line_offers:
                continue
            line_body = line.get("body") or {}
            # эффективный выбор позиции: ручной ?? best-группа → её best-оффер
            effective_id = str(line_body.get("selected_offer_id") or "")
            if not effective_id:
                best_gid = best_group_by_line.get(line_id) or ""
                g = groups_by_id.get(best_gid)
                if g is not None:
                    effective_id = str((g.get("body") or {}).get("best_offer_id") or "")
            effective = next(
                (o for o in line_offers if str(o.get("row_id") or "") == effective_id), None
            )
            ref_price = _num_or((effective or {}).get("body", {}).get("price"), None)
            prices = [
                p
                for p in (
                    _num_or((o.get("body") or {}).get("price"), None) for o in line_offers
                )
                if p is not None and p > 0
            ]
            min_price = min(prices) if prices else None

            for o in line_offers:
                body = dict(o.get("body") or {})
                seller_cf = str(body.get("seller") or "").strip().casefold()
                alternatives = sum(
                    1
                    for other in line_offers
                    if str(other.get("row_id") or "") != str(o.get("row_id") or "")
                    and str((other.get("body") or {}).get("seller") or "").strip().casefold()
                    != seller_cf
                    and (other.get("body") or {}).get("is_stale") is not True
                )
                price = _num_or(body.get("price"), None)
                is_effective = str(o.get("row_id") or "") == effective_id
                label, tone = _benefit_label(
                    is_effective=is_effective,
                    price=price,
                    reference=ref_price,
                    min_price=min_price,
                    total_offers=len(line_offers),
                )
                updates: dict[str, Any] = {}
                if _num_or(body.get("alternatives_count"), None) != alternatives:
                    updates["alternatives_count"] = alternatives
                if str(body.get("benefit_label") or "") != label:
                    updates["benefit_label"] = label
                if str(body.get("benefit_tone") or "") != tone:
                    updates["benefit_tone"] = tone
                if bool(body.get("is_effective")) is not is_effective:
                    updates["is_effective"] = is_effective
                want_match_label = _match_label(
                    _valid_match_kind(body.get("match_kind")), body.get("in_stock") is True
                )
                if str(body.get("match_label") or "") != want_match_label:
                    updates["match_label"] = want_match_label
                if updates:
                    body.update(updates)
                    await io.update(OFFERS_TABLE, str(o.get("row_id") or ""), body)
                    o["body"] = body
                    annotated += 1
        return annotated

    def _best_offer(
        self,
        offers: list[dict[str, Any]],
        registry: _SellerRegistry | None = None,
    ) -> dict[str, Any] | None:
        """best-оффер группы: живой приоритетный → цена → наличие → stale.

        Порядок критериев (ТЗ): точность решается уровнем группы; внутри
        группы — приоритетный поставщик бьёт цену, дальше цена, наличие
        (под заказ уступает) — последним. Stale/disabled уступают живым.
        Ручной выбор (is_selected) это не ограничивает.
        """
        def key(o: dict[str, Any]) -> tuple:
            body = o.get("body") or {}
            price = _num_or(body.get("price"), None)
            disabled = (
                registry is not None
                and registry.is_disabled(str(body.get("seller") or ""))
            )
            # Порядок автовыбора (ТЗ 2026-10-07): наличие → цена → приоритет.
            # Оффер БЕЗ цены (включая текстовые «Уточняйте»: price_num=None)
            # выигрывает только внутри своей группы наличие, когда priced
            # варианта там нет; под заказ уступает наличию; приоритетный
            # поставщик бьёт цену среди priced внутри того же наличия.
            return (
                1 if (body.get("is_stale") is True or disabled) else 0,
                0 if body.get("in_stock") is True else 1,
                0 if price is not None else 1,
                0 if body.get("priority") is True else 1,
                price if price is not None else float("inf"),
                str(o.get("row_id") or ""),
            )

        if not offers:
            return None
        return sorted(offers, key=key)[0]

    # --------------------------------------------------------------- продавцы

    async def _register_sellers(
        self,
        io: ModuleRowIO,
        offers: list[dict[str, Any]],
        registry: _SellerRegistry,
    ) -> int:
        seen: set[str] = {
            str((o.get("body") or {}).get("seller") or "").strip().casefold()
            for o in offers
        }
        created = 0
        for token in sorted(seen):
            if not token:
                continue
            if registry.find(token) is not None:
                continue
            name = next(
                (
                    str((o.get("body") or {}).get("seller") or "").strip()
                    for o in offers
                    if str((o.get("body") or {}).get("seller") or "").strip().casefold() == token
                ),
                "",
            )
            if not name:
                continue
            try:
                await io.create(SELLERS_TABLE, {"name": name, "is_enabled": True})
                registry.add(name, {"name": name, "is_enabled": True})
                created += 1
            except Exception:
                logger.warning("pipeline: seller auto-map failed for %r", name, exc_info=True)
        return created

    # ---------------------------------------------------------------- бюджет

    async def _sync_budget(
        self,
        io: ModuleRowIO,
        *,
        lines: list[dict[str, Any]],
        groups: list[dict[str, Any]],
        offers: list[dict[str, Any]],
        registry: _SellerRegistry,
    ) -> dict[str, Any]:
        """Снапшот budget_lines: выбранный оффер → best-группа → best-оффер."""
        stats = {"budget_created": 0, "budget_updated": 0}
        budget_rows = [r for r in await io.list(BUDGET_TABLE) if isinstance(r, dict)]
        budget_by_line = {
            str((r.get("body") or {}).get("line_id") or ""): r for r in budget_rows
        }
        offers_by_id = {
            str(o.get("row_id") or ""): o for o in offers
        }

        # WAVE10: сборки позиции. Если у позиции есть best-сборка с ценой, строка
        # бюджета берётся ИЗ СБОРКИ (build_id + сумма), а не из одиночного
        # оффера — расчёты остаются в «Найденных товарах», но сопоставляются
        # со Сборкой.
        builds = [r for r in await io.list(BUILDS_TABLE) if isinstance(r, dict)]
        best_build_by_line: dict[str, dict[str, Any]] = {}
        for b in builds:
            bbody = b.get("body") or {}
            blid = str(bbody.get("line_id") or "")
            if not blid:
                continue
            price = _num_or(bbody.get("price_total"), None)
            if price in (None, 0):
                continue
            cur = best_build_by_line.get(blid)
            # best-сборка: явный is_best → дешевле.
            if cur is None:
                best_build_by_line[blid] = b
                continue
            cur_body = cur.get("body") or {}
            if (bbody.get("is_best") is True) or (
                cur_body.get("is_best") is not True
                and _num_or(bbody.get("price_total"), float("inf"))
                < _num_or(cur_body.get("price_total"), float("inf"))
            ):
                best_build_by_line[blid] = b

        groups_by_line: dict[str, list[dict[str, Any]]] = {}
        for g in groups:
            body = g.get("body") or {}
            lid = str(body.get("line_id") or "")
            # группы-кандидаты слота сборки (build_id) не «лицо» позиции
            if lid and not str(body.get("build_id") or ""):
                groups_by_line.setdefault(lid, []).append(g)

        for line in lines:
            line_id = str(line.get("row_id") or "")
            line_body = line.get("body") or {}

            # Приоритет — сборка позиции (если собрана).
            build = best_build_by_line.get(line_id)
            if build is not None:
                bbody = build.get("body") or {}
                build_id = str(build.get("row_id") or "")
                snapshot = {
                    "line_id": line_id,
                    "build_id": build_id,
                    "title": str(bbody.get("name") or line_body.get("title") or "").strip()
                    or "Сборка",
                    "part_number": str(line_body.get("part_number") or "").strip()
                    or "Сборка",
                    "qty": line_body.get("qty") or 1,
                    "price_in": _num_or(bbody.get("price_total"), None),
                    # «под заказ» — флаг сборки (пишет _sync_builds), а не разбор
                    # текста match_label.
                    "on_order": bbody.get("on_order") is True,
                    "match_kind": _valid_match_kind(bbody.get("match_kind")),
                    "seller": "",
                    "brand": "",
                }
                existing = budget_by_line.get(line_id)
                if existing is None:
                    body = dict(snapshot)
                    body.setdefault("vat", 0.22)
                    body["markup"] = DEFAULT_MARKUP
                    body["markup_source"] = "default"
                    await io.create(BUDGET_TABLE, body)
                    stats["budget_created"] += 1
                else:
                    row_id = str(existing.get("row_id") or "")
                    body = dict(existing.get("body") or {})
                    body.update(snapshot)
                    if body != (existing.get("body") or {}):
                        await io.update(BUDGET_TABLE, row_id, body)
                        stats["budget_updated"] += 1
                continue

            offer = None
            selected_id = str(line_body.get("selected_offer_id") or "")
            if selected_id:
                candidate = offers_by_id.get(selected_id)
                if candidate is not None and str(
                    (candidate.get("body") or {}).get("line_id") or ""
                ) == line_id:
                    offer = candidate
            if offer is None:
                line_groups = sorted(
                    groups_by_line.get(line_id) or [],
                    key=lambda g: (
                        MATCH_ORDER.get(
                            _valid_match_kind((g.get("body") or {}).get("match_kind")), 3
                        ),
                        _num_or((g.get("body") or {}).get("face_price"), float("inf")),
                        str(g.get("row_id") or ""),
                    ),
                )
                for g in line_groups:
                    best_id = str((g.get("body") or {}).get("best_offer_id") or "")
                    if best_id and offers_by_id.get(best_id) is not None:
                        offer = offers_by_id[best_id]
                        break

            if offer is None:
                continue

            obody = offer.get("body") or {}
            seller = str(obody.get("seller") or "").strip()
            snapshot = {
                "line_id": line_id,
                # строка больше не из сборки (сборку удалили / осталась без цены)
                # — гасим ссылку, иначе UI/документы читают мёртвую сборку.
                "build_id": "",
                "title": str(obody.get("title") or line_body.get("title") or "").strip()
                or "Не найден",
                "part_number": str(
                    line_body.get("part_number") or obody.get("part_number") or ""
                ).strip()
                or "Не определен",
                "qty": line_body.get("qty") or 1,
                # None = цены у оффера нет (UI рисует «Нет цены»/«Под заказ»
                # warning-цветом); 0.0 раньше маскировала отсутствие цены.
                "price_in": _num_or(obody.get("price"), None),
                "on_order": obody.get("in_stock") is not True,
                # точность выбранного оффера: UI красит наименование/P/N
                # warning-цветом для аналогов и сомнений
                "match_kind": _valid_match_kind(obody.get("match_kind")),
                "seller": seller or "Не найден",
                "brand": str(obody.get("brand") or "").strip(),
            }

            existing = budget_by_line.get(line_id)
            if existing is None:
                body = dict(snapshot)
                body.setdefault("vat", 0.22)
                seller_pct = registry.margin_pct(seller)
                if seller_pct is not None:
                    body["markup"] = seller_pct / 100.0
                    body["markup_source"] = "seller"
                else:
                    body["markup"] = DEFAULT_MARKUP
                    body["markup_source"] = "default"
                await io.create(BUDGET_TABLE, body)
                stats["budget_created"] += 1
                continue

            row_id = str(existing.get("row_id") or "")
            if not row_id:
                continue
            body = dict(existing.get("body") or {})
            body.update(snapshot)
            # маржа: обновляем только «дефолтные» строки; ручные не трогаем
            source = str(body.get("markup_source") or "")
            if source != "manual":
                seller_pct = registry.margin_pct(seller)
                want_markup = (seller_pct / 100.0) if seller_pct is not None else DEFAULT_MARKUP
                want_source = "seller" if seller_pct is not None else "default"
                if _num_or(body.get("markup"), None) != want_markup or source != want_source:
                    body["markup"] = want_markup
                    body["markup_source"] = want_source
            if body == (existing.get("body") or {}):
                continue  # без изменений — не пишем (write-amplification)
            await io.update(BUDGET_TABLE, row_id, body)
            stats["budget_updated"] += 1
        return stats

    # ---------------------------------------------------------------- сборка

    async def _sync_builds(
        self,
        io: ModuleRowIO,
        *,
        groups: list[dict[str, Any]],
        offers: list[dict[str, Any]],
    ) -> dict[str, int]:
        """equipment_builds: состав/цена из слотов + best-сборка позиции.

        WAVE10. Слот сборки = тип комплектующего; его кандидаты — группы
        `found_groups` с `build_id`+`slot_type_id` (материализуются как обычные
        группы, офферы/цены — тот же пайплайн). Цена сборки = Σ(best-группа
        слота × qty). Среди сборок одной позиции выбирается best (точность →
        цена); остальные — альтернативы. Легаси-слоты (`slots` → item → offer)
        продолжают учитываться, пока не переведены на группы.
        """
        items = [r for r in await io.list(ITEMS_TABLE) if isinstance(r, dict)]
        builds = [r for r in await io.list(BUILDS_TABLE) if isinstance(r, dict)]
        lines_rows = [r for r in await io.list(LINES_TABLE) if isinstance(r, dict)]
        if not builds:
            # Сборок нет вовсе — всё равно гасим протухшие снапшоты на позициях.
            lines_updated = 0
            for line_row in lines_rows:
                if await self._write_line_build_snapshot(
                    io,
                    lines_rows=lines_rows,
                    line_id=str(line_row.get("row_id") or ""),
                    snap={
                        "builds_count": 0,
                        "build_best_id": "",
                        "build_best_price": None,
                        "build_match_label": "",
                        "build_benefit_label": "",
                        "build_benefit_tone": "",
                    },
                ):
                    lines_updated += 1
            return {
                "builds_updated": 0,
                "build_slot_groups_updated": 0,
                "build_lines_updated": lines_updated,
            }
        items_by_id = {str(r.get("row_id") or ""): r for r in items}
        offers_by_id = {str(r.get("row_id") or ""): r for r in offers}

        # Кандидаты слотов: группы с build_id И slot_type_id. Группа без слота
        # (build_id есть, slot_type_id пуст) в цену сборки не входит: иначе она
        # попала бы в фантомный слот "" и молча увеличила price_total.
        groups_by_build_slot: dict[tuple[str, str], list[dict[str, Any]]] = {}
        unassigned_slot_groups = 0
        for g in groups:
            gbody = g.get("body") or {}
            bid = str(gbody.get("build_id") or "")
            if not bid:
                continue
            slot = str(gbody.get("slot_type_id") or "")
            if not slot:
                unassigned_slot_groups += 1
                continue
            groups_by_build_slot.setdefault((bid, slot), []).append(g)
        if unassigned_slot_groups:
            logger.warning(
                "pipeline: %d групп(а) сборки без slot_type_id — не учтены в цене",
                unassigned_slot_groups,
            )

        updated = 0
        slot_groups_updated = 0
        lines_updated = 0
        builds_by_line: dict[str, list[dict[str, Any]]] = {}
        for build in builds:
            bid = str(build.get("row_id") or "")
            body = dict(build.get("body") or {})
            line_id = str(body.get("line_id") or "")
            if line_id:
                builds_by_line.setdefault(line_id, []).append(build)

            # Слоты: объединяем ключи из легаси `slots` и из групп сборки.
            legacy_slots = body.get("slots") if isinstance(body.get("slots"), dict) else {}
            slot_keys = set(str(k) for k in legacy_slots.keys())
            slot_keys |= {slot for (b, slot) in groups_by_build_slot.keys() if b == bid}

            count = 0
            total = 0.0
            worst_rank = -1  # худшая точность среди компонентов (0=exact лучше)
            any_on_order = False
            want_slots = dict(legacy_slots)
            for slot in slot_keys:
                # 1) группа-кандидат слота → best по (точность, приоритет, цена)
                slot_groups = groups_by_build_slot.get((bid, slot)) or []
                priced = [
                    g
                    for g in slot_groups
                    if _num_or((g.get("body") or {}).get("face_price"), None) is not None
                    and int(_num_or((g.get("body") or {}).get("offers_count"), 0) or 0) > 0
                ]
                slot_price: float | None = None
                chosen_g: dict[str, Any] | None = None
                if priced:
                    best_g = sorted(
                        priced,
                        key=lambda g: (
                            MATCH_ORDER.get(
                                _valid_match_kind((g.get("body") or {}).get("match_kind")), 3
                            ),
                            0 if (g.get("body") or {}).get("face_priority") is True else 1,
                            _num_or((g.get("body") or {}).get("face_price"), float("inf")),
                            str(g.get("row_id") or ""),
                        ),
                    )[0]
                    # Ручной выбор слота (slots[slot] = group_id) перекрывает
                    # автобest, если указывает на живого кандидата слота.
                    manual_id = str(legacy_slots.get(slot) or "")
                    chosen_g = next(
                        (g for g in priced if str(g.get("row_id") or "") == manual_id),
                        best_g,
                    )
                    # Цена/точность/наличие сборки считаются ПО ВЫБРАННОМУ
                    # кандидату, а не по автобest: иначе ручной выбор в UI
                    # расходится с итоговой ценой сборки.
                    gb = chosen_g.get("body") or {}
                    slot_price = _num_or(gb.get("face_price"), None)
                    rank = MATCH_ORDER.get(_valid_match_kind(gb.get("match_kind")), 3)
                    worst_rank = max(worst_rank, rank)
                    if gb.get("face_in_stock") is not True:
                        any_on_order = True
                # is_best/альтернативы группы-кандидата: видно в pick-списке слота.
                chosen_id = str((chosen_g or {}).get("row_id") or "")
                if chosen_id:
                    # Самовосстановление состава: если ручной выбор протух
                    # (кандидат удалён или остался без офферов), в slots
                    # пишется фактически учтённый в цене кандидат — иначе UI
                    # показывает одно, а price_total считает другое.
                    want_slots[slot] = chosen_id
                slot_alts = max(0, len(priced) - 1)
                for g in slot_groups:
                    gid = str(g.get("row_id") or "")
                    gbody = dict(g.get("body") or {})
                    gupdates: dict[str, Any] = {}
                    want_best = chosen_id != "" and gid == chosen_id
                    if bool(gbody.get("is_best")) is not want_best:
                        gupdates["is_best"] = want_best
                    want_alts = slot_alts if gbody.get("offers_count") else 0
                    if _num_or(gbody.get("alternatives_count"), 0) != want_alts:
                        gupdates["alternatives_count"] = want_alts
                    if gupdates:
                        gbody.update(gupdates)
                        await io.update(GROUPS_TABLE, gid, gbody)
                        g["body"] = gbody
                        slot_groups_updated += 1
                # 2) легаси item-слот (если группы слота цены не дали)
                slot_qty = 1.0
                if slot_price is None:
                    item = items_by_id.get(str(legacy_slots.get(slot) or ""))
                    if item is not None:
                        ibody = item.get("body") or {}
                        slot_qty = _num_or(ibody.get("qty"), 1.0) or 1.0
                        offer = offers_by_id.get(str(ibody.get("offer_id") or ""))
                        if offer is not None:
                            slot_price = _num_or((offer.get("body") or {}).get("price"), None)
                            if (offer.get("body") or {}).get("in_stock") is not True:
                                any_on_order = True
                if slot_price is not None:
                    count += 1
                    total += slot_price * slot_qty
            total = round(total, 2)

            # match_kind сборки — по слабейшему звену (сборка не надёжнее
            # самого сомнительного компонента).
            kind_by_rank = {0: MATCH_EXACT, 1: MATCH_ANALOG, 2: MATCH_DOUBT}
            want_kind = kind_by_rank.get(worst_rank, MATCH_ANALOG) if count else MATCH_ANALOG
            want_label = _match_label(
                _valid_match_kind(want_kind), not any_on_order, has_offers=count > 0
            )

            updates: dict[str, Any] = {}
            if body.get("components_count") != count:
                updates["components_count"] = count
            if _num_or(body.get("price_total"), 0.0) != total:
                updates["price_total"] = total
            if str(body.get("match_kind") or "") != want_kind:
                updates["match_kind"] = want_kind
            if str(body.get("match_label") or "") != want_label:
                updates["match_label"] = want_label
            # «под заказ» — отдельный флаг: бюджет читает его напрямую, а не
            # парсит текст match_label (локализация/формулировка могут меняться).
            if bool(body.get("on_order")) is not any_on_order:
                updates["on_order"] = any_on_order
            if want_slots != (body.get("slots") if isinstance(body.get("slots"), dict) else {}):
                updates["slots"] = want_slots
            if updates:
                body.update(updates)
                await io.update(BUILDS_TABLE, bid, body)
                build["body"] = body
                updated += 1

        # best-сборка позиции: точность → цена; остальные — альтернативы.
        for line_id, line_builds in builds_by_line.items():
            priced = [
                b
                for b in line_builds
                if _num_or((b.get("body") or {}).get("price_total"), None) not in (None, 0)
            ]
            ranked = sorted(
                priced,
                key=lambda b: (
                    MATCH_ORDER.get(
                        _valid_match_kind((b.get("body") or {}).get("match_kind")), 3
                    ),
                    _num_or((b.get("body") or {}).get("price_total"), float("inf")),
                    str(b.get("row_id") or ""),
                ),
            )
            # ручной выбор сборки (is_selected) перекрывает автобest
            selected = next(
                (b for b in line_builds if (b.get("body") or {}).get("is_selected") is True),
                None,
            )
            winner = str(
                (selected or (ranked[0] if ranked else {})).get("row_id") or ""
            )
            min_price = (
                _num_or((ranked[0].get("body") or {}).get("price_total"), None) if ranked else None
            )
            alternatives = max(0, len(ranked) - 1)
            for b in line_builds:
                bid = str(b.get("row_id") or "")
                body = dict(b.get("body") or {})
                price = _num_or(body.get("price_total"), None)
                want_best = bid != "" and bid == winner
                label, tone = _benefit_label(
                    is_effective=want_best,
                    price=price,
                    reference=min_price,
                    min_price=min_price,
                    total_offers=len(ranked),
                )
                updates = {}
                if bool(body.get("is_best")) is not want_best:
                    updates["is_best"] = want_best
                if _num_or(body.get("alternatives_count"), 0) != alternatives:
                    updates["alternatives_count"] = alternatives
                if str(body.get("benefit_label") or "") != label:
                    updates["benefit_label"] = label
                if str(body.get("benefit_tone") or "") != tone:
                    updates["benefit_tone"] = tone
                if updates:
                    body.update(updates)
                    await io.update(BUILDS_TABLE, bid, body)
                    b["body"] = body
                    updated += 1

            # снапшот сборок на позиции: список показывает «Сборка» (счётчик,
            # цена лучшей, совпадение, выгода) без дрилл-дауна в сборки.
            winner_build = next(
                (b for b in line_builds if str(b.get("row_id") or "") == winner), None
            )
            wbody = (winner_build or {}).get("body") or {}
            if await self._write_line_build_snapshot(
                io,
                lines_rows=lines_rows,
                line_id=line_id,
                snap={
                    "builds_count": len(line_builds),
                    "build_best_id": winner,
                    "build_best_price": _num_or(wbody.get("price_total"), None),
                    "build_match_label": str(wbody.get("match_label") or ""),
                    "build_benefit_label": str(wbody.get("benefit_label") or ""),
                    "build_benefit_tone": str(wbody.get("benefit_tone") or ""),
                },
            ):
                lines_updated += 1

        # Позиции без сборок: гасим протухший снапшот (сборки удалили) — иначе
        # «Сборка ₽» и бейдж выгоды продолжают показывать несуществующее.
        for line_row in lines_rows:
            lid = str(line_row.get("row_id") or "")
            if lid in builds_by_line:
                continue
            if await self._write_line_build_snapshot(
                io,
                lines_rows=lines_rows,
                line_id=lid,
                snap={
                    "builds_count": 0,
                    "build_best_id": "",
                    "build_best_price": None,
                    "build_match_label": "",
                    "build_benefit_label": "",
                    "build_benefit_tone": "",
                },
            ):
                lines_updated += 1

        return {
            "builds_updated": updated,
            "build_slot_groups_updated": slot_groups_updated,
            "build_lines_updated": lines_updated,
        }

    async def _write_line_build_snapshot(
        self,
        io: ModuleRowIO,
        *,
        lines_rows: list[dict[str, Any]],
        line_id: str,
        snap: dict[str, Any],
    ) -> bool:
        """Пишет снапшот сборок на позицию только если он изменился."""
        line_row = next(
            (r for r in lines_rows if str(r.get("row_id") or "") == line_id), None
        )
        if line_row is None:
            return False
        lbody = dict(line_row.get("body") or {})
        changed = {k: v for k, v in snap.items() if lbody.get(k) != v}
        if not changed:
            return False
        lbody.update(changed)
        await io.update(LINES_TABLE, line_id, lbody)
        line_row["body"] = lbody
        return True

    # ---------------------------------------------------------------- закупка

    async def _sync_procurement(
        self,
        io: ModuleRowIO,
        *,
        registry: _SellerRegistry,
    ) -> dict[str, Any]:
        """Таблица «Закупка»: агрегат по проекту (все чаты), эффективный выбор.

        Закупка принадлежит проекту, а не одному чату: читает бюджетные
        снапшоты ВСЕХ чатов проекта (каждый снапшот = эффективный оффер
        позиции: явный выбор → иначе best, ровно как в «Бюджетировании») и
        офферы всех чатов (для счётчиков). Своих данных не держит — строки
        таблицы это вычисляемые агрегаты, пишутся в бакет активного чата
        (on_load страницы их всегда пересобирает).
        """
        stats = {"procurement_rows": 0, "procurement_created": 0, "procurement_updated": 0}
        budget_rows = [
            r for r in await io.list_project_wide(BUDGET_TABLE) if isinstance(r, dict)
        ]
        offers = [
            r for r in await io.list_project_wide(OFFERS_TABLE) if isinstance(r, dict)
        ]
        groups = [
            r for r in await io.list_project_wide(GROUPS_TABLE) if isinstance(r, dict)
        ]

        offers_count_by_seller: dict[str, int] = {}
        for o in offers:
            seller = str((o.get("body") or {}).get("seller") or "").strip()
            if seller:
                offers_count_by_seller[seller.casefold()] = (
                    offers_count_by_seller.get(seller.casefold(), 0) + 1
                )

        # WAVE10: выбранные комплектующие сборок (группы слота с is_best) —
        # строка бюджета сборки не имеет одного продавца, поэтому закупка
        # раскладывается по поставщикам компонентов.
        chosen_groups_by_build: dict[str, list[dict[str, Any]]] = {}
        for g in groups:
            gbody = g.get("body") or {}
            bid = str(gbody.get("build_id") or "")
            if bid and gbody.get("is_best") is True:
                chosen_groups_by_build.setdefault(bid, []).append(gbody)

        # Позиции проекта из бюджетных снапшотов (line_id уникален в рамках
        # чата; при дубле из другого чата побеждает последний апдейт).
        lines_by_seller: dict[str, list[dict[str, Any]]] = {}
        for r in budget_rows:
            body = r.get("body") or {}
            line_id = str(body.get("line_id") or "")
            if not line_id:
                continue
            seller = str(body.get("seller") or "").strip()
            if seller:
                lines_by_seller.setdefault(seller.casefold(), []).append(body)
                continue
            # строка сборки: раскладываем по поставщикам её компонентов
            build_id = str(body.get("build_id") or "")
            if not build_id:
                continue
            qty = _num_or(body.get("qty"), 1.0) or 1.0
            for gb in chosen_groups_by_build.get(build_id) or []:
                comp_seller = str(gb.get("face_seller") or "").strip()
                comp_price = _num_or(gb.get("face_price"), None)
                if not comp_seller or comp_price is None:
                    continue
                lines_by_seller.setdefault(comp_seller.casefold(), []).append(
                    {
                        "line_id": line_id,
                        "qty": qty,
                        "price_in": comp_price,
                        "seller": comp_seller,
                        # маржа — из реестра поставщика (не ручная)
                        "markup_source": "",
                    }
                )

        proc_rows = [r for r in await io.list(PROCUREMENT_TABLE) if isinstance(r, dict)]
        proc_by_seller: dict[str, dict[str, Any]] = {}
        for r in proc_rows:
            token = str((r.get("body") or {}).get("seller") or "").strip().casefold()
            if not token:
                continue
            if token in proc_by_seller:
                # дубль строки поставщика в бакете — лишнюю удаляем
                await io.delete(PROCUREMENT_TABLE, str(r.get("row_id") or ""))
                continue
            proc_by_seller[token] = r

        # Поставщики с офферами ИЛИ с эффективными позициями: строка нужна
        # даже без выборов — иначе на Закупках нельзя выбрать его товары.
        all_sellers = set(offers_count_by_seller) | set(lines_by_seller)
        keep_sellers: set[str] = set()
        for token in sorted(all_sellers):
            seller_lines = lines_by_seller.get(token, [])
            seller_entry = registry.find(token)
            if seller_entry is None:
                continue
            seller_body = seller_entry["body"]
            if seller_body.get("is_enabled") is False:
                continue
            keep_sellers.add(token)
            display_name = str(seller_body.get("name") or token)

            sum_rub = 0.0
            margin_rub = 0.0
            qty_total = 0.0
            seller_pct = registry.margin_pct(display_name)
            for body in seller_lines:
                qty = _num_or(body.get("qty"), 1.0) or 1.0
                qty_total += qty
                price = _num_or(body.get("price_in"), 0.0) or 0.0
                # Эффективная маржа строки: ручная правка — из бюджета; иначе
                # актуальная маржа поставщика из реестра (бюджетные строки
                # других чатов могут ещё не догнать смену margin_pct).
                if str(body.get("markup_source") or "") == "manual":
                    markup = _num_or(body.get("markup"), DEFAULT_MARKUP)
                else:
                    markup = (seller_pct / 100.0) if seller_pct is not None else DEFAULT_MARKUP
                # Шаблон.xlsx: маржа за сумму = qty * (цена_с_маржой − вход),
                # цена_с_маржой = вход * (1 + наценка) → qty * вход * наценка.
                sum_rub += qty * price
                margin_rub += qty * price * markup

            margin_pct = seller_pct if seller_pct is not None else DEFAULT_MARGIN_PCT
            delivery = _num_or(seller_body.get("delivery_rub"), 0.0) or 0.0
            offers_count = offers_count_by_seller.get(token, 0)
            selected_count = len(seller_lines)

            existing = proc_by_seller.get(token)
            # Доставка поставщика (из реестра) — всегда расход: вычитается из
            # маржи. Отдельного включения больше нет (колонка убрана из UI).
            if existing is None:
                body = {
                    "seller": display_name,
                    "is_registered": True,
                    "offers_count": offers_count,
                    "selected_count": selected_count,
                    "qty_total": qty_total,
                    "sum_rub": round(sum_rub, 2),
                    "margin_pct": margin_pct,
                    "delivery_rub": delivery,
                    "sum_margin_rub": round(margin_rub - delivery, 2),
                    "sum_with_margin_rub": round(sum_rub + margin_rub + delivery, 2),
                }
                await io.create(PROCUREMENT_TABLE, body)
                stats["procurement_created"] += 1
                stats["procurement_rows"] += 1
                continue

            row_id = str(existing.get("row_id") or "")
            body = dict(existing.get("body") or {})
            updates = {
                "is_registered": True,
                "offers_count": offers_count,
                "selected_count": selected_count,
                "qty_total": qty_total,
                "sum_rub": round(sum_rub, 2),
                "delivery_rub": delivery,
                "sum_margin_rub": round(margin_rub - delivery, 2),
                "sum_with_margin_rub": round(sum_rub + margin_rub + delivery, 2),
                # margin_pct — зеркало реестра поставщиков: правка в «Закупке»
                # пишется в реестр через procurement_apply, обратная связь
                # приходит отсюда. Расхождений быть не должно.
                "margin_pct": margin_pct,
            }
            changed = any(body.get(k) != v for k, v in updates.items())
            if changed:
                body.update(updates)
                await io.update(PROCUREMENT_TABLE, row_id, body)
                stats["procurement_updated"] += 1
            stats["procurement_rows"] += 1

        # поставщики без позиций / отключённые — строки удаляем
        for token, row in proc_by_seller.items():
            if token not in keep_sellers:
                await io.delete(PROCUREMENT_TABLE, str(row.get("row_id") or ""))
        return stats

    # ------------------------------------------------- закупка: ручные правки

    async def apply_procurement_row(
        self,
        io: ModuleRowIO,
        *,
        row_id: str,
    ) -> dict[str, Any]:
        """Ручная правка строки «Закупка»: маржа → реестр и бюджетные строки.

        Маржа поставщика живёт в trusted_sellers (реестр общий на контур):
        правка в «Закупке» пишется туда, дальше агрегаты пересобираются тем же
        «проектным» проходом, что и обычный sync (_sync_procurement) — иначе
        суммы строки после apply расходятся с суммами после sync.
        """
        proc_rows = [r for r in await io.list(PROCUREMENT_TABLE) if isinstance(r, dict)]
        row = next((r for r in proc_rows if str(r.get("row_id")) == row_id), None)
        if row is None:
            raise AppError(
                code="NOT_FOUND", title="Not Found", status=404, detail="procurement row not found"
            )
        body = dict(row.get("body") or {})
        seller = str(body.get("seller") or "").strip()
        token = seller.casefold()

        sellers = [r for r in await io.list(SELLERS_TABLE) if isinstance(r, dict)]
        registry = _SellerRegistry(sellers)
        entry = registry.find(token)
        sellers_updated = 0
        if entry is not None:
            seller_body = dict(entry["body"])
            entry_id = str(entry["row_id"] or "")
            margin_pct = _num_or(body.get("margin_pct"), None)
            if margin_pct is not None and _num_or(seller_body.get("margin_pct"), None) != margin_pct:
                seller_body["margin_pct"] = margin_pct
                await io.update(SELLERS_TABLE, entry_id, seller_body)
                sellers_updated += 1
                entry["body"] = seller_body

        # маржа поставщика → бюджетные строки активного чата (не ручные);
        # строки других чатов обновит их собственный прогон пайплайна, а
        # агрегаты «Закупки» уже считаются по актуальной марже реестра.
        budget_rows = [r for r in await io.list(BUDGET_TABLE) if isinstance(r, dict)]
        budget_updated = 0
        for b in budget_rows:
            bbody = dict(b.get("body") or {})
            if str(bbody.get("seller") or "").strip().casefold() != token:
                continue
            if str(bbody.get("markup_source") or "") == "manual":
                continue
            pct = _num_or(body.get("margin_pct"), None)
            want_markup = (pct / 100.0) if pct is not None else DEFAULT_MARKUP
            if _num_or(bbody.get("markup"), None) != want_markup:
                bbody["markup"] = want_markup
                bbody["markup_source"] = "seller" if pct is not None else "default"
                await io.update(BUDGET_TABLE, str(b.get("row_id") or ""), bbody)
                budget_updated += 1

        # Агрегаты строки — единым проектным проходом (бюджетные снапшоты всех
        # чатов, выбор ?? best), а не только по чекбоксам активного чата.
        proc_stats = await self._sync_procurement(io, registry=registry)
        return {
            "kind": "equipment.procurement_apply",
            "row_id": row_id,
            "sellers_updated": sellers_updated,
            "budget_updated": budget_updated,
            **proc_stats,
        }


def _valid_match_kind(raw: Any) -> str:
    kind = str(raw or "").strip().lower()
    return kind if kind in MATCH_KINDS else MATCH_ANALOG


async def cascade_equipment_delete(
    session: AsyncSession,
    *,
    cabinet_id: str,
    project_id: str | None,
    table_slug: str,
    row_id: str,
    row_body: dict[str, Any],
    principal: Principal,
    employee: EmployeeRow | None,
    session_id: str | None,
    io: ModuleRowIO | None = None,
) -> dict[str, int]:
    """Каскадное удаление связанных строк mod_equipment.

    Связь по позиции заказчика (request_lines.row_id):
    - удаление позиции → её found_groups + found_offers + budget_lines
      + сборки позиции (equipment_builds) вместе с их группами-кандидатами
      слотов и офферами;
    - удаление строки бюджета → корневая позиция со всем каскадом;
    - удаление found_groups → её офферы;
    - удаление сборки → её группы-кандидаты слотов (build_id) и их офферы.

    Записи идут через ModuleRowIO(run_actions=False) в бакете чата самой
    строки (session_id), пайплайн дочищает остатки на следующем прогоне.
    """
    if table_slug not in (LINES_TABLE, BUDGET_TABLE, GROUPS_TABLE, BUILDS_TABLE):
        return {}
    if _IN_CASCADE_DELETE.get():
        return {}  # каскад уже идёт — не рекурсируем через сервисные хуки
    _IN_CASCADE_DELETE.set(True)
    try:
        if io is None:
            io = ModuleRowIO(
                session,
                cabinet_id=cabinet_id,
                project_id=project_id,
                principal=principal,
                employee=employee,
                session_id=session_id,
            )
        deleted: dict[str, int] = {}

        async def _delete_where(table: str, field: str, value: str) -> None:
            for row in await io.list(table):
                body = row.get("body") or {}
                if str(body.get(field) or "") != value:
                    continue
                rid = str(row.get("row_id") or "")
                if await io.delete(table, rid):
                    deleted[table] = deleted.get(table, 0) + 1

        async def _cascade_build(build_id: str) -> None:
            """Сборка → её группы-кандидаты слотов и их офферы."""
            slot_groups = [
                row
                for row in await io.list(GROUPS_TABLE)
                if str((row.get("body") or {}).get("build_id") or "") == build_id
            ]
            for row in slot_groups:
                gid = str(row.get("row_id") or "")
                await _delete_where(OFFERS_TABLE, "group_id", gid)
                if await io.delete(GROUPS_TABLE, gid):
                    deleted[GROUPS_TABLE] = deleted.get(GROUPS_TABLE, 0) + 1

        async def _cascade_line(line_id: str) -> None:
            # офферы — по line_id и по группам позиции (страховка на разрыв дубля)
            await _delete_where(OFFERS_TABLE, "line_id", line_id)
            await _delete_where(GROUPS_TABLE, "line_id", line_id)
            await _delete_where(BUDGET_TABLE, "line_id", line_id)
            # WAVE10: сборки позиции и их комплектующие. Группы слотов несут
            # build_id (не line_id), поэтому без этого каскада они остаются
            # «живыми» для пайплайна и продолжают материализоваться.
            builds = [
                row
                for row in await io.list(BUILDS_TABLE)
                if str((row.get("body") or {}).get("line_id") or "") == line_id
            ]
            for row in builds:
                bid = str(row.get("row_id") or "")
                await _cascade_build(bid)
                if await io.delete(BUILDS_TABLE, bid):
                    deleted[BUILDS_TABLE] = deleted.get(BUILDS_TABLE, 0) + 1

        if table_slug == LINES_TABLE:
            await _cascade_line(row_id)
        elif table_slug == BUDGET_TABLE:
            line_id = str(row_body.get("line_id") or "")
            if line_id:
                # удаляем корневую позицию — она подтянет свой каскад
                lines = await io.list(LINES_TABLE)
                line = next(
                    (r for r in lines if str(r.get("row_id") or "") == line_id), None
                )
                if line is not None and await io.delete(LINES_TABLE, line_id):
                    deleted[LINES_TABLE] = deleted.get(LINES_TABLE, 0) + 1
                await _cascade_line(line_id)
        elif table_slug == GROUPS_TABLE:
            await _delete_where(OFFERS_TABLE, "group_id", row_id)
        elif table_slug == BUILDS_TABLE:
            await _cascade_build(row_id)
        return deleted
    finally:
        _IN_CASCADE_DELETE.set(False)


def mark_manual_overrides(
    *,
    table_slug: str,
    existing_body: dict[str, Any],
    body: dict[str, Any],
) -> dict[str, Any]:
    """Ручные правки UI поверх синхронизации (вызывается только для UI-записей).

    - budget_lines: изменение markup → markup_source='manual' (pipeline не
      перезаписывает маржу таких строк);
    - found_offers: изменение синхронизируемого поля → body.manual[field]=true
      (pipeline не перезаписывает это поле оффера).
    """
    if table_slug == "budget_lines":
        if "markup" in body and body.get("markup") != existing_body.get("markup"):
            body["markup_source"] = "manual"
        return body
    if table_slug == "found_offers":
        manual = body.get("manual") if isinstance(body.get("manual"), dict) else {}
        touched = False
        for field in SYNCED_OFFER_FIELDS:
            if field not in body or field not in existing_body:
                continue
            if body.get(field) != existing_body.get(field):
                manual[field] = True
                touched = True
        if touched:
            body["manual"] = manual
        return body
    return body


def _score_for_match(match_kind: str) -> float:
    return {MATCH_EXACT: 1.0, MATCH_ANALOG: 0.5, MATCH_DOUBT: 0.25}.get(match_kind, 0.5)


_MATCH_LABELS = {
    MATCH_EXACT: "Точное",
    MATCH_ANALOG: "Аналог",
    MATCH_DOUBT: "Есть сомнения",
}


def _match_label(match_kind: str, in_stock: bool, *, has_offers: bool = True) -> str:
    """«Совпадение» для UI: «Точное» / «Аналог» / «Есть сомнения» +
    « (под заказ)», когда позиция не в наличии. Без офферов вовсе —
    « (нет офферов)»: «под заказ» при пустой группе вводил в заблуждение
    (инцидент 2026-10-07: каталог был в error, группы без офферов
    показывались как «Точное (под заказ)»)."""
    base = _MATCH_LABELS.get(match_kind, "Аналог")
    if not has_offers:
        # Без офферов суффикс не нужен: «Нет оффера» показывается в колонке
        # «Товар» warning-цветом (UI).
        return base
    return base if in_stock else f"{base} (под заказ)"


def _benefit_label(
    *,
    is_effective: bool,
    price: float | None,
    reference: float | None,
    min_price: float | None,
    total_offers: int,
) -> tuple[str, str]:
    """Бейдж «Выгода» для оффера — та же семантика, что в клиентском
    computeBenefitBadges ( Flutter benefit_badge.dart):

    - единственный оффер позиции → «Единственный»;
    - эффективный (выбранный ?? best) и при этом минимальная цена →
      «Лучшая цена»;
    - эффективный, но дороже минимума → переплата «−n%» (красный);
    - остальные → выгода относительно эффективной цены: «+n%» зелёный
      (дешевле), «−n%» красный (дороже), «Одинаковая» при разнице <0.5%.

    Возвращает (label, tone): tone ∈ best|better|worse|same.
    """
    if total_offers <= 1:
        return "Единственный", "same"
    if price is None:
        return "", "same"
    if is_effective:
        if min_price is not None and price <= min_price + 0.005:
            return "Лучшая цена", "best"
        if min_price and min_price > 0:
            overpay = (min_price - price) / min_price * 100
            return f"−{abs(overpay):.1f}%", "worse"
        return "", "same"
    if reference is None or reference <= 0:
        return "", "same"
    if abs(price - reference) <= 0.005:
        return "Одинаковая", "same"
    savings_pct = (reference - price) / reference * 100
    if abs(savings_pct) < 0.5:
        return "Одинаковая", "same"
    if savings_pct > 0:
        return f"+{savings_pct:.1f}%", "better"
    return f"−{abs(savings_pct):.1f}%", "worse"


class _SellerRegistry:
    """name/alias (case-insensitive) → строка trusted_sellers."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._by_token: dict[str, dict[str, Any]] = {}
        for r in rows:
            body = r.get("body") or {}
            name = str(body.get("name") or "").strip()
            if not name:
                continue
            entry = {"row_id": str(r.get("row_id") or ""), "body": body}
            for tok in [name] + alias_tokens(body.get("aliases")):
                self._by_token.setdefault(tok.casefold(), entry)

    def find(self, token: str) -> dict[str, Any] | None:
        return self._by_token.get((token or "").strip().casefold())

    def add(self, name: str, body: dict[str, Any]) -> None:
        entry = {"row_id": "", "body": body}
        self._by_token.setdefault(name.strip().casefold(), entry)

    def margin_pct(self, name: str) -> float | None:
        entry = self.find(name)
        if entry is None:
            return None
        return _float_or_none(entry["body"].get("margin_pct"))

    def is_priority(self, seller: str) -> bool:
        entry = self.find(seller)
        if entry is None:
            return False
        return entry["body"].get("priority_purchase") is True

    def is_disabled(self, seller: str) -> bool:
        entry = self.find(seller)
        if entry is None:
            return False
        return entry["body"].get("is_enabled") is False
