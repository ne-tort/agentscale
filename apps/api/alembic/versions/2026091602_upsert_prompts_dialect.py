"""Re-upsert product modules after prompts seed dialect fix (META-P1c).

The prompt_paths materialize rule used the deprecated single-brace
`{active_profile_id}` placeholder in its `source.filter.profile_id`. It now
uses the canonical `{{active_profile_id}}` Mustache dialect. The validator
rejects single-brace placeholders, so the DB meta must be refreshed from
seeds or materialize validation will fail on the stale platform instance.
"""

from alembic import op

from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026091602"
down_revision = "2026091601"
branch_labels = None
depends_on = None


def upgrade() -> None:
    upsert_product_modules(op.get_bind())


def downgrade() -> None:
    pass
