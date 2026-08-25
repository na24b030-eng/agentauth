"""Add durable one-shot demo failure controls.

Revision ID: 0002_demo_faults
Revises: 0001_initial
"""

from alembic import op

revision = "0002_demo_faults"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0001 builds current metadata for fresh hackathon installs; IF NOT EXISTS also upgrades
    # databases that already applied 0001 before this table was introduced.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS demo_faults (
            key VARCHAR(80) PRIMARY KEY,
            armed BOOLEAN NOT NULL DEFAULT FALSE,
            armed_by_user_id UUID NULL REFERENCES users(id),
            armed_at TIMESTAMPTZ NULL,
            consumed_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_demo_faults_armed ON demo_faults (armed)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS demo_faults")
