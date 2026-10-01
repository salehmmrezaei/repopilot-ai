"""Bounded repair controller.

Revision ID: 0015_repair_runs
Revises: 0014_execution_runs
"""

import sqlalchemy as sa
from alembic import op

revision = "0015_repair_runs"
down_revision = "0014_execution_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "repair_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "root_agent_id",
            sa.Uuid(),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("request_key", sa.Uuid(), nullable=False),
        sa.Column("profile", sa.String(20), nullable=False),
        sa.Column("max_revisions", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("current_agent_id", sa.Uuid(), nullable=False),
        sa.Column("current_execution_id", sa.Uuid()),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(100)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("root_agent_id", "request_key", name="uq_repair_request"),
        sa.CheckConstraint("max_revisions BETWEEN 1 AND 2", name="ck_repair_budget"),
        sa.CheckConstraint("revision BETWEEN 0 AND max_revisions", name="ck_repair_revision"),
        sa.CheckConstraint(
            "status IN ('running','passed','exhausted','stopped','cancelled')",
            name="ck_repair_status",
        ),
        sa.CheckConstraint("profile IN ('auto','python','javascript')", name="ck_repair_profile"),
    )
    op.create_index(
        "uq_repair_active",
        "repair_runs",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
        sqlite_where=sa.text("status = 'running'"),
    )

    op.create_index("ix_repair_runs_current_agent_id", "repair_runs", ["current_agent_id"])
    op.create_index("ix_repair_runs_current_execution_id", "repair_runs", ["current_execution_id"])


def downgrade() -> None:
    op.drop_table("repair_runs")
