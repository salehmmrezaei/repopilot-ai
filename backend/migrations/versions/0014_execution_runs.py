"""Durable explicit sandbox execution requests."""

import sqlalchemy as sa
from alembic import op

revision = "0014_execution_runs"
down_revision = "0013_patch_proposals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "execution_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "agent_run_id",
            sa.Uuid(),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("request_key", sa.Uuid(), nullable=False),
        sa.Column("profile", sa.String(20), nullable=False),
        sa.Column("patch_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), server_default="queued", nullable=False),
        sa.Column("stage", sa.String(40), server_default="queued", nullable=False),
        sa.Column("result", sa.JSON()),
        sa.Column("error", sa.String(300)),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_dispatched_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("agent_run_id", "request_key", name="uq_execution_request"),
        sa.CheckConstraint(
            "status IN ('queued','running','completed','failed','cancelled')",
            name="ck_execution_status",
        ),
        sa.CheckConstraint(
            "profile IN ('auto','python','javascript')", name="ck_execution_profile"
        ),
    )
    op.create_index(
        "uq_execution_active",
        "execution_runs",
        ["agent_run_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running')"),
    )
    op.create_index("ix_execution_dispatch", "execution_runs", ["status", "created_at"])
    op.add_column("agent_runs", sa.Column("execution_feedback", sa.JSON()))


def downgrade() -> None:
    op.drop_column("agent_runs", "execution_feedback")
    op.drop_table("execution_runs")
