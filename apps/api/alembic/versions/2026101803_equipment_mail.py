"""WAVE16: почта — профили отправителей (SMTP), контакты, исходящие письма.

mail_senders (профили: SMTP host/port/user/password secret_ref/TLS/default),
mail_contacts (адресная книга), mail_outbox (лог: queued/sent/failed).
Действие equipment.mail_send — отправка строки mail_outbox через профиль.
"""

from alembic import op

from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026101803"
down_revision = "2026101802"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
