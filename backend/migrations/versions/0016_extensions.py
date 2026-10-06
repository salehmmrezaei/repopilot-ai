"""Repository refresh snapshots and encrypted GitHub OAuth identities."""

import sqlalchemy as sa
from alembic import op

revision = "0016_extensions"
down_revision = "0015_repair_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("import_jobs", sa.Column("commit_sha", sa.String(40), nullable=True))
    op.execute(
        "UPDATE import_jobs SET commit_sha = repositories.last_commit_sha "
        "FROM repositories WHERE import_jobs.repository_id = repositories.id "
        "AND import_jobs.is_current AND import_jobs.status = 'completed'"
    )
    op.create_table(
        "github_accounts",
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("github_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("login", sa.String(100), nullable=False),
        sa.Column("encrypted_token", sa.Text()),
        sa.Column("private_access", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "oauth_states",
        sa.Column("state_hash", sa.String(64), primary_key=True),
        sa.Column("browser_hash", sa.String(64), nullable=False),
        sa.Column("session_hash", sa.String(64)),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("encrypted_verifier", sa.Text(), nullable=False),
        sa.Column("private_access", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "pull_reviews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "repository_id",
            sa.Uuid(),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("request_key", sa.Uuid(), nullable=False),
        sa.Column("pull_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("base_sha", sa.String(40)),
        sa.Column("head_sha", sa.String(40)),
        sa.Column("result", sa.JSON()),
        sa.Column("error_code", sa.String(80)),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("estimated_cost_usd", sa.Float()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("repository_id", "request_key", name="uq_review_request"),
    )


def downgrade() -> None:
    op.drop_table("pull_reviews")
    op.drop_table("oauth_states")
    op.drop_table("github_accounts")
    op.drop_column("import_jobs", "commit_sha")
