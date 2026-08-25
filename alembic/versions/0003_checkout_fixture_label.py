"""Label Checkouts changed by a developer webhook fixture.

Revision ID: 0003_checkout_fixture_label
Revises: 0002_demo_faults
"""

from alembic import op

revision = "0003_checkout_fixture_label"
down_revision = "0002_demo_faults"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE checkouts ADD COLUMN IF NOT EXISTS "
        "test_fixture_applied BOOLEAN NOT NULL DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE checkouts DROP COLUMN IF EXISTS test_fixture_applied")
