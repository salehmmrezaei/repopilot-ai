"""Durable bounded read-only investigations and per-call usage."""

from alembic import op

revision = "0012_investigations"
down_revision = "0011_usage_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE agent_runs (
	id UUID NOT NULL,
	conversation_id UUID NOT NULL,
	request_key UUID NOT NULL,
	question TEXT NOT NULL,
	source_index_id UUID NOT NULL,
	commit_sha VARCHAR(40) NOT NULL,
	config_hash VARCHAR(64) NOT NULL,
	model VARCHAR(100) NOT NULL,
	status VARCHAR(20) DEFAULT 'queued' NOT NULL,
	result JSON,
	error_code VARCHAR(80),
	error_message VARCHAR(300),
	lease_token UUID,
	lease_expires_at TIMESTAMP WITH TIME ZONE,
	last_dispatched_at TIMESTAMP WITH TIME ZONE,
	started_at TIMESTAMP WITH TIME ZONE,
	finished_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_agent_request UNIQUE (conversation_id, request_key),
	CONSTRAINT ck_agent_status CHECK (
        status IN ('queued','running','completed','failed','cancelled')),
	FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE
)

""")
    op.execute("""CREATE INDEX ix_agent_dispatch ON agent_runs (status, created_at)""")
    op.execute(
        """CREATE UNIQUE INDEX uq_agent_active ON agent_runs (conversation_id)
        WHERE status IN ('queued','running')"""
    )
    op.execute("""
CREATE TABLE agent_events (
	run_id UUID NOT NULL,
	sequence INTEGER NOT NULL,
	kind VARCHAR(30) NOT NULL,
	summary VARCHAR(1000) NOT NULL,
	tool VARCHAR(30),
	duration_ms INTEGER,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (run_id, sequence),
	CONSTRAINT ck_agent_event_sequence CHECK (sequence >= 1 AND sequence <= 32),
	FOREIGN KEY(run_id) REFERENCES agent_runs (id) ON DELETE CASCADE
)

""")
    op.execute("""
CREATE TABLE agent_calls (
	run_id UUID NOT NULL,
	step INTEGER NOT NULL,
	input_rate NUMERIC(20, 8) NOT NULL,
	output_rate NUMERIC(20, 8) NOT NULL,
	input_tokens INTEGER,
	output_tokens INTEGER,
	cost_usd NUMERIC(24, 12),
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	finished_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (run_id, step),
	CONSTRAINT ck_agent_step CHECK (step >= 1 AND step <= 5),
	CONSTRAINT ck_agent_rates CHECK (input_rate >= 0 AND output_rate >= 0),
	CONSTRAINT ck_agent_usage CHECK ((input_tokens IS NULL
        AND output_tokens IS NULL
        AND cost_usd IS NULL)
        OR (input_tokens IS NOT NULL
        AND output_tokens IS NOT NULL
        AND cost_usd IS NOT NULL
        AND input_tokens >= 0
        AND output_tokens >= 0
        AND cost_usd >= 0)),
	FOREIGN KEY(run_id) REFERENCES agent_runs (id) ON DELETE CASCADE
)

""")


def downgrade() -> None:
    op.drop_table("agent_calls")
    op.drop_table("agent_events")
    op.drop_table("agent_runs")
