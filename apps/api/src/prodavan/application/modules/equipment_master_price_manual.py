"""Ручное добавление товара в «Мастер прайс» через OpenSearch.

Мастер-прайс — виртуальная таблица поверх OS-индексов готовых каталогов,
поэтому «добавить товар вручную» нельзя выразить записью в БД модуля: строка
обязана попасть в индекс, иначе она не появится ни в списке, ни в экспорте.

Схема: строка реальной таблицы `master_price_manual` (её пишет обычная
meta-форма) зеркалится в ОТДЕЛЬНЫЙ индекс `equipment__c_master_price_manual`.
Отдельный — а не в индекс каталога — потому что индексация каталога делает
wipe+reindex: ручные позиции в чужом индексе были бы стёрты при первой же
переиндексации поставщика, и товар молча пропал бы из прайса.

Почему индекс вообще виден мастер-прайсу: `MasterPriceService._ready_indexes`
добавляет его к готовым каталогам, а документ несёт `supplier` — тот же
keyword-фильтр по списку поставщиков с флагом `master_price`, что и у
каталожных строк. Дальше строка живёт по общим правилам: сортировка, наценка,
экспорт, удаление (`os:{doc_id}`).

Дедуп: `doc_id = sha1(cabinet|supplier|pn-or-title)`. Повторное добавление того
же товара того же продавца обновляет документ (цена/наличие), а не плодит
копию — и не даёт «двух прайсов» после многократного сохранения формы, которая
пишет поля по одному.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

logger = logging.getLogger(__name__)

OS_NAMESPACE = "equipment"

#: Таблица, которую пишет meta-форма (реальные строки в БД модуля).
MANUAL_TABLE = "master_price_manual"

#: Логическое имя индекса. Кабинет НЕ в имени: мастер-прайс читает индексы по
#: списку каталогов кабинета, а ручные позиции фильтруются по `cabinet_id`
#: внутри документа — так один индекс не плодит десятки мелких.
MANUAL_INDEX = "c_master_price_manual"

DEFAULT_CURRENCY = "RUB"


def manual_doc_id(*, cabinet_id: str, supplier: str, part_number: str, title: str) -> str:
    """Стабильный id документа: один товар продавца = один документ.

    Партномер приоритетнее названия: у одного продавца «Мышь Logitech M100»
    может встретиться в двух строках с разными PN — это разные товары.
    Без PN опираемся на название (тогда повторное сохранение обновляет строку).
    """
    key = "|".join(
        (
            (cabinet_id or "").strip().lower(),
            (supplier or "").strip().casefold(),
            (part_number or "").strip().casefold(),
            (title or "").strip().casefold(),
        )
    )
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def _price_pair(raw: Any) -> tuple[str, float | None]:
    """Цена → (строковое значение, число). Пустое → ("", None)."""
    if raw is None or raw == "":
        return "", None
    if isinstance(raw, bool):
        return "", None
    if isinstance(raw, (int, float)):
        return str(raw), float(raw)
    text = str(raw).strip().replace(" ", "").replace(",", ".")
    if not text:
        return "", None
    try:
        return text, float(text)
    except ValueError:
        # «По запросу» и прочие нечисловые значения: строка остаётся как есть,
        # числовое поле пустое (сортировка/наценка по ней не считаются).
        return str(raw).strip(), None


def _truthy(raw: Any, *, default: bool = True) -> bool:
    if raw is None or raw == "":
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().casefold() in {"1", "true", "да", "yes", "есть", "on"}


def manual_doc(
    body: dict[str, Any],
    *,
    cabinet_id: str,
    company_id: str = "",
    project_id: str | None = None,
) -> dict[str, Any]:
    """Строка `master_price_manual` → документ OS (поля канонического маппинга).

    Набор полей совпадает с каталожным документом: мастер-прайс читает
    `supplier`/`title`/`title.raw`/`src_hash`/`price_num`/`rrc_num` одними и
    теми же запросами, поэтому ручная строка не требует отдельной ветки чтения.
    """
    from prodavan.application.modules.equipment_catalog_opensearch import source_hash

    supplier = str(body.get("supplier") or "").strip()
    title = str(body.get("title") or "").strip()
    pn = str(body.get("part_number") or "").strip()
    price_raw, price_num = _price_pair(body.get("price"))
    rrc_raw, rrc_num = _price_pair(body.get("rrc"))
    currency = str(body.get("currency") or DEFAULT_CURRENCY).strip().upper() or DEFAULT_CURRENCY
    return {
        "catalog_id": MANUAL_INDEX,
        "source_catalog": "Мастер-прайс (вручную)",
        "company_id": (company_id or "").strip(),
        "project_id": project_id,
        "cabinet_id": (cabinet_id or "").strip(),
        "part_number": pn,
        "title": title,
        "brand": str(body.get("brand") or "").strip(),
        "price": price_raw,
        "rrc": rrc_raw,
        "rrc_num": rrc_num,
        "supplier": supplier,
        "lead_time": str(body.get("lead_time") or "").strip(),
        "currency": currency,
        "price_num": price_num,
        "in_stock": _truthy(body.get("in_stock")),
        # Та же идентичность позиции, что у каталога (supplier|title) — по ней
        # подбор товаров матчит предложение к позиции заказчика.
        "src_hash": source_hash(supplier, title),
    }


def manual_mappings() -> dict[str, Any]:
    """Маппинг индекса: ровно канонический (запросы мастер-прайса — общие)."""
    from prodavan.application.modules.equipment_catalog_opensearch import (
        canonical_doc_mappings,
    )

    return canonical_doc_mappings()


async def _company_id_for_cabinet(session: Any, cabinet_id: str) -> str:
    from sqlalchemy import select as _select

    from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow

    q = await session.execute(
        _select(CabinetInstanceRow.company_id).where(CabinetInstanceRow.id == cabinet_id)
    )
    return str(q.scalar_one_or_none() or "")


async def index_manual_row(
    session: Any,
    *,
    cabinet_id: str,
    body: dict[str, Any],
    company_id: str | None = None,
    project_id: str | None = None,
) -> bool:
    """Зеркалить строку в OS. Никогда не роняет вызывающую запись.

    Форма сохраняет поля по одному, поэтому вызов идёт на каждое изменение:
    upsert по стабильному doc_id + refresh, чтобы товар сразу появился в
    мастер-прайсе (без refresh он «всплывает» через ~1с и выглядит как
    потерянная запись).
    """
    if not cabinet_id:
        return False
    body = body if isinstance(body, dict) else {}
    # Неполная строка (форма только создана, ни одного поля ещё нет) — нечего
    # индексировать: пустой документ засорил бы прайс строкой без названия.
    if not str(body.get("title") or "").strip() or not str(body.get("supplier") or "").strip():
        return False
    try:
        from prodavan.core.infra.opensearch_manager import get_search_index_service

        cid = (company_id or "").strip() or await _company_id_for_cabinet(session, cabinet_id)
        svc = get_search_index_service()
        await svc.ensure_index(
            namespace=OS_NAMESPACE,
            index=MANUAL_INDEX,
            mappings=manual_mappings(),
            company_id=cid or "platform",
            cabinet_id=None,
            session=session,
        )
        doc_id = manual_doc_id(
            cabinet_id=cabinet_id,
            supplier=str(body.get("supplier") or ""),
            part_number=str(body.get("part_number") or ""),
            title=str(body.get("title") or ""),
        )
        await svc.bulk_index(
            namespace=OS_NAMESPACE,
            index=MANUAL_INDEX,
            documents=[
                {
                    "doc_id": doc_id,
                    "document": manual_doc(
                        body,
                        cabinet_id=cabinet_id,
                        company_id=cid,
                        project_id=project_id,
                    ),
                }
            ],
            company_id=cid or "platform",
            cabinet_id=None,
            refresh=True,
            session=session,
        )
        return True
    except Exception:  # noqa: BLE001 — индексация не должна ломать сохранение
        logger.exception(
            "master_price manual index failed cabinet=%s title=%s",
            cabinet_id,
            str(body.get("title") or "")[:80],
        )
        return False


async def delete_manual_doc(
    session: Any,
    *,
    cabinet_id: str,
    body: dict[str, Any],
    company_id: str | None = None,
) -> bool:
    """Убрать документ ручной позиции (строка удалена в UI)."""
    if not cabinet_id:
        return False
    body = body if isinstance(body, dict) else {}
    try:
        from prodavan.core.infra.opensearch_manager import get_search_index_service

        cid = (company_id or "").strip() or await _company_id_for_cabinet(session, cabinet_id)
        doc_id = manual_doc_id(
            cabinet_id=cabinet_id,
            supplier=str(body.get("supplier") or ""),
            part_number=str(body.get("part_number") or ""),
            title=str(body.get("title") or ""),
        )
        await get_search_index_service().delete_document(
            namespace=OS_NAMESPACE,
            index=MANUAL_INDEX,
            doc_id=doc_id,
            company_id=cid or "platform",
            cabinet_id=None,
            refresh=True,
            session=session,
        )
        return True
    except Exception:  # noqa: BLE001
        logger.exception("master_price manual delete failed cabinet=%s", cabinet_id)
        return False


def manual_sellers(seller_rows: list[dict[str, Any]]) -> list[str]:
    """Продавцы, доступные для ручного товара: флаг `master_price`, не отключены.

    Порядок — тот, что нужен форме: «ИТ Взлёт» первым (историческая номенклатура
    каталога и основной поставщик), остальные по алфавиту. Если «ИТ Взлёт» нет
    среди отмеченных — просто алфавит, поэтому первым окажется он же по буквам
    (И) либо ближайший по алфавиту.
    """
    names: list[str] = []
    for row in seller_rows:
        body = row.get("body") if isinstance(row.get("body"), dict) else row
        if not isinstance(body, dict):
            continue
        if body.get("master_price") is not True:
            continue
        if body.get("is_enabled") is False:
            continue
        name = str(body.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
    return sorted(names, key=_seller_sort_key)


#: Приоритетный продавец по умолчанию.
PREFERRED_SELLER = "ИТ Взлёт"


def _fold_seller(name: str) -> str:
    """Нормализация имени для сравнения/сортировки.

    `ё → е` и схлопывание пробелов: в данных поставщиков встречается и «Взлёт»,
    и «Взлет», и неразрывный пробел — без нормализации «ИТ Взлёт» переставал
    быть приоритетным и уезжал в конец алфавита (поймано тестом).
    """
    text = (name or "").replace("\u00a0", " ").strip().casefold()
    return " ".join(text.replace("ё", "е").split())


_PREFERRED_FOLDED = _fold_seller(PREFERRED_SELLER)


def _seller_sort_key(name: str) -> tuple[int, str]:
    folded = _fold_seller(name)
    return (0 if folded == _PREFERRED_FOLDED else 1, folded)
