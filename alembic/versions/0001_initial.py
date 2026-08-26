"""Initial TrustCart system of record.

Revision ID: 0001_initial
Revises: none
"""

from trustcart import models  # noqa: F401
from trustcart.db import Base

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    # Replay protection is valuable across service restarts but need not enter WAL.
    op.execute("ALTER TABLE proof_nonces SET UNLOGGED")


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
