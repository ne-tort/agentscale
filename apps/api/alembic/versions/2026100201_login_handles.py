"""Login handles: «{company}@agentscale.local» / «{employee}@{company}.local».

- companies.login_slug: уникальный слаг имени (login handle = {slug}@agentscale.local);
  бэкфилл из name (транслит) с суффиксом при коллизии.
- unique index на lower(companies.name) — имя компании уникально.
- employees.login (64→200): хранит ПОЛНЫЙ handle {local}@{company}.local;
  бэкфилл: local = старый login, компания = первое (по created_at) membership,
  без membership — {login}@unknown.local.

Идентификаторы входа для существующих KC-юзеров меняются только для НОВЫХ
компаний/сотрудников (свежие dev/prod БД пусты); KC username существующих
пользователей не трогаем — soft-bind в EntitlementService матчит и старый,
и новый формат.
"""

import sqlalchemy as sa

from alembic import op

revision = "2026100201"
down_revision = "2026100101"


def _translit(value: str) -> str:
    table = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
    import re

    translit = "".join(table.get(ch, ch) for ch in (value or "").strip().lower())
    slug = re.sub(r"[^a-z0-9._-]+", "-", translit).strip("-") or "company"
    return slug


def upgrade() -> None:
    op.add_column("companies", sa.Column("login_slug", sa.String(64), nullable=True))
    op.create_index("ix_companies_login_slug", "companies", ["login_slug"], unique=True)

    conn = op.get_bind()
    # Backfill login_slug from name (dedupe with -N suffix).
    rows = conn.execute(sa.text("SELECT id, name FROM companies ORDER BY created_at")).fetchall()
    taken: set[str] = set()
    for cid, name in rows:
        base = _translit(name)[:64]
        slug = base
        n = 2
        while slug in taken or not slug:
            suffix = f"-{n}"
            slug = base[: 64 - len(suffix)] + suffix
            n += 1
        taken.add(slug)
        conn.execute(
            sa.text("UPDATE companies SET login_slug = :slug WHERE id = :cid"),
            {"slug": slug, "cid": cid},
        )
    op.alter_column("companies", "login_slug", nullable=False)

    op.create_index(
        "uq_companies_name_lower", "companies", [sa.text("lower(name)")], unique=True
    )

    op.alter_column("employees", "login", type_=sa.String(200), existing_type=sa.String(64))

    # Backfill employees.login → full handle (company = first membership).
    emps = conn.execute(sa.text("SELECT id, login FROM employees")).fetchall()
    for eid, login in emps:
        if not login or "@" in login:
            continue
        comp = conn.execute(
            sa.text(
                "SELECT c.login_slug FROM memberships m "
                "JOIN companies c ON c.id = m.company_id "
                "WHERE m.employee_id = :eid ORDER BY m.created_at LIMIT 1"
            ),
            {"eid": eid},
        ).fetchone()
        slug = comp[0] if comp and comp[0] else "unknown"
        handle = f"{login}@{slug}.local"[:200]
        conn.execute(
            sa.text("UPDATE employees SET login = :h WHERE id = :eid"),
            {"h": handle, "eid": eid},
        )


def downgrade() -> None:
    # Handle → local part back (strip @company.local), drop slug/index.
    conn = op.get_bind()
    emps = conn.execute(sa.text("SELECT id, login FROM employees")).fetchall()
    for eid, login in emps:
        if login and "@" in login:
            local = login.split("@", 1)[0]
            conn.execute(
                sa.text("UPDATE employees SET login = :l WHERE id = :eid"),
                {"l": local, "eid": eid},
            )
    op.alter_column("employees", "login", type_=sa.String(64), existing_type=sa.String(200))
    op.drop_index("uq_companies_name_lower", table_name="companies")
    op.alter_column("companies", "login_slug", nullable=True)
    op.drop_index("ix_companies_login_slug", table_name="companies")
    op.drop_column("companies", "login_slug")
