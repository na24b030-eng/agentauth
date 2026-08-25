"""Rename the model-control label for the Gemini runtime.

Revision ID: 0004_gemini_agent_runtime
Revises: 0003_checkout_fixture_label
"""

from alembic import op

revision = "0004_gemini_agent_runtime"
down_revision = "0003_checkout_fixture_label"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'agent_runs' AND column_name = 'reasoning_effort'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'agent_runs' AND column_name = 'thinking_level'
            ) THEN
                ALTER TABLE agent_runs RENAME COLUMN reasoning_effort TO thinking_level;
            END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'agent_runs' AND column_name = 'thinking_level'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'agent_runs' AND column_name = 'reasoning_effort'
            ) THEN
                ALTER TABLE agent_runs RENAME COLUMN thinking_level TO reasoning_effort;
            END IF;
        END $$
        """
    )
