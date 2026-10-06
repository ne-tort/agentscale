"""Промпты «Подбора техники» по умолчанию: AGENTS.md в корне workspace
(авто-подхват claw-агентом) + правила модуля в prompts/equipment/*.md.
Сид-строки equipment_prompts (template rows): вставляются во все инстансы
модуля, тела пользовательских правок не перезаписывают.

Seeds only — no schema changes; refresh platform + instance copies via
upsert_product_modules.
"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100511"
down_revision = "2026100510"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
