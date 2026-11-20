"""Initial schema: documents, jobs, attempts, stage runs, job events.

Revision ID: 0001
Revises:
Create Date: 2026-11-20
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("pipeline_version", sa.Text(), nullable=False),
        sa.Column(
            "options",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("dedup_key", sa.String(64), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("run_after", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("lease_owner", sa.Text()),
        sa.Column("lease_expires_at", TZ),
        sa.Column("fence_token", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error_type", sa.Text()),
        sa.Column("last_error_message", sa.Text()),
        sa.Column("result_prefix", sa.Text()),
        sa.Column("request_id", sa.Text()),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", TZ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'dead')", name="ck_jobs_status"
        ),
    )
    op.create_index(
        "uq_jobs_dedup_key_active",
        "jobs",
        ["dedup_key"],
        unique=True,
        postgresql_where=sa.text("status <> 'dead'"),
    )
    op.create_index(
        "ix_jobs_queued_run_after",
        "jobs",
        ["run_after"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "ix_jobs_running_lease_expires_at",
        "jobs",
        ["lease_expires_at"],
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index("ix_jobs_document_id", "jobs", ["document_id"])
    op.create_index("ix_jobs_created_at", "jobs", ["created_at"])

    op.create_table(
        "job_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.Text(), nullable=False),
        sa.Column("fence_token", sa.BigInteger(), nullable=False),
        sa.Column("started_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", TZ),
        sa.Column("outcome", sa.Text()),
        sa.Column("error_type", sa.Text()),
        sa.Column("error_message", sa.Text()),
        sa.Column("traceback_tail", sa.Text()),
        sa.UniqueConstraint("job_id", "attempt_no", name="uq_job_attempts_job_id_attempt_no"),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('succeeded', 'failed', 'timeout', 'lost', 'released')",
            name="ck_job_attempts_outcome",
        ),
    )

    op.create_table(
        "stage_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("attempt_id", sa.Uuid(), sa.ForeignKey("job_attempts.id"), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("started_at", TZ, nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_type", sa.Text()),
        sa.CheckConstraint("status IN ('succeeded', 'failed')", name="ck_stage_runs_status"),
    )
    op.create_index("ix_stage_runs_stage_started_at", "stage_runs", ["stage", "started_at"])
    op.create_index("ix_stage_runs_attempt_id", "stage_runs", ["attempt_id"])

    op.create_table(
        "job_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), sa.ForeignKey("job_attempts.id")),
        sa.Column("from_status", sa.Text()),
        sa.Column("to_status", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_job_events_job_id_id", "job_events", ["job_id", "id"])


def downgrade() -> None:
    op.drop_table("job_events")
    op.drop_table("stage_runs")
    op.drop_table("job_attempts")
    op.drop_table("jobs")
    op.drop_table("documents")
