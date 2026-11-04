"""SQLAlchemy declarative base and metadata."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for every ORM model.

    ``eager_defaults`` is enabled for the whole hierarchy because the application
    is async end to end. Most tables carry ``updated_at`` as
    ``server_default=func.now(), onupdate=func.now()``; after an ORM UPDATE of
    such a row SQLAlchemy deliberately leaves that attribute *expired* so the
    database-computed value can be re-read on next access. Reading it later —
    serialising the row in a ``*_public()`` helper, for instance — then triggers a
    lazy SELECT from a plain (non-greenlet) call site, and asyncpg raises
    ``MissingGreenlet`` instead of returning a timestamp, which surfaces as a 500
    on every endpoint that returns a just-updated row.

    With ``eager_defaults`` the UPDATE uses ``RETURNING`` so the new value is
    fetched as part of the flush and nothing is left expired. On servers without
    ``RETURNING`` the mapper falls back to the SELECT that used to happen
    lazily, only now it happens while the greenlet is still alive.
    """

    __mapper_args__ = {"eager_defaults": True}
