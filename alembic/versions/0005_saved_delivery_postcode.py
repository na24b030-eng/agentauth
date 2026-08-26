"""Add the demo buyer's saved delivery postcode.

Revision ID: 0005_saved_delivery_postcode
Revises: 0004_gemini_agent_runtime
"""

from alembic import op

revision = "0005_saved_delivery_postcode"
down_revision = "0004_gemini_agent_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
        "default_postcode VARCHAR(6) NOT NULL DEFAULT '560001'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS default_postcode")
