"""Product module catalog meta — used by Alembic seed and tests."""

from __future__ import annotations

from typing import Any

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


def _eq_field(key: str, label_ru: str, label_en: str) -> dict[str, Any]:
    return {"key": key, "label": {"ru": label_ru, "en": label_en}}


# PC/server component types — order = matching priority (lower sort_order first).
_EQUIPMENT_TYPE_SEEDS: list[dict[str, Any]] = [
    {
        "row_id": "etype_cpu",
        "name": "Процессор",
        "sort_order": 10,
        "build_scope": "all",
        "fields": [
            _eq_field("cores", "Ядра", "Cores"),
            _eq_field("threads", "Потоки", "Threads"),
            _eq_field("base_clock", "Базовая частота", "Base clock"),
            _eq_field("boost_clock", "Turbo частота", "Boost clock"),
            _eq_field("socket", "Сокет", "Socket"),
            _eq_field("tdp", "TDP", "TDP"),
            _eq_field("lithography", "Техпроцесс", "Lithography"),
            _eq_field("memory_channels", "Каналы памяти", "Memory channels"),
            _eq_field("pcie_gen", "PCIe поколение", "PCIe gen"),
            _eq_field("max_memory", "Макс. память", "Max memory"),
        ],
    },
    {
        "row_id": "etype_motherboard",
        "name": "Материнская плата",
        "sort_order": 20,
        "build_scope": "all",
        "fields": [
            _eq_field("socket", "Сокет", "Socket"),
            _eq_field("chipset", "Чипсет", "Chipset"),
            _eq_field("form_factor", "Форм-фактор", "Form factor"),
            _eq_field("ram_type", "Тип ОЗУ", "RAM type"),
            _eq_field("ram_slots", "Слоты ОЗУ", "RAM slots"),
            _eq_field("max_ram", "Макс. ОЗУ", "Max RAM"),
            _eq_field("sata_ports", "SATA порты", "SATA ports"),
            _eq_field("m2_slots", "M.2 слоты", "M.2 slots"),
            _eq_field("cpu_sockets", "Сокетов CPU", "CPU sockets"),
            _eq_field("memory_speed_max", "Макс. частота ОЗУ", "Max memory speed"),
            _eq_field("pcie_gen", "PCIe поколение", "PCIe gen"),
            _eq_field("ecc_support", "Поддержка ECC", "ECC support"),
        ],
    },
    {
        "row_id": "etype_ram",
        "name": "Оперативная память",
        "sort_order": 30,
        "build_scope": "all",
        "fields": [
            _eq_field("ram_type", "Тип", "Type"),
            _eq_field("module_capacity", "Объём модуля", "Module capacity"),
            _eq_field("modules", "Модулей", "Modules"),
            _eq_field("total_capacity", "Суммарный объём", "Total capacity"),
            _eq_field("frequency", "Частота", "Frequency"),
            _eq_field("ecc", "ECC", "ECC"),
            _eq_field("form_factor", "Форм-фактор", "Form factor"),
            _eq_field("voltage", "Напряжение", "Voltage"),
            _eq_field("registered", "Registered/LRDIMM", "Registered"),
        ],
    },
    {
        "row_id": "etype_storage",
        "name": "Накопитель",
        "sort_order": 40,
        "build_scope": "all",
        "fields": [
            _eq_field("drive_type", "Тип", "Drive type"),
            _eq_field("interface", "Интерфейс", "Interface"),
            _eq_field("capacity", "Объём", "Capacity"),
            _eq_field("form_factor", "Форм-фактор", "Form factor"),
            _eq_field("seq_read", "Чтение", "Seq. read"),
            _eq_field("seq_write", "Запись", "Seq. write"),
            _eq_field("protocol_gen", "Поколение протокола", "Protocol gen"),
            _eq_field("hot_swap", "Hot-swap", "Hot-swap"),
        ],
    },
    {
        "row_id": "etype_gpu",
        "name": "Видеокарта",
        "sort_order": 50,
        "build_scope": "all",
        "fields": [
            _eq_field("gpu_memory", "Память", "Memory"),
            _eq_field("memory_bus", "Шина памяти", "Memory bus"),
            _eq_field("interface", "Интерфейс", "Interface"),
            _eq_field("tdp", "TDP", "TDP"),
            _eq_field("length_mm", "Длина, мм", "Length mm"),
            _eq_field("power_connectors", "Питание", "Power connectors"),
            _eq_field("slot_width", "Ширина слотов", "Slot width"),
            _eq_field("recommended_psu_w", "Реком. БП, Вт", "Recommended PSU W"),
        ],
    },
    {
        "row_id": "etype_psu",
        "name": "Блок питания",
        "sort_order": 60,
        "build_scope": "all",
        "fields": [
            _eq_field("wattage", "Мощность", "Wattage"),
            _eq_field("efficiency", "КПД", "Efficiency"),
            _eq_field("modular", "Модульность", "Modular"),
            _eq_field("form_factor", "Форм-фактор", "Form factor"),
            _eq_field("pcie_cables", "PCIe кабели", "PCIe cables"),
            _eq_field("atx_version", "ATX версия", "ATX version"),
            _eq_field("eps_8pin", "EPS 8-pin", "EPS 8-pin"),
            _eq_field("12vhpwr", "12VHPWR", "12VHPWR"),
        ],
    },
    {
        "row_id": "etype_cooling",
        "name": "Охлаждение",
        "sort_order": 70,
        "build_scope": "all",
        "fields": [
            _eq_field("cooling_kind", "Тип", "Kind"),
            _eq_field("socket_compat", "Сокеты", "Socket compat"),
            _eq_field("tdp_rating", "TDP рейтинг", "TDP rating"),
            _eq_field("radiator_size", "Радиатор", "Radiator size"),
            _eq_field("height_mm", "Высота, мм", "Height mm"),
            _eq_field("mount_type", "Крепление", "Mount type"),
            _eq_field("clearance_mm", "Клиренс, мм", "Clearance mm"),
        ],
    },
    {
        "row_id": "etype_case",
        "name": "Корпус",
        "sort_order": 80,
        "build_scope": "all",
        "fields": [
            _eq_field("form_factor_support", "Форм-факторы", "Form factors"),
            _eq_field("max_gpu_length", "Макс. GPU", "Max GPU length"),
            _eq_field("max_cooler_height", "Макс. кулер", "Max cooler height"),
            _eq_field("drive_bays", "Отсеки", "Drive bays"),
            _eq_field("psu_form_factor", "БП форм-фактор", "PSU form factor"),
            _eq_field("rad_support", "Радиаторы СЖО", "Rad support"),
            _eq_field("psu_max_length", "Макс. длина БП", "PSU max length"),
        ],
    },
    {
        "row_id": "etype_case_fans",
        "name": "Корпусные вентиляторы",
        "sort_order": 85,
        "build_scope": "all",
        "fields": [
            _eq_field("count", "Количество", "Count"),
            _eq_field("size_mm", "Размер, мм", "Size mm"),
            _eq_field("pwm", "PWM", "PWM"),
            _eq_field("connector", "Разъём", "Connector"),
        ],
    },
    {
        "row_id": "etype_nic",
        "name": "Сетевой адаптер",
        "sort_order": 90,
        "build_scope": "all",
        "fields": [
            _eq_field("port_speed", "Скорость", "Port speed"),
            _eq_field("ports", "Порты", "Ports"),
            _eq_field("interface", "Интерфейс", "Interface"),
            _eq_field("rdma", "RDMA", "RDMA"),
            _eq_field("form_factor", "Форм-фактор", "Form factor"),
        ],
    },
    {
        "row_id": "etype_raid_hba",
        "name": "RAID/HBA контроллер",
        "sort_order": 100,
        "build_scope": "server",
        "fields": [
            _eq_field("interface", "Интерфейс", "Interface"),
            _eq_field("internal_ports", "Внутр. порты", "Internal ports"),
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
            _eq_field("remote_console", "Удалённая консоль", "Remote console"),
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
                "filter": {"profile_id": "{active_profile_id}"},
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
                            "section_title": {"ru": "Промпты", "en": "Prompts"},
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
                "scope": {"projects": "all"},
            },
            {
                "slug": "request_lines",
                "label": {"ru": "Позиции заказчика", "en": "Request lines"},
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all"},
            },
            {
                "slug": "found_offers",
                "label": {"ru": "Найденные товары", "en": "Found offers"},
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all"},
            },
            {
                "slug": "equipment_types",
                "label": {"ru": "Типы комплектующих", "en": "Component types"},
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all"},
            },
            {
                "slug": "equipment_items",
                "label": {
                    "ru": "Характеристики оборудования",
                    "en": "Equipment characteristics",
                },
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all"},
            },
            {
                "slug": "equipment_builds",
                "label": {"ru": "Сборка", "en": "Builds"},
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all"},
            },
            {
                "slug": "trusted_sellers",
                "label": {
                    "ru": "Проверенные продавцы",
                    "en": "Trusted sellers",
                },
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all"},
            },
            {
                "slug": "web_shops",
                "label": {
                    "ru": "Интернет магазины",
                    "en": "Web shops",
                },
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all"},
            },
            {
                "slug": "s4b_settings",
                "label": {"ru": "S4B", "en": "S4B"},
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all"},
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
                "name": "artifact_ref",
                "label": {"ru": "SQLite", "en": "SQLite"},
                "type": "file_ref",
                "required": False,
            },
            {
                "table_slug": "catalogs",
                "name": "status",
                "label": {"ru": "Статус", "en": "Status"},
                "type": "enum",
                "required": True,
                "default": "draft",
                "enum": {
                    "values": ["draft", "indexing", "ready", "error"],
                    "labels": {
                        "draft": "Черновик",
                        "indexing": "Индексация",
                        "ready": "Готово",
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
                "name": "error",
                "label": {"ru": "Ошибка", "en": "Error"},
                "type": "text",
                "required": False,
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
            {
                "table_slug": "found_offers",
                "name": "line_id",
                "label": {"ru": "Позиция", "en": "Line"},
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
                    "values": ["exact", "analog"],
                    "labels": {"exact": "Точное", "analog": "Аналог"},
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
                "label": {"ru": "Запрос", "en": "Request title"},
                "type": "text",
                "required": False,
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
                    "values": ["all", "pc", "server"],
                    "labels": {"all": "Все", "pc": "ПК", "server": "Сервер"},
                },
            },
            _project_ids_column("equipment_types"),
            {
                "table_slug": "equipment_items",
                "name": "name",
                "label": {"ru": "Название", "en": "Name"},
                "type": "text",
                "required": True,
            },
            {
                "table_slug": "equipment_items",
                "name": "offer_id",
                "label": {"ru": "Найденный товар", "en": "Found offer"},
                "type": "ref",
                "required": False,
                "ref": {"table_slug": "found_offers"},
            },
            {
                "table_slug": "equipment_items",
                "name": "offer_title",
                "label": {"ru": "Товар", "en": "Offer"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "equipment_items",
                "name": "type_id",
                "label": {"ru": "Тип", "en": "Type"},
                "type": "ref",
                "required": False,
                "ref": {"table_slug": "equipment_types"},
            },
            {
                "table_slug": "equipment_items",
                "name": "type_name",
                "label": {"ru": "Тип", "en": "Type"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "equipment_items",
                "name": "part_number",
                "label": {"ru": "Партномер", "en": "Part number"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "equipment_items",
                "name": "qty",
                "label": {"ru": "Количество", "en": "Quantity"},
                "type": "number",
                "required": False,
            },
            {
                "table_slug": "equipment_items",
                "name": "attrs",
                "label": {"ru": "Параметры", "en": "Attributes"},
                "type": "json",
                "required": False,
                "default": {},
            },
            _project_ids_column("equipment_items"),
            {
                "table_slug": "equipment_builds",
                "name": "name",
                "label": {"ru": "Название", "en": "Name"},
                "type": "text",
                "required": True,
            },
            {
                "table_slug": "equipment_builds",
                "name": "build_kind",
                "label": {"ru": "Тип", "en": "Kind"},
                "type": "enum",
                "required": True,
                "default": "pc",
                "enum": {
                    "values": ["pc", "server"],
                    "labels": {"pc": "ПК", "server": "Сервер"},
                },
            },
            {
                "table_slug": "equipment_builds",
                "name": "slots",
                "label": {"ru": "Комплектующие", "en": "Slots"},
                "type": "json",
                "required": False,
                "default": {},
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
            _project_ids_column("equipment_builds"),
            {
                "table_slug": "trusted_sellers",
                "name": "name",
                "label": {"ru": "Название", "en": "Name"},
                "type": "text",
                "required": True,
            },
            {
                "table_slug": "trusted_sellers",
                "name": "aliases",
                "label": {"ru": "Алиасы", "en": "Aliases"},
                "type": "text",
                "required": False,
                "default": "",
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
                "table_slug": "s4b_settings",
                "name": "name",
                "label": {"ru": "Название", "en": "Name"},
                "type": "text",
                "required": True,
                "default": "S4B",
            },
            {
                "table_slug": "s4b_settings",
                "name": "base_url",
                "label": {"ru": "Ссылка", "en": "URL"},
                "type": "text",
                "required": False,
                "default": "",
            },
            {
                "table_slug": "s4b_settings",
                "name": "login",
                "label": {"ru": "Логин", "en": "Login"},
                "type": "text",
                "required": False,
                "default": "",
            },
            {
                "table_slug": "s4b_settings",
                "name": "password",
                "label": {"ru": "Пароль", "en": "Password"},
                "type": "secret_ref",
                "required": False,
            },
            {
                "table_slug": "s4b_settings",
                "name": "mcp_zip",
                "label": {"ru": "MCP (zip)", "en": "MCP (zip)"},
                "type": "file_ref",
                "required": False,
            },
            {
                "table_slug": "s4b_settings",
                "name": "enabled",
                "label": {"ru": "Включено", "en": "Enabled"},
                "type": "bool",
                "required": False,
                "default": True,
            },
            _project_ids_column("s4b_settings"),
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
                            "title": "Базы данных",
                            "icon": "storage",
                            "target": {"kind": "view", "view": "catalogs_list"},
                        },
                        {
                            "title": "Позиции заказчика",
                            "icon": "list_alt",
                            "target": {"kind": "view", "view": "request_lines_list"},
                        },
                        {
                            "title": "Найденные товары",
                            "icon": "inventory_2",
                            "target": {"kind": "view", "view": "found_offers_list"},
                        },
                        {
                            "title": "Характеристики оборудования",
                            "icon": "tune",
                            "target": {
                                "kind": "view",
                                "view": "equipment_items_list",
                            },
                        },
                        {
                            "title": "Типы комплектующих",
                            "icon": "category",
                            "target": {
                                "kind": "view",
                                "view": "equipment_types_list",
                            },
                        },
                        {
                            "title": "Сборка",
                            "icon": "precision_manufacturing",
                            "target": {
                                "kind": "view",
                                "view": "equipment_builds_list",
                            },
                        },
                        {
                            "title": "Проверенные продавцы",
                            "icon": "verified",
                            "target": {
                                "kind": "view",
                                "view": "trusted_sellers_list",
                            },
                        },
                        {
                            "title": "Интернет магазины",
                            "icon": "language",
                            "target": {
                                "kind": "view",
                                "view": "web_shops_list",
                            },
                        },
                        {
                            "title": "S4B",
                            "icon": "storefront",
                            "target": {"kind": "view", "view": "s4b_settings_list"},
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
                        {"field": "row_count", "label": {"ru": "Строк", "en": "Rows"}},
                    ],
                    "row_style": [
                        {
                            "when": {"field": "status", "eq": "error"},
                            "accent": "error",
                        },
                        {
                            "when": {"field": "paused", "eq": True},
                            "accent": "warning",
                        },
                    ],
                    "row_tap": {"kind": "open_view", "view": "catalogs_settings"},
                    "inline_add": {"field": "name", "title": "Добавить базу"},
                    "empty": _empty("Нет баз", "No databases", icon="storage"),
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
                    "fields": [
                        {"column": "name", "widget": "value", "icon": "storage"},
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
                                        "in": ["draft", "ready", "indexing"],
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
                                        "in": ["draft", "ready", "indexing"],
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
                            "visible_when": {"field": "status", "eq": "ready"},
                        },
                        {
                            "column": "project_ids",
                            "widget": "project_multiselect",
                            "icon": "folder_outlined",
                            "visible_when": {"field": "status", "eq": "ready"},
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
                        {"field": "title", "label": {"ru": "Название", "en": "Title"}},
                        {"field": "part_number", "label": {"ru": "Партномер", "en": "P/N"}},
                        {"field": "qty", "label": {"ru": "Кол-во", "en": "Qty"}},
                        {"field": "found_count", "label": {"ru": "Найдено", "en": "Found"}},
                        {"field": "status", "label": {"ru": "Статус", "en": "Status"}},
                    ],
                    "row_tap": {"kind": "open_view", "view": "offers_for_line"},
                    "inline_add": {"field": "title", "title": "Добавить позицию"},
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
                        {"column": "project_ids", "widget": "project_multiselect"},
                    ],
                },
            },
            {
                "slug": "offers_for_line",
                "table_slug": "found_offers",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {"ru": "Офферы позиции", "en": "Line offers"}
                    },
                    "title_field": "title",
                    "subtitle_fields": ["part_number", "match_kind", "score"],
                    "columns": [
                        {"field": "title", "label": {"ru": "Товар", "en": "Title"}},
                        {"field": "part_number", "label": {"ru": "Партномер", "en": "P/N"}},
                        {"field": "price", "label": {"ru": "Цена", "en": "Price"}},
                        {"field": "match_kind", "label": {"ru": "Совпадение", "en": "Match"}},
                        {"field": "score", "label": {"ru": "Оценка", "en": "Score"}},
                    ],
                    "context_bind": {"line_id": "contextRowId"},
                    "selection": {
                        "kind": "single",
                        "field": "is_selected",
                        "action": "select_offer_primary",
                    },
                    "row_tap": {"kind": "open_form", "view": "found_offers_form"},
                    "inline_add": {"field": "title", "title": "Добавить товар"},
                    "empty": _empty("Нет кандидатов", "No offers", icon="inventory_2"),
                },
            },
            {
                "slug": "found_offers_list",
                "table_slug": "found_offers",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {"ru": "Найденные товары", "en": "Found offers"}
                    },
                    "title_field": "title",
                    "subtitle_fields": ["source_title", "match_kind"],
                    "columns": [
                        {"field": "title", "label": {"ru": "Товар", "en": "Title"}},
                        {"field": "part_number", "label": {"ru": "Партномер", "en": "P/N"}},
                        {"field": "price", "label": {"ru": "Цена", "en": "Price"}},
                        {"field": "match_kind", "label": {"ru": "Совпадение", "en": "Match"}},
                        {"field": "score", "label": {"ru": "Оценка", "en": "Score"}},
                        {"field": "source_title", "label": {"ru": "Запрос", "en": "Request"}},
                    ],
                    "row_tap": {"kind": "open_form", "view": "found_offers_form"},
                    "inline_add": {"field": "title", "title": "Добавить товар"},
                    "empty": _empty("Нет товаров", "No offers", icon="inventory_2"),
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
                    "fields": [
                        {"column": "title", "widget": "value"},
                        {"column": "line_id", "widget": "ref"},
                        {"column": "part_number", "widget": "value"},
                        {"column": "price", "widget": "value"},
                        {"column": "catalog_id", "widget": "ref"},
                        {"column": "score", "widget": "value"},
                        {"column": "match_kind", "widget": "choice"},
                        {"column": "is_selected", "widget": "switch"},
                        {"column": "source_title", "widget": "value"},
                        {"column": "project_ids", "widget": "project_multiselect"},
                    ],
                },
            },
            {
                "slug": "equipment_items_list",
                "table_slug": "equipment_items",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {
                            "ru": "Характеристики оборудования",
                            "en": "Equipment characteristics",
                        }
                    },
                    "title_field": "name",
                    "subtitle_fields": ["type_name", "part_number"],
                    "columns": [
                        {
                            "field": "name",
                            "label": {"ru": "Название", "en": "Name"},
                        },
                        {
                            "field": "type_name",
                            "label": {"ru": "Тип", "en": "Type"},
                        },
                        {
                            "field": "part_number",
                            "label": {"ru": "Партномер", "en": "P/N"},
                        },
                        {
                            "field": "qty",
                            "label": {"ru": "Кол-во", "en": "Qty"},
                        },
                    ],
                    "row_tap": {
                        "kind": "open_view",
                        "view": "equipment_item_settings",
                    },
                    "inline_add": {
                        "field": "name",
                        "title": "Добавить позицию",
                    },
                    "empty": _empty(
                        "Нет характеристик",
                        "No characteristics",
                        icon="tune",
                    ),
                },
            },
            {
                "slug": "equipment_item_settings",
                "table_slug": "equipment_items",
                "kind": "detail",
                "ui_json": {
                    "version": 1,
                    "kind": "detail",
                    "mode": "edit",
                    "title": {
                        "ru": "Характеристики",
                        "en": "Characteristics",
                    },
                    "fields": [
                        {
                            "column": "name",
                            "widget": "value",
                            "icon": "label_outline",
                        },
                        {
                            "column": "offer_id",
                            "widget": "type_ref_picker",
                            "pick_view": "found_offers_pick",
                            "title_field": "offer_title",
                            "icon": "inventory_2",
                            "empty_style": "warning",
                            "empty_label": {
                                "ru": "Не выбран",
                                "en": "Not selected",
                            },
                            "label": {
                                "ru": "Найденный товар",
                                "en": "Found offer",
                            },
                        },
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
                            "column": "qty",
                            "widget": "value",
                            "icon": "numbers",
                        },
                        {
                            "column": "attrs",
                            "widget": "schema_attrs",
                            "type_id_field": "type_id",
                            "types_table": "equipment_types",
                            "fields_from": "fields_json",
                            "section_title": {
                                "ru": "Характеристики",
                                "en": "Characteristics",
                            },
                            "visible_when": {
                                "field": "type_id",
                                "not_empty": True,
                            },
                        },
                    ],
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
                            "clear_fields": ["attrs"],
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
                "slug": "equipment_items_pick",
                "table_slug": "equipment_items",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {
                            "ru": "Выбор комплектующего",
                            "en": "Select component",
                        }
                    },
                    "title_field": "name",
                    "subtitle_fields": ["part_number", "offer_title"],
                    "columns": [
                        {
                            "field": "name",
                            "label": {"ru": "Название", "en": "Name"},
                        }
                    ],
                    "row_filter_from_context": {"type_id": "_pick_type_id"},
                    "selection": {
                        "kind": "single",
                        "control": "switch",
                        "placement": "trailing",
                        "disable_row_tap": True,
                        "match_map_field": "slots",
                        "match_map_key_from_context": "_slot_key",
                        "set_on_context": {
                            "map_field": "slots",
                            "map_key_from_context": "_slot_key",
                            "value_from": "row_id",
                            "recompute_build_totals": True,
                            "pop_after": True,
                        },
                    },
                    "empty": _empty(
                        "Нет позиций",
                        "No items",
                        icon="tune",
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
                    "subtitle_fields": ["build_kind", "components_count", "price_total"],
                    "columns": [
                        {
                            "field": "name",
                            "label": {"ru": "Название", "en": "Name"},
                        },
                        {
                            "field": "build_kind",
                            "label": {"ru": "Тип", "en": "Kind"},
                        },
                        {
                            "field": "components_count",
                            "label": {"ru": "Комплектующих", "en": "Components"},
                        },
                        {
                            "field": "price_total",
                            "label": {"ru": "Цена", "en": "Price"},
                        },
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
                            "column": "build_kind",
                            "widget": "choice",
                            "icon": "precision_manufacturing",
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
                            "column": "slots",
                            "widget": "build_slots",
                            "types_table": "equipment_types",
                            "items_table": "equipment_items",
                            "offers_table": "found_offers",
                            "pick_view": "equipment_items_pick",
                        },
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
                            "ru": "Проверенные продавцы",
                            "en": "Trusted sellers",
                        }
                    },
                    "title_field": "name",
                    "subtitle_fields": ["aliases"],
                    "columns": [
                        {
                            "field": "name",
                            "label": {"ru": "Название", "en": "Name"},
                        },
                        {
                            "field": "aliases",
                            "label": {"ru": "Алиасы", "en": "Aliases"},
                        },
                    ],
                    "row_tap": {
                        "kind": "open_view",
                        "view": "trusted_sellers_settings",
                    },
                    "inline_add": {
                        "field": "name",
                        "title": "Добавить продавца",
                    },
                    "empty": _empty(
                        "Нет продавцов",
                        "No sellers",
                        icon="verified",
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
                        "ru": "Продавец",
                        "en": "Seller",
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
                "slug": "s4b_settings_list",
                "table_slug": "s4b_settings",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {"ru": "S4B", "en": "S4B"},
                    },
                    "title_field": "name",
                    "subtitle_fields": ["base_url", "login"],
                    "columns": [
                        {
                            "field": "name",
                            "label": {"ru": "Название", "en": "Name"},
                        },
                        {
                            "field": "base_url",
                            "label": {"ru": "Ссылка", "en": "URL"},
                        },
                        {
                            "field": "enabled",
                            "label": {"ru": "Вкл.", "en": "On"},
                        },
                    ],
                    "row_tap": {
                        "kind": "open_form",
                        "view": "s4b_settings_form",
                    },
                    "inline_add": {
                        "field": "name",
                        "title": "Добавить S4B",
                    },
                    "empty": _empty(
                        "Нет настроек S4B",
                        "No S4B settings",
                        icon="storefront",
                    ),
                },
            },
            {
                "slug": "s4b_settings_form",
                "table_slug": "s4b_settings",
                "kind": "form",
                "ui_json": {
                    "version": 1,
                    "kind": "form",
                    "mode": "edit",
                    "title": {"ru": "S4B", "en": "S4B"},
                    "fields": [
                        {
                            "column": "name",
                            "widget": "value",
                            "icon": "storefront",
                        },
                        {
                            "column": "base_url",
                            "widget": "value",
                            "icon": "link",
                        },
                        {
                            "column": "login",
                            "widget": "value",
                            "icon": "person",
                        },
                        {
                            "column": "password",
                            "widget": "value",
                            "icon": "password",
                            "secret": True,
                        },
                        {
                            "column": "mcp_zip",
                            "widget": "file_upload",
                            "accept": ".zip",
                            "icon": "inventory_2",
                        },
                        {"column": "project_ids", "widget": "project_multiselect"},
                        {
                            "column": "enabled",
                            "widget": "pause_toggle",
                            "invert": True,
                            "pause_label": {
                                "ru": "Приостановить",
                                "en": "Pause",
                            },
                            "resume_label": {
                                "ru": "Возобновить",
                                "en": "Resume",
                            },
                        },
                    ],
                },
            },
        ],
        "tabs": [
            {
                "id": "tab_equipment",
                "title": "Подбор техники",
                "subtitle": "Каталоги, позиции, офферы и характеристики",
                "order": 10,
                "icon": "precision_manufacturing",
                "view_slug": "equipment_hub",
                "table_slug": "catalogs",
                "enabled": True,
                "default_project_bind": "local",
                "nav": {"contour": "employee", "placement": "data"},
            }
        ],
        "actions": [
            {
                "id": "index_catalog_file",
                "label": {"ru": "Индексировать", "en": "Index"},
                "kind": "content.index_tabular",
                "enabled": True,
                "params": {
                    "table_slug": "catalogs",
                    "source_column": "source_file",
                    "artifact_column": "artifact_ref",
                    "status_column": "status",
                    "row_count_column": "row_count",
                    "columns_json_column": "columns_json",
                    "error_column": "error",
                    "source_kind_column": "source_kind",
                    "expected_source_kind": "local",
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
        ],
        "materialize": [
            {
                "id": "catalog_merged_sqlite",
                "enabled": True,
                "when": ["project.created", "project.resumed", "project.sync"],
                "priority": 40,
                "source": {
                    "type": "rows",
                    "table_slug": "catalogs",
                    "filter": {"status": "ready", "paused": False},
                },
                "target": {
                    "workspace_path": "catalogs/catalog.sqlite",
                    "format": "merge_mapped_sqlite",
                    "artifact_field": "artifact_ref",
                    "map_field": "column_map",
                    "schema": list(_CATALOG_MERGE_SCHEMA),
                    "required_map_keys": ["title", "price"],
                    "provenance": {"target": "source_catalog", "from": "name"},
                },
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
                "id": "s4b_mcp_package",
                "enabled": True,
                "when": ["project.created", "project.resumed", "project.sync"],
                "priority": 65,
                "source": {
                    "type": "rows",
                    "table_slug": "s4b_settings",
                    "filter": {"enabled": True},
                },
                "target": {
                    "workspace_path": "packages/{{name}}",
                    "format": "mcp_package",
                    "field": "mcp_zip",
                },
            },
        ],
        "mcp_tools": [
            {
                "id": "equipment_catalog_list",
                "name": "equipment_catalog_list",
                "label": "List equipment catalogs",
                "description": (
                    "List ready non-paused catalog cards (Postgres SoT); "
                    "includes source_kind local|remote. Local search file is "
                    "/workspace/catalogs/catalog.sqlite; remote DSN is injected "
                    "as EQUIPMENT_CATALOG_DSN_<ROW> for future MCP live query."
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
                "id": "equipment_catalog_query",
                "name": "equipment_catalog_query",
                "label": "Query catalog SQLite",
                "description": (
                    "RO search in merged /workspace/catalogs/catalog.sqlite "
                    "(canonical columns: title, price, part_number, brand, supplier, "
                    "lead_time, source_catalog). Prefer part_number exact; else title LIKE."
                ),
                "enabled": True,
                "kind": "workspace_sqlite_query",
                "params_schema": {
                    "type": "object",
                    "properties": {
                        "part_number": {"type": "string"},
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 20},
                    },
                },
                "implementation": {
                    "workspace_path": "catalogs/catalog.sqlite",
                    "table": "rows",
                    "helper": "prodavan.application.modules.catalog_sqlite_query",
                },
            },
            {
                "id": "equipment_offers_upsert",
                "name": "equipment_offers_upsert",
                "label": "Upsert found offers",
                "description": (
                    "Agent writes candidates into found_offers (Postgres SoT) "
                    "and updates request_lines.found_count — never write offers only into Pod FS"
                ),
                "enabled": True,
                "kind": "rows_upsert",
                "implementation": {"table_slug": "found_offers"},
            },
            {
                "id": "equipment_types_list",
                "name": "equipment_types_list",
                "label": "List component types",
                "description": (
                    "List equipment_types rows (name, sort_order, fields_json schema). "
                    "Use field keys when writing equipment_items.attrs."
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
                "id": "equipment_items_list",
                "name": "equipment_items_list",
                "label": "List equipment characteristics",
                "description": (
                    "List equipment_items (name, type_id, type_name, part_number, qty, attrs)."
                ),
                "enabled": True,
                "kind": "rows_query",
                "params_schema": {"type": "object", "properties": {}},
                "implementation": {
                    "table_slug": "equipment_items",
                    "query": {"limit": 500},
                },
            },
            {
                "id": "equipment_items_upsert",
                "name": "equipment_items_upsert",
                "label": "Upsert equipment characteristics",
                "description": (
                    "Create/update equipment_items. attrs values are plain strings keyed by "
                    "equipment_types.fields_json[].key. Prefer type_id from equipment_types_list."
                ),
                "enabled": True,
                "kind": "rows_upsert",
                "implementation": {"table_slug": "equipment_items"},
            },
            {
                "id": "equipment_builds_list",
                "name": "equipment_builds_list",
                "label": "List builds",
                "description": (
                    "List equipment_builds (name, build_kind, slots, components_count, price_total)."
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
                    "Create/update equipment_builds. slots maps equipment_types.row_id → "
                    "equipment_items.row_id. price_total is sum of linked found_offers.price * qty."
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
                "id": "trusted_sellers_upsert",
                "name": "trusted_sellers_upsert",
                "label": "Upsert trusted sellers",
                "description": (
                    "Create/update trusted_sellers. aliases is a comma-separated string."
                ),
                "enabled": True,
                "kind": "rows_upsert",
                "implementation": {"table_slug": "trusted_sellers"},
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
                "id": "web_shops_upsert",
                "name": "web_shops_upsert",
                "label": "Upsert web shops",
                "description": (
                    "Create/update web_shops. cookies is a free-form string "
                    "(Cookie header / jar dump) for later automation."
                ),
                "enabled": True,
                "kind": "rows_upsert",
                "implementation": {"table_slug": "web_shops"},
            },
        ],
        "container_env": [
            {
                "env_name": "S4B_BASE_URL",
                "value_from": {
                    "table_slug": "s4b_settings",
                    "field": "base_url",
                },
                "when": [
                    "project.launch",
                    "project.sync",
                    "project.resumed",
                    "project.reload",
                ],
            },
            {
                "env_name": "S4B_LOGIN",
                "value_from": {
                    "table_slug": "s4b_settings",
                    "field": "login",
                },
                "when": [
                    "project.launch",
                    "project.sync",
                    "project.resumed",
                    "project.reload",
                ],
            },
        ],
        "container_env_secrets": [
            {
                "env_name": "S4B_PASSWORD",
                "secret_ref_from": {
                    "table_slug": "s4b_settings",
                    "field": "password",
                },
                "when": [
                    "project.launch",
                    "project.sync",
                    "project.resumed",
                    "project.reload",
                ],
            },
            {
                "foreach_rows": {
                    "table_slug": "catalogs",
                    "field": "remote_dsn",
                    "env_name_prefix": "EQUIPMENT_CATALOG_DSN_",
                    "match": {"source_kind": "remote", "status": "ready"},
                    "registry_env_name": "EQUIPMENT_REMOTE_CATALOGS",
                },
                "when": [
                    "project.launch",
                    "project.sync",
                    "project.resumed",
                    "project.reload",
                ],
            },
        ],
        "seed_rows": {"items": _equipment_type_seed_rows()},
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
