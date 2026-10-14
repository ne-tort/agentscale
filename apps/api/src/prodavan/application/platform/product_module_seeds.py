"""Product module catalog meta — used by Alembic seed and tests."""

from __future__ import annotations

from typing import Any

from prodavan.application.platform.equipment_prompt_content import (
    EQUIPMENT_AGENTS_MD,
    EQUIPMENT_RULES_FILES,
)

# Default prompt path cards for profile_default (empty files_json until edited).
_PROMPT_PATH_SEEDS: list[tuple[str, str, str]] = [
    ("path_agents", "AGENTS.md", ""),
    ("path_rules", "rules", "rules"),
    ("path_skills", "skills", "skills"),
    ("path_output_schema", "output_schema", "prompts/output-schema"),
    ("path_guardrails", "guardrails", "prompts/guardrails"),
    ("path_examples", "examples", "prompts/examples"),
    ("path_others", "others", "prompts/others"),
]


def _empty(title_ru: str, title_en: str, *, icon: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"title": {"ru": title_ru, "en": title_en}}
    if icon:
        out["icon"] = icon
    return out


def _equipment_mcp_list_header() -> dict[str, Any]:
    """Shared MCP zip upload strip for catalogs / request_lines / found_offers lists."""
    return {
        "table_slug": "equipment_mcp",
        "ensure_row": {
            "name": "prodavan-equipment",
            "version": "3.0.0",
            "enabled": True,
        },
        "fields": [
            {
                "column": "file_ref",
                "widget": "file_upload",
                "accept": ".zip",
                "label": {"ru": "MCP", "en": "MCP"},
                "empty_style": "warning",
            }
        ],
    }


