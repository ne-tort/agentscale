"""OpenSearch-индекс «Интернет товары» (web_products).

Отдельный индекс на КАБИНЕТ (`equipment__web_products_{cabinet}`), а не общий
каталог: товары из интернета живут 72 часа и не должны смешиваться с
индексированными каталогами поставщиков — иначе их переиндексация затирала бы
друг друга.

Что даёт индекс: поиск по найденным в интернете товарам идёт тем же путём, что
и по каталогу (партномер / `src_hash` / текст), поэтому пайплайн подбора
(`found_offers`) видит их наравне с каталогом и выбирает по цене.

Дедупликация: `doc_id = sha1(cabinet|part_number|shop|title)`. Повторное
добавление того же товара из того же магазина перезаписывает документ, а не
плодит копию — цены и наличие обновляются.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

logger = logging.getLogger(__name__)

OS_NAMESPACE = "equipment"
_WEB_PRODUCTS_PREFIX = "web_products"


def web_products_index_name(cabinet_id: str) -> str:
    """Логическое имя индекса интернет-товаров кабинета."""
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (cabinet_id or ""))
    safe = safe.strip("_").lower() or "unknown"
    if not safe[0].isalpha():
        safe = f"c_{safe}"
    return f"{_WEB_PRODUCTS_PREFIX}_{safe}"[:64]


def web_product_doc_id(*, cabinet_id: str, part_number: str, shop: str, title: str) -> str:
    """Стабильный id документа: один товар = один документ."""
    key = "|".join(
        (
            (cabinet_id or "").strip().lower(),
            (part_number or "").strip().casefold(),
            (shop or "").strip().casefold(),
            (title or "").strip().casefold(),
        )
    )
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def web_product_doc(row_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Строка web_products → документ индекса (те же поля, что у каталога).

    `src_hash` заполняется партномером: пайплайн подбора матчит каталог по
    `part_number` ИЛИ `src_hash`, поэтому товар без партномера не потеряется,
    если агент передал хотя бы название.
    """
    pn = str(body.get("part_number") or "").strip()
    title = str(body.get("title") or "").strip()
    price = body.get("price")
    try:
        price_num = float(price) if price not in (None, "") else None
    except (TypeError, ValueError):
        price_num = None
    return {
        "row_id": row_id,
        "part_number": pn,
        "src_hash": pn.casefold() if pn else "",
        "name": title,
        "brand": str(body.get("brand") or "").strip(),
        "supplier": str(body.get("shop") or "").strip() or "internet",
        "price_rub": price_num,
        "currency": "RUB",
        "qty": str(body.get("qty") or "").strip(),
        "url": str(body.get("url") or "").strip(),
        "source_kind": "web",
        "line_id": str(body.get("line_id") or "").strip(),
        "project_ids": body.get("project_ids") or [],
    }


async def index_web_product(
    session: Any,
    *,
    cabinet_id: str,
    row_id: str,
    body: dict[str, Any],
    company_id: str | None = None,
) -> bool:
    """Проиндексировать одну строку web_products (идемпотентно, upsert по doc_id).

    Никогда не роняет вызывающий код: подбор товаров — не критичный путь, а
    недоступный OpenSearch не должен ломать добавление товара в базу.
    """
    if not cabinet_id or not row_id:
        return False
    try:
        from prodavan.core.infra.opensearch_manager import get_search_index_service

        svc = get_search_index_service()
        index = web_products_index_name(cabinet_id)
        doc = web_product_doc(row_id, body)
        await svc.ensure_index(
            namespace=OS_NAMESPACE,
            index=index,
            mappings=_web_products_mappings(),
            company_id=company_id or "platform",
            cabinet_id=cabinet_id,
            session=session,
        )
        await svc.bulk_index(
            namespace=OS_NAMESPACE,
            index=index,
            documents=[
                {
                    "doc_id": web_product_doc_id(
                        cabinet_id=cabinet_id,
                        part_number=str(body.get("part_number") or ""),
                        shop=str(body.get("shop") or ""),
                        title=str(body.get("title") or ""),
                    ),
                    "document": doc,
                }
            ],
            company_id=company_id or "platform",
            cabinet_id=cabinet_id,
            refresh=True,
            session=session,
        )
        return True
    except Exception:  # noqa: BLE001 — индексация не должна ломать запись
        logger.exception(
            "web_products index failed cabinet=%s row=%s", cabinet_id, row_id
        )
        return False


async def delete_web_product_doc(
    session: Any,
    *,
    cabinet_id: str,
    body: dict[str, Any],
    company_id: str | None = None,
) -> bool:
    """Удалить документ из индекса (при удалении строки / истечении TTL)."""
    if not cabinet_id:
        return False
    try:
        from prodavan.core.infra.opensearch_manager import get_search_index_service

        svc = get_search_index_service()
        index = web_products_index_name(cabinet_id)
        doc_id = web_product_doc_id(
            cabinet_id=cabinet_id,
            part_number=str(body.get("part_number") or ""),
            shop=str(body.get("shop") or ""),
            title=str(body.get("title") or ""),
        )
        await svc.delete_document(
            namespace=OS_NAMESPACE,
            index=index,
            doc_id=doc_id,
            company_id=company_id or "platform",
            cabinet_id=cabinet_id,
            session=session,
        )
        return True
    except Exception:  # noqa: BLE001
        logger.exception("web_products delete doc failed cabinet=%s", cabinet_id)
        return False


def _web_products_mappings() -> dict[str, Any]:
    """Маппинг: текстовый поиск по названию + точный по ключам подбора."""
    return {
        "properties": {
            "row_id": {"type": "keyword"},
            "part_number": {"type": "keyword"},
            "src_hash": {"type": "keyword"},
            "name": {
                "type": "text",
                "fields": {"raw": {"type": "keyword", "ignore_above": 512}},
            },
            "brand": {"type": "keyword"},
            "supplier": {"type": "keyword"},
            "price_rub": {"type": "double"},
            "currency": {"type": "keyword"},
            "qty": {"type": "keyword"},
            "url": {"type": "keyword", "index": False},
            "source_kind": {"type": "keyword"},
            "line_id": {"type": "keyword"},
            "project_ids": {"type": "keyword"},
        }
    }
