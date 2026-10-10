"""Лимит шагов (turns) на один ход агента — настройка чата.

`agent_sessions.max_turns`:
- NULL  — без ограничений (поведение по умолчанию);
- N > 0 — не больше N шагов модели за один ход, после чего рантайм
  останавливается с `done.reason = "max_turns"`, а чат показывает
  «Лимит шагов закончен, попросите агента продолжить».

Хранится на сессии, а не на проекте: это настройка конкретного диалога
(«сколько может длиться непрерывная сессия»), и менять её можно на лету —
значение уходит в теле каждого send, перезапуск пода не нужен.

Рантайм поддерживает `max_turns` в теле send штатно (SendRequestSchema →
`turnLimitsFromConfig`: `sendMaxTurns ?? config.runtime?.max_turns ?? default`),
поэтому схема схемы `.prodavan/config.yaml` не меняется.
"""

import sqlalchemy as sa

from alembic import op

revision = "2026100529"
down_revision = "2026100528"


def upgrade() -> None:
    op.add_column(
        "agent_sessions",
        sa.Column("max_turns", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_sessions", "max_turns")
