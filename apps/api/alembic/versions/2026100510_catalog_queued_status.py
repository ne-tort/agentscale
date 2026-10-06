"""Каталоги: статус «queued» (В очереди) — индексация-действие больше не
пишет «В процессе» до реального старта задачи; отдельная celery-очередь
`indexing` (deployment agentscale-celery-indexer) не блокирует другие
каталоги и sweep-задачи. poll_while/акценты/условия пикеров обновлены.

Meta documents (tables/columns/views) are SoT-seeded — refresh platform +
instance copies via upsert_product_modules.
"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100510"
down_revision = "2026100509"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
