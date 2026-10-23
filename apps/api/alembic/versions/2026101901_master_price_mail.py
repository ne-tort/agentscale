"""WAVE16.3: отправка мастер-прайса почтой со страницы Мастер-прайс.

- mail_senders.smtp_ssl: implicit TLS (SMTPS, 465) — так слушает Яндекс.
- Таблица master_price_mail: получатель/тема/комментарий для страницы.
- Действие master_price_send (kind equipment.master_price_mail) — тот же
  xlsx, что и «Скачать», уходит вложением через профиль «price».
- Сид-строка mail_sender_price: ящик price@itvzlet.ru (пароль НЕ в сидах —
  secret_ref проставляется в контуре через upload secret).
"""

from alembic import op

from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026101901"
down_revision = "2026101803"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