def _eq_field(
    key: str,
    label_ru: str,
    label_en: str,
    *,
    kind: str = "text",
    required: bool = False,
    compat_domain: str = "",
    values: list[str] | None = None,
    groups: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Характеристика типа комплектующего (шаблон для карточек и слотов).

    kind: text (свободное), enum (значение из values), groups (значение из
    groups — «диапазон» типа «512 ГБ»). required — обязательно для идентификации
    совместимости при заполнении карточки. compat_domain связывает типы в
    матрицу совместимости: поля разных типов с одним доменом (socket, ram_type,
    form_factor) должны совпадать в сборке.
    """
    out: dict[str, Any] = {
        "key": key,
        "label": {"ru": label_ru, "en": label_en},
        "kind": kind,
        "required": required,
    }
    if compat_domain:
        out["compat_domain"] = compat_domain
    if values:
        out["values"] = values
    if groups:
        out["groups"] = groups
    return out


def _gb_groups(values: list[int]) -> list[dict[str, str]]:
    return [{"value": str(v), "label": f"{v} ГБ"} for v in values]


# Общие домены совместимости: списки значений дублируются в полях разных типов
# намеренно (тип — самодостаточный шаблон), а связывает их имя домена.
_SOCKET_VALUES = ["AM4", "AM5", "LGA1200", "LGA1700", "LGA1851", "SP3", "SP5", "sTR5"]
_RAM_TYPE_VALUES = ["DDR4", "DDR5"]
_FORM_FACTOR_VALUES = ["ITX", "mATX", "ATX", "E-ATX", "SSI-EEB"]

# WAVE13: виды сборок. ПК/сервер — собираются из комплектующих; ноутбук/
# неттоп/моноблок/телефон — устройства-комплекты: один слот самого устройства,
# комплектующих нет, а требования к характеристикам выбирают модель.
_BUILD_KIND_VALUES = ["pc", "server", "laptop", "nettop", "monoblock", "phone"]
_BUILD_KIND_LABELS = {
    "pc": "ПК",
    "server": "Сервер",
    "laptop": "Ноутбук",
    "nettop": "Неттоп",
    "monoblock": "Моноблок",
    "phone": "Телефон",
}
# build_scope типа комплектующего: в каких видах сборок этот тип доступен.
_BUILD_SCOPE_VALUES = ["all"] + _BUILD_KIND_VALUES
_BUILD_SCOPE_LABELS = {"all": "Все", **_BUILD_KIND_LABELS}

# PC/server component types — order = matching priority (lower sort_order first).
#
# WAVE12: тип — ШАБЛОН для «Карточек комплектующих» и слотов готовых сборок.
# У каждого типа есть обязательные характеристики со значениями из заранее
# заданных групп (не свободные строки): по ним грубо группируется совместимость
# (SSD 128/256/512/1024…, сокет, тип ОЗУ). Поля с одинаковым compat_domain
# образуют матрицу совместимости: cpu.socket ↔ motherboard.socket ↔
# cooling.socket, motherboard.ram_type ↔ ram.ram_type, motherboard.form_factor
# ↔ case.form_factor. Остальные поля — информационные (kind=text).
_EQUIPMENT_TYPE_SEEDS: list[dict[str, Any]] = [
    {
        "row_id": "etype_cpu",
        "name": "Процессор",
        "sort_order": 10,
        "build_scope": "all",
        "fields": [
            _eq_field(
                "socket", "Сокет", "Socket",
                kind="enum", required=True, compat_domain="socket",
                values=_SOCKET_VALUES,
            ),
            _eq_field(
                "cores", "Ядра", "Cores",
                kind="groups", required=True,
                groups=[
                    {"value": str(n), "label": f"{n} ядер"} for n in (2, 4, 6, 8, 12, 16, 24, 32, 64)
                ],
            ),
            _eq_field("generation", "Поколение / платформа", "Generation"),
            _eq_field("threads", "Потоки", "Threads"),
            _eq_field("boost_clock", "Turbo частота", "Boost clock"),
            _eq_field("tdp", "TDP", "TDP"),
        ],
    },
    {
        "row_id": "etype_motherboard",
        "name": "Материнская плата",
        "sort_order": 20,
        "build_scope": "all",
        "fields": [
            _eq_field(
                "socket", "Сокет", "Socket",
                kind="enum", required=True, compat_domain="socket",
                values=_SOCKET_VALUES,
            ),
            _eq_field(
                "ram_type", "Тип ОЗУ", "RAM type",
                kind="enum", required=True, compat_domain="ram_type",
                values=_RAM_TYPE_VALUES,
            ),
            _eq_field(
                "form_factor", "Форм-фактор", "Form factor",
                kind="enum", required=True, compat_domain="form_factor",
                values=_FORM_FACTOR_VALUES,
            ),
            _eq_field("chipset", "Чипсет", "Chipset"),
            _eq_field("ram_slots", "Слоты ОЗУ", "RAM slots"),
            _eq_field("m2_slots", "M.2 слоты", "M.2 slots"),
        ],
    },
    {
        "row_id": "etype_ram",
        "name": "Оперативная память",
        "sort_order": 30,
        "build_scope": "all",
        "fields": [
            _eq_field(
                "ram_type", "Тип ОЗУ", "RAM type",
                kind="enum", required=True, compat_domain="ram_type",
                values=_RAM_TYPE_VALUES,
            ),
            _eq_field(
                "module_capacity", "Объём модуля", "Module capacity",
                kind="groups", required=True,
                groups=_gb_groups([4, 8, 16, 24, 32, 48, 64, 128]),
            ),
            _eq_field("frequency", "Частота", "Frequency"),
            _eq_field("ecc", "ECC", "ECC"),
            _eq_field("registered", "Registered/LRDIMM", "Registered"),
        ],
    },
    {
        "row_id": "etype_storage",
        "name": "Накопитель",
        "sort_order": 40,
        "build_scope": "all",
        "fields": [
            _eq_field(
                "drive_type", "Тип", "Drive type",
                kind="enum", required=True,
                values=["SSD", "HDD"],
            ),
            _eq_field(
                "capacity", "Объём", "Capacity",
                kind="groups", required=True,
                groups=_gb_groups([128, 256, 512, 1024, 2048, 4096, 8192]),
            ),
            _eq_field("interface", "Интерфейс", "Interface"),
            _eq_field("form_factor", "Форм-фактор", "Form factor"),
        ],
    },
    {
        "row_id": "etype_gpu",
        "name": "Видеокарта",
        "sort_order": 50,
        "build_scope": "all",
        "fields": [
            _eq_field(
                "vram", "Память", "VRAM",
                kind="groups", required=True,
                groups=_gb_groups([2, 4, 6, 8, 12, 16, 24, 48]),
            ),
            _eq_field("chipset", "Чипсет / GPU", "Chipset"),
            _eq_field("tdp", "TDP", "TDP"),
            _eq_field("length_mm", "Длина, мм", "Length mm"),
        ],
    },
    {
        "row_id": "etype_psu",
        "name": "Блок питания",
        "sort_order": 60,
        "build_scope": "all",
        "fields": [
            _eq_field(
                "wattage", "Мощность", "Wattage",
                kind="groups", required=True,
                groups=[
                    {"value": str(n), "label": f"{n} Вт"}
                    for n in (400, 450, 550, 650, 750, 850, 1000, 1200, 1600)
                ],
            ),
            _eq_field("efficiency", "КПД", "Efficiency"),
            _eq_field("modular", "Модульность", "Modular"),
        ],
    },
    {
        "row_id": "etype_cooling",
        "name": "Охлаждение",
        "sort_order": 70,
        "build_scope": "all",
        "fields": [
            _eq_field(
                "socket", "Сокет", "Socket",
                kind="enum", required=True, compat_domain="socket",
                values=_SOCKET_VALUES,
            ),
            _eq_field(
                "cooling_kind", "Тип", "Kind",
                kind="groups", required=True,
                groups=[
                    {"value": "air", "label": "Воздушное"},
                    {"value": "aio", "label": "СЖО (водяное)"},
                ],
            ),
            _eq_field("tdp_rating", "TDP рейтинг", "TDP rating"),
            _eq_field("height_mm", "Высота, мм", "Height mm"),
        ],
    },
    {
        "row_id": "etype_case",
        "name": "Корпус",
        "sort_order": 80,
        "build_scope": "all",
        "fields": [
            _eq_field(
                "form_factor", "Форм-фактор материнской платы", "MB form factor",
                kind="enum", required=True, compat_domain="form_factor",
                values=_FORM_FACTOR_VALUES,
            ),
            _eq_field("max_gpu_length", "Макс. GPU", "Max GPU length"),
            _eq_field("max_cooler_height", "Макс. кулер", "Max cooler height"),
            _eq_field("drive_bays", "Отсеки", "Drive bays"),
        ],
    },
    {
        "row_id": "etype_case_fans",
        "name": "Корпусные вентиляторы",
        "sort_order": 85,
        "build_scope": "all",
        "fields": [
            _eq_field(
                "size_mm", "Размер, мм", "Size mm",
                kind="groups",
                groups=[
                    {"value": str(n), "label": f"{n} мм"} for n in (80, 92, 120, 140, 200)
                ],
            ),
            _eq_field("pwm", "PWM", "PWM"),
        ],
    },
    {
        "row_id": "etype_nic",
        "name": "Сетевой адаптер",
        "sort_order": 90,
        "build_scope": "all",
        "fields": [
            _eq_field(
                "port_speed", "Скорость", "Port speed",
                kind="enum",
                values=["1G", "2.5G", "10G", "25G", "40G", "100G"],
            ),
            _eq_field("ports", "Порты", "Ports"),
            _eq_field("interface", "Интерфейс", "Interface"),
        ],
    },
    {
        "row_id": "etype_raid_hba",
        "name": "RAID/HBA контроллер",
        "sort_order": 100,
        "build_scope": "server",
        "fields": [
            _eq_field("interface", "Интерфейс", "Interface"),
            _eq_field("raid_levels", "Уровни RAID", "RAID levels"),
            _eq_field("cache", "Кэш", "Cache"),
        ],
    },
    {
        "row_id": "etype_backplane",
        "name": "Дисковая корзина",
        "sort_order": 110,
        "build_scope": "server",
        "fields": [
            _eq_field("bays", "Отсеки", "Bays"),
            _eq_field("drive_form_factor", "Форм-фактор дисков", "Drive form factor"),
            _eq_field("interface", "Интерфейс", "Interface"),
        ],
    },
    {
        "row_id": "etype_bmc",
        "name": "Модуль управления BMC",
        "sort_order": 120,
        "build_scope": "server",
        "fields": [
            _eq_field("mgmt_port", "Порт управления", "Mgmt port"),
            _eq_field("protocols", "Протоколы", "Protocols"),
        ],
    },
    # WAVE13: устройства-комплекты. У них нет комплектующих — устройство само
    # идёт единым слотом; требования к характеристикам (группы значений)
    # выбирают модель. Подбор и цена — та же механика карточек.
    {
        "row_id": "etype_laptop",
        "name": "Ноутбук",
        "sort_order": 130,
        "build_scope": "laptop",
        "fields": [
            _eq_field("ram_gb", "ОЗУ", "RAM", kind="groups", required=True,
                      groups=_gb_groups([4, 8, 16, 32, 64])),
            _eq_field("storage_gb", "Накопитель", "Storage", kind="groups", required=True,
                      groups=_gb_groups([128, 256, 512, 1024, 2048])),
            _eq_field("screen_inch", "Экран, дюймов", "Screen", kind="groups", required=True,
                      groups=[{"value": v, "label": f"{v}″"} for v in ("13.3", "14", "15.6", "16", "17.3")]),
            _eq_field("battery_wh", "Батарея", "Battery", kind="groups",
                      groups=[{"value": v, "label": f"{v} Вт·ч"} for v in ("38", "42", "50", "56", "70", "90")]),
            _eq_field("cpu_class", "Класс CPU", "CPU class"),
            _eq_field("weight_kg", "Вес, кг", "Weight"),
        ],
    },
    {
        "row_id": "etype_nettop",
        "name": "Неттоп",
        "sort_order": 140,
        "build_scope": "nettop",
        "fields": [
            _eq_field("ram_gb", "ОЗУ", "RAM", kind="groups", required=True,
                      groups=_gb_groups([4, 8, 16, 32, 64])),
            _eq_field("storage_gb", "Накопитель", "Storage", kind="groups", required=True,
                      groups=_gb_groups([128, 256, 512, 1024, 2048])),
            _eq_field("cpu_class", "Класс CPU", "CPU class"),
        ],
    },
    {
        "row_id": "etype_monoblock",
        "name": "Моноблок",
        "sort_order": 150,
        "build_scope": "monoblock",
        "fields": [
            _eq_field("ram_gb", "ОЗУ", "RAM", kind="groups", required=True,
                      groups=_gb_groups([4, 8, 16, 32, 64])),
            _eq_field("storage_gb", "Накопитель", "Storage", kind="groups", required=True,
                      groups=_gb_groups([128, 256, 512, 1024, 2048])),
            _eq_field("screen_inch", "Экран, дюймов", "Screen", kind="groups", required=True,
                      groups=[{"value": v, "label": f"{v}″"} for v in ("21.5", "23.8", "27")]),
            _eq_field("cpu_class", "Класс CPU", "CPU class"),
        ],
    },
    {
        "row_id": "etype_phone",
        "name": "Телефон",
        "sort_order": 160,
        "build_scope": "phone",
        "fields": [
            # «SSD считать за память телефона»: накопитель = storage_gb.
            _eq_field("storage_gb", "Память", "Storage", kind="groups", required=True,
                      groups=_gb_groups([32, 64, 128, 256, 512, 1024])),
            _eq_field("ram_gb", "ОЗУ", "RAM", kind="groups", required=True,
                      groups=_gb_groups([3, 4, 6, 8, 12, 16])),
            _eq_field("screen_inch", "Экран, дюймов", "Screen", kind="groups", required=True,
                      groups=[{"value": v, "label": f"{v}″"} for v in ("5.4", "6.1", "6.4", "6.7", "6.9")]),
            _eq_field("camera_mp", "Камера", "Camera", kind="groups",
                      groups=[{"value": v, "label": f"{v} МП"} for v in ("8", "12", "16", "48", "50", "64", "108", "200")]),
            _eq_field("battery_mah", "Аккумулятор", "Battery", kind="groups",
                      groups=[{"value": v, "label": f"{v} мА·ч"} for v in ("3000", "4000", "4500", "5000", "6000")]),
            _eq_field("sim", "SIM", "SIM"),
        ],
    },
]


def _equipment_type_seed_rows() -> list[dict[str, Any]]:
    return [
        {
            "table_slug": "equipment_types",
            "row_id": t["row_id"],
            "body": {
                "name": t["name"],
                "sort_order": t["sort_order"],
                "build_scope": t["build_scope"],
                "fields_json": list(t["fields"]),
            },
        }
        for t in _EQUIPMENT_TYPE_SEEDS
    ]


def _equipment_prompt_seed_rows() -> list[dict[str, Any]]:
    """Default «Подбор техники» prompts seeded into every instance.

    Row 1: AGENTS.md at the workspace root — auto-injected into the agent
    system message by the claw runtime (rules_import=auto).
    Row 2: module rules in prompts/equipment/*.md — read by the agent
    on demand. Both are template rows: `_apply_seed_rows_to_instances`
    never overwrites bodies edited from the UI.
    """
    return [
        {
            "table_slug": "equipment_prompts",
            "row_id": "equipment_prompts_agents_default",
            "body": {
                "name": "AGENTS.md (системный)",
                "path": "",
                "files_json": [
                    {
                        "id": "agents_md_default",
                        "name": "AGENTS.md",
                        "priority": 10,
                        "body": EQUIPMENT_AGENTS_MD,
                    }
                ],
                "enabled": True,
            },
        },
        {
            "table_slug": "equipment_prompts",
            "row_id": "equipment_prompts_rules_default",
            "body": {
                "name": "Правила подбора (системные)",
                "path": "prompts/equipment",
                "files_json": [
                    {
                        "id": f"rules_default_{name}",
                        "name": name,
                        "priority": priority,
                        "body": body,
                    }
                    for name, body, priority in EQUIPMENT_RULES_FILES
                ],
                "enabled": True,
            },
        },
    ]


_CATALOG_MERGE_SCHEMA = [
    "title",
    "price",
    "part_number",
    "brand",
    "supplier",
    "lead_time",
    "source_catalog",
]

_CATALOG_COLUMN_MAP_SCHEMA = [
    {
        "key": "title",
        "label": {"ru": "Название", "en": "Title"},
        "required": True,
        "synonyms": [
            "title",
            "name",
            "наименование",
            "название",
            "товар",
            "product",
            "description",
            "описание",
        ],
    },
    {
        "key": "price",
        "label": {"ru": "Цена", "en": "Price"},
        "required": True,
        "synonyms": ["price", "цена", "cost", "стоимость", "amount", "сумма"],
    },
    {
        "key": "part_number",
        "label": {"ru": "P/N", "en": "P/N"},
        "required": False,
        "synonyms": [
            "part_number",
            "pn",
            "p_n",
            "sku",
            "артикул",
            "партномер",
            "article",
            "mpn",
        ],
    },
    {
        "key": "brand",
        "label": {"ru": "Бренд", "en": "Brand"},
        "required": False,
        "synonyms": [
            "brand",
            "бренд",
            "make",
            "manufacturer",
            "производитель",
            "vendor_brand",
            "марка",
        ],
    },
    {
        "key": "supplier",
        "label": {"ru": "Поставщик", "en": "Supplier"},
        "required": False,
        "synonyms": ["supplier", "vendor", "поставщик", "продавец", "seller"],
    },
    {
        "key": "lead_time",
        "label": {"ru": "Срок", "en": "Lead time"},
        "required": False,
        "synonyms": [
            "lead_time",
            "delivery",
            "срок",
            "срок_поставки",
            "availability",
            "наличие",
        ],
    },
    {
        "key": "rrc",
        "label": {"ru": "РРЦ", "en": "RRP"},
        "required": False,
        "synonyms": ["rrc", "rrp", "msrp", "ррц", "рец. цена", "recommended price"],
    },
    {
        "key": "currency",
        "label": {"ru": "Валюта", "en": "Currency"},
        "required": False,
        "synonyms": [
            "currency",
            "валюта",
            "вал.",
            "cur",
            "curr",
            "currency_code",
        ],
    },
]


def _project_ids_column(table_slug: str) -> dict[str, Any]:
    return {
        "table_slug": table_slug,
        "name": "project_ids",
        "label": {"ru": "Проекты", "en": "Projects"},
        "type": "json",
        "required": False,
        "default": [],
        "ui": {"widget": "project_multiselect", "empty_means": "all_bound"},
    }


def _prompts_materialize_rules() -> list[dict[str, Any]]:
    return [
        {
            "id": "prompt_paths_files",
            "enabled": True,
            "when": ["project.created", "project.resumed", "project.sync"],
            "priority": 10,
            "source": {
                "type": "rows",
                "table_slug": "prompt_paths",
                "filter": {"profile_id": "{{active_profile_id}}"},
            },
            "target": {"workspace_path": ".", "format": "prompt_paths"},
        }
    ]


def _prompt_path_seed_rows() -> list[dict[str, Any]]:
    return [
        {
            "table_slug": "prompt_paths",
            "row_id": row_id,
            "body": {
                "profile_id": "profile_default",
                "name": name,
                "path": path,
                "files_json": [],
            },
        }
        for row_id, name, path in _PROMPT_PATH_SEEDS
    ]


def mod_prompts_meta() -> dict[str, list[Any]]:
    return {
        "tables": [
            {
                "slug": "prompt_profiles",
                "label": "Профили",
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all"},
            },
            {
                "slug": "profile_settings",
                "label": "Настройки профиля",
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all"},
            },
            {
                "slug": "prompt_paths",
                "label": "Пути промптов",
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all"},
            },
        ],
        "columns": [
            {
                "table_slug": "prompt_profiles",
                "name": "name",
                "label": "Имя",
                "type": "text",
                "required": True,
            },
            {
                "table_slug": "prompt_profiles",
                "name": "is_default",
                "label": "По умолчанию",
                "type": "bool",
                "required": False,
                "default": False,
            },
            _project_ids_column("prompt_profiles"),
            {
                "table_slug": "profile_settings",
                "name": "profile_id",
                "label": "Profile",
                "type": "text",
                "required": True,
            },
            {
                "table_slug": "profile_settings",
                "name": "active",
                "label": "Active",
                "type": "bool",
                "required": False,
                "default": False,
            },
            {
                "table_slug": "prompt_paths",
                "name": "profile_id",
                "label": "Profile",
                "type": "text",
                "required": True,
            },
            {
                "table_slug": "prompt_paths",
                "name": "name",
                "label": "Имя",
                "type": "text",
                "required": True,
            },
            {
                "table_slug": "prompt_paths",
                "name": "path",
                "label": "Путь",
                "type": "text",
                "required": True,
                "default": "",
                "ui": {"normalize": "workspace_path"},
            },
            {
                "table_slug": "prompt_paths",
                "name": "files_json",
                "label": {"ru": "Промпты", "en": "Prompts"},
                "type": "json",
                "required": False,
                "default": [],
            },
        ],
        "views": [
            {
                "slug": "prompt_profiles_list",
                "table_slug": "prompt_profiles",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {"ru": "Профили", "en": "Profiles"},
                    },
                    "title_field": "name",
                    "subtitle_fields": [],
                    "columns": [
                        {
                            "field": "name",
                            "label": {"ru": "Имя", "en": "Name"},
                        },
                        {
                            "field": "project_ids",
                            "label": {"ru": "Проекты", "en": "Projects"},
                        },
                        {
                            "field": "prompts_count",
                            "label": {"ru": "Промпты", "en": "Prompts"},
                            "source": "aggregate.prompt_paths.files_json",
                        },
                    ],
                    "row_tap": {"kind": "open_view", "view": "prompts_hub"},
                    "inline_add": {"field": "name", "title": "Добавить профиль"},
                    "empty": _empty("Нет профилей", "No profiles"),
                },
            },
            {
                "slug": "prompt_profiles_form",
                "table_slug": "prompt_profiles",
                "kind": "form",
                "ui_json": {
                    "version": 1,
                    "kind": "form",
                    "mode": "edit",
                    "title": {"ru": "Профиль", "en": "Profile"},
                    "fields": [
                        {"column": "project_ids", "widget": "project_multiselect"},
                        {"column": "name", "widget": "value"},
                    ],
                },
            },
            {
                "slug": "prompts_hub",
                "table_slug": "prompt_paths",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title_template": {
                            "ru": "Профиль {name}",
                            "en": "Profile {name}",
                        },
                    },
                    "title_field": "name",
                    "subtitle_fields": ["path"],
                    "columns": [
                        {
                            "field": "name",
                            "label": {"ru": "Имя", "en": "Name"},
                        },
                        {
                            "field": "path",
                            "label": {"ru": "Путь", "en": "Path"},
                        },
                        {
                            "field": "files_json",
                            "label": {"ru": "Промпты", "en": "Prompts"},
                            "format": "list_count",
                        },
                    ],
                    "context_bind": {"profile_id": "contextRowId"},
                    "context_header": {
                        "table_slug": "prompt_profiles",
                        "fields": [
                            {"column": "name", "widget": "value"},
                            {
                                "column": "project_ids",
                                "widget": "project_multiselect",
                                "subtitle": "count_or_hide",
                            },
                        ],
                    },
                    "row_tap": {"kind": "open_view", "view": "prompt_path_settings"},
                    "inline_add": {
                        "field": "name",
                        "title": {"ru": "Добавить промпт", "en": "Add prompt"},
                    },
                    "empty": _empty("Нет промптов", "No prompts"),
                    "profile_table": "prompt_profiles",
                    "settings_table": "profile_settings",
                },
            },
            {
                "slug": "prompt_path_settings",
                "table_slug": "prompt_paths",
                "kind": "detail",
                "ui_json": {
                    "version": 1,
                    "kind": "detail",
                    "mode": "edit",
                    "title_template": {
                        "ru": "Промпт {name}",
                        "en": "Prompt {name}",
                    },
                    "fields": [
                        {"column": "name", "widget": "value"},
                        {"column": "path", "widget": "value"},
                        {
                            "column": "files_json",
                            "widget": "prompt_files_editor",
                        },
                    ],
                },
            },
        ],
        "tabs": [
            {
                "id": "tab_prompts",
                "title": "Промпты",
                "subtitle": "Инструкции для агента",
                "order": 10,
                "icon": "psychology_outlined",
                "view_slug": "prompt_profiles_list",
                "table_slug": "prompt_profiles",
                "enabled": True,
                "default_project_bind": "global",
                "nav": {"contour": "employee", "placement": "management"},
            }
        ],
        "materialize": _prompts_materialize_rules(),
        "seed_rows": {
            "items": [
                {
                    "table_slug": "prompt_profiles",
                    "row_id": "profile_default",
                    "body": {"name": "Default", "is_default": True, "project_ids": []},
                },
                {
                    "table_slug": "profile_settings",
                    "row_id": "settings_default",
                    "body": {"profile_id": "profile_default", "active": True},
                },
                *_prompt_path_seed_rows(),
            ]
        },
    }


def mod_files_meta() -> dict[str, list[Any]]:
    return {
        "tables": [
            {
                "slug": "files",
                "label": "Файлы",
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all"},
            }
        ],
        "columns": [
            {
                "table_slug": "files",
                "name": "name",
                "label": "Имя",
                "type": "text",
                "required": True,
            },
            {
                "table_slug": "files",
                "name": "target_path",
                "label": "Путь в workspace",
                "type": "text",
                "required": False,
                "default": "",
            },
            {
                "table_slug": "files",
                "name": "file_ref",
                "label": "Файл",
                "type": "file_ref",
                "required": False,
            },
            _project_ids_column("files"),
        ],
        "views": [
            {
                "slug": "files_list",
                "table_slug": "files",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {"ru": "Файлы", "en": "Files"},
                    },
                    "title_field": "name",
                    "subtitle_fields": ["target_path"],
                    "columns": [
                        {
                            "field": "name",
                            "label": {"ru": "Название", "en": "Name"},
                        },
                        {
                            "field": "target_path",
                            "label": {"ru": "Путь", "en": "Path"},
                        },
                    ],
                    "row_tap": {"kind": "open_form", "view": "files_form"},
                    "inline_add": {"field": "name", "title": "Добавить файл"},
                    "empty": _empty("Нет файлов", "No files", icon="attach_file"),
                },
            },
            {
                "slug": "files_form",
                "table_slug": "files",
                "kind": "form",
                "ui_json": {
                    "version": 1,
                    "kind": "form",
                    "mode": "edit",
                    "title": {"ru": "Файл", "en": "File"},
                    "fields": [
                        {"column": "project_ids", "widget": "project_multiselect"},
                        {"column": "name", "widget": "value"},
                        {"column": "target_path", "widget": "value"},
                        {"column": "file_ref", "widget": "file_upload"},
                    ],
                },
            },
        ],
        "tabs": [
            {
                "id": "tab_files",
                "title": "Файлы",
                "subtitle": "Дополнительные файлы для агента",
                "order": 30,
                "icon": "attach_file",
                "view_slug": "files_list",
                "table_slug": "files",
                "enabled": True,
                "default_project_bind": "global",
                "nav": {"contour": "employee", "placement": "management"},
            }
        ],
        "materialize": [
            {
                "id": "files_to_workspace",
                "enabled": True,
                "when": ["project.created", "project.resumed", "project.sync"],
                "priority": 50,
                "source": {"type": "rows", "table_slug": "files"},
                "target": {
                    "workspace_path": "{{target_path}}",
                    "format": "copy_blob",
                    "field": "file_ref",
                },
            }
        ],
        "seed_rows": {"items": []},
    }


def mod_mcp_meta() -> dict[str, list[Any]]:
    return {
        # Chat display aliases for the prodavan-modules platform MCP server
        # (materialized into every project) — friendly labels instead of
        # "prodavan-modules · modules_list" in the chat.
        "mcp_aliases": [
            {
                "server": "prodavan-modules",
                "tool": "modules_list",
                "label": "Список модулей",
                "description": "Модули проекта и их настройки",
            },
            {
                "server": "prodavan-modules",
                "tool": "module_meta_list",
                "label": "Список настроек модулей",
                "description": "Мета-документы модулей проекта",
            },
            {
                "server": "prodavan-modules",
                "tool": "module_meta_get",
                "label": "Чтение настройки модуля",
                "description": "Чтение мета-документа модуля",
            },
            {
                "server": "prodavan-modules",
                "tool": "module_meta_put",
                "label": "Сохранение настройки модуля",
                "description": "Запись мета-документа модуля",
            },
            {
                "server": "prodavan-modules",
                "tool": "module_data_list",
                "label": "Чтение данных модуля",
                "description": "Строки таблиц данных модуля",
            },
            {
                "server": "prodavan-modules",
                "tool": "module_data_create",
                "label": "Создание записи",
                "description": "Новая строка в таблице данных модуля",
            },
            {
                "server": "prodavan-modules",
                "tool": "module_data_update",
                "label": "Обновление записи",
                "description": "Изменение строки в таблице данных модуля",
            },
            {
                "server": "prodavan-modules",
                "tool": "module_data_delete",
                "label": "Удаление записи",
                "description": "Удаление строки из таблицы данных модуля",
            },
            {
                "server": "prodavan-modules",
                "tool": "module_action_invoke",
                "label": "Действие модуля",
                "description": "Запуск действия из настроек модуля",
            },
        ],
        "tables": [
            {
                "slug": "mcp_packages",
                "label": "MCP packages",
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all"},
            }
        ],
        "columns": [
            {
                "table_slug": "mcp_packages",
                "name": "name",
                "label": "Имя",
                "type": "text",
                "required": True,
            },
            {
                "table_slug": "mcp_packages",
                "name": "version",
                "label": "Version",
                "type": "text",
                "required": True,
                "default": "1.0.0",
            },
            {
                "table_slug": "mcp_packages",
                "name": "enabled",
                "label": "Enabled",
                "type": "bool",
                "required": False,
                "default": True,
            },
            {
                "table_slug": "mcp_packages",
                "name": "file_ref",
                "label": "Zip package",
                "type": "file_ref",
                "required": False,
            },
            _project_ids_column("mcp_packages"),
        ],
        "views": [
            {
                "slug": "mcp_packages_list",
                "table_slug": "mcp_packages",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {"ru": "MCP", "en": "MCP"},
                    },
                    "title_field": "name",
                    "subtitle_fields": ["version"],
                    "columns": [
                        {
                            "field": "name",
                            "label": {"ru": "Название", "en": "Name"},
                        },
                        {
                            "field": "version",
                            "label": {"ru": "Версия", "en": "Version"},
                        },
                    ],
                    "row_tap": {"kind": "open_form", "view": "mcp_packages_form"},
                    "inline_add": {"field": "name", "title": "Добавить MCP"},
                    "empty": _empty("Нет MCP", "No MCP", icon="hub"),
                },
            },
            {
                "slug": "mcp_packages_form",
                "table_slug": "mcp_packages",
                "kind": "form",
                "ui_json": {
                    "version": 1,
                    "kind": "form",
                    "mode": "edit",
                    "title": {"ru": "MCP", "en": "MCP"},
                    "fields": [
                        {"column": "project_ids", "widget": "project_multiselect"},
                        {"column": "name", "widget": "value"},
                        {"column": "version", "widget": "value"},
                        {"column": "enabled", "widget": "switch"},
                        {
                            "column": "file_ref",
                            "widget": "file_upload",
                            "accept": ".zip",
                        },
                    ],
                },
            },
        ],
        "tabs": [
            {
                "id": "tab_mcp",
                "title": "MCP",
                "subtitle": "Инструменты и интеграции",
                "order": 20,
                "icon": "hub",
                "view_slug": "mcp_packages_list",
                "table_slug": "mcp_packages",
                "enabled": True,
                "default_project_bind": "global",
                "nav": {"contour": "employee", "placement": "management"},
            }
        ],
        "materialize": [
            {
                "id": "mcp_packages_enabled",
                "enabled": True,
                "when": ["project.created", "project.resumed", "project.sync"],
                "priority": 60,
                "source": {
                    "type": "rows",
                    "table_slug": "mcp_packages",
                    "filter": {"enabled": True},
                },
                "target": {
                    "workspace_path": "packages/{{name}}",
                    "format": "mcp_package",
                    "field": "file_ref",
                },
            }
        ],
        "seed_rows": {"items": []},
    }


def mod_equipment_meta() -> dict[str, list[Any]]:
    """Подбор техники — hub on Данные; catalogs + request lines + found offers."""
    return {
        "tables": [
            {
                "slug": "catalogs",
                "label": {"ru": "Базы данных", "en": "Databases"},
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all", "chats": "all"},
            },
            {
                "slug": "request_lines",
                "label": {"ru": "Позиции заказчика", "en": "Request lines"},
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all", "chats": "current"},
            },
            # WAVE7: выбор ИИ — группы кандидатов (партномер + алиасы + точность).
            # Офферы материализуются автоматикой из OpenSearch (equipment.pipeline).
            {
                "slug": "found_groups",
                "label": {"ru": "Найденные товары", "en": "Found products"},
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all", "chats": "current"},
            },
            {
                "slug": "found_offers",
                "label": {"ru": "Предложения поставщиков", "en": "Supplier offers"},
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all", "chats": "current"},
            },
            {
                "slug": "equipment_types",
                "label": {"ru": "Типы комплектующих", "en": "Component types"},
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all", "chats": "all"},
            },
            {
                "slug": "equipment_builds",
                "label": {"ru": "Сборка", "en": "Builds"},
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all", "chats": "current"},
            },
            {
                "slug": "trusted_sellers",
                "label": {
                    "ru": "Поставщики",
                    "en": "Suppliers",
                },
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all", "chats": "all"},
            },
            {
                "slug": "web_shops",
                "label": {
                    "ru": "Интернет магазины",
                    "en": "Web shops",
                },
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all", "chats": "all"},
            },
            {
                "slug": "templates",
                "label": "Шаблоны документов",
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"chats": "all", "projects": "all"},
            },
            {
                "slug": "equipment_mcp",
                "label": {"ru": "MCP", "en": "MCP"},
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all", "chats": "all"},
            },
            {
                "slug": "budget_lines",
                "label": {"ru": "Бюджетирование", "en": "Budget"},
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all", "chats": "current"},
            },
            # «Поиск товаров»: виртуальная таблица ручного поиска по всем
            # готовым каталогам OpenSearch (серверные поиск/пагинация/фильтр).
            {
                "slug": "equipment_search",
                "label": {"ru": "Поиск товаров", "en": "Product search"},
                "storage_kind": "opensearch_virtual",
                "enabled": True,
                "scope": {"projects": "all", "chats": "all"},
            },
            # Мастер-прайс: виртуальная таблица — строки НЕ хранятся в БД,
            # читаются из OpenSearch готовых каталогов по поставщикам с
            # флагом master_price (пагинация/поиск на стороне сервера).
            {
                "slug": "master_price",
                "label": {"ru": "Мастер прайс", "en": "Master price"},
                "storage_kind": "opensearch_virtual",
                "enabled": True,
                "scope": {"projects": "all", "chats": "all"},
            },
            # Промпты подбора техники (управляемые инструкции; мердж в workspace
            # через prompt_paths/fragments — совместим с mod_prompts).
            {
                "slug": "equipment_prompts",
                "label": {"ru": "Промпты", "en": "Prompts"},
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all", "chats": "all"},
            },
            # Реквизиты НАШЕЙ стороны + устойчивые условия: живут на уровне
            # кабинета (chats=all) — одинаковы во всех чатах/проектах и сразу
            # подставляются в форму (дефолты колонок = значения из шаблона).
            {
                "slug": "document_company_fields",
                "label": {"ru": "Реквизиты компании", "en": "Company details"},
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all", "chats": "all"},
            },
            # Реквизиты документов (КП/Спецификация): то, что нельзя вывести
            # из данных — стороны, номера договоров, сроки, адреса.
            {
                "slug": "document_fields",
                "label": {"ru": "Реквизиты документов", "en": "Document details"},
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all", "chats": "current"},
            },
            # Оверрайды инструкций MCP-инструментов (описания тулзов агента).
            {
                "slug": "mcp_tool_overrides",
                "label": {"ru": "Инструкции MCP", "en": "MCP instructions"},
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all", "chats": "all"},
            },
            # WAVE7: «Закупка» — агрегат по поставщикам (материализуется пайплайном).
            # chats=all: общий проектный датасет (агрегатор между чатами), а не
            # per-chat зеркала — строки одни на проект, видны из любого чата.
            {
                "slug": "procurement",
                "label": {"ru": "Закупка", "en": "Procurement"},
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all", "chats": "all"},
            },
            # WAVE12: каталог «Готовые сборки» v2 — глобальная библиотека.
            # «Карточки комплектующих» (part_cards): партномер + тип +
            # характеристики из групп типа. «Готовые сборки» (ready_builds) =
            # слоты (ready_build_slots) с требованиями (constraints) к группам
            # характеристик типа; пайплайн каталога подбирает карточки и цены.
            # chats=all: строки общие на проект (session_id NULL), видны из
            # любого чата; читать только io.list (list_project_wide не видит
            # NULL-строки). Хранят КЛЮЧИ (партномер/алиасы/хэш), а не офферы.
            {
                "slug": "part_cards",
                "label": {"ru": "Карточки комплектующих", "en": "Component cards"},
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all", "chats": "all"},
            },
            {
                "slug": "ready_builds",
                "label": {"ru": "Готовые сборки", "en": "Ready builds"},
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all", "chats": "all"},
            },
            {
                "slug": "ready_build_slots",
                "label": {"ru": "Слоты готовой сборки", "en": "Ready build slots"},
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all", "chats": "all"},
            },
        ],
        "columns": [
            {
                "table_slug": "catalogs",
                "name": "name",
                "label": {"ru": "Название", "en": "Name"},
                "type": "text",
                "required": True,
            },
            {
                "table_slug": "catalogs",
                "name": "source_kind",
                "label": {"ru": "Тип", "en": "Type"},
                "type": "enum",
                "required": True,
                "default": "local",
                "enum": {
                    "values": ["local", "remote"],
                    "labels": {
                        "local": "Локальная",
                        "remote": "Удалённая",
                    },
                },
            },
            {
                "table_slug": "catalogs",
                "name": "source_file",
                "label": {"ru": "Файл", "en": "File"},
                "type": "file_ref",
                "required": False,
            },
            {
                "table_slug": "catalogs",
                "name": "remote_dsn",
                "label": {"ru": "Ссылка БД", "en": "Database URL"},
                "type": "secret_ref",
                "required": False,
            },
            {
                "table_slug": "catalogs",
                "name": "remote_user",
                "label": {"ru": "Логин", "en": "Username"},
                "type": "text",
                "required": False,
                "default": "",
            },
            {
                "table_slug": "catalogs",
                "name": "remote_password",
                "label": {"ru": "Пароль", "en": "Password"},
                "type": "secret_ref",
                "required": False,
            },
            {
                "table_slug": "catalogs",
                "name": "remote_database",
                "label": {"ru": "База", "en": "Database"},
                "type": "text",
                "required": False,
                "default": "",
            },
            {
                "table_slug": "catalogs",
                "name": "remote_table",
                "label": {"ru": "Таблица", "en": "Table"},
                "type": "text",
                "required": False,
                "default": "",
            },
            {
                "table_slug": "catalogs",
                "name": "remote_dsn_has_database",
                "label": {"ru": "DSN с БД", "en": "DSN has database"},
                "type": "bool",
                "required": False,
                "default": False,
            },
            {
                "table_slug": "catalogs",
                "name": "remote_dsn_url_has_database",
                "label": {"ru": "URL с /dbname", "en": "URL has /dbname"},
                "type": "bool",
                "required": False,
                "default": False,
            },
            {
                "table_slug": "catalogs",
                "name": "remote_dsn_has_user",
                "label": {"ru": "DSN с логином", "en": "DSN has user"},
                "type": "bool",
                "required": False,
                "default": False,
            },
            {
                "table_slug": "catalogs",
                "name": "remote_dsn_has_password",
                "label": {"ru": "DSN с паролем", "en": "DSN has password"},
                "type": "bool",
                "required": False,
                "default": False,
            },
            {
                "table_slug": "catalogs",
                "name": "remote_auth_failed",
                "label": {"ru": "Ошибка входа", "en": "Auth failed"},
                "type": "bool",
                "required": False,
                "default": False,
            },
            {
                "table_slug": "catalogs",
                "name": "remote_dsn_reachable",
                "label": {"ru": "DSN доступен", "en": "DSN reachable"},
                "type": "bool",
                "required": False,
                "default": False,
            },
            {
                "table_slug": "catalogs",
                "name": "status",
                "label": {"ru": "Статус", "en": "Status"},
                "type": "enum",
                "required": True,
                "default": "draft",
                "enum": {
                    "values": ["draft", "queued", "indexing", "ready", "error"],
                    "labels": {
                        "draft": "Без индексирования",
                        "queued": "В очереди",
                        "indexing": "В процессе",
                        "ready": "Обработано",
                        "error": "Ошибка",
                    },
                },
            },
            {
                "table_slug": "catalogs",
                "name": "paused",
                "label": {"ru": "Пауза", "en": "Pause"},
                "type": "bool",
                "required": False,
                "default": False,
            },
            {
                "table_slug": "catalogs",
                "name": "row_count",
                "label": {"ru": "Строк", "en": "Rows"},
                "type": "number",
                "required": False,
                "default": 0,
            },
            {
                "table_slug": "catalogs",
                "name": "columns_json",
                "label": {"ru": "Столбцы", "en": "Columns"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "catalogs",
                "name": "column_map",
                "label": {
                    "ru": "Сопоставление колонок",
                    "en": "Column mapping",
                },
                "type": "json",
                "required": False,
                "default": {},
            },
            {
                "table_slug": "catalogs",
                "name": "last_indexed_at",
                "label": {"ru": "Индексировано", "en": "Last indexed"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "catalogs",
                "name": "reindex_interval_hours",
                "label": {
                    "ru": "Интервал обновления (ч)",
                    "en": "Update interval (h)",
                },
                "type": "number",
                "required": False,
                "default": 24,
            },
            {
                "table_slug": "catalogs",
                "name": "index_name",
                "label": {"ru": "Индекс OS", "en": "OS index"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "catalogs",
                "name": "error",
                "label": {"ru": "Ошибка", "en": "Error"},
                "type": "text",
                "required": False,
            },
            # Indexing progress (worker heartbeat): rendered by the UI as
            # «В процессе (x из y)»; used by the beat sweep to heal rows stuck
            # in `indexing` after a worker restart.
            {
                "table_slug": "catalogs",
                "name": "indexed_count",
                "label": {"ru": "Проиндексировано", "en": "Indexed"},
                "type": "number",
                "required": False,
                "read_only": True,
                "hidden": True,
            },
            {
                "table_slug": "catalogs",
                "name": "total_rows",
                "label": {"ru": "Всего строк", "en": "Total rows"},
                "type": "number",
                "required": False,
                "read_only": True,
                "hidden": True,
            },
            {
                "table_slug": "catalogs",
                "name": "indexing_started_at",
                "label": {"ru": "Начало индексации", "en": "Indexing started"},
                "type": "datetime",
                "required": False,
                "read_only": True,
                "hidden": True,
            },
            _project_ids_column("catalogs"),
            {
                "table_slug": "request_lines",
                "name": "title",
                "label": {"ru": "Название", "en": "Title"},
                "type": "text",
                "required": True,
            },
            {
                "table_slug": "request_lines",
                "name": "part_number",
                "label": {"ru": "Партномер", "en": "Part number"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "request_lines",
                "name": "qty",
                "label": {"ru": "Кол-во", "en": "Qty"},
                "type": "number",
                "required": False,
                "default": 1,
            },
            {
                "table_slug": "request_lines",
                "name": "found_count",
                "label": {"ru": "Найдено", "en": "Found"},
                "type": "number",
                "required": False,
                "default": 0,
            },
            {
                "table_slug": "request_lines",
                "name": "selected_offer_id",
                "label": {"ru": "Выбранный оффер", "en": "Selected offer"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "request_lines",
                "name": "status",
                "label": {"ru": "Статус", "en": "Status"},
                "type": "enum",
                "required": True,
                "default": "open",
                "enum": {
                    "values": ["open", "matched", "selected"],
                    "labels": {"open": "Открыта", "matched": "Есть кандидаты", "selected": "Выбрано"},
                },
            },
            _project_ids_column("request_lines"),
            # WAVE10: снапшот сборок позиции (пишет пайплайн). Позиция может
            # иметь несколько сборок-вариантов; в списке видно лучшую и цену.
            {
                "table_slug": "request_lines",
                "name": "builds_count",
                "label": {"ru": "Сборок", "en": "Builds"},
                "type": "number",
                "required": False,
                "default": 0,
                "read_only": True,
            },
            {
                "table_slug": "request_lines",
                "name": "build_best_id",
                "label": {"ru": "Лучшая сборка", "en": "Best build"},
                "type": "text",
                "required": False,
                "read_only": True,
                "hidden": True,
            },
            {
                "table_slug": "request_lines",
                "name": "build_best_price",
                "label": {"ru": "Цена сборки", "en": "Build price"},
                "type": "number",
                "required": False,
                "read_only": True,
            },
            {
                "table_slug": "request_lines",
                "name": "build_match_label",
                "label": {"ru": "Совпадение сборки", "en": "Build match"},
                "type": "text",
                "required": False,
                "default": "",
                "read_only": True,
            },
            {
                "table_slug": "request_lines",
                "name": "build_benefit_label",
                "label": {"ru": "Выгода сборки", "en": "Build benefit"},
                "type": "text",
                "required": False,
                "default": "",
                "read_only": True,
            },
            {
                "table_slug": "request_lines",
                "name": "build_benefit_tone",
                "label": {"ru": "Тон выгоды сборки", "en": "Build benefit tone"},
                "type": "text",
                "required": False,
                "default": "",
                "read_only": True,
                "hidden": True,
            },
            # WAVE7 found_groups: выбор ИИ + автополя пайплайна.
            {
                "table_slug": "found_groups",
                "name": "line_id",
                "label": {"ru": "Позиция заказчика", "en": "Request line"},
                "type": "ref",
                # WAVE10: группа принадлежит ЛИБО позиции (line_id), ЛИБО слоту
                # сборки (build_id + slot_type_id) — поэтому не required.
                "required": False,
                "ref": {"table_slug": "request_lines"},
            },
            {
                "table_slug": "found_groups",
                "name": "build_id",
                "label": {"ru": "Сборка", "en": "Build"},
                "type": "ref",
                "required": False,
                "ref": {"table_slug": "equipment_builds"},
            },
            {
                "table_slug": "found_groups",
                "name": "slot_type_id",
                "label": {"ru": "Тип комплектующего", "en": "Component type"},
                "type": "ref",
                "required": False,
                "ref": {"table_slug": "equipment_types"},
            },
            {
                # Идентичность КОНКРЕТНОГО слота (две строки одного типа — два
                # слота): без неё две группы одного типа схлопывались бы в один
                # слот. По умолчанию = slot_type_id (совместимость со сборками,
                # у которых по одному компоненту на тип).
                "table_slug": "found_groups",
                "name": "slot_id",
                "label": {"ru": "Слот сборки", "en": "Build slot"},
                "type": "text",
                "required": False,
                "default": "",
                "hidden": True,
            },
            {
                # «line» — кандидат позиции; «build» — кандидат слота сборки.
                # Пишет пайплайн; глобальный список фильтрует по нему.
                "table_slug": "found_groups",
                "name": "owner_kind",
                "label": {"ru": "Владелец группы", "en": "Owner kind"},
                "type": "enum",
                "required": False,
                "default": "line",
                "read_only": True,
                "hidden": True,
                "enum": {
                    "values": ["line", "build"],
                    "labels": {"line": "Позиция", "build": "Сборка"},
                },
            },
            {
                "table_slug": "found_groups",
                "name": "part_number",
                "label": {"ru": "Партномер", "en": "Part number"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "found_groups",
                "name": "aliases_pn",
                "label": {"ru": "Алиасы партномера", "en": "P/N aliases"},
                "type": "text",
                "required": False,
                "default": "",
            },
            {
                "table_slug": "found_groups",
                "name": "aliases_hash",
                "label": {"ru": "Алиасы хэшей позиций", "en": "Hash aliases"},
                "type": "text",
                "required": False,
                "default": "",
            },
            {
                "table_slug": "found_groups",
                "name": "match_kind",
                "label": {"ru": "Совпадение", "en": "Match"},
                "type": "enum",
                "required": True,
                "default": "analog",
                "enum": {
                    "values": ["exact", "analog", "doubt"],
                    "labels": {
                        "exact": "Точное",
                        "analog": "Аналог",
                        "doubt": "Есть сомнения",
                    },
                },
            },
            {
                "table_slug": "found_groups",
                "name": "note",
                "label": {"ru": "Комментарий ИИ", "en": "AI note"},
                "type": "text",
                "required": False,
                "default": "",
            },
            {
                "table_slug": "found_groups",
                "name": "face_title",
                "label": {"ru": "Товар (лицо группы)", "en": "Face title"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "found_groups",
                "name": "face_price",
                "label": {"ru": "Цена ₽", "en": "Price RUB"},
                "type": "number",
                "required": False,
            },
            {
                "table_slug": "found_groups",
                "name": "face_seller",
                "label": {"ru": "Поставщик (лицо группы)", "en": "Face seller"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "found_groups",
                "name": "face_brand",
                "label": {"ru": "Бренд (лицо группы)", "en": "Face brand"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "found_groups",
                "name": "face_stale",
                "label": {"ru": "Лицо устарело", "en": "Face stale"},
                "type": "bool",
                "required": False,
                "default": False,
                "read_only": True,
                "hidden": True,
            },
            {
                "table_slug": "found_groups",
                "name": "face_priority",
                "label": {"ru": "Лицо — приоритетный поставщик", "en": "Face priority"},
                "type": "bool",
                "required": False,
                "default": False,
                "read_only": True,
                "hidden": True,
            },
            {
                "table_slug": "found_groups",
                "name": "face_in_stock",
                "label": {"ru": "Лицо в наличии", "en": "Face in stock"},
                "type": "bool",
                "required": False,
                "default": False,
                "read_only": True,
                "hidden": True,
            },
            {
                "table_slug": "found_groups",
                "name": "match_label",
                "label": {"ru": "Совпадение", "en": "Match"},
                "type": "text",
                "required": False,
                "default": "",
                "read_only": True,
            },
            {
                "table_slug": "found_groups",
                "name": "alternatives_count",
                "label": {"ru": "Альтернативы", "en": "Alternatives"},
                "type": "number",
                "required": False,
                "default": 0,
                "read_only": True,
            },
            {
                "table_slug": "found_groups",
                "name": "best_offer_id",
                "label": {"ru": "Лучший оффер", "en": "Best offer"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "found_groups",
                "name": "offers_count",
                "label": {"ru": "Предложений", "en": "Offers"},
                "type": "number",
                "required": False,
                "default": 0,
            },
            {
                "table_slug": "found_groups",
                "name": "rank",
                "label": {"ru": "Ранг точности", "en": "Match rank"},
                "type": "number",
                "required": False,
                "read_only": True,
                "hidden": True,
            },
            {
                "table_slug": "found_groups",
                "name": "is_best",
                "label": {"ru": "Лучший", "en": "Best"},
                "type": "bool",
                "required": False,
                "default": False,
            },
            {
                "table_slug": "found_groups",
                "name": "synced_at",
                "label": {"ru": "Сверено", "en": "Synced at"},
                "type": "text",
                "required": False,
            },
            _project_ids_column("found_groups"),
            {
                "table_slug": "found_offers",
                "name": "line_id",
                "label": {"ru": "Запрос", "en": "Request"},
                "type": "ref",
                "required": False,
                "ref": {"table_slug": "request_lines"},
            },
            {
                "table_slug": "found_offers",
                "name": "title",
                "label": {"ru": "Товар", "en": "Title"},
                "type": "text",
                "required": True,
            },
            {
                "table_slug": "found_offers",
                "name": "part_number",
                "label": {"ru": "Партномер", "en": "Part number"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "found_offers",
                "name": "brand",
                "label": {"ru": "Бренд", "en": "Brand"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "found_offers",
                "name": "seller",
                "label": {"ru": "Поставщик", "en": "Supplier"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "found_offers",
                "name": "currency",
                "label": {"ru": "Валюта", "en": "Currency"},
                "type": "enum",
                "required": False,
                "enum": {
                    "values": ["RUB", "USD", "EUR"],
                    "labels": {"RUB": "₽", "USD": "$", "EUR": "€"},
                },
                "default": "RUB",
            },
            {
                "table_slug": "found_offers",
                "name": "price_orig",
                "label": {"ru": "Цена в валюте", "en": "Price (orig)"},
                "type": "number",
                "required": False,
            },
            {
                "table_slug": "found_offers",
                "name": "src_hash",
                "label": {"ru": "Хэш позиции", "en": "Source hash"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "found_offers",
                "name": "is_stale",
                "label": {"ru": "Устарела", "en": "Stale"},
                "type": "bool",
                "required": False,
                "default": False,
            },
            {
                "table_slug": "found_offers",
                "name": "price",
                "label": {"ru": "Цена", "en": "Price"},
                "type": "number",
                "required": False,
            },
            {
                "table_slug": "found_offers",
                "name": "catalog_id",
                "label": {"ru": "БД", "en": "Catalog"},
                "type": "ref",
                "required": False,
                "ref": {"table_slug": "catalogs"},
            },
            {
                "table_slug": "found_offers",
                "name": "score",
                "label": {"ru": "Релевантность", "en": "Score"},
                "type": "number",
                "required": False,
            },
            {
                "table_slug": "found_offers",
                "name": "match_kind",
                "label": {"ru": "Совпадение", "en": "Match"},
                "type": "enum",
                "required": False,
                "default": "analog",
                "enum": {
                    "values": ["exact", "analog", "doubt"],
                    "labels": {
                        "exact": "Точное",
                        "analog": "Аналог",
                        "doubt": "Есть сомнения",
                    },
                },
            },
            {
                "table_slug": "found_offers",
                "name": "is_selected",
                "label": {"ru": "Выбран", "en": "Selected"},
                "type": "bool",
                "required": False,
                "default": False,
            },
            {
                "table_slug": "found_offers",
                "name": "source_title",
                "label": {"ru": "Название запроса", "en": "Request title"},
                "type": "text",
                "required": False,
            },
            # WAVE7: офферы материализует автоматика (group_id, priority, manual).
            {
                "table_slug": "found_offers",
                "name": "group_id",
                "label": {"ru": "Группа", "en": "Group"},
                "type": "ref",
                "required": False,
                "ref": {"table_slug": "found_groups"},
            },
            {
                "table_slug": "found_offers",
                "name": "in_stock",
                "label": {"ru": "В наличии", "en": "In stock"},
                "type": "bool",
                "required": False,
                "default": False,
            },
            {
                "table_slug": "found_offers",
                "name": "lead_time",
                "label": {"ru": "Срок", "en": "Lead time"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "found_offers",
                "name": "priority",
                "label": {"ru": "Приоритетный поставщик", "en": "Priority supplier"},
                "type": "bool",
                "required": False,
                "default": False,
            },
            {
                "table_slug": "found_offers",
                "name": "is_best",
                "label": {"ru": "Лучший в группе", "en": "Best in group"},
                "type": "bool",
                "required": False,
                "default": False,
            },
            {
                "table_slug": "found_offers",
                "name": "manual",
                "label": {"ru": "Ручные правки", "en": "Manual overrides"},
                "type": "json",
                "required": False,
                "default": {},
            },
            {
                "table_slug": "found_offers",
                "name": "alternatives_count",
                "label": {"ru": "Альтернативы", "en": "Alternatives"},
                "type": "number",
                "required": False,
                "default": 0,
                "read_only": True,
            },
            {
                "table_slug": "found_offers",
                "name": "benefit_label",
                "label": {"ru": "Выгода", "en": "Benefit"},
                "type": "text",
                "required": False,
                "default": "",
                "read_only": True,
            },
            {
                "table_slug": "found_offers",
                "name": "is_effective",
                "label": {"ru": "Текущий выбор позиции", "en": "Effective choice"},
                "type": "bool",
                "required": False,
                "default": False,
                "read_only": True,
                "hidden": True,
            },
            {
                "table_slug": "found_offers",
                "name": "benefit_tone",
                "label": {"ru": "Тон выгоды", "en": "Benefit tone"},
                "type": "text",
                "required": False,
                "default": "",
                "read_only": True,
                "hidden": True,
            },
            {
                "table_slug": "found_offers",
                "name": "match_label",
                "label": {"ru": "Совпадение", "en": "Match"},
                "type": "text",
                "required": False,
                "default": "",
                "read_only": True,
            },
            _project_ids_column("found_offers"),
            {
                "table_slug": "equipment_types",
                "name": "name",
                "label": {"ru": "Название", "en": "Name"},
                "type": "text",
                "required": True,
            },
            {
                "table_slug": "equipment_types",
                "name": "sort_order",
                "label": {"ru": "Порядок", "en": "Order"},
                "type": "number",
                "required": False,
                "default": 1000,
            },
            {
                "table_slug": "equipment_types",
                "name": "fields_json",
                "label": {"ru": "Характеристики", "en": "Fields"},
                "type": "json",
                "required": False,
                "default": [],
            },
            {
                "table_slug": "equipment_types",
                "name": "build_scope",
                "label": {"ru": "Сборка", "en": "Build"},
                "type": "enum",
                "required": True,
                "default": "all",
                "enum": {
                    "values": _BUILD_SCOPE_VALUES,
                    "labels": _BUILD_SCOPE_LABELS,
                },
            },
            _project_ids_column("equipment_types"),
            {
                "table_slug": "equipment_builds",
                "name": "name",
                "label": {"ru": "Название", "en": "Name"},
                "type": "text",
                "required": True,
            },
            # WAVE10: сборка привязана к позиции заказчика. На одну позицию —
            # несколько сборок (разные конфигурации), одна из них is_best;
            # остальные — альтернативы (alternatives_count).
            {
                "table_slug": "equipment_builds",
                "name": "line_id",
                "label": {"ru": "Позиция заказчика", "en": "Request line"},
                "type": "ref",
                "required": False,
                "ref": {"table_slug": "request_lines"},
            },
            {
                # WAVE12: сборка чата — ГЛОБАЛЬНАЯ ССЫЛКА на «Готовую сборку»
                # каталога, а не копия. Состав/цены зеркалятся пайплайном из
                # ready_build_slots: изменение каталога доезжает до заказа.
                "table_slug": "equipment_builds",
                "name": "ready_build_id",
                "label": {"ru": "Готовая сборка", "en": "Ready build"},
                "type": "ref",
                "required": True,
                "ref": {"table_slug": "ready_builds"},
            },
            {
                "table_slug": "equipment_builds",
                "name": "build_kind",
                "label": {"ru": "Тип", "en": "Kind"},
                "type": "enum",
                "required": True,
                "default": "pc",
                "enum": {
                    "values": _BUILD_KIND_VALUES,
                    "labels": _BUILD_KIND_LABELS,
                },
            },
            {
                # Денормализация из готовой сборки (пишет зеркало пайплайна):
                # имя каталога, чтобы список «Сборка» показывал, что привязано,
                # без джойна в каталог.
                "table_slug": "equipment_builds",
                "name": "ready_build_name",
                "label": {"ru": "Готовая сборка", "en": "Ready build"},
                "type": "text",
                "required": False,
                "default": "",
                "read_only": True,
            },
            {
                # Денормализация из готовой сборки (пишет зеркало пайплайна):
                # {slot_id: qty} — количество на слот (2 плашки ОЗУ) и
                # {slot_id: type_id} — тип каждого слота. Руками не правится.
                "table_slug": "equipment_builds",
                "name": "slot_qty",
                "label": {"ru": "Количество по слотам", "en": "Slot quantity"},
                "type": "json",
                "required": False,
                "default": {},
                "read_only": True,
                "hidden": True,
            },
            {
                "table_slug": "equipment_builds",
                "name": "slot_types",
                "label": {"ru": "Типы слотов", "en": "Slot types"},
                "type": "json",
                "required": False,
                "default": {},
                "read_only": True,
                "hidden": True,
            },
            {
                # WAVE13: сколько таких сборок заказано на позицию. Масштабирует
                # ВСЕ компоненты (включая мульти-слоты: 2 плашки × 2 сборки = 4
                # планки ОЗУ в закупке).
                "table_slug": "equipment_builds",
                "name": "qty",
                "label": {"ru": "Количество", "en": "Quantity"},
                "type": "number",
                "required": False,
                "default": 1,
            },
            {
                "table_slug": "equipment_builds",
                "name": "components_count",
                "label": {"ru": "Комплектующих", "en": "Components"},
                "type": "number",
                "required": False,
                "default": 0,
            },
            {
                "table_slug": "equipment_builds",
                "name": "price_total",
                "label": {"ru": "Цена", "en": "Price"},
                "type": "number",
                "required": False,
                "default": 0,
            },
            # WAVE10 — поля пайплайна (read-only, как «лица» у found_groups).
            {
                "table_slug": "equipment_builds",
                "name": "match_kind",
                "label": {"ru": "Совпадение", "en": "Match"},
                "type": "enum",
                "required": False,
                "default": "analog",
                "read_only": True,
                "enum": {
                    "values": ["exact", "analog", "doubt"],
                    "labels": {
                        "exact": "Точное",
                        "analog": "Аналог",
                        "doubt": "Есть сомнения",
                    },
                },
            },
            {
                "table_slug": "equipment_builds",
                "name": "match_label",
                "label": {"ru": "Совпадение", "en": "Match label"},
                "type": "text",
                "required": False,
                "default": "",
                "read_only": True,
            },
            {
                "table_slug": "equipment_builds",
                "name": "is_best",
                "label": {"ru": "Лучшая", "en": "Best"},
                "type": "bool",
                "required": False,
                "default": False,
                "read_only": True,
            },
            {
                # «под заказ»: хотя бы один компонент сборки не в наличии.
                # Бюджет читает флаг напрямую (не парсит текст match_label).
                "table_slug": "equipment_builds",
                "name": "on_order",
                "label": {"ru": "Под заказ", "en": "On order"},
                "type": "bool",
                "required": False,
                "default": False,
                "read_only": True,
                "hidden": True,
            },
            {
                "table_slug": "equipment_builds",
                "name": "is_selected",
                "label": {"ru": "Выбрана", "en": "Selected"},
                "type": "bool",
                "required": False,
                "default": False,
            },
            {
                "table_slug": "equipment_builds",
                "name": "alternatives_count",
                "label": {"ru": "Альтернатив", "en": "Alternatives"},
                "type": "number",
                "required": False,
                "default": 0,
                "read_only": True,
            },
            {
                "table_slug": "equipment_builds",
                "name": "benefit_label",
                "label": {"ru": "Выгода", "en": "Benefit"},
                "type": "text",
                "required": False,
                "default": "",
                "read_only": True,
            },
            {
                "table_slug": "equipment_builds",
                "name": "benefit_tone",
                "label": {"ru": "Тон выгоды", "en": "Benefit tone"},
                "type": "text",
                "required": False,
                "default": "",
                "read_only": True,
                "hidden": True,
            },
            {
                "table_slug": "equipment_builds",
                "name": "note",
                "label": {"ru": "Комментарий ИИ", "en": "AI note"},
                "type": "text",
                "required": False,
                "default": "",
            },
            _project_ids_column("equipment_builds"),
            {
                "table_slug": "trusted_sellers",
                "name": "name",
                "label": {"ru": "Компания", "en": "Company"},
                "type": "text",
                "required": True,
                "unique": True,
            },
            {
                "table_slug": "trusted_sellers",
                "name": "aliases",
                "label": {"ru": "Алиасы", "en": "Aliases"},
                "type": "text",
                "required": False,
                "default": "",
                "unique": True,
            },
            {
                "table_slug": "trusted_sellers",
                "name": "is_verified",
                "label": {"ru": "Проверен", "en": "Verified"},
                "type": "bool",
                "required": False,
                "default": False,
            },
            {
                "table_slug": "trusted_sellers",
                "name": "payment_deferral",
                "label": {"ru": "Отсрочка платежа", "en": "Payment deferral"},
                "type": "bool",
                "required": False,
                "default": False,
            },
            {
                "table_slug": "trusted_sellers",
                "name": "email",
                "label": {"ru": "Email", "en": "Email"},
                "type": "text",
                "required": False,
                "default": "",
            },
            {
                "table_slug": "trusted_sellers",
                "name": "comment",
                "label": {"ru": "Комментарий", "en": "Comment"},
                "type": "text",
                "required": False,
                "default": "",
            },
            {
                "table_slug": "trusted_sellers",
                "name": "margin_pct",
                "label": {"ru": "Маржа %", "en": "Margin %"},
                "type": "number",
                "required": False,
            },
            {
                "table_slug": "trusted_sellers",
                "name": "delivery_rub",
                "label": {"ru": "Доставка ₽", "en": "Delivery RUB"},
                "type": "number",
                "required": False,
            },
            {
                "table_slug": "trusted_sellers",
                "name": "is_enabled",
                "label": {"ru": "Включён", "en": "Enabled"},
                "type": "bool",
                "required": False,
                "default": True,
            },
            {
                "table_slug": "trusted_sellers",
                "name": "priority_purchase",
                "label": {"ru": "Приоритетная закупка", "en": "Priority purchase"},
                "type": "bool",
                "required": False,
                "default": False,
            },
            {
                "table_slug": "trusted_sellers",
                "name": "inn",
                "label": {"ru": "ИНН", "en": "INN"},
                "type": "text",
                "required": False,
                "default": "",
            },
            {
                "table_slug": "trusted_sellers",
                "name": "kpp",
                "label": {"ru": "КПП", "en": "KPP"},
                "type": "text",
                "required": False,
                "default": "",
            },
            {
                "table_slug": "trusted_sellers",
                "name": "legal_address",
                "label": {"ru": "Юр. адрес", "en": "Legal address"},
                "type": "text",
                "required": False,
                "default": "",
            },
            {
                "table_slug": "trusted_sellers",
                "name": "bank_name",
                "label": {"ru": "Банк", "en": "Bank"},
                "type": "text",
                "required": False,
                "default": "",
            },
            {
                "table_slug": "trusted_sellers",
                "name": "bik",
                "label": {"ru": "БИК", "en": "BIK"},
                "type": "text",
                "required": False,
                "default": "",
            },
            {
                "table_slug": "trusted_sellers",
                "name": "bank_account",
                "label": {"ru": "Счёт", "en": "Account"},
                "type": "text",
                "required": False,
                "default": "",
            },
            {
                "table_slug": "trusted_sellers",
                "name": "phone",
                "label": {"ru": "Телефон", "en": "Phone"},
                "type": "text",
                "required": False,
                "default": "",
            },
            {
                "table_slug": "trusted_sellers",
                "name": "master_price",
                "label": {"ru": "Мастер прайс", "en": "Master price"},
                "type": "bool",
                "required": False,
                "default": False,
            },
            _project_ids_column("trusted_sellers"),
            {
                "table_slug": "web_shops",
                "name": "name",
                "label": {"ru": "Название", "en": "Name"},
                "type": "text",
                "required": True,
            },
            {
                "table_slug": "templates",
                "name": "template_type",
                "label": "Тип шаблона",
                "type": "enum",
                "required": True,
                "default": "budget",
                "enum": {
                    "values": [
                        "budget",
                        "commercial_proposal",
                        "specification",
                        "master_price",
                    ],
                    "labels": {
                        "budget": "Бюджетирование (xlsx)",
                        "commercial_proposal": "КП (PDF)",
                        "specification": "Спецификация (PDF)",
                        "master_price": "Мастер-прайс (xlsx)",
                    },
                },
            },
            {
                "table_slug": "templates",
                "name": "title",
                "label": "Название",
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "templates",
                "name": "file",
                "label": "Файл (xlsx)",
                "type": "file_ref",
                "required": True,
            },
            {
                "table_slug": "templates",
                "name": "active",
                "label": "Активен",
                "type": "bool",
                "required": False,
                "default": True,
            },

            {
                "table_slug": "web_shops",
                "name": "url",
                "label": {"ru": "Ссылка", "en": "URL"},
                "type": "text",
                "required": False,
                "default": "",
            },
            {
                "table_slug": "web_shops",
                "name": "cookies",
                "label": {"ru": "Cookies", "en": "Cookies"},
                "type": "text",
                "required": False,
                "default": "",
            },
            _project_ids_column("web_shops"),
            {
                "table_slug": "equipment_mcp",
                "name": "name",
                "label": "Имя",
                "type": "text",
                "required": True,
            },
            {
                "table_slug": "equipment_mcp",
                "name": "version",
                "label": "Version",
                "type": "text",
                "required": True,
                "default": "1.0.0",
            },
            {
                "table_slug": "equipment_mcp",
                "name": "enabled",
                "label": "Enabled",
                "type": "bool",
                "required": False,
                "default": True,
            },
            {
                "table_slug": "equipment_mcp",
                "name": "file_ref",
                "label": "Zip package",
                "type": "file_ref",
                "required": False,
            },
            _project_ids_column("equipment_mcp"),
            # Бюджетирование — best-offer snapshot of request_lines (sync action).
            {
                "table_slug": "budget_lines",
                "name": "line_id",
                "label": {"ru": "Позиция", "en": "Request line"},
                "type": "ref",
                "required": False,
                "ref": {"table_slug": "request_lines"},
            },
            # WAVE10: строка бюджета может быть сборкой (сумма best-офферов по
            # слотам) — тогда указывает на equipment_builds вместо прямой позиции.
            {
                "table_slug": "budget_lines",
                "name": "build_id",
                "label": {"ru": "Сборка", "en": "Build"},
                "type": "ref",
                "required": False,
                "ref": {"table_slug": "equipment_builds"},
            },
            {
                "table_slug": "budget_lines",
                "name": "title",
                "label": {"ru": "Наименование", "en": "Title"},
                "type": "text",
                "required": False,
                "read_only": True,
            },
            {
                "table_slug": "budget_lines",
                "name": "part_number",
                "label": {"ru": "Партномер", "en": "Part number"},
                "type": "text",
                "required": False,
                "read_only": True,
            },
            {
                "table_slug": "budget_lines",
                "name": "seller",
                "label": {"ru": "Поставщик", "en": "Seller"},
                "type": "text",
                "required": False,
                "read_only": True,
            },
            {
                "table_slug": "budget_lines",
                "name": "qty",
                "label": {"ru": "Кол-во", "en": "Qty"},
                "type": "number",
                "required": False,
                "default": 1,
            },
            {
                "table_slug": "budget_lines",
                "name": "price_in",
                "label": {"ru": "Вход с НДС", "en": "In price with VAT"},
                "type": "number",
                "required": False,
            },
            # снапшот «оффер под заказ» (pipeline): UI рисует «Под заказ» /
            # «{цена} (Под заказ)» warning-цветом в «Вход с НДС»
            {
                "table_slug": "budget_lines",
                "name": "on_order",
                "label": {"ru": "Под заказ", "en": "On order"},
                "type": "bool",
                "required": False,
                "default": False,
                "read_only": True,
                "hidden": True,
            },
            # снапшот точности выбранного оффера: analog/doubt → warning
            # на наименовании и партномере строки бюджета
            {
                "table_slug": "budget_lines",
                "name": "match_kind",
                "label": {"ru": "Точность", "en": "Match"},
                "type": "text",
                "required": False,
                "read_only": True,
                "hidden": True,
            },
            # бренд выбранного оффера (снапшот; для экспортов/аналитики)
            {
                "table_slug": "budget_lines",
                "name": "brand",
                "label": {"ru": "Бренд", "en": "Brand"},
                "type": "text",
                "required": False,
                "read_only": True,
                "hidden": True,
            },
            {
                "table_slug": "budget_lines",
                "name": "vat",
                "label": {"ru": "НДС", "en": "VAT"},
                "type": "number",
                "required": False,
                "default": 0.22,
            },
            {
                "table_slug": "budget_lines",
                "name": "markup",
                "label": {"ru": "Наценка", "en": "Markup"},
                "type": "number",
                "required": False,
                "default": 0.1,
            },
            # WAVE7: 'seller' — маржа поставщика (обновляется при смене margin_pct),
            # 'manual' — ручная правка UI (синхронизация не трогает).
            {
                "table_slug": "budget_lines",
                "name": "markup_source",
                "label": {"ru": "Источник маржи", "en": "Markup source"},
                "type": "enum",
                "required": False,
                "default": "default",
                "enum": {
                    "values": ["default", "seller", "manual"],
                    "labels": {
                        "default": "Дефолт",
                        "seller": "Поставщик",
                        "manual": "Вручную",
                    },
                },
            },
            {
                "table_slug": "budget_lines",
                "name": "comment",
                "label": {"ru": "Комментарий", "en": "Comment"},
                "type": "text",
                "required": False,
            },
            _project_ids_column("budget_lines"),
            # WAVE7 «Закупка»: агрегаты по поставщикам (chats=current, пайплайн).
            {
                "table_slug": "procurement",
                "name": "seller",
                "label": {"ru": "Поставщик", "en": "Supplier"},
                "type": "text",
                "required": True,
            },
            {
                "table_slug": "procurement",
                "name": "is_registered",
                "label": {"ru": "В реестре", "en": "Registered"},
                "type": "bool",
                "required": False,
                "default": True,
            },
            {
                "table_slug": "procurement",
                "name": "offers_count",
                "label": {"ru": "Товаров", "en": "Offers"},
                "type": "number",
                "required": False,
                "default": 0,
            },
            {
                "table_slug": "procurement",
                "name": "selected_count",
                "label": {"ru": "Товаров", "en": "Products"},
                "type": "number",
                "required": False,
                "default": 0,
            },
            {
                "table_slug": "procurement",
                "name": "qty_total",
                "label": {"ru": "Количество", "en": "Quantity"},
                "type": "number",
                "required": False,
                "default": 0,
                "read_only": True,
            },
            {
                "table_slug": "procurement",
                "name": "sum_rub",
                "label": {"ru": "Сумма ₽", "en": "Sum RUB"},
                "type": "number",
                "required": False,
                "default": 0,
            },
            {
                "table_slug": "procurement",
                "name": "margin_pct",
                "label": {"ru": "Маржа %", "en": "Margin %"},
                "type": "number",
                "required": False,
            },

            {
                "table_slug": "procurement",
                "name": "delivery_rub",
                "label": {"ru": "Доставка ₽", "en": "Delivery RUB"},
                "type": "number",
                "required": False,
                "default": 0,
            },
            {
                "table_slug": "procurement",
                "name": "sum_with_margin_rub",
                "label": {"ru": "Сумма с маржой ₽", "en": "Sum with margin RUB"},
                "type": "number",
                "required": False,
                "default": 0,
            },
            {
                "table_slug": "procurement",
                "name": "sum_margin_rub",
                "label": {"ru": "Маржа ₽", "en": "Margin RUB"},
                "type": "number",
                "required": False,
                "default": 0,
            },
            _project_ids_column("procurement"),
            # ------------------------------------------------ WAVE12: каталог
            # «Готовые сборки» v2. Карточка комплектующего = партномер + тип +
            # характеристики (значения из групп типа). Пайплайн каталога
            # резолвит ключи карточек в лучшие цены батч-запросом в OpenSearch.
            # Все вычисляемые поля — read_only.
            {
                "table_slug": "part_cards",
                "name": "type_id",
                "label": {"ru": "Тип комплектующего", "en": "Component type"},
                "type": "ref",
                "required": True,
                "ref": {"table_slug": "equipment_types"},
            },
            {
                "table_slug": "part_cards",
                "name": "type_name",
                "label": {"ru": "Тип", "en": "Type"},
                "type": "text",
                "required": False,
                "default": "",
                "read_only": True,
            },
            {
                "table_slug": "part_cards",
                "name": "part_number",
                "label": {"ru": "Партномер", "en": "Part number"},
                "type": "text",
                "required": False,
                "default": "",
            },
            {
                "table_slug": "part_cards",
                "name": "aliases_pn",
                "label": {"ru": "Алиасы партномера", "en": "P/N aliases"},
                "type": "text",
                "required": False,
                "default": "",
            },
            {
                "table_slug": "part_cards",
                "name": "aliases_hash",
                "label": {"ru": "Алиасы хэшей позиций", "en": "Hash aliases"},
                "type": "text",
                "required": False,
                "default": "",
            },
            {
                # Характеристики карточки — значения из групп типа
                # (equipment_types.fields_json). Обязательные поля типа должны
                # быть заполнены: по ним работает матрица совместимости.
                "table_slug": "part_cards",
                "name": "attrs",
                "label": {"ru": "Характеристики", "en": "Attributes"},
                "type": "json",
                "required": False,
                "default": {},
            },
            {
                "table_slug": "part_cards",
                "name": "attrs_label",
                "label": {"ru": "Характеристики", "en": "Attributes"},
                "type": "text",
                "required": False,
                "default": "",
                "read_only": True,
            },
            {
                "table_slug": "part_cards",
                "name": "note",
                "label": {"ru": "Комментарий", "en": "Note"},
                "type": "text",
                "required": False,
                "default": "",
            },
            {
                "table_slug": "part_cards",
                "name": "offers_count",
                "label": {"ru": "Предложений", "en": "Offers"},
                "type": "number",
                "required": False,
                "default": 0,
                "read_only": True,
            },
            {
                "table_slug": "part_cards",
                "name": "best_price",
                "label": {"ru": "Лучшая цена ₽", "en": "Best price"},
                "type": "number",
                "required": False,
                "read_only": True,
            },
            {
                "table_slug": "part_cards",
                "name": "best_title",
                "label": {"ru": "Товар", "en": "Product"},
                "type": "text",
                "required": False,
                "default": "",
                "read_only": True,
            },
            {
                "table_slug": "part_cards",
                "name": "best_seller",
                "label": {"ru": "Поставщик", "en": "Supplier"},
                "type": "text",
                "required": False,
                "default": "",
                "read_only": True,
            },
            {
                "table_slug": "part_cards",
                "name": "in_stock",
                "label": {"ru": "В наличии", "en": "In stock"},
                "type": "bool",
                "required": False,
                "default": False,
                "read_only": True,
            },
            {
                "table_slug": "part_cards",
                "name": "synced_at",
                "label": {"ru": "Сверено", "en": "Synced at"},
                "type": "text",
                "required": False,
                "read_only": True,
                "hidden": True,
            },
            _project_ids_column("part_cards"),
            # Готовая сборка: набор слотов с требованиями к группам
            # характеристик + опциональный целевой коридор цены.
            {
                "table_slug": "ready_builds",
                "name": "name",
                "label": {"ru": "Название", "en": "Name"},
                "type": "text",
                "required": True,
            },
            {
                "table_slug": "ready_builds",
                "name": "build_kind",
                "label": {"ru": "Платформа", "en": "Platform"},
                "type": "enum",
                "required": True,
                "default": "pc",
                "enum": {
                    "values": _BUILD_KIND_VALUES,
                    "labels": _BUILD_KIND_LABELS,
                },
            },
            {
                # Целевой коридор: система перебирает умные комбинации
                # кандидатов (лидеры цены по каждому партномеру) и выбирает
                # конфигурацию внутри [min, max]. Пусто = дешевейшая.
                "table_slug": "ready_builds",
                "name": "target_price_min",
                "label": {"ru": "Целевая цена от, ₽", "en": "Target price from"},
                "type": "number",
                "required": False,
            },
            {
                "table_slug": "ready_builds",
                "name": "target_price_max",
                "label": {"ru": "Целевая цена до, ₽", "en": "Target price to"},
                "type": "number",
                "required": False,
            },
            {
                "table_slug": "ready_builds",
                "name": "note",
                "label": {"ru": "Комментарий", "en": "Note"},
                "type": "text",
                "required": False,
                "default": "",
            },
            {
                "table_slug": "ready_builds",
                "name": "is_enabled",
                "label": {"ru": "Включена", "en": "Enabled"},
                "type": "bool",
                "required": False,
                "default": True,
            },
            {
                # Состав сборки одной строкой: «Процессор: LGA1700 · 6 ядер ·
                # ОЗУ: DDR4 · 16 ГБ ×2». Пишется пайплайном из слотов.
                "table_slug": "ready_builds",
                "name": "spec_summary",
                "label": {"ru": "Состав", "en": "Spec"},
                "type": "text",
                "required": False,
                "default": "",
                "read_only": True,
            },
            {
                "table_slug": "ready_builds",
                "name": "slots_count",
                "label": {"ru": "Типов комплектующих", "en": "Component kinds"},
                "type": "number",
                "required": False,
                "default": 0,
                "read_only": True,
            },
            {
                "table_slug": "ready_builds",
                "name": "qty_total",
                "label": {"ru": "Компонентов всего", "en": "Total components"},
                "type": "number",
                "required": False,
                "default": 0,
                "read_only": True,
            },
            {
                # Плашки ОЗУ и диски — производные от слотов своего типа.
                "table_slug": "ready_builds",
                "name": "ram_sticks",
                "label": {"ru": "ОЗУ, плашек", "en": "RAM sticks"},
                "type": "number",
                "required": False,
                "default": 0,
                "read_only": True,
            },
            {
                "table_slug": "ready_builds",
                "name": "storage_drives",
                "label": {"ru": "Накопителей, шт", "en": "Drives"},
                "type": "number",
                "required": False,
                "default": 0,
                "read_only": True,
            },
            {
                "table_slug": "ready_builds",
                "name": "price_total",
                "label": {"ru": "Цена ₽", "en": "Price"},
                "type": "number",
                "required": False,
                "read_only": True,
            },
            {
                # Коридор возможных конфигураций: дешевейшая/дорожайшая
                # комбинация из текущих кандидатов слотов.
                "table_slug": "ready_builds",
                "name": "price_min",
                "label": {"ru": "Мин. комбинация ₽", "en": "Min combo"},
                "type": "number",
                "required": False,
                "read_only": True,
            },
            {
                "table_slug": "ready_builds",
                "name": "price_max",
                "label": {"ru": "Макс. комбинация ₽", "en": "Max combo"},
                "type": "number",
                "required": False,
                "read_only": True,
            },
            {
                "table_slug": "ready_builds",
                "name": "price_note",
                "label": {"ru": "Подбор цены", "en": "Price note"},
                "type": "text",
                "required": False,
                "default": "",
                "read_only": True,
            },
            {
                "table_slug": "ready_builds",
                "name": "on_order",
                "label": {"ru": "Под заказ", "en": "On order"},
                "type": "bool",
                "required": False,
                "default": False,
                "read_only": True,
            },
            {
                "table_slug": "ready_builds",
                "name": "unresolved_count",
                "label": {"ru": "Неразрешённых слотов", "en": "Unresolved slots"},
                "type": "number",
                "required": False,
                "default": 0,
                "read_only": True,
            },
            {
                # Матрица совместимости: домены (socket/ram_type/form_factor)
                # выбранных карточек сходятся. Грубая проверка — конфликты не
                # блокируют, а фиксируются в compat_issues.
                "table_slug": "ready_builds",
                "name": "compat_ok",
                "label": {"ru": "Совместимость", "en": "Compatibility"},
                "type": "bool",
                "required": False,
                "default": True,
                "read_only": True,
            },
            {
                "table_slug": "ready_builds",
                "name": "compat_issues",
                "label": {"ru": "Замечания совместимости", "en": "Compatibility issues"},
                "type": "text",
                "required": False,
                "default": "",
                "read_only": True,
            },
            {
                "table_slug": "ready_builds",
                "name": "synced_at",
                "label": {"ru": "Сверено", "en": "Synced at"},
                "type": "text",
                "required": False,
                "read_only": True,
                "hidden": True,
            },
            _project_ids_column("ready_builds"),
            # Слот готовой сборки: тип + требования (значения групп характеристик
            # типа) + количество + опционально закреплённая карточка.
            {
                "table_slug": "ready_build_slots",
                "name": "build_id",
                "label": {"ru": "Сборка", "en": "Build"},
                "type": "ref",
                "required": True,
                "ref": {"table_slug": "ready_builds"},
            },
            {
                "table_slug": "ready_build_slots",
                "name": "type_id",
                "label": {"ru": "Тип комплектующего", "en": "Component type"},
                "type": "ref",
                "required": True,
                "ref": {"table_slug": "equipment_types"},
            },
            {
                "table_slug": "ready_build_slots",
                "name": "type_name",
                "label": {"ru": "Тип", "en": "Type"},
                "type": "text",
                "required": False,
                "default": "",
                "read_only": True,
            },
            {
                # Требования слота: {ключ характеристики: значение группы},
                # например {"capacity": "512"}. Пусто = любая карточка типа.
                "table_slug": "ready_build_slots",
                "name": "constraints",
                "label": {"ru": "Требования", "en": "Constraints"},
                "type": "json",
                "required": False,
                "default": {},
            },
            {
                "table_slug": "ready_build_slots",
                "name": "constraints_label",
                "label": {"ru": "Требования", "en": "Constraints"},
                "type": "text",
                "required": False,
                "default": "",
                "read_only": True,
            },
            {
                "table_slug": "ready_build_slots",
                "name": "qty",
                "label": {"ru": "Количество", "en": "Quantity"},
                "type": "number",
                "required": False,
                "default": 1,
            },
            {
                # Ручная замена: закреплённая карточка из альтернатив слота.
                # Пусто = автоподбор дешевейшей подходящей (или по коридору).
                "table_slug": "ready_build_slots",
                "name": "card_id",
                "label": {"ru": "Закреплённая карточка", "en": "Pinned card"},
                "type": "ref",
                "required": False,
                "ref": {"table_slug": "part_cards"},
            },
            {
                "table_slug": "ready_build_slots",
                "name": "note",
                "label": {"ru": "Комментарий", "en": "Note"},
                "type": "text",
                "required": False,
                "default": "",
            },
            {
                "table_slug": "ready_build_slots",
                "name": "resolved_card_id",
                "label": {"ru": "Разрешённая карточка", "en": "Resolved card"},
                "type": "text",
                "required": False,
                "default": "",
                "read_only": True,
                "hidden": True,
            },
            {
                "table_slug": "ready_build_slots",
                "name": "resolved_title",
                "label": {"ru": "Компонент", "en": "Component"},
                "type": "text",
                "required": False,
                "default": "",
                "read_only": True,
            },
            {
                "table_slug": "ready_build_slots",
                "name": "resolved_part_number",
                "label": {"ru": "Партномер", "en": "Part number"},
                "type": "text",
                "required": False,
                "default": "",
                "read_only": True,
            },
            {
                "table_slug": "ready_build_slots",
                "name": "resolved_price",
                "label": {"ru": "Цена ₽", "en": "Price"},
                "type": "number",
                "required": False,
                "read_only": True,
            },
            {
                "table_slug": "ready_build_slots",
                "name": "resolved_seller",
                "label": {"ru": "Поставщик", "en": "Supplier"},
                "type": "text",
                "required": False,
                "default": "",
                "read_only": True,
            },
            {
                "table_slug": "ready_build_slots",
                "name": "in_stock",
                "label": {"ru": "В наличии", "en": "In stock"},
                "type": "bool",
                "required": False,
                "default": False,
                "read_only": True,
            },
            {
                "table_slug": "ready_build_slots",
                "name": "line_total",
                "label": {"ru": "Сумма слота ₽", "en": "Slot total"},
                "type": "number",
                "required": False,
                "read_only": True,
            },
            {
                "table_slug": "ready_build_slots",
                "name": "alternatives_count",
                "label": {"ru": "Альтернатив", "en": "Alternatives"},
                "type": "number",
                "required": False,
                "default": 0,
                "read_only": True,
            },
            {
                "table_slug": "ready_build_slots",
                "name": "synced_at",
                "label": {"ru": "Сверено", "en": "Synced at"},
                "type": "text",
                "required": False,
                "read_only": True,
                "hidden": True,
            },
            _project_ids_column("ready_build_slots"),
            {
                "table_slug": "equipment_prompts",
                "name": "name",
                "label": {"ru": "Название", "en": "Name"},
                "type": "text",
                "required": True,
            },
            {
                "table_slug": "equipment_prompts",
                "name": "path",
                "label": {"ru": "Путь в workspace", "en": "Workspace path"},
                "type": "text",
                "required": False,
                "default": "prompts/equipment",
            },
            {
                "table_slug": "equipment_prompts",
                "name": "files_json",
                "label": {"ru": "Промпты", "en": "Prompts"},
                "type": "json",
                "required": False,
                "default": [],
            },
            {
                "table_slug": "equipment_prompts",
                "name": "enabled",
                "label": {"ru": "Включён", "en": "Enabled"},
                "type": "bool",
                "required": False,
                "default": True,
            },
            _project_ids_column("equipment_prompts"),
            {
                "table_slug": "equipment_search",
                "name": "supplier",
                "label": {"ru": "Поставщик", "en": "Supplier"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "equipment_search",
                "name": "brand",
                "label": {"ru": "Бренд", "en": "Brand"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "equipment_search",
                "name": "part_number",
                "label": {"ru": "PN", "en": "PN"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "equipment_search",
                "name": "title",
                "label": {"ru": "Наименование", "en": "Title"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "equipment_search",
                "name": "in_stock",
                "label": {"ru": "Наличие", "en": "Stock"},
                "type": "bool",
                "required": False,
            },
            {
                "table_slug": "equipment_search",
                "name": "price",
                "label": {"ru": "Цена", "en": "Price"},
                "type": "number",
                "required": False,
            },
            {
                "table_slug": "equipment_search",
                "name": "currency",
                "label": {"ru": "Валюта", "en": "Currency"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "equipment_search",
                "name": "src_hash",
                "label": {"ru": "Ключ позиции", "en": "Position key"},
                "type": "text",
                "required": False,
                "hidden": True,
            },
            {
                "table_slug": "master_price",
                "name": "supplier",
                "label": {"ru": "Поставщик", "en": "Supplier"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "master_price",
                "name": "category",
                "label": {"ru": "Вид оборудования", "en": "Category"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "master_price",
                "name": "brand",
                "label": {"ru": "Бренд", "en": "Brand"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "master_price",
                "name": "part_number",
                "label": {"ru": "PN", "en": "PN"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "master_price",
                "name": "title",
                "label": {"ru": "Наименование", "en": "Title"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "master_price",
                "name": "in_stock",
                "label": {"ru": "Наличие", "en": "Stock"},
                "type": "bool",
                "required": False,
            },
            {
                "table_slug": "master_price",
                "name": "price",
                "label": {"ru": "Цена", "en": "Price"},
                "type": "number",
                "required": False,
            },
            {
                "table_slug": "master_price",
                "name": "currency",
                "label": {"ru": "Валюта", "en": "Currency"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "master_price",
                "name": "rrc",
                "label": {"ru": "РРЦ", "en": "RRP"},
                "type": "number",
                "required": False,
            },
            {
                "table_slug": "mcp_tool_overrides",
                "name": "tool",
                "label": {"ru": "Инструмент", "en": "Tool"},
                "type": "text",
                "required": True,
            },
            {
                "table_slug": "mcp_tool_overrides",
                "name": "description",
                "label": {"ru": "Описание (замена)", "en": "Description (replace)"},
                "type": "text",
                "required": False,
                "default": "",
            },
            {
                "table_slug": "mcp_tool_overrides",
                "name": "extra_instructions",
                "label": {"ru": "Доп. инструкции (добавить)", "en": "Extra instructions (append)"},
                "type": "text",
                "required": False,
                "default": "",
            },
            {
                "table_slug": "mcp_tool_overrides",
                "name": "enabled",
                "label": {"ru": "Включено", "en": "Enabled"},
                "type": "bool",
                "required": False,
                "default": True,
            },
            _project_ids_column("mcp_tool_overrides"),
            {
                "table_slug": "document_company_fields",
                "name": "supplier_name",
                "label": {"ru": "Поставщик", "en": "Supplier"},
                "type": "text",
                "required": False,
                "default": 'ООО "ИТ Взлёт"',
            },
            {
                "table_slug": "document_company_fields",
                "name": "supplier_inn",
                "label": {"ru": "ИНН поставщика", "en": "Supplier INN"},
                "type": "text",
                "required": False,
                "default": "7716960580",
            },
            {
                "table_slug": "document_company_fields",
                "name": "supplier_kpp",
                "label": {"ru": "КПП поставщика", "en": "Supplier KPP"},
                "type": "text",
                "required": False,
                "default": "771601001",
            },
            {
                "table_slug": "document_company_fields",
                "name": "supplier_address",
                "label": {"ru": "Адрес поставщика", "en": "Supplier address"},
                "type": "text",
                "required": False,
                "default": "127282, г. Москва, Чермянский проезд, д. 5, стр. 1",
            },
            {
                "table_slug": "document_company_fields",
                "name": "supplier_email",
                "label": {"ru": "Email поставщика", "en": "Supplier email"},
                "type": "text",
                "required": False,
                "default": "info@itvzlet.ru",
            },
            {
                "table_slug": "document_company_fields",
                "name": "supplier_signatory",
                "label": {"ru": "Подписант поставщика", "en": "Supplier signatory"},
                "type": "text",
                "required": False,
                "default": "Троцкий А.В.",
            },
            {
                "table_slug": "document_company_fields",
                "name": "city",
                "label": {"ru": "Город", "en": "City"},
                "type": "text",
                "required": False,
                "default": "г. Москва",
            },
            {
                "table_slug": "document_company_fields",
                "name": "app_number",
                "label": {"ru": "№ приложения", "en": "Appendix number"},
                "type": "text",
                "required": False,
                "default": "1",
            },
            {
                "table_slug": "document_company_fields",
                "name": "delivery_place",
                "label": {"ru": "Условия доставки", "en": "Delivery terms"},
                "type": "text",
                "required": False,
                "default": "склада Заказчика",
            },
            {
                "table_slug": "document_company_fields",
                "name": "kp_valid_days",
                "label": {"ru": "КП действует, дней", "en": "KP valid days"},
                "type": "number",
                "required": False,
                "default": 2,
            },
            {
                "table_slug": "document_company_fields",
                "name": "contract_seq",
                "label": {"ru": "Счётчик № договоров", "en": "Contract seq"},
                "type": "number",
                "required": False,
                "default": 0,
                "read_only": True,
                "hidden": True,
            },
            {
                "table_slug": "document_company_fields",
                "name": "spec_seq",
                "label": {"ru": "Счётчик № спецификаций", "en": "Spec seq"},
                "type": "number",
                "required": False,
                "default": 0,
                "read_only": True,
                "hidden": True,
            },
            {
                "table_slug": "document_fields",
                "name": "customer_name",
                "label": {"ru": "Покупатель", "en": "Customer"},
                "type": "text",
                "required": False,
                "default": "ООО «Ромашка»",
            },
            {
                "table_slug": "document_fields",
                "name": "customer_signatory",
                "label": {"ru": "Подписант покупателя", "en": "Customer signatory"},
                "type": "text",
                "required": False,
                "default": "Иванов И.И.",
            },
            {
                "table_slug": "document_fields",
                "name": "customer_basis",
                "label": {"ru": "Основание покупателя", "en": "Customer basis"},
                "type": "text",
                "required": False,
                "default": "Устава",
            },
            {
                "table_slug": "document_fields",
                "name": "contract_number",
                "label": {"ru": "№ договора", "en": "Contract number"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "document_fields",
                "name": "contract_date",
                "label": {"ru": "Дата договора", "en": "Contract date"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "document_fields",
                "name": "spec_number",
                "label": {"ru": "№ спецификации", "en": "Spec number"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "document_fields",
                "name": "delivery_address",
                "label": {"ru": "Адрес поставки", "en": "Delivery address"},
                "type": "text",
                "required": False,
                "default": "125167, г. Москва, Ленинградский пр., д. 37, пом. 21/10",
            },
            {
                "table_slug": "document_fields",
                "name": "delivery_days",
                "label": {"ru": "Срок поставки (раб. дней)", "en": "Delivery days"},
                "type": "text",
                "required": False,
                "default": "7 (семи)",
            },
            {
                "table_slug": "document_fields",
                "name": "payment_days",
                "label": {"ru": "Срок оплаты (раб. дней)", "en": "Payment days"},
                "type": "text",
                "required": False,
                "default": "30 (тридцати)",
            },
            {
                "table_slug": "document_fields",
                "name": "lead_time_note",
                "label": {"ru": "Срок поставки (примечание)", "en": "Lead time note"},
                "type": "text",
                "required": False,
                "default": "10-12 недель",
            },
        ],
        "views": [
            {
                "slug": "equipment_hub",
                "table_slug": "catalogs",
                "kind": "hub",
                "ui_json": {
                    "version": 1,
                    "kind": "hub",
                    "items": [
                        {
                            "title": "Бюджетирование",
                            "icon": "request_quote",
                            "target": {
                                "kind": "view",
                                "view": "budget_lines_list",
                            },
                            "scope": {"active_chat": "required"},
                        },
                        {
                            "title": "Закупка",
                            "icon": "shopping_cart",
                            "target": {"kind": "view", "view": "procurement_list"},
                            "scope": {"active_chat": "required"},
                        },
                        {
                            "title": "Позиции заказчика",
                            "icon": "list_alt",
                            "target": {"kind": "view", "view": "request_lines_list"},
                            "scope": {"active_chat": "required"},
                        },
                        {
                            "title": "Найденные товары",
                            "icon": "inventory_2",
                            "target": {"kind": "view", "view": "found_groups_list"},
                            "scope": {"active_chat": "required"},
                        },
                        {
                            "title": "Сборка",
                            "icon": "precision_manufacturing",
                            "target": {
                                "kind": "view",
                                "view": "equipment_builds_list",
                            },
                            "scope": {"active_chat": "required"},
                        },
                        {
                            # виртуальная таблица из OpenSearch: чат не нужен
                            "title": "Мастер прайс",
                            "icon": "inventory_2",
                            "target": {
                                "kind": "view",
                                "view": "master_price_list",
                            },
                        },
                        {
                            "title": "Поиск товаров",
                            "icon": "search",
                            "target": {
                                "kind": "view",
                                "view": "equipment_search_list",
                            },
                        },
                    ],
                },
            },
            {
                "slug": "catalogs_list",
                "table_slug": "catalogs",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {"title": {"ru": "Базы данных", "en": "Databases"}},
                    "title_field": "name",
                    "subtitle_fields": ["row_count"],
                    "columns": [
                        {
                            "field": "name",
                            "label": {"ru": "Название", "en": "Name"},
                        },
                        {
                            "field": "source_kind",
                            "label": {"ru": "Тип", "en": "Type"},
                        },
                        {
                            "field": "status",
                            "label": {"ru": "Статус", "en": "Status"},
                            "format": "index_progress",
                        },
                        {"field": "row_count", "label": {"ru": "Строк", "en": "Rows"}},
                    ],
                    "poll_while": {
                        "field": "status",
                        "equals": ["queued", "indexing"],
                        "interval_ms": 3000,
                    },
                    "row_style": [
                        {
                            "when": {"field": "status", "eq": "error"},
                            "accent": "error",
                        },
                        {
                            "when": {"field": "status", "eq": "queued"},
                            "accent": "warning",
                        },
                        {
                            "when": {"field": "status", "eq": "indexing"},
                            "accent": "warning",
                        },
                        {
                            "when": {"field": "status", "eq": "draft"},
                            "accent": "warning",
                        },
                        {
                            "when": {"field": "status", "eq": "ready"},
                            "accent": "success",
                        },
                        {
                            "when": {"field": "paused", "eq": True},
                            "accent": "warning",
                        },
                    ],
                    "row_tap": {"kind": "open_view", "view": "catalogs_settings"},
                    "inline_add": {"field": "name", "title": "Добавить базу"},
                    "list_header": _equipment_mcp_list_header(),
                    "empty": _empty("Нет баз", "No databases", icon="storage"),
                },
            },
            {
                "slug": "equipment_mcp_list",
                "table_slug": "equipment_mcp",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {"ru": "MCP", "en": "MCP"},
                    },
                    "title_field": "name",
                    "subtitle_fields": ["version"],
                    "columns": [
                        {
                            "field": "name",
                            "label": {"ru": "Название", "en": "Name"},
                        },
                        {
                            "field": "version",
                            "label": {"ru": "Версия", "en": "Version"},
                        },
                    ],
                    "row_tap": {"kind": "open_form", "view": "equipment_mcp_form"},
                    "inline_add": {"field": "name", "title": "Добавить MCP"},
                    "empty": _empty("Нет MCP", "No MCP", icon="hub"),
                },
            },
            {
                "slug": "equipment_mcp_form",
                "table_slug": "equipment_mcp",
                "kind": "form",
                "ui_json": {
                    "version": 1,
                    "kind": "form",
                    "mode": "edit",
                    "title": {"ru": "MCP", "en": "MCP"},
                    "fields": [
                        {"column": "project_ids", "widget": "project_multiselect"},
                        {"column": "name", "widget": "value"},
                        {"column": "version", "widget": "value"},
                        {"column": "enabled", "widget": "switch"},
                        {
                            "column": "file_ref",
                            "widget": "file_upload",
                            "accept": ".zip",
                        },
                    ],
                },
            },
            {
                "slug": "budget_lines_list",
                "table_slug": "budget_lines",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {"ru": "Бюджетирование", "en": "Budget"},
                        # AppBar actions (top-right): budget xlsx / КП PDF /
                        # spec PDF, sync to the right of the export buttons.
                        "actions": [
                            {
                                "kind": "invoke_action",
                                "action": "budget_export",
                                "icon": "download",
                                "label": {"ru": "Скачать xlsx", "en": "Download xlsx"},
                            },
                            {
                                "kind": "invoke_action",
                                "action": "kp_export",
                                "icon": "picture_as_pdf",
                                "label": {"ru": "Скачать КП (PDF)", "en": "Download quote (PDF)"},
                            },
                            {
                                "kind": "invoke_action",
                                "action": "spec_export",
                                "icon": "table_view",
                                "label": {"ru": "Спецификация (PDF)", "en": "Specification (PDF)"},
                            },
                            {
                                "kind": "invoke_action",
                                "action": "budget_sync_lines",
                                "icon": "sync",
                                "label": {"ru": "Синхронизировать", "en": "Sync"},
                            },
                        ],
                    },
                    # Inject an icon button into the project chat header when the
                    # equipment module is bound and a chat is open (chat-scoped
                    # budget rows); opens this view in the module runtime host.
                    "chat_header": {
                        "icon": "request_quote",
                        "label": {"ru": "Бюджетирование", "en": "Budget"},
                    },
                    # сверка цен с OpenSearch при открытии (price_in ← офферы)
                    "on_load": {"action": "equipment_pipeline_sync"},
                    "title_field": "title",
                    "subtitle_fields": ["part_number"],
                    # Totals strip above the table (Flutter-side computation from
                    # the same budget_lines bodies the table renders).
                    "summary": {"kind": "budget_totals"},
                    # Comfortable row padding for the budget table (default
                    # dataRowMinHeight 40 → taller airy rows; ui_json knob,
                    # see AppEntityCollection).
                    # Панель реквизитов документов — справа от суммаризатора:
                    # поля, которые нельзя вывести из данных. Две группы:
                    # компания (singleton на весь кабинет) + сделка (singleton чата).
                    "doc_fields": {
                        "company_table": "document_company_fields",
                        "deal_table": "document_fields",
                        "company_title": {"ru": "Поставщик", "en": "Supplier"},
                        "deal_title": {"ru": "Сделка", "en": "Deal"},
                        "company_fields": [
                            {"column": "supplier_name"},
                            {"column": "supplier_inn"},
                            {"column": "supplier_kpp"},
                            {"column": "supplier_address"},
                            {"column": "supplier_email"},
                            {"column": "supplier_signatory"},
                            {"column": "city"},
                            {"column": "app_number"},
                            {"column": "delivery_place"},
                            {"column": "kp_valid_days"},
                        ],
                        "deal_fields": [
                            {"column": "customer_name"},
                            {"column": "customer_signatory"},
                            {"column": "customer_basis"},
                            {"column": "contract_number", "auto": "contract_seq"},
                            {"column": "contract_date", "auto": "today"},
                            {"column": "spec_number", "auto": "spec_seq"},
                            {"column": "delivery_address"},
                            {"column": "delivery_days"},
                            {"column": "payment_days"},
                            {"column": "lead_time_note"},
                        ],
                    },
                    "row_min_height": 52,
                    "columns": [
                        {
                            "field": "title",
                            "label": {"ru": "Наименование", "en": "Title"},
                            "max_lines": 2,
                            "max_width": 240,
                            # аналог/сомнение → warning-текст ячейки
                            "warning_when_match": ["analog", "doubt"],
                        },
                        {
                            "field": "part_number",
                            "label": {"ru": "Партномер", "en": "P/N"},
                            "max_lines": 2,
                            "max_width": 80,
                            "warning_when_match": ["analog", "doubt"],
                        },
                        # Markup takes the supplier column's slot (supplier
                        # dropped by request; markup editable in place).
                        {
                            "field": "markup",
                            "label": {"ru": "Наценка", "en": "Markup"},
                            "align": "end",
                            "max_width": 110,
                            "editable": True,
                        },
                        {
                            "field": "qty",
                            "label": {"ru": "Кол-во", "en": "Qty"},
                            "align": "end",
                            "max_width": 90,
                        },
                        {
                            "field": "price_in",
                            "label": {"ru": "Вход с НДС", "en": "In w/ VAT"},
                            "format": "budget_price_in",
                            "align": "end",
                            "max_width": 130,
                        },
                        {
                            "field": "vat",
                            "label": {"ru": "НДС", "en": "VAT"},
                            "align": "end",
                            "max_width": 100,
                            "editable": True,
                        },
                        {
                            "field": "price_out",
                            "label": {"ru": "Цена с маржой, с НДС", "en": "Price out w/ VAT"},
                            "format": "budget_calc",
                            "variant": "price_out",
                            "align": "end",
                            "max_width": 150,
                        },
                        {
                            "field": "price_no_vat",
                            "label": {"ru": "Цена без НДС", "en": "Price w/o VAT"},
                            "format": "budget_calc",
                            "variant": "price_no_vat",
                            "align": "end",
                            "max_width": 130,
                        },
                        {
                            "field": "margin_total",
                            "label": {"ru": "Маржа", "en": "Margin"},
                            "format": "budget_calc",
                            "variant": "margin_total",
                            "align": "end",
                            "max_width": 150,
                        },
                    ],
                    # computed columns are Flutter-side; backend ships raw fields
                    "row_tap": {
                        "kind": "open_view",
                        "view": "groups_for_line",
                        "context_field": "line_id",
                    },
                    "empty": _empty("Нет позиций", "No budget lines", icon="request_quote"),
                },
            },
            {
                "slug": "catalogs_settings",
                "table_slug": "catalogs",
                "kind": "detail",
                "ui_json": {
                    "version": 1,
                    "kind": "detail",
                    "mode": "edit",
                    "title": {"ru": "Настройки БД", "en": "Database settings"},
                    "poll_while": {
                        "field": "status",
                        "equals": ["queued", "indexing"],
                        "interval_ms": 3000,
                    },
                    "fields": [
                        {"column": "name", "widget": "value", "icon": "storage"},
                        {
                            "column": "status",
                            "widget": "value",
                            "read_only": True,
                            "icon": "flag_outlined",
                            "trailing_action": {
                                "action_id": "index_catalog_opensearch",
                                "icon": "refresh",
                                "tooltip": {
                                    "ru": "Обновить индекс",
                                    "en": "Reindex",
                                },
                            },
                            "accent_map": {
                                "draft": "warning",
                                "queued": "warning",
                                "indexing": "warning",
                                "ready": "success",
                                "error": "error",
                            },
                        },
                        {
                            "column": "source_kind",
                            "widget": "choice",
                            "icon": "category",
                        },
                        {
                            "column": "source_file",
                            "widget": "file_upload",
                            "accept": ".csv,.xlsx,.xls",
                            "subtitle_from": "row_count",
                            "empty_style": "warning",
                            "visible_when": {"field": "source_kind", "eq": "local"},
                        },
                        {
                            "column": "remote_dsn",
                            "widget": "value",
                            "secret": True,
                            "icon": "link",
                            "hint": "postgresql://user:pass@host:5432/dbname",
                            "visible_when": {"field": "source_kind", "eq": "remote"},
                        },
                        {
                            "column": "remote_user",
                            "widget": "value",
                            "icon": "person_outline",
                            "visible_when": {
                                "any": [
                                    {
                                        "all": [
                                            {
                                                "field": "source_kind",
                                                "eq": "remote",
                                            },
                                            {
                                                "field": "remote_dsn",
                                                "not_empty": True,
                                            },
                                            {
                                                "field": "remote_dsn_has_user",
                                                "eq": False,
                                            },
                                        ]
                                    },
                                    {
                                        "all": [
                                            {
                                                "field": "source_kind",
                                                "eq": "remote",
                                            },
                                            {
                                                "field": "remote_dsn",
                                                "not_empty": True,
                                            },
                                            {
                                                "field": "remote_auth_failed",
                                                "eq": True,
                                            },
                                        ]
                                    },
                                ]
                            },
                        },
                        {
                            "column": "remote_password",
                            "widget": "value",
                            "secret": True,
                            "icon": "lock_outline",
                            "visible_when": {
                                "any": [
                                    {
                                        "all": [
                                            {
                                                "field": "source_kind",
                                                "eq": "remote",
                                            },
                                            {
                                                "field": "remote_dsn",
                                                "not_empty": True,
                                            },
                                            {
                                                "field": "remote_dsn_has_password",
                                                "eq": False,
                                            },
                                        ]
                                    },
                                    {
                                        "all": [
                                            {
                                                "field": "source_kind",
                                                "eq": "remote",
                                            },
                                            {
                                                "field": "remote_dsn",
                                                "not_empty": True,
                                            },
                                            {
                                                "field": "remote_auth_failed",
                                                "eq": True,
                                            },
                                        ]
                                    },
                                ]
                            },
                        },
                        {
                            "column": "remote_database",
                            "widget": "remote_database_picker",
                            "icon": "storage",
                            "list_action": "list_catalog_remote_databases",
                            "empty_style": "warning",
                            "empty_label": {
                                "ru": "Не выбрана",
                                "en": "Not selected",
                            },
                            "visible_when": {
                                "all": [
                                    {"field": "source_kind", "eq": "remote"},
                                    {
                                        "field": "remote_dsn_url_has_database",
                                        "eq": False,
                                    },
                                    {
                                        "field": "status",
                                        "in": ["draft", "ready", "queued", "indexing"],
                                    },
                                    {
                                        "any": [
                                            {
                                                "field": "remote_dsn_reachable",
                                                "eq": True,
                                            },
                                            {
                                                "field": "status",
                                                "in": ["ready", "indexing"],
                                            },
                                        ]
                                    },
                                ]
                            },
                        },
                        {
                            "column": "remote_table",
                            "widget": "remote_table_picker",
                            "icon": "table_chart",
                            "list_action": "list_catalog_remote_tables",
                            "empty_style": "warning",
                            "empty_label": {
                                "ru": "Не выбрана",
                                "en": "Not selected",
                            },
                            "visible_when": {
                                "all": [
                                    {"field": "source_kind", "eq": "remote"},
                                    {
                                        "field": "remote_dsn_has_database",
                                        "eq": True,
                                    },
                                    {
                                        "field": "status",
                                        "in": ["draft", "ready", "queued", "indexing"],
                                    },
                                    {
                                        "any": [
                                            {
                                                "field": "remote_dsn_reachable",
                                                "eq": True,
                                            },
                                            {
                                                "field": "status",
                                                "in": ["ready", "indexing"],
                                            },
                                        ]
                                    },
                                ]
                            },
                        },
                        {
                            "column": "error",
                            "widget": "value",
                            "read_only": True,
                            "copy_on_tap": True,
                            "icon": "error_outline",
                            "accent": "error",
                            "visible_when": {"field": "status", "eq": "error"},
                        },
                        {
                            "column": "column_map",
                            "widget": "column_map",
                            "source_columns_from": "columns_json",
                            "schema": _CATALOG_COLUMN_MAP_SCHEMA,
                            "visible_when": {
                                "field": "columns_json",
                                "not_empty": True,
                            },
                        },
                        {
                            "column": "reindex_interval_hours",
                            "widget": "value",
                            "icon": "schedule",
                            "visible_when": {"field": "source_kind", "eq": "remote"},
                        },
                        {
                            "column": "last_indexed_at",
                            "widget": "value",
                            "read_only": True,
                            "icon": "update",
                            "visible_when": {
                                "field": "last_indexed_at",
                                "not_empty": True,
                            },
                        },
                        {
                            "column": "index_name",
                            "widget": "value",
                            "read_only": True,
                            "icon": "dns",
                            "visible_when": {
                                "field": "index_name",
                                "not_empty": True,
                            },
                        },
                        {
                            "column": "project_ids",
                            "widget": "project_multiselect",
                            "icon": "folder_outlined",
                            "visible_when": {
                                "any": [
                                    {"field": "status", "eq": "ready"},
                                    {
                                        "field": "columns_json",
                                        "not_empty": True,
                                    },
                                ]
                            },
                        },
                        {
                            "column": "paused",
                            "widget": "pause_toggle",
                            "accent": "warning",
                            "pause_label": {
                                "ru": "Приостановить",
                                "en": "Pause",
                            },
                            "resume_label": {
                                "ru": "Возобновить",
                                "en": "Resume",
                            },
                            "pause_icon": "pause_outlined",
                            "resume_icon": "play_arrow_outlined",
                            "visible_when": {"field": "status", "eq": "ready"},
                        },
                    ],
                },
            },
            {
                "slug": "request_lines_list",
                "table_slug": "request_lines",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {"ru": "Позиции заказчика", "en": "Request lines"}
                    },
                    "title_field": "title",
                    "subtitle_fields": ["part_number", "found_count"],
                    "columns": [
                        {
                            "field": "title",
                            "label": {"ru": "Название", "en": "Title"},
                            "max_lines": 3,
                            "max_width": 300,
                        },
                        {
                            "field": "part_number",
                            "label": {"ru": "Партномер", "en": "P/N"},
                            "max_lines": 2,
                            "max_width": 150,
                        },
                        {
                            "field": "qty",
                            "label": {"ru": "Кол-во", "en": "Qty"},
                            "align": "end",
                            "max_width": 90,
                        },
                        {
                            "field": "found_count",
                            "label": {"ru": "Найдено", "en": "Found"},
                            "align": "end",
                            "max_width": 100,
                        },
                        {
                            "field": "build_best_price",
                            "label": {"ru": "Сборка ₽", "en": "Build RUB"},
                            "format": "offer_price",
                            "align": "end",
                            "max_width": 120,
                        },
                        {
                            "field": "build_benefit_label",
                            "label": {"ru": "Выгода", "en": "Benefit"},
                            "format": "benefit_static",
                            "label_field": "build_benefit_label",
                            "tone_field": "build_benefit_tone",
                            "align": "center",
                            "max_width": 130,
                        },
                    ],
                    "row_tap": {"kind": "open_view", "view": "groups_for_line"},
                    "toolbar": [
                        {
                            "kind": "open_view",
                            "view": "equipment_builds_list",
                            "icon": "precision_manufacturing",
                            "label": {"ru": "Сборки", "en": "Builds"},
                            "context": "row",
                        },
                    ],
                    "inline_add": {"field": "title", "title": "Добавить позицию"},
                    "list_header": _equipment_mcp_list_header(),
                    "empty": _empty("Нет позиций", "No lines", icon="list_alt"),
                },
            },
            {
                "slug": "request_lines_form",
                "table_slug": "request_lines",
                "kind": "form",
                "ui_json": {
                    "version": 1,
                    "kind": "form",
                    "mode": "edit",
                    "title": {"ru": "Позиция", "en": "Line"},
                    "fields": [
                        {"column": "title", "widget": "value"},
                        {"column": "part_number", "widget": "value"},
                        {"column": "qty", "widget": "value"},
                    ],
                },
            },
            {
                "slug": "groups_for_line",
                "table_slug": "found_groups",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {"ru": "Найденные товары", "en": "Found products"}
                    },
                    "title_field": "face_title",
                    "subtitle_fields": ["part_number", "match_label", "note"],
                    # сверка с OpenSearch при открытии (дрилл-даун позиции/бюджета)
                    "on_load": {"action": "equipment_pipeline_sync"},
                    # только кандидаты позиции (не кандидаты слотов сборок)
                    "row_filter": {"owner_kind": "line"},
                    "columns": [
                        {
                            "field": "face_title",
                            "label": {"ru": "Товар", "en": "Product"},
                            "max_lines": 3,
                            "max_width": 260,
                        },
                        {
                            "field": "face_brand",
                            "label": {"ru": "Бренд", "en": "Brand"},
                            "max_lines": 2,
                            "max_width": 110,
                        },
                        {
                            "field": "part_number",
                            "label": {"ru": "Партномер", "en": "P/N"},
                            "max_lines": 2,
                            "max_width": 140,
                        },
                        {
                            "field": "match_label",
                            "label": {"ru": "Совпадение", "en": "Match"},
                            "max_width": 150,
                        },
                        {
                            "field": "face_price",
                            "label": {"ru": "Цена ₽", "en": "Price RUB"},
                            "format": "offer_price",
                            "align": "end",
                            "max_width": 110,
                        },
                        {
                            "field": "face_seller",
                            "label": {"ru": "Поставщик", "en": "Supplier"},
                            "max_lines": 2,
                            "max_width": 150,
                        },
                        {
                            "field": "alternatives_count",
                            "label": {"ru": "Альтернативы", "en": "Alternatives"},
                            "align": "end",
                            "max_width": 110,
                        },
                        {
                            "field": "benefit",
                            "label": {"ru": "Выгода", "en": "Benefit"},
                            "format": "benefit",
                            "price_field": "face_price",
                            "current_field": "is_best",
                            "align": "center",
                            "max_width": 130,
                        },
                    ],
                    "sort": [
                        {"field": "rank", "dir": "asc"},
                        {"field": "face_priority", "dir": "desc"},
                        {"field": "face_price", "dir": "asc"},
                        {"field": "face_in_stock", "dir": "desc"},
                    ],
                    # Цвет строки — только warning устаревшего лица; «лучшая»
                    # подсветка живёт в бейдже «Выгода», а не всей строке.
                    "row_style": [
                        {"when": {"field": "face_stale", "eq": True}, "accent": "warning"},
                    ],
                    "context_bind": {"line_id": "contextRowId"},
                    "row_tap": {"kind": "open_view", "view": "offers_for_group"},
                    "empty": _empty("Нет товаров", "No products", icon="inventory_2"),
                },
            },
            {
                "slug": "offers_for_group",
                "table_slug": "found_offers",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {"ru": "Предложения группы", "en": "Group offers"}
                    },
                    "title_field": "title",
                    "subtitle_fields": ["seller", "part_number"],
                    "columns": [
                        {
                            "field": "title",
                            "label": {"ru": "Товар", "en": "Product"},
                            "max_lines": 3,
                            "max_width": 260,
                        },
                        {
                            "field": "seller",
                            "label": {"ru": "Поставщик", "en": "Supplier"},
                            "max_lines": 2,
                            "max_width": 150,
                        },
                        {
                            "field": "part_number",
                            "label": {"ru": "Партномер", "en": "P/N"},
                            "max_lines": 2,
                            "max_width": 80,
                        },
                        {
                            "field": "price",
                            "label": {"ru": "Цена ₽", "en": "Price RUB"},
                            "format": "offer_price",
                            "align": "end",
                            "max_width": 110,
                        },
                        {
                            "field": "in_stock",
                            "label": {"ru": "Наличие", "en": "Stock"},
                            "format": "bool_yes_no",
                            "max_width": 90,
                        },
                    ],
                    # наличие сверху; подзаказные и безценовые — ниже
                    "sort": [
                        {"field": "in_stock", "dir": "desc"},
                        {"field": "price", "dir": "asc"},
                    ],
                    # позиция пропала из каталога или под заказ → warning;
                    # приоритетный поставщик → зелёный текст
                    "row_style": [
                        {
                            "when": {"field": "is_stale", "eq": True},
                            "accent": "warning",
                        },
                        {
                            "when": {"field": "in_stock", "eq": False},
                            "accent": "warning",
                        },
                        {
                            "when": {"field": "priority", "eq": True},
                            "accent": "success",
                        },
                    ],
                    "context_bind": {"group_id": "contextRowId"},
                    "selection": {
                        "kind": "single",
                        "field": "is_selected",
                        "action": "select_offer_primary",
                        "control": "checkbox",
                    },
                    "row_tap": {"kind": "open_form", "view": "found_offers_form"},
                    "empty": _empty("Нет предложений", "No offers", icon="inventory_2"),
                },
            },
            {
                "slug": "found_groups_list",
                "table_slug": "found_groups",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "title_field": "face_title",
                    "subtitle_fields": ["part_number", "match_label", "note"],
                    "scaffold": {
                        "title": {"ru": "Найденные товары", "en": "Found products"},
                        "actions": [
                            {
                                "kind": "invoke_action",
                                "action": "equipment_pipeline_sync",
                                "icon": "refresh",
                                "label": {"ru": "Обновить цены", "en": "Refresh prices"},
                            },
                        ],
                    },
                    # сверка с OpenSearch при открытии таблицы
                    "on_load": {"action": "equipment_pipeline_sync"},
                    # только кандидаты позиций; кандидаты слотов сборок живут
                    # внутри сборки (found_groups_pick)
                    "row_filter": {"owner_kind": "line"},
                    # без офферов: «Товар» = «Нет оффера» warning; продавец/бренд — прочерк
                    "no_offers_placeholder": {"text": "Нет оффера"},
                    "dash_empty_fields": ["face_seller", "face_brand"],
                    "columns": [
                        {
                            "field": "face_title",
                            "label": {"ru": "Товар", "en": "Product"},
                            "max_lines": 3,
                            "max_width": 260,
                        },
                        {
                            "field": "face_brand",
                            "label": {"ru": "Бренд", "en": "Brand"},
                            "max_lines": 2,
                            "max_width": 110,
                        },
                        {
                            "field": "part_number",
                            "label": {"ru": "Партномер", "en": "P/N"},
                            "max_lines": 2,
                            "max_width": 140,
                            "format": "pn_fallback",
                        },
                        {
                            "field": "match_label",
                            "label": {"ru": "Совпадение", "en": "Match"},
                            "max_width": 150,
                        },
                        {
                            "field": "face_price",
                            "label": {"ru": "Цена ₽", "en": "Price RUB"},
                            "format": "face_price",
                            "align": "end",
                            "max_width": 110,
                        },
                        {
                            "field": "face_seller",
                            "label": {"ru": "Поставщик", "en": "Supplier"},
                            "max_lines": 2,
                            "max_width": 150,
                        },
                        {
                            "field": "alternatives_count",
                            "label": {"ru": "Альтернативы", "en": "Alternatives"},
                            "align": "end",
                            "max_width": 110,
                        },
                        {
                            "field": "benefit",
                            "label": {"ru": "Выгода", "en": "Benefit"},
                            "format": "benefit",
                            "price_field": "face_price",
                            "current_field": "is_best",
                            # общий список группует все позиции — выгода
                            # считается внутри каждой позиции отдельно
                            "group_field": "line_id",
                            "align": "center",
                            "max_width": 130,
                        },
                    ],
                    "sort": [
                        {"field": "rank", "dir": "asc"},
                        {"field": "face_priority", "dir": "desc"},
                        {"field": "face_price", "dir": "asc"},
                        {"field": "face_in_stock", "dir": "desc"},
                    ],
                    # Цвет строки — только warning устаревшего лица; «лучшая»
                    # подсветка живёт в бейдже «Выгода», а не всей строке.
                    "row_style": [
                        {"when": {"field": "face_stale", "eq": True}, "accent": "warning"},
                    ],
                    "row_tap": {"kind": "open_view", "view": "offers_for_group"},
                    "list_header": _equipment_mcp_list_header(),
                    "empty": _empty("Нет товаров", "No products", icon="inventory_2"),
                },
            },
            {
                "slug": "found_offers_form",
                "table_slug": "found_offers",
                "kind": "form",
                "ui_json": {
                    "version": 1,
                    "kind": "form",
                    "mode": "edit",
                    "title": {"ru": "Найденный товар", "en": "Offer"},
                    # Только «каталожные» поля: связь с позицией/группой, точность
                    # и флаги выбора/staleness вычисляет пайплайн — ручная правка
                    # рвёт связность (см. SYNCED_OFFER_FIELDS).
                    "fields": [
                        {"column": "title", "widget": "value"},
                        {"column": "brand", "widget": "value"},
                        {"column": "part_number", "widget": "value"},
                        {"column": "seller", "widget": "value"},
                        {"column": "price", "widget": "value"},
                        {"column": "price_orig", "widget": "value"},
                        {"column": "currency", "widget": "choice"},
                        {"column": "in_stock", "widget": "switch"},
                        {"column": "lead_time", "widget": "value"},
                    ],
                },
            },
            {
                "slug": "procurement_list",
                "table_slug": "procurement",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {"ru": "Закупка", "en": "Procurement"},
                    },
                    "title_field": "seller",
                    "subtitle_fields": ["selected_count", "qty_total"],
                    # сверка с OpenSearch при открытии таблицы
                    "on_load": {"action": "equipment_pipeline_sync"},
                    "columns": [
                        {
                            "field": "seller",
                            "label": {"ru": "Поставщик", "en": "Supplier"},
                            "max_lines": 2,
                            # фиксированная ширина: имя поставщика короткое,
                            # а без width DataTable растягивает первую колонку
                            "width": 220,
                        },
                        {
                            "field": "selected_count",
                            "label": {"ru": "Товаров", "en": "Products"},
                            "align": "end",
                            "max_width": 90,
                        },
                        {
                            "field": "qty_total",
                            "label": {"ru": "Количество", "en": "Quantity"},
                            "align": "end",
                            "max_width": 100,
                        },
                        {
                            "field": "sum_rub",
                            "label": {"ru": "Сумма ₽", "en": "Sum RUB"},
                            "align": "end",
                            "max_width": 130,
                        },

                        {
                            "field": "delivery_rub",
                            "label": {"ru": "Доставка ₽", "en": "Delivery RUB"},
                            "align": "end",
                            "max_width": 110,
                        },
                        {
                            "field": "sum_with_margin_rub",
                            "label": {"ru": "Сумма с маржой ₽", "en": "Sum w/ margin"},
                            "align": "end",
                            "max_width": 150,
                        },
                        {
                            "field": "sum_margin_rub",
                            "label": {"ru": "Маржа", "en": "Margin"},
                            "format": "margin_pair",
                            "align": "end",
                            "max_width": 150,
                        },
                    ],
                    "row_tap": {
                        "kind": "open_view",
                        "view": "supplier_offers",
                    },
                    "empty": _empty(
                        "Нет поставщиков с товарами",
                        "No suppliers with offers",
                        icon="shopping_cart",
                    ),
                },
            },
            {
                "slug": "supplier_offers",
                "table_slug": "found_offers",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {"ru": "Товары поставщика", "en": "Supplier offers"},
                        # заголовок дрилл-дауна из «Закупки»: имя поставщика
                        # подставляется из тела контекстной строки (seller)
                        "title_template": {"ru": "Товары {seller}", "en": "{seller} offers"},
                    },
                    # Закупка агрегирует ВСЕ чаты проекта — дрилл-даун тоже:
                    # офферы поставщика из всех позиций всех заказов (чатов).
                    # Выбор чекбоксом пишется в бакет чата самой строки
                    # (select_offer_primary: сессия берётся из строки).
                    "data_scope": {"chats": "all"},
                    "title_field": "title",
                    "subtitle_fields": ["seller", "part_number"],
                    "columns": [
                        {
                            "field": "source_title",
                            "label": {"ru": "Позиция заказчика", "en": "Request"},
                            "max_lines": 2,
                            "max_width": 180,
                        },
                        {
                            "field": "title",
                            "label": {"ru": "Товар", "en": "Product"},
                            "max_lines": 3,
                            "max_width": 220,
                        },
                        {
                            "field": "part_number",
                            "label": {"ru": "Партномер", "en": "P/N"},
                            "max_lines": 2,
                            "max_width": 110,
                        },
                        {
                            "field": "price",
                            "label": {"ru": "Цена ₽", "en": "Price RUB"},
                            "format": "offer_price",
                            "align": "end",
                            "max_width": 110,
                        },
                        {
                            "field": "benefit_label",
                            "label": {"ru": "Выгода", "en": "Benefit"},
                            "format": "benefit_static",
                            "align": "center",
                            "max_width": 130,
                        },
                        {
                            "field": "alternatives_count",
                            "label": {"ru": "Альтернативы", "en": "Alternatives"},
                            "align": "end",
                            "max_width": 100,
                        },
                        {
                            "field": "match_label",
                            "label": {"ru": "Совпадение", "en": "Match"},
                            "max_width": 150,
                        },
                    ],
                    # чекбокс: один выбранный товар на позицию заказчика
                    # (сервер снимает выбор у других поставщиков)
                    "row_style": [
                        {
                            "when": {"field": "is_stale", "eq": True},
                            "accent": "warning",
                        },
                        # эффективный выбор позиции (selected ?? best) — зелёный
                        # сразу, без клика: это связанное состояние из других
                        # таблиц (бюджет/группы), а не локальный UI-стейт
                        {
                            "when": {"field": "is_effective", "eq": True},
                            "accent": "success",
                        },
                        {
                            "when": {"field": "priority", "eq": True},
                            "accent": "success",
                        },
                    ],
                    "context_bind": {"seller": {"field": "seller"}},
                    "selection": {
                        "kind": "single",
                        "field": "is_selected",
                        "action": "select_offer_primary",
                        "control": "checkbox",
                        "placement": "trailing",
                    },
                    # тап по строке = выбрать/снять товар для позиции
                    # (select_offer_primary тогглит, пайплайн пересчитывает
                    # бюджет/закупку/лица групп — связь с другими таблицами)
                    "row_tap": {"kind": "invoke_action", "action": "select_offer_primary"},
                    "empty": _empty("Нет товаров", "No offers", icon="shopping_cart"),
                },
            },
            {
                "slug": "equipment_types_list",
                "table_slug": "equipment_types",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {
                            "ru": "Типы комплектующих",
                            "en": "Component types",
                        }
                    },
                    "title_field": "name",
                    "subtitle_fields": [],
                    "columns": [
                        {
                            "field": "name",
                            "label": {"ru": "Название", "en": "Name"},
                        },
                    ],
                    "row_tap": {
                        "kind": "open_view",
                        "view": "equipment_type_settings",
                    },
                    "inline_add": {
                        "field": "name",
                        "title": "Добавить тип",
                    },
                    "empty": _empty(
                        "Нет типов",
                        "No types",
                        icon="category",
                    ),
                },
            },
            {
                "slug": "equipment_types_pick",
                "table_slug": "equipment_types",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {
                            "ru": "Выбор типа",
                            "en": "Select type",
                        }
                    },
                    "title_field": "name",
                    "subtitle_fields": [],
                    "columns": [
                        {
                            "field": "name",
                            "label": {"ru": "Название", "en": "Name"},
                        }
                    ],
                    "selection": {
                        "kind": "single",
                        "control": "switch",
                        "placement": "trailing",
                        "disable_row_tap": True,
                        "match_context_field": "type_id",
                            "set_on_context": {
                            "field": "type_id",
                            "value_from": "row_id",
                            "also_copy": [{"from": "name", "to": "type_name"}],
                            # смена типа сбрасывает характеристики карточки,
                            # требования слота и закреплённую карточку
                            "clear_fields": ["attrs", "constraints", "card_id"],
                            "pop_after": True,
                        },
                    },
                    "inline_add": {
                        "field": "name",
                        "title": "Добавить тип",
                    },
                    "empty": _empty(
                        "Нет типов",
                        "No types",
                        icon="category",
                    ),
                },
            },
            {
                "slug": "request_lines_pick",
                "table_slug": "request_lines",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {
                            "ru": "Выбор позиции заказчика",
                            "en": "Select request line",
                        }
                    },
                    "title_field": "title",
                    "subtitle_fields": ["part_number", "qty"],
                    "columns": [
                        {
                            "field": "title",
                            "label": {"ru": "Позиция", "en": "Line"},
                        },
                        {
                            "field": "part_number",
                            "label": {"ru": "Партномер", "en": "Part number"},
                        },
                    ],
                    "selection": {
                        "kind": "single",
                        "control": "switch",
                        "placement": "trailing",
                        "disable_row_tap": True,
                        "match_context_field": "line_id",
                        "set_on_context": {
                            "field": "line_id",
                            "value_from": "row_id",
                            "also_copy": [{"from": "title", "to": "source_title"}],
                            "pop_after": True,
                        },
                    },
                    "empty": _empty(
                        "Нет позиций",
                        "No request lines",
                        icon="list_alt",
                    ),
                },
            },
            {
                "slug": "found_offers_pick",
                "table_slug": "found_offers",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {
                            "ru": "Выбор товара",
                            "en": "Select offer",
                        }
                    },
                    "title_field": "title",
                    "subtitle_fields": ["part_number", "price"],
                    "columns": [
                        {
                            "field": "title",
                            "label": {"ru": "Товар", "en": "Title"},
                        },
                        {
                            "field": "price",
                            "label": {"ru": "Цена", "en": "Price"},
                        },
                    ],
                    "selection": {
                        "kind": "single",
                        "control": "switch",
                        "placement": "trailing",
                        "disable_row_tap": True,
                        "match_context_field": "offer_id",
                        "set_on_context": {
                            "field": "offer_id",
                            "value_from": "row_id",
                            "also_copy": [{"from": "title", "to": "offer_title"}],
                            "pop_after": True,
                        },
                    },
                    "empty": _empty(
                        "Нет товаров",
                        "No offers",
                        icon="inventory_2",
                    ),
                },
            },
            {
                "slug": "equipment_type_settings",
                "table_slug": "equipment_types",
                "kind": "detail",
                "ui_json": {
                    "version": 1,
                    "kind": "detail",
                    "mode": "edit",
                    "title": {
                        "ru": "Тип комплектующего",
                        "en": "Component type",
                    },
                    "fields": [
                        {
                            "column": "name",
                            "widget": "value",
                            "icon": "category",
                        },
                        {
                            "column": "build_scope",
                            "widget": "choice",
                            "icon": "precision_manufacturing",
                        },
                        {
                            "column": "sort_order",
                            "widget": "value",
                            "icon": "sort",
                        },
                        {
                            "column": "fields_json",
                            "widget": "fields_schema_editor",
                            "section_title": {
                                "ru": "Характеристики",
                                "en": "Characteristics",
                            },
                        },
                    ],
                    # карточки этого типа (партномера + заполненные характеристики)
                    "links": [
                        {
                            "label": {
                                "ru": "Карточки типа",
                                "en": "Type cards",
                            },
                            "icon": "inventory_2",
                            "open_view": "part_cards_list",
                        },
                    ],
                },
            },
            {
                "slug": "equipment_builds_list",
                "table_slug": "equipment_builds",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {"ru": "Сборка", "en": "Builds"},
                    },
                    "title_field": "name",
                    "subtitle_fields": ["ready_build_name", "components_count", "price_total"],
                    # Открыто из позиции (toolbar «Сборки») → только сборки этой
                    # позиции; иначе виден весь список.
                    "row_filter_from_context": {"line_id": "contextRowId"},
                    # создание из позиции сразу привязывает сборку к ней
                    # (готовую сборку выбирают в карточке через ready_builds_pick)
                    "context_bind": {"line_id": "contextRowId"},
                    "columns": [
                        {
                            "field": "name",
                            "label": {"ru": "Название", "en": "Name"},
                            "max_lines": 2,
                            "max_width": 240,
                        },
                        {
                            "field": "ready_build_name",
                            "label": {"ru": "Готовая сборка", "en": "Ready build"},
                            "max_lines": 2,
                            "max_width": 200,
                        },
                        {
                            "field": "build_kind",
                            "label": {"ru": "Тип", "en": "Kind"},
                            "max_width": 90,
                        },
                        {
                            "field": "match_label",
                            "label": {"ru": "Совпадение", "en": "Match"},
                            "max_width": 150,
                        },
                        {
                            "field": "components_count",
                            "label": {"ru": "Комплектующих", "en": "Components"},
                            "align": "end",
                            "max_width": 120,
                        },
                        {
                            "field": "price_total",
                            "label": {"ru": "Цена ₽", "en": "Price RUB"},
                            "format": "offer_price",
                            "align": "end",
                            "max_width": 120,
                        },
                        {
                            "field": "alternatives_count",
                            "label": {"ru": "Альтернативы", "en": "Alternatives"},
                            "align": "end",
                            "max_width": 110,
                        },
                        {
                            "field": "benefit",
                            "label": {"ru": "Выгода", "en": "Benefit"},
                            "format": "benefit",
                            "price_field": "price_total",
                            "current_field": "is_selected",
                            "group_field": "line_id",
                            "align": "center",
                            "max_width": 130,
                        },
                    ],
                    "sort": [
                        {"field": "is_best", "dir": "desc"},
                        {"field": "price_total", "dir": "asc"},
                    ],
                    "row_tap": {
                        "kind": "open_view",
                        "view": "equipment_build_settings",
                    },
                    "inline_add": {
                        "field": "name",
                        "title": "Добавить сборку",
                    },
                    "empty": _empty(
                        "Нет сборок",
                        "No builds",
                        icon="precision_manufacturing",
                    ),
                },
            },
            # WAVE12: сборка чата — глобальная ссылка на готовую сборку
            # каталога. Состав и цены зеркалятся пайплайном; локальных слотов
            # нет — правка состава идёт в «Готовых сборках» (или новой сборкой).
            {
                "slug": "equipment_build_settings",
                "table_slug": "equipment_builds",
                "kind": "detail",
                "ui_json": {
                    "version": 1,
                    "kind": "detail",
                    "mode": "edit",
                    "title": {"ru": "Сборка", "en": "Build"},
                    "fields": [
                        {
                            "column": "name",
                            "widget": "value",
                            "icon": "label_outline",
                        },
                        {
                            "column": "ready_build_id",
                            "widget": "pick",
                            "icon": "precision_manufacturing",
                            "pick_view": "ready_builds_pick",
                            # показываем имя готовой сборки (денормализация
                            # зеркала), а не только row_id привязки
                            "title_field": "ready_build_name",
                            "empty_style": "warning",
                            "empty_label": {
                                "ru": "Не выбрана",
                                "en": "Not selected",
                            },
                        },
                        {
                            "column": "line_id",
                            "widget": "ref",
                            "icon": "list_alt",
                        },
                        {
                            # сколько таких сборок заказано — масштабирует все
                            # компоненты (2 плашки × 2 сборки = 4 планки ОЗУ)
                            "column": "qty",
                            "widget": "value",
                            "icon": "numbers",
                        },
                        {
                            "column": "build_kind",
                            "widget": "choice",
                            "icon": "precision_manufacturing",
                            "read_only": True,
                        },
                        {
                            "column": "match_label",
                            "widget": "value",
                            "read_only": True,
                            "icon": "fact_check",
                        },
                        {
                            "column": "components_count",
                            "widget": "value",
                            "read_only": True,
                            "icon": "numbers",
                        },
                        {
                            "column": "price_total",
                            "widget": "value",
                            "read_only": True,
                            "icon": "payments",
                        },
                        {
                            "column": "note",
                            "widget": "value",
                            "icon": "edit_note",
                        },
                    ],
                    # переход в каталог — к самой готовой сборке (её слоты,
                    # требования, выбранные компоненты), а не к строке ссылки
                    "links": [
                        {
                            "label": {
                                "ru": "Открыть готовую сборку",
                                "en": "Open ready build",
                            },
                            "icon": "precision_manufacturing",
                            "open_view": "ready_build_settings",
                            "row_id_from_field": "ready_build_id",
                        },
                    ],
                },
            },
            # WAVE12: «Карточки комплектующих» — партномер + тип + характеристики
            # из групп типа. Лидеры цен (best_*) резолвит пайплайн каталога.
            {
                "slug": "part_cards_list",
                "table_slug": "part_cards",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {
                            "ru": "Карточки комплектующих",
                            "en": "Component cards",
                        },
                        "actions": [
                            {
                                "kind": "invoke_action",
                                "action": "ready_builds_sync",
                                "icon": "refresh",
                                "label": {
                                    "ru": "Обновить цены",
                                    "en": "Refresh prices",
                                },
                            },
                        ],
                    },
                    "title_field": "part_number",
                    "subtitle_fields": ["type_name", "attrs_label"],
                    # открыто из типа → только его карточки; иначе весь список
                    "row_filter_from_context": {"type_id": "contextRowId"},
                    "columns": [
                        {
                            "field": "type_name",
                            "label": {"ru": "Тип", "en": "Type"},
                            "max_width": 140,
                        },
                        {
                            "field": "part_number",
                            "label": {"ru": "Партномер", "en": "P/N"},
                            "max_lines": 2,
                            "max_width": 170,
                        },
                        {
                            "field": "attrs_label",
                            "label": {"ru": "Характеристики", "en": "Attributes"},
                            "max_lines": 2,
                            "max_width": 200,
                        },
                        {
                            "field": "best_title",
                            "label": {"ru": "Товар", "en": "Product"},
                            "max_lines": 2,
                            "max_width": 220,
                        },
                        {
                            "field": "best_price",
                            "label": {"ru": "Цена ₽", "en": "Price"},
                            "format": "offer_price",
                            "align": "end",
                            "max_width": 110,
                        },
                        {
                            "field": "best_seller",
                            "label": {"ru": "Поставщик", "en": "Supplier"},
                            "max_lines": 2,
                            "max_width": 140,
                        },
                        {
                            "field": "offers_count",
                            "label": {"ru": "Офферов", "en": "Offers"},
                            "align": "end",
                            "max_width": 90,
                        },
                        {
                            "field": "in_stock",
                            "label": {"ru": "Наличие", "en": "Stock"},
                            "format": "bool_yes_no",
                            "max_width": 90,
                        },
                    ],
                    "sort": [
                        {"field": "type_name", "dir": "asc"},
                        {"field": "best_price", "dir": "asc"},
                    ],
                    "row_tap": {"kind": "open_view", "view": "part_card_settings"},
                    "inline_add": {
                        "field": "part_number",
                        "title": "Добавить карточку",
                    },
                    "empty": _empty(
                        "Нет карточек комплектующих",
                        "No component cards",
                        icon="inventory_2",
                    ),
                },
            },
            {
                "slug": "part_card_settings",
                "table_slug": "part_cards",
                "kind": "detail",
                "ui_json": {
                    "version": 1,
                    "kind": "detail",
                    "mode": "edit",
                    "title": {"ru": "Карточка комплектующего", "en": "Component card"},
                    "fields": [
                        {
                            "column": "type_id",
                            "widget": "type_ref_picker",
                            "pick_view": "equipment_types_pick",
                            "empty_style": "warning",
                            "empty_label": {
                                "ru": "Не выбран",
                                "en": "Not selected",
                            },
                        },
                        {
                            "column": "part_number",
                            "widget": "value",
                            "icon": "qr_code_2",
                        },
                        {
                            "column": "aliases_pn",
                            "widget": "value",
                            "icon": "alt_route",
                        },
                        {
                            "column": "aliases_hash",
                            "widget": "value",
                            "icon": "fingerprint",
                        },
                        {
                            # значения — из групп типа (equipment_types.fields_json);
                            # обязательные поля типа нужны матрице совместимости
                            "column": "attrs",
                            "widget": "constraint_attrs",
                            "type_id_field": "type_id",
                            "types_table": "equipment_types",
                            "fields_from": "fields_json",
                            "section_title": {
                                "ru": "Характеристики",
                                "en": "Attributes",
                            },
                            "visible_when": {
                                "field": "type_id",
                                "not_empty": True,
                            },
                        },
                        {
                            "column": "best_title",
                            "widget": "value",
                            "read_only": True,
                        },
                        {
                            "column": "best_price",
                            "widget": "value",
                            "read_only": True,
                        },
                        {
                            "column": "best_seller",
                            "widget": "value",
                            "read_only": True,
                        },
                        {
                            "column": "offers_count",
                            "widget": "value",
                            "read_only": True,
                        },
                        {"column": "note", "widget": "value", "icon": "edit_note"},
                    ],
                },
            },
            # Выбор карточки в слот готовой сборки: альтернативы ЭТОГО типа,
            # удовлетворяющие требованиям слота (constraints), с лидерами цен
            # по каждому партномеру — как «Найденные товары».
            {
                "slug": "part_cards_pick",
                "table_slug": "part_cards",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {"ru": "Заменить компонент", "en": "Replace component"},
                    },
                    "title_field": "best_title",
                    "subtitle_fields": ["part_number", "attrs_label"],
                    "row_filter_from_context": {"type_id": "type_id"},
                    # строка подходит, если её attrs покрывают constraints
                    # контекстной строки (слота): attrs[k] == v для всех k,v
                    "row_matches_context_map": {
                        "field": "attrs",
                        "context_field": "constraints",
                    },
                    "columns": [
                        {
                            "field": "best_title",
                            "label": {"ru": "Товар", "en": "Product"},
                            "max_lines": 2,
                            "max_width": 240,
                        },
                        {
                            "field": "part_number",
                            "label": {"ru": "Партномер", "en": "P/N"},
                            "max_lines": 2,
                            "max_width": 150,
                            "format": "pn_fallback",
                        },
                        {
                            "field": "attrs_label",
                            "label": {"ru": "Характеристики", "en": "Attributes"},
                            "max_lines": 2,
                            "max_width": 180,
                        },
                        {
                            "field": "best_price",
                            "label": {"ru": "Цена ₽", "en": "Price"},
                            "format": "offer_price",
                            "align": "end",
                            "max_width": 110,
                        },
                        {
                            "field": "best_seller",
                            "label": {"ru": "Поставщик", "en": "Supplier"},
                            "max_lines": 2,
                            "max_width": 140,
                        },
                        {
                            "field": "in_stock",
                            "label": {"ru": "Наличие", "en": "Stock"},
                            "format": "bool_yes_no",
                            "max_width": 90,
                        },
                        {
                            "field": "offers_count",
                            "label": {"ru": "Офферов", "en": "Offers"},
                            "align": "end",
                            "max_width": 90,
                        },
                    ],
                    "sort": [{"field": "best_price", "dir": "asc"}],
                    "selection": {
                        "kind": "single",
                        "control": "switch",
                        "placement": "trailing",
                        # текущий выбор — card_id открытого слота
                        "match_context_field": "card_id",
                        "set_on_context": {
                            "field": "card_id",
                            "value_from": "row_id",
                            "pop_after": True,
                        },
                    },
                    "empty": _empty(
                        "Нет карточек по требованиям слота",
                        "No matching cards",
                        icon="inventory_2",
                    ),
                },
            },
            # Выбор готовой сборки для связи «Сборка» ↔ каталог (чат/заказ).
            {
                "slug": "ready_builds_pick",
                "table_slug": "ready_builds",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {"ru": "Выбор готовой сборки", "en": "Select ready build"},
                    },
                    "title_field": "name",
                    "subtitle_fields": ["spec_summary", "price_total"],
                    "row_filter": {"is_enabled": True},
                    "columns": [
                        {
                            "field": "name",
                            "label": {"ru": "Сборка", "en": "Build"},
                            "max_lines": 2,
                            "max_width": 220,
                        },
                        {
                            "field": "spec_summary",
                            "label": {"ru": "Состав", "en": "Spec"},
                            "max_lines": 2,
                            "max_width": 300,
                        },
                        {
                            "field": "price_total",
                            "label": {"ru": "Цена ₽", "en": "Price"},
                            "format": "offer_price",
                            "align": "end",
                            "max_width": 110,
                        },
                    ],
                    "sort": [{"field": "price_total", "dir": "asc"}],
                    "selection": {
                        "kind": "single",
                        "control": "switch",
                        "placement": "trailing",
                        "disable_row_tap": True,
                        "match_context_field": "ready_build_id",
                        "set_on_context": {
                            "field": "ready_build_id",
                            "value_from": "row_id",
                            "also_copy": [
                                {"from": "name", "to": "name"},
                                {"from": "name", "to": "ready_build_name"},
                                {"from": "build_kind", "to": "build_kind"},
                            ],
                            "pop_after": True,
                        },
                    },
                    "empty": _empty(
                        "Нет готовых сборок",
                        "No ready builds",
                        icon="precision_manufacturing",
                    ),
                },
            },
            {
                "slug": "ready_builds_list",
                "table_slug": "ready_builds",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {"ru": "Готовые сборки", "en": "Ready builds"},
                        "actions": [
                            {
                                "kind": "invoke_action",
                                "action": "ready_builds_sync",
                                "icon": "refresh",
                                "label": {
                                    "ru": "Обновить цены",
                                    "en": "Refresh prices",
                                },
                            },
                        ],
                    },
                    "title_field": "name",
                    "subtitle_fields": ["spec_summary", "price_total"],
                    "columns": [
                        {
                            "field": "name",
                            "label": {"ru": "Сборка", "en": "Build"},
                            "max_lines": 2,
                            "max_width": 240,
                        },
                        {
                            "field": "spec_summary",
                            "label": {"ru": "Состав", "en": "Spec"},
                            "max_lines": 2,
                            "max_width": 320,
                        },
                        {
                            "field": "slots_count",
                            "label": {"ru": "Типов компл.", "en": "Kinds"},
                            "align": "end",
                            "max_width": 80,
                        },
                        {
                            "field": "qty_total",
                            "label": {"ru": "Компл. всего", "en": "Units"},
                            "align": "end",
                            "max_width": 80,
                        },
                        {
                            "field": "price_total",
                            "label": {"ru": "Цена ₽", "en": "Price"},
                            "format": "offer_price",
                            "align": "end",
                            "max_width": 110,
                        },
                        {
                            "field": "price_min",
                            "label": {"ru": "Мин. ₽", "en": "Min"},
                            "format": "offer_price",
                            "align": "end",
                            "max_width": 100,
                        },
                        {
                            "field": "price_max",
                            "label": {"ru": "Макс. ₽", "en": "Max"},
                            "format": "offer_price",
                            "align": "end",
                            "max_width": 100,
                        },
                        {
                            "field": "on_order",
                            "label": {"ru": "Под заказ", "en": "On order"},
                            "format": "bool_yes_no",
                            "max_width": 100,
                        },
                        {
                            "field": "compat_ok",
                            "label": {"ru": "Совместима", "en": "Compatible"},
                            "format": "bool_yes_no",
                            "max_width": 110,
                        },
                        {
                            "field": "unresolved_count",
                            "label": {"ru": "Без компонента", "en": "Unresolved"},
                            "align": "end",
                            "max_width": 110,
                        },
                    ],
                    "sort": [
                        {"field": "name", "dir": "asc"},
                    ],
                    "row_tap": {"kind": "open_view", "view": "ready_build_settings"},
                    "inline_add": {"field": "name", "title": "Добавить сборку"},
                    "empty": _empty(
                        "Нет готовых сборок",
                        "No ready builds",
                        icon="precision_manufacturing",
                    ),
                },
            },
            {
                "slug": "ready_build_settings",
                "table_slug": "ready_builds",
                "kind": "detail",
                "ui_json": {
                    "version": 1,
                    "kind": "detail",
                    "mode": "edit",
                    "title": {"ru": "Готовая сборка", "en": "Ready build"},
                    "fields": [
                        {"column": "name", "widget": "value", "icon": "label_outline"},
                        # замечания/комментарий — сразу после названия и только
                        # когда непустые (warning-тон)
                        {
                            "column": "compat_issues",
                            "widget": "value",
                            "read_only": True,
                            "accent": "warning",
                            "visible_when": {"field": "compat_issues", "not_empty": True},
                        },
                        {
                            "column": "note",
                            "widget": "value",
                            "icon": "edit_note",
                            "accent": "warning",
                            "visible_when": {"field": "note", "not_empty": True},
                        },
                        {
                            "column": "build_kind",
                            "widget": "choice",
                            "icon": "precision_manufacturing",
                        },
                        # целевой коридор цены: два ввода + мин/макс комбинации
                        # зелёным справа в спокойном состоянии
                        {
                            "column": "target_price_min",
                            "widget": "target_price_range",
                            "icon": "savings",
                        },
                        {
                            "column": "price_total",
                            "widget": "value",
                            "read_only": True,
                            "icon": "payments",
                        },
                        # слоты — таблицей прямо на странице сборки
                        {
                            "column": "spec_summary",
                            "widget": "ready_build_slots_table",
                            "slots_table": "ready_build_slots",
                            "cards_table": "part_cards",
                            "pick_view": "part_cards_pick",
                        },
                    ],
                    # «Состав» — кликабельная ссылка сразу после названия,
                    # ведёт на подстраницу с редактируемыми характеристиками
                    "links": [
                        {
                            "label": {"ru": "Состав", "en": "Spec"},
                            "icon": "tune",
                            "open_view": "ready_build_spec",
                            "after_field": "name",
                            "text_from_field": "spec_summary",
                        },
                    ],
                },
            },
            # «Состав» сборки: контракт характеристик по слотам. Каждое поле —
            # выбор из групп типа; изменение меняет constraints слотов и,
            # значит, подбор карточек. Пишет строки ready_build_slots.
            {
                "slug": "ready_build_spec",
                "table_slug": "ready_builds",
                "kind": "detail",
                "ui_json": {
                    "version": 1,
                    "kind": "detail",
                    "mode": "edit",
                    "title": {"ru": "Состав сборки", "en": "Build spec"},
                    "fields": [
                        {
                            "column": "name",
                            "widget": "build_spec_editor",
                            "slots_table": "ready_build_slots",
                            "types_table": "equipment_types",
                        },
                    ],
                },
            },
            {
                "slug": "ready_build_slots_list",
                "table_slug": "ready_build_slots",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {"ru": "Слоты сборки", "en": "Build slots"},
                    },
                    "title_field": "type_name",
                    "subtitle_fields": ["constraints_label", "resolved_part_number"],
                    "context_bind": {"build_id": "contextRowId"},
                    # список слотов открывается из сборки — только её строки
                    "row_filter_from_context": {"build_id": "contextRowId"},
                    "columns": [
                        {
                            "field": "type_name",
                            "label": {"ru": "Тип", "en": "Type"},
                            "max_width": 150,
                        },
                        {
                            "field": "constraints_label",
                            "label": {"ru": "Требования", "en": "Constraints"},
                            "max_lines": 2,
                            "max_width": 170,
                        },
                        {
                            "field": "qty",
                            "label": {"ru": "Кол-во", "en": "Qty"},
                            "align": "end",
                            "max_width": 85,
                        },
                        {
                            "field": "resolved_title",
                            "label": {"ru": "Компонент", "en": "Component"},
                            "max_lines": 2,
                            "max_width": 220,
                        },
                        {
                            "field": "resolved_price",
                            "label": {"ru": "Цена ₽", "en": "Price"},
                            "format": "offer_price",
                            "align": "end",
                            "max_width": 110,
                        },
                        {
                            "field": "line_total",
                            "label": {"ru": "Сумма ₽", "en": "Total"},
                            "format": "offer_price",
                            "align": "end",
                            "max_width": 110,
                        },
                        {
                            "field": "alternatives_count",
                            "label": {"ru": "Альтернатив", "en": "Alternatives"},
                            "align": "end",
                            "max_width": 110,
                        },
                    ],
                    "sort": [{"field": "type_name", "dir": "asc"}],
                    "row_tap": {"kind": "open_form", "view": "ready_build_slot_settings"},
                    "inline_add": {"field": "type_name", "title": "Добавить слот"},
                    "empty": _empty("Нет слотов", "No slots", icon="tune"),
                },
            },
            {
                "slug": "ready_build_slot_settings",
                "table_slug": "ready_build_slots",
                "kind": "detail",
                "ui_json": {
                    "version": 1,
                    "kind": "detail",
                    "mode": "edit",
                    "title": {"ru": "Слот сборки", "en": "Build slot"},
                    "fields": [
                        {
                            "column": "type_id",
                            "widget": "type_ref_picker",
                            "pick_view": "equipment_types_pick",
                            "empty_style": "warning",
                            "empty_label": {
                                "ru": "Не выбран",
                                "en": "Not selected",
                            },
                        },
                        {
                            # требования слота — значения из групп типа
                            # (SSD 512 ГБ, сокет LGA1700…)
                            "column": "constraints",
                            "widget": "constraint_attrs",
                            "type_id_field": "type_id",
                            "types_table": "equipment_types",
                            "fields_from": "fields_json",
                            "section_title": {
                                "ru": "Требования",
                                "en": "Constraints",
                            },
                            "visible_when": {
                                "field": "type_id",
                                "not_empty": True,
                            },
                        },
                        {"column": "qty", "widget": "value", "icon": "numbers"},
                        {
                            "column": "card_id",
                            "widget": "pick",
                            "icon": "inventory_2",
                            # ручная замена: карточки типа по требованиям слота
                            "pick_view": "part_cards_pick",
                            "title_field": "resolved_title",
                        },
                        {"column": "resolved_title", "widget": "value", "read_only": True},
                        {"column": "resolved_price", "widget": "value", "read_only": True},
                        {"column": "line_total", "widget": "value", "read_only": True},
                        {"column": "resolved_seller", "widget": "value", "read_only": True},
                        {"column": "note", "widget": "value", "icon": "edit_note"},
                    ],
                },
            },
            {
                "slug": "trusted_sellers_list",
                "table_slug": "trusted_sellers",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {
                            "ru": "Поставщики",
                            "en": "Suppliers",
                        }
                    },
                    "title_field": "name",
                    "subtitle_fields": ["aliases", "comment"],
                    "columns": [
                        {
                            "field": "name",
                            "label": {"ru": "Компания", "en": "Company"},
                            "max_lines": 2,
                            "max_width": 260,
                        },
                        {
                            "field": "is_verified",
                            "label": {"ru": "Проверен", "en": "Verified"},
                            "format": "bool_yes_no",
                            "max_width": 90,
                        },
                        {
                            "field": "payment_deferral",
                            "label": {"ru": "Отсрочка", "en": "Deferral"},
                            "format": "bool_yes_no",
                            "max_width": 90,
                        },
                        {
                            "field": "email",
                            "label": {"ru": "Email", "en": "Email"},
                            "max_lines": 2,
                            "max_width": 180,
                        },
                        {
                            "field": "comment",
                            "label": {"ru": "Комментарий", "en": "Comment"},
                            "max_lines": 3,
                            "max_width": 260,
                        },
                                            {
                            "field": "master_price",
                            "label": {"ru": "Мастер прайс", "en": "Master price"},
                            "format": "bool_yes_no",
                            "max_width": 110,
                        },
                    ],
                    # Disabled suppliers stay visible but highlighted (warning).
                    "row_style": [
                        {
                            "when": {"field": "is_enabled", "eq": False},
                            "accent": "warning",
                        },
                    ],
                    "row_tap": {
                        "kind": "open_view",
                        "view": "trusted_sellers_settings",
                    },
                    "inline_add": {
                        "field": "name",
                        "title": "Добавить поставщика",
                    },
                    "empty": _empty(
                        "Нет поставщиков",
                        "No suppliers",
                        icon="local_shipping",
                    ),
                },
            },
            {
                "slug": "trusted_sellers_settings",
                "table_slug": "trusted_sellers",
                "kind": "detail",
                "ui_json": {
                    "version": 1,
                    "kind": "detail",
                    "mode": "edit",
                    "title": {
                        "ru": "Поставщик",
                        "en": "Supplier",
                    },
                    "fields": [
                        {
                            "column": "name",
                            "widget": "value",
                            "icon": "storefront",
                        },
                        {
                            "column": "aliases",
                            "widget": "value",
                            "icon": "alternate_email",
                        },
                        {
                            "column": "is_enabled",
                            "widget": "switch",
                            "icon": "toggle_on",
                        },
                        {
                            "column": "master_price",
                            "widget": "switch",
                            "icon": "inventory_2",
                        },
                        {
                            "column": "is_verified",
                            "widget": "switch",
                            "icon": "verified",
                        },
                        {
                            "column": "payment_deferral",
                            "widget": "switch",
                            "icon": "schedule",
                        },
                        {
                            "column": "priority_purchase",
                            "widget": "switch",
                            "icon": "star",
                        },
                        {
                            "column": "margin_pct",
                            "widget": "number",
                            "icon": "percent",
                        },
                        {
                            "column": "delivery_rub",
                            "widget": "number",
                            "icon": "local_shipping",
                        },
                        {
                            "column": "email",
                            "widget": "text",
                            "icon": "email",
                        },
                        {
                            "column": "phone",
                            "widget": "text",
                            "icon": "phone",
                        },
                        {
                            "column": "comment",
                            "widget": "text",
                            "icon": "notes",
                        },
                        {
                            "column": "inn",
                            "widget": "text",
                            "icon": "badge",
                        },
                        {
                            "column": "kpp",
                            "widget": "text",
                            "icon": "badge",
                        },
                        {
                            "column": "legal_address",
                            "widget": "text",
                            "icon": "location_on",
                        },
                        {
                            "column": "bank_name",
                            "widget": "text",
                            "icon": "account_balance",
                        },
                        {
                            "column": "bik",
                            "widget": "text",
                            "icon": "tag",
                        },
                        {
                            "column": "bank_account",
                            "widget": "text",
                            "icon": "credit_card",
                        },
                    ],
                },
            },
            {
                "slug": "web_shops_list",
                "table_slug": "web_shops",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {
                            "ru": "Интернет магазины",
                            "en": "Web shops",
                        }
                    },
                    "title_field": "name",
                    "subtitle_fields": ["url"],
                    "columns": [
                        {
                            "field": "name",
                            "label": {"ru": "Название", "en": "Name"},
                        },
                        {
                            "field": "url",
                            "label": {"ru": "Ссылка", "en": "URL"},
                        },
                    ],
                    "row_tap": {
                        "kind": "open_view",
                        "view": "web_shops_settings",
                    },
                    "inline_add": {
                        "field": "name",
                        "title": "Добавить магазин",
                    },
                    "empty": _empty(
                        "Нет магазинов",
                        "No shops",
                        icon="language",
                    ),
                },
            },
            {
                "slug": "web_shops_settings",
                "table_slug": "web_shops",
                "kind": "detail",
                "ui_json": {
                    "version": 1,
                    "kind": "detail",
                    "mode": "edit",
                    "title": {
                        "ru": "Магазин",
                        "en": "Shop",
                    },
                    "fields": [
                        {
                            "column": "name",
                            "widget": "value",
                            "icon": "language",
                        },
                        {
                            "column": "url",
                            "widget": "value",
                            "icon": "link",
                        },
                        {
                            "column": "cookies",
                            "widget": "text_editor",
                            "icon": "cookie",
                        },
                    ],
                },
            },
        {
                "slug": "equipment_prompts_list",
                "table_slug": "equipment_prompts",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {"title": {"ru": "Промпты подбора", "en": "Selection prompts"}},
                    "title_field": "name",
                    "subtitle_fields": ["path"],
                    "columns": [
                        {"field": "name", "label": {"ru": "Название", "en": "Name"}},
                        {"field": "path", "label": {"ru": "Путь", "en": "Path"}},
                        {"field": "files_json", "label": {"ru": "Промптов", "en": "Prompts"}, "format": "list_count"},
                        {"field": "enabled", "label": {"ru": "Включён", "en": "Enabled"}, "format": "bool_yes_no"},
                    ],
                    "row_tap": {"kind": "open_form", "view": "equipment_prompt_settings"},
                    "inline_add": {"field": "name", "title": "Добавить промпт"},
                    "empty": _empty("Нет промптов", "No prompts", icon="psychology_outlined"),
                },
            },
            {
                "slug": "equipment_prompt_settings",
                "table_slug": "equipment_prompts",
                "kind": "detail",
                "ui_json": {
                    "version": 1,
                    "kind": "detail",
                    "mode": "edit",
                    "title": {"ru": "Промпт подбора", "en": "Selection prompt"},
                    "fields": [
                        {"column": "name", "widget": "value", "icon": "label_outline"},
                        {"column": "path", "widget": "value", "icon": "folder_outlined"},
                        {"column": "enabled", "widget": "switch", "icon": "toggle_on"},
                        {"column": "files_json", "widget": "prompt_files_editor"},
                    ],
                },
            },
            {
                "slug": "equipment_search_list",
                "table_slug": "equipment_search",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {"ru": "Поиск товаров", "en": "Product search"}
                    },
                    "server_paged": True,
                    # тумблер «только в наличии» в шапке
                    "stock_filter": True,
                    "search": {
                        "fields": ["title", "part_number", "brand", "supplier"]
                    },
                    "title_field": "title",
                    "subtitle_fields": ["supplier", "part_number"],
                    "columns": [
                        {
                            "field": "brand",
                            "label": {"ru": "Бренд", "en": "Brand"},
                            "max_width": 110,
                        },
                        {
                            "field": "part_number",
                            "label": {"ru": "PN", "en": "PN"},
                            "max_lines": 2,
                            "max_width": 110,
                        },
                        {
                            "field": "title",
                            "label": {"ru": "Наименование", "en": "Title"},
                            "max_lines": 2,
                            "max_width": 320,
                        },
                        {
                            "field": "in_stock",
                            "label": {"ru": "Наличие", "en": "Stock"},
                            "format": "stock_label",
                            "max_width": 110,
                        },
                        {
                            "field": "price",
                            "label": {"ru": "Цена", "en": "Price"},
                            "format": "offer_price",
                            "align": "end",
                            "max_width": 110,
                        },
                        {
                            "field": "currency",
                            "label": {"ru": "Валюта", "en": "Currency"},
                            "max_width": 80,
                        },
                    ],
                    # тап по товару → список позиций заказчика для сопоставления
                    "row_tap": {"kind": "match_product", "action": "equipment_match_to_line"},
                    "empty": _empty(
                        "Ничего не найдено",
                        "Nothing found",
                        icon="search",
                    ),
                },
            },
            {
                "slug": "master_price_list",
                "table_slug": "master_price",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {"ru": "Мастер прайс", "en": "Master price"},
                        "actions": [
                            {
                                "kind": "invoke_action",
                                "action": "master_price_export",
                                "icon": "download",
                                "label": {
                                    "ru": "Скачать мастер-прайс",
                                    "en": "Download master price",
                                },
                            }
                        ],
                    },
                    "server_paged": True,
                    "search": {
                        "fields": ["title", "part_number", "brand", "supplier"]
                    },
                    "title_field": "title",
                    "subtitle_fields": ["supplier", "part_number"],
                    "columns": [
                        {
                            "field": "supplier",
                            "label": {"ru": "Поставщик", "en": "Supplier"},
                            "max_lines": 2,
                            "max_width": 150,
                        },
                        {
                            "field": "brand",
                            "label": {"ru": "Бренд", "en": "Brand"},
                            "max_width": 110,
                        },
                        {
                            "field": "part_number",
                            "label": {"ru": "PN", "en": "PN"},
                            "max_lines": 2,
                            "max_width": 110,
                        },
                        {
                            "field": "title",
                            "label": {"ru": "Наименование", "en": "Title"},
                            "max_lines": 2,
                            "max_width": 320,
                        },
                        {
                            "field": "in_stock",
                            "label": {"ru": "Наличие", "en": "Stock"},
                            "format": "bool_yes_no",
                            "max_width": 90,
                        },
                        {
                            "field": "price",
                            "label": {"ru": "Цена", "en": "Price"},
                            "format": "offer_price",
                            "align": "end",
                            "max_width": 110,
                        },
                        {
                            "field": "currency",
                            "label": {"ru": "Валюта", "en": "Currency"},
                            "max_width": 80,
                        },
                        {
                            "field": "rrc",
                            "label": {"ru": "РРЦ", "en": "RRP"},
                            "format": "offer_price",
                            "align": "end",
                            "max_width": 110,
                        },
                    ],
                    "row_tap": {"kind": "match_product", "action": "equipment_match_to_line"},
                    "empty": _empty(
                        "Нет позиций мастер-прайса",
                        "No master price rows",
                        icon="inventory_2",
                    ),
                },
            },
            {
                "slug": "mcp_tool_overrides_list",
                "table_slug": "mcp_tool_overrides",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {"title": {"ru": "Инструкции MCP", "en": "MCP instructions"}},
                    "title_field": "tool",
                    "subtitle_fields": ["description"],
                    "columns": [
                        {"field": "tool", "label": {"ru": "Инструмент", "en": "Tool"}},
                        {"field": "description", "label": {"ru": "Описание", "en": "Description"}, "max_lines": 2},
                        {"field": "enabled", "label": {"ru": "Включено", "en": "Enabled"}, "format": "bool_yes_no"},
                    ],
                    "row_tap": {"kind": "open_form", "view": "mcp_tool_override_form"},
                    "inline_add": {"field": "tool", "title": "Добавить переопределение"},
                    "empty": _empty("Нет переопределений", "No overrides", icon="tune"),
                },
            },
            {
                "slug": "mcp_tool_override_form",
                "table_slug": "mcp_tool_overrides",
                "kind": "form",
                "ui_json": {
                    "version": 1,
                    "kind": "form",
                    "mode": "edit",
                    "title": {"ru": "Инструкция MCP", "en": "MCP instruction"},
                    "fields": [
                        {"column": "tool", "widget": "value"},
                        {"column": "description", "widget": "text"},
                        {"column": "extra_instructions", "widget": "text"},
                        {"column": "enabled", "widget": "switch"},
                    ],
                },
            },
        {
                "slug": "templates_list",
                "table_slug": "templates",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {"ru": "Шаблоны", "en": "Templates"},
                    },
                    "title_field": "template_type",
                    "subtitle_fields": ["title"],
                    "columns": [
                        {
                            "field": "template_type",
                            "label": {"ru": "Тип", "en": "Type"},
                            "max_width": 220,
                        },
                        {
                            "field": "title",
                            "label": {"ru": "Название", "en": "Title"},
                            "max_lines": 2,
                            "max_width": 260,
                        },
                        {
                            "field": "file",
                            "label": {"ru": "Файл", "en": "File"},
                            "format": "file_name",
                            "max_lines": 2,
                            "max_width": 260,
                        },
                        {
                            "field": "active",
                            "label": {"ru": "Активен", "en": "Active"},
                            "format": "bool_yes_no",
                            "max_width": 100,
                        },
                    ],
                    "row_tap": {"kind": "open_form", "view": "template_form"},
                    "inline_add": {"field": "title", "title": "Добавить шаблон"},
                    "empty": _empty(
                        "Нет шаблонов - используются встроенные",
                        "No templates - built-ins are used",
                        icon="upload_file",
                    ),
                },
            },
            {
                "slug": "template_form",
                "table_slug": "templates",
                "kind": "form",
                "ui_json": {
                    "version": 1,
                    "kind": "form",
                    "mode": "edit",
                    "title": {"ru": "Шаблон", "en": "Template"},
                    "fields": [
                        {"column": "template_type", "widget": "choice", "icon": "category"},
                        {"column": "title", "widget": "value", "icon": "title"},
                        {
                            "column": "file",
                            "widget": "file_upload",
                            "icon": "upload_file",
                            "accept": ".xlsx",
                        },
                        {"column": "active", "widget": "switch"},
                    ],
                },
            },
            {
                "slug": "equipment_hub_management",
                "table_slug": "catalogs",
                "kind": "hub",
                "ui_json": {
                    "version": 1,
                    "kind": "hub",
                    "items": [
                        {
                            "title": "Базы данных",
                            "icon": "storage",
                            "target": {"kind": "view", "view": "catalogs_list"},
                        },
                        {
                            "title": "Типы комплектующих",
                            "icon": "category",
                            "target": {"kind": "view", "view": "equipment_types_list"},
                        },
                        {
                            # WAVE12: карточки = партномера + характеристики из
                            # групп типа; по ним собираются готовые сборки.
                            "title": "Карточки комплектующих",
                            "icon": "inventory_2",
                            "target": {"kind": "view", "view": "part_cards_list"},
                        },
                        {
                            # WAVE12: глобальная библиотека сборок. Слоты =
                            # типы + требования к группам характеристик;
                            # совместимость — автоматическая матрица доменов.
                            "title": "Готовые сборки",
                            "icon": "precision_manufacturing",
                            "target": {"kind": "view", "view": "ready_builds_list"},
                        },
                        {
                            "title": "Поставщики",
                            "icon": "local_shipping",
                            "target": {"kind": "view", "view": "trusted_sellers_list"},
                        },
                        {
                            "title": "Интернет магазины",
                            "icon": "language",
                            "target": {"kind": "view", "view": "web_shops_list"},
                        },
                        {
                            "title": "Шаблоны",
                            "icon": "upload_file",
                            "target": {"kind": "view", "view": "templates_list"},
                        },
                        {
                            "title": "Промпты",
                            "icon": "psychology_outlined",
                            "target": {"kind": "view", "view": "equipment_prompts_list"},
                        },
                        {
                            "title": "Инструкции MCP",
                            "icon": "tune",
                            "target": {"kind": "view", "view": "mcp_tool_overrides_list"},
                        },
                    ],
                },
            },
],
        "tabs": [
            {
                "id": "tab_equipment_management",
                "title": "Подбор техники",
                "subtitle": "Базы данных, поставщики, типы и шаблоны",
                "order": 5,
                "icon": "precision_manufacturing",
                "view_slug": "equipment_hub_management",
                "table_slug": "catalogs",
                "enabled": True,
                "default_project_bind": "global",
                "nav": {"contour": "employee", "placement": "management"},
            },
            {
                "id": "tab_equipment",
                "title": "Подбор техники",
                "subtitle": "Каталоги, позиции, офферы и характеристики",
                "order": 10,
                "icon": "precision_manufacturing",
                "view_slug": "equipment_hub",
                "table_slug": "catalogs",
                "enabled": True,
                "default_project_bind": "global",
                "nav": {"contour": "employee", "placement": "data"},
            }
        ],
        "actions": [
            {
                "id": "index_catalog_opensearch",
                "label": {"ru": "Переиндексировать", "en": "Reindex"},
                "kind": "content.index_opensearch",
                "enabled": True,
                "params": {
                    "table_slug": "catalogs",
                    "source_kind_column": "source_kind",
                    "file_column": "source_file",
                    "map_field": "column_map",
                    "status_column": "status",
                    "error_column": "error",
                    "columns_json_column": "columns_json",
                    "os_namespace": "equipment",
                },
                "trigger": {"on": ["row.created", "row.updated"], "async": True},
                "ui": {"placement": ["toolbar"], "icon": "sync"},
            },
            {
                "id": "probe_catalog_remote",
                "label": {"ru": "Проверить БД", "en": "Probe database"},
                "kind": "content.probe_remote_sql",
                "enabled": True,
                "params": {
                    "table_slug": "catalogs",
                    "dsn_column": "remote_dsn",
                    "remote_table_column": "remote_table",
                    "remote_database_column": "remote_database",
                    "remote_user_column": "remote_user",
                    "remote_password_column": "remote_password",
                    "status_column": "status",
                    "row_count_column": "row_count",
                    "columns_json_column": "columns_json",
                    "error_column": "error",
                    "source_kind_column": "source_kind",
                    "expected_source_kind": "remote",
                },
                "trigger": {"on": ["row.created", "row.updated"], "async": True},
                "ui": {"placement": ["toolbar"], "icon": "cloud_sync"},
            },
            {
                "id": "list_catalog_remote_databases",
                "label": {"ru": "Список баз", "en": "List databases"},
                "kind": "content.list_remote_sql_databases",
                "enabled": True,
                "params": {
                    "table_slug": "catalogs",
                    "dsn_column": "remote_dsn",
                    "remote_user_column": "remote_user",
                    "remote_password_column": "remote_password",
                },
                "trigger": {"on": []},
                "ui": {"placement": []},
            },
            {
                "id": "list_catalog_remote_tables",
                "label": {"ru": "Список таблиц", "en": "List tables"},
                "kind": "content.list_remote_sql_tables",
                "enabled": True,
                "params": {
                    "table_slug": "catalogs",
                    "dsn_column": "remote_dsn",
                    "remote_database_column": "remote_database",
                    "remote_user_column": "remote_user",
                    "remote_password_column": "remote_password",
                },
                "trigger": {"on": []},
                "ui": {"placement": []},
            },
            {
                "id": "select_offer_primary",
                "label": {"ru": "Выбрать", "en": "Select"},
                "kind": "data.select_row",
                "enabled": True,
                "params": {
                    "table_slug": "found_offers",
                    "select_field": "is_selected",
                    "group_by": "line_id",
                    "parent": {
                        "table_slug": "request_lines",
                        "id_from": "line_id",
                        "set_field": "selected_offer_id",
                    },
                },
                "ui": {"placement": ["row_action"]},
            },
            {
                "id": "equipment_match_to_line",
                "label": {
                    "ru": "Сопоставить с позицией",
                    "en": "Match to line",
                },
                "kind": "equipment.match_to_line",
                "enabled": True,
                "params": {},
                "trigger": {"on": []},
                "ui": {"placement": []},
            },
            {
                "id": "master_price_export",
                "label": {
                    "ru": "Скачать мастер-прайс",
                    "en": "Download master price",
                },
                "kind": "equipment.master_price",
                "enabled": True,
                "params": {
                    "sellers_table": "trusted_sellers",
                    "templates_type": "master_price",
                },
                "trigger": {"on": []},
                "ui": {"placement": ["scaffold"], "icon": "download"},
            },
            {
                "id": "budget_sync_lines",
                "label": {"ru": "Синхронизировать", "en": "Sync"},
                "kind": "equipment.budget_sync",
                "enabled": True,
                "params": {
                    "groups_table": "found_groups",
                    "lines_table": "request_lines",
                    "offers_table": "found_offers",
                    "budget_table": "budget_lines",
                    "procurement_table": "procurement",
                },
                "trigger": {"on": ["row.created", "row.updated"], "async": True},
                "ui": {"placement": ["scaffold"], "icon": "sync"},
            },
                # WAVE7: единый пайплайн — материализация офферов из OpenSearch,
                # best/лица групп, бюджет, закупка. Кнопка «Обновить цены» +
                # авто-запуск после записей found_groups/request_lines/found_offers
                # (пайплайн пишет с run_actions=False — рекурсии нет).
                {
                    "id": "equipment_pipeline_sync",
                    "label": {"ru": "Обновить цены", "en": "Refresh prices"},
                    "kind": "equipment.pipeline",
                    "enabled": True,
                    "params": {
                        "groups_table": "found_groups",
                        "lines_table": "request_lines",
                        "offers_table": "found_offers",
                        "builds_table": "equipment_builds",
                        "budget_table": "budget_lines",
                        "procurement_table": "procurement",
                        "materialize": True,
                    },
                    "trigger": {"on": ["row.created", "row.updated"], "async": True},
                    "ui": {"placement": ["scaffold"], "icon": "refresh"},
                },
                # WAVE12: пайплайн каталога «Готовые сборки» v2. Резолвит ключи
                # карточек (партномер/алиасы/хэш) в лучшие цены батч-запросом в
                # OpenSearch, подбирает компоненты слотов по constraints и
                # доменам совместимости, считает коридор цен и итоги сборок.
                # Триггер — записи в таблицы каталога + кнопка «Обновить цены»
                # + beat-расписание (цены меняются в каталоге, а не в наших
                # строках, поэтому одних триггеров мало).
                {
                    "id": "ready_builds_sync",
                    "label": {"ru": "Обновить цены", "en": "Refresh prices"},
                    "kind": "equipment.ready_builds",
                    "enabled": True,
                    "params": {
                        "cards_table": "part_cards",
                        "builds_table": "ready_builds",
                        "slots_table": "ready_build_slots",
                    },
                    "trigger": {"on": ["row.created", "row.updated"], "async": True},
                    "ui": {"placement": ["scaffold"], "icon": "refresh"},
                },
                # WAVE12: привязка «Готовой сборки» к позиции заказчика —
                # ГЛОБАЛЬНАЯ ССЫЛКА, а не копия: в чате создаётся строка
                # equipment_builds с ready_build_id, а состав/цены зеркалит
                # пайплайн из каталога при каждом прогоне. Вызывается из MCP
                # (ready_build_attach) и кнопкой «В чат» на карточке сборки.
                {
                    "id": "ready_build_attach",
                    "label": {"ru": "В чат", "en": "Attach to position"},
                    "kind": "equipment.attach_ready_build",
                    "enabled": True,
                    "params": {},
                    "trigger": {"on": [], "async": False},
                    "ui": {"placement": ["row"], "icon": "add_shopping_cart"},
                },
                # WAVE7: ручная правка строки «Закупка» (маржа % → реестр
                # поставщиков и бюджетные строки, доставка → суммы).
                {
                    "id": "procurement_apply",
                    "label": {"ru": "Применить закупку", "en": "Apply procurement"},
                    "kind": "equipment.procurement_apply",
                    "enabled": True,
                    "params": {
                        "procurement_table": "procurement",
                    },
                    "trigger": {"on": ["row.updated"], "async": True},
                    "ui": {"placement": []},
                },
                {
                    "id": "budget_export",
                "label": {"ru": "Скачать xlsx", "en": "Download xlsx"},
                "kind": "equipment.budget_export",
                "enabled": True,
                "params": {
                    "budget_table": "budget_lines",
                    "lines_table": "request_lines",
                    "fields_table": "document_fields",
                    "company_fields_table": "document_company_fields",
                },
                "trigger": {"on": []},
                "ui": {"placement": ["toolbar", "scaffold"], "icon": "download"},
            },
            {
                "id": "kp_export",
                "label": {"ru": "Скачать КП (PDF)", "en": "Download quote (PDF)"},
                "kind": "equipment.kp_export",
                "enabled": True,
                "params": {
                    "budget_table": "budget_lines",
                    "fields_table": "document_fields",
                    "company_fields_table": "document_company_fields",
                },
                "trigger": {"on": []},
                "ui": {"placement": ["toolbar", "scaffold"], "icon": "picture_as_pdf"},
            },
            {
                "id": "spec_export",
                "label": {"ru": "Спецификация (PDF)", "en": "Specification (PDF)"},
                "kind": "equipment.spec_export",
                "enabled": True,
                "params": {
                    "budget_table": "budget_lines",
                    "fields_table": "document_fields",
                    "company_fields_table": "document_company_fields",
                },
                "trigger": {"on": []},
                "ui": {"placement": ["toolbar", "scaffold"], "icon": "table_view"},
            },
        ],
        "materialize": [
            {
                "id": "equipment_prompts_files",
                "enabled": True,
                "when": ["project.created", "project.resumed", "project.sync"],
                "priority": 11,
                "source": {
                    "type": "rows",
                    "table_slug": "equipment_prompts",
                    "filter": {"enabled": True},
                },
                "target": {"workspace_path": ".", "format": "prompt_paths"},
            },
            {
                "id": "catalog_manifest",
                "enabled": True,
                "when": ["project.created", "project.resumed", "project.sync"],
                "priority": 41,
                "source": {
                    "type": "rows",
                    "table_slug": "catalogs",
                    "filter": {"status": "ready", "paused": False},
                },
                "target": {
                    "workspace_path": "catalogs/manifest.json",
                    "format": "json_rows",
                },
            },
            {
                "id": "equipment_mcp_package",
                "enabled": True,
                "when": ["project.created", "project.resumed", "project.sync"],
                "priority": 66,
                "source": {
                    "type": "rows",
                    "table_slug": "equipment_mcp",
                    "filter": {"enabled": True},
                },
                "target": {
                    "workspace_path": "packages/{{name}}",
                    "format": "mcp_package",
                    "field": "file_ref",
                },
            },
        ],
# Chat display aliases for the prodavan-equipment MCP package tools
        # (keys = wire tool names; labels shown in the chat instead of
        # "MCP: prodavan-equipment.<tool>"). Editable per-instance via the
        # module meta editor (slug mcp_aliases).
        "mcp_aliases": [
            {
                "server": "prodavan-equipment",
                "tool": "equipment_catalog_sources",
                "label": "Источники каталога",
                "description": "Список каталогов техники с их статусами",
            },
            {
                "server": "prodavan-equipment",
                "tool": "equipment_catalog_search",
                "label": "Поиск товара",
                "description": "Поиск позиций по каталогам техники",
            },
            {
                "server": "prodavan-equipment",
                "tool": "request_lines_list",
                "label": "Позиции заявки",
                "description": "Список позиций текущей заявки",
            },
            {
                "server": "prodavan-equipment",
                "tool": "request_lines_get",
                "label": "Позиция заявки",
                "description": "Позиция заявки по идентификатору",
            },
            {
                "server": "prodavan-equipment",
                "tool": "request_lines_upsert",
                "label": "Запись позиции заявки",
                "description": "Создание или обновление позиции заявки",
            },
            {
                "server": "prodavan-equipment",
                "tool": "request_lines_delete",
                "label": "Удаление позиции заявки",
                "description": "Удаление позиции заявки (с группами и офферами)",
            },
            {
                "server": "prodavan-equipment",
                "tool": "found_groups_list",
                "label": "Найденные группы",
                "description": "Список подобранных групп товаров",
            },
            {
                "server": "prodavan-equipment",
                "tool": "found_groups_get",
                "label": "Найденная группа",
                "description": "Подобранная группа товаров по идентификатору",
            },
            {
                "server": "prodavan-equipment",
                "tool": "found_groups_upsert",
                "label": "Запись группы товаров",
                "description": "Создание или обновление подобранной группы",
            },
            {
                "server": "prodavan-equipment",
                "tool": "found_groups_delete",
                "label": "Удаление группы товаров",
                "description": "Удаление подобранной группы (с её офферами)",
            },
            {
                "server": "prodavan-equipment",
                "tool": "equipment_types_list",
                "label": "Типы комплектующих",
                "description": "Список типов комплектующих (слоты сборки)",
            },
            {
                "server": "prodavan-equipment",
                "tool": "equipment_builds_list",
                "label": "Сборки",
                "description": "Список сборок ПК/серверов по позициям",
            },
            {
                "server": "prodavan-equipment",
                "tool": "equipment_builds_get",
                "label": "Сборка",
                "description": "Сборка по идентификатору",
            },
            {
                "server": "prodavan-equipment",
                "tool": "equipment_builds_upsert",
                "label": "Запись сборки",
                "description": "Создание или обновление сборки",
            },
            {
                "server": "prodavan-equipment",
                "tool": "equipment_builds_delete",
                "label": "Удаление сборки",
                "description": "Удаление сборки (с её комплектующими)",
            },
            {
                "server": "prodavan-equipment",
                "tool": "part_cards_list",
                "label": "Карточки комплектующих",
                "description": "Карточки партномеров: тип, характеристики, лидеры цен",
            },
            {
                "server": "prodavan-equipment",
                "tool": "part_card_upsert",
                "label": "Запись карточки",
                "description": "Создание или обновление карточки комплектующего",
            },
            {
                "server": "prodavan-equipment",
                "tool": "part_card_delete",
                "label": "Удаление карточки",
                "description": "Удаление карточки комплектующего",
            },
            {
                "server": "prodavan-equipment",
                "tool": "ready_builds_list",
                "label": "Готовые сборки",
                "description": "Каталог готовых сборок с составом и ценами",
            },
            {
                "server": "prodavan-equipment",
                "tool": "ready_build_get",
                "label": "Готовая сборка",
                "description": "Готовая сборка со слотами по идентификатору",
            },
            {
                "server": "prodavan-equipment",
                "tool": "ready_build_upsert",
                "label": "Запись готовой сборки",
                "description": "Сборка вместе со слотами одним вызовом",
            },
            {
                "server": "prodavan-equipment",
                "tool": "ready_build_attach",
                "label": "Сборка в чат",
                "description": "Привязка готовой сборки к позиции заказчика (глобальная связь)",
            },
            {
                "server": "prodavan-equipment",
                "tool": "ready_build_slot_upsert",
                "label": "Слот готовой сборки",
                "description": "Создание или правка одного слота сборки (не всех)",
            },
        ],
        "mcp_tools": [
                    {
                "id": "equipment_catalog_list",
                "name": "equipment_catalog_list",
                "label": "List equipment catalogs",
                "description": (
                    "List ready non-paused catalog cards (Postgres SoT); "
                    "includes source_kind local|remote. Search is OpenSearch via "
                    "Pod catalog-search (no catalog.sqlite / EQUIPMENT_* env)."
                ),
                "enabled": True,
                "kind": "rows_query",
                "params_schema": {"type": "object", "properties": {}},
                "implementation": {
                    "table_slug": "catalogs",
                    "query": {
                        "filter": {"status": "ready", "paused": False},
                        "limit": 100,
                    },
                },
            },
            {
                "id": "equipment_types_list",
                "name": "equipment_types_list",
                "label": "List component types",
                "description": (
                    "List equipment_types rows (name, sort_order, fields_json schema "
                    "with kind/required/compat_domain/groups). Use field keys when "
                    "writing part_cards.attrs and ready_build_slots.constraints."
                ),
                "enabled": True,
                "kind": "rows_query",
                "params_schema": {"type": "object", "properties": {}},
                "implementation": {
                    "table_slug": "equipment_types",
                    "query": {"limit": 200},
                },
            },
            {
                "id": "part_cards_list",
                "name": "part_cards_list",
                "label": "List component cards",
                "description": (
                    "List part_cards (type_id, part_number, aliases, attrs, "
                    "best_price/best_seller leaders resolved by the catalog pipeline)."
                ),
                "enabled": True,
                "kind": "rows_query",
                "params_schema": {"type": "object", "properties": {}},
                "implementation": {
                    "table_slug": "part_cards",
                    "query": {"limit": 500},
                },
            },
            {
                "id": "equipment_builds_list",
                "name": "equipment_builds_list",
                "label": "List builds",
                "description": (
                    "List equipment_builds (name, ready_build_id, line_id, "
                    "components_count, price_total). Chat builds are links to "
                    "ready_builds — composition is mirrored from the catalog."
                ),
                "enabled": True,
                "kind": "rows_query",
                "params_schema": {"type": "object", "properties": {}},
                "implementation": {
                    "table_slug": "equipment_builds",
                    "query": {"limit": 200},
                },
            },
            {
                "id": "equipment_builds_upsert",
                "name": "equipment_builds_upsert",
                "label": "Upsert builds",
                "description": (
                    "Create/update equipment_builds — a chat-side link to a "
                    "ready_builds row (ready_build_id required on create). "
                    "Composition/prices come from the catalog via pipeline."
                ),
                "enabled": True,
                "kind": "rows_upsert",
                "implementation": {"table_slug": "equipment_builds"},
            },
            {
                "id": "trusted_sellers_list",
                "name": "trusted_sellers_list",
                "label": "List trusted sellers",
                "description": "List trusted_sellers (name, aliases).",
                "enabled": True,
                "kind": "rows_query",
                "params_schema": {"type": "object", "properties": {}},
                "implementation": {
                    "table_slug": "trusted_sellers",
                    "query": {"limit": 500},
                },
            },
            {
                "id": "web_shops_list",
                "name": "web_shops_list",
                "label": "List web shops",
                "description": "List web_shops (name, url, cookies).",
                "enabled": True,
                "kind": "rows_query",
                "params_schema": {"type": "object", "properties": {}},
                "implementation": {
                    "table_slug": "web_shops",
                    "query": {"limit": 200},
                },
            },
            {
                "id": "budget_lines_list",
                "name": "budget_lines_list",
                "label": "List budget lines",
                "description": (
                    "Budget rows for the current chat (budget_lines) — best-offer "
                    "snapshot synced from request_lines / found_offers."
                ),
                "enabled": True,
                "kind": "rows_query",
                "params_schema": {"type": "object", "properties": {}},
                "implementation": {
                    "table_slug": "budget_lines",
                    "query": {"limit": 500},
                },
            },
            {
                "id": "found_groups_list",
                "name": "found_groups_list",
                "label": "List found groups",
                "description": (
                    "Agent-selected candidate groups (found_groups) for request lines: "
                    "line_id, part_number, aliases, match_kind (exact|analog|doubt), "
                    "offers_count, face fields. Offers/prices are materialized by the "
                    "platform pipeline — do not write found_offers."
                ),
                "enabled": True,
                "kind": "rows_query",
                "params_schema": {"type": "object", "properties": {}},
                "implementation": {
                    "table_slug": "found_groups",
                    "query": {"limit": 500},
                },
            },
        ],
        "container_env": [],
        "container_env_secrets": [],
        "seed_rows": {
            "items": [
                {
                "row_id": "tpl_budget_builtin",
                "table_slug": "templates",
                "body": {
                    "template_type": "budget",
                    "title": "Бюджетирование (встроенный)",
                    "file": {"storage_key": "builtin/kp-template.xlsx", "filename": "budget.xlsx"},
                    "active": True,
                },
            },
            {
                "row_id": "tpl_kp_builtin",
                "table_slug": "templates",
                "body": {
                    "template_type": "commercial_proposal",
                    "title": "Коммерческое предложение (встроенный)",
                    "file": {
                        "storage_key": "builtin/commercial-proposal-template.xlsx",
                        "filename": "commercial-proposal.xlsx",
                    },
                    "active": True,
                },
            },
            {
                "row_id": "tpl_spec_builtin",
                "table_slug": "templates",
                "body": {
                    "template_type": "specification",
                    "title": "Спецификация (встроенный)",
                    "file": {
                        "storage_key": "builtin/specification-template.xlsx",
                        "filename": "specification.xlsx",
                    },
                    "active": True,
                },
            },
                {
                    "table_slug": "templates",
                    "row_id": "tpl_master_price_builtin",
                    "body": {
                        "template_type": "master_price",
                        "title": "Мастер-прайс (встроенный)",
                        "file": {
                            "storage_key": "builtin/master-price-template.xlsx",
                            "filename": "master-price.xlsx",
                        },
                        "active": True,
                    },
                },
                *_equipment_type_seed_rows(),
                *_equipment_prompt_seed_rows(),
                {
                    "table_slug": "equipment_mcp",
                    "row_id": "equipment_mcp_default",
                    "body": {
                        "name": "prodavan-equipment",
                        "version": "3.0.0",
                        "enabled": True,
                    },
                },
            ]
        },
    }


PRODUCT_MODULES: list[tuple[str, str, dict[str, Any]]] = [
    ("mod_prompts", "Промпты", mod_prompts_meta()),
    ("mod_mcp", "MCP", mod_mcp_meta()),
    ("mod_files", "Файлы", mod_files_meta()),
    ("mod_equipment", "Подбор техники", mod_equipment_meta()),
]

EXAMPLE_MODULE_IDS: tuple[str, ...] = (
    "mod_example_suppliers",
    "mod_example_notes",
    "mod_example_hub",
    "mod_example_sku",
)
