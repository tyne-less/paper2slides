"""Indexes for the time-window analytics queries in sql/analytics.

Replaces stage_runs(stage, started_at), which no query used, with stage_runs(started_at), and
adds finished_at indexes on job_attempts and jobs. Measurements: sql/analytics/RESULTS.md.
Built CONCURRENTLY so that existing tables keep accepting writes during the migration.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-20
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_stage_runs_started_at",
            "stage_runs",
            ["started_at"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.create_index(
            "ix_job_attempts_finished_at",
            "job_attempts",
            ["finished_at"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.create_index(
            "ix_jobs_finished_at",
            "jobs",
            ["finished_at"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.drop_index(
            "ix_stage_runs_stage_started_at",
            table_name="stage_runs",
            postgresql_concurrently=True,
            if_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_stage_runs_stage_started_at",
            "stage_runs",
            ["stage", "started_at"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.drop_index("ix_jobs_finished_at", table_name="jobs", postgresql_concurrently=True)
        op.drop_index(
            "ix_job_attempts_finished_at", table_name="job_attempts", postgresql_concurrently=True
        )
        op.drop_index(
            "ix_stage_runs_started_at", table_name="stage_runs", postgresql_concurrently=True
        )
