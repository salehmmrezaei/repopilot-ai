"""Add proposal mode while preserving existing investigation runs."""

import sqlalchemy as sa
from alembic import op

revision = "0013_patch_proposals"
down_revision = "0012_investigations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs", sa.Column("mode", sa.String(20), nullable=False, server_default="investigate")
    )
    op.create_check_constraint("ck_agent_mode", "agent_runs", "mode IN ('investigate','propose')")


def downgrade() -> None:
    # Drop proposal results before older application versions can misinterpret them.
    op.execute("DELETE FROM agent_runs WHERE mode = 'propose'")
    op.drop_constraint("ck_agent_mode", "agent_runs", type_="check")
    op.drop_column("agent_runs", "mode")
