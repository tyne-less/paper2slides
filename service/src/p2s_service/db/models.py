import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JOB_STATUSES = ("queued", "running", "succeeded", "dead")
ATTEMPT_OUTCOMES = ("succeeded", "failed", "timeout", "lost", "released")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    page_count: Mapped[int] = mapped_column(Integer)
    original_filename: Mapped[str] = mapped_column(Text)
    storage_key: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(_in("status", JOB_STATUSES), name="ck_jobs_status"),
        # One active job per (file, pipeline version, options); a dead job can be resubmitted.
        Index(
            "uq_jobs_dedup_key_active",
            "dedup_key",
            unique=True,
            postgresql_where=text("status <> 'dead'"),
        ),
        Index("ix_jobs_queued_run_after", "run_after", postgresql_where=text("status = 'queued'")),
        Index(
            "ix_jobs_running_lease_expires_at",
            "lease_expires_at",
            postgresql_where=text("status = 'running'"),
        ),
        Index("ix_jobs_document_id", "document_id"),
        Index("ix_jobs_created_at", "created_at"),
        Index("ix_jobs_finished_at", "finished_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"))
    pipeline_version: Mapped[str] = mapped_column(Text)
    options: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    dedup_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Incremented on every claim; completion must present the current value.
    fence_token: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    last_error_type: Mapped[str | None] = mapped_column(Text)
    last_error_message: Mapped[str | None] = mapped_column(Text)
    result_prefix: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobAttempt(Base):
    __tablename__ = "job_attempts"
    __table_args__ = (
        UniqueConstraint("job_id", "attempt_no", name="uq_job_attempts_job_id_attempt_no"),
        Index("ix_job_attempts_finished_at", "finished_at"),
        CheckConstraint(
            f"outcome IS NULL OR {_in('outcome', ATTEMPT_OUTCOMES)}", name="ck_job_attempts_outcome"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"))
    attempt_no: Mapped[int] = mapped_column(Integer)
    worker_id: Mapped[str] = mapped_column(Text)
    fence_token: Mapped[int] = mapped_column(BigInteger)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(Text)
    error_type: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    traceback_tail: Mapped[str | None] = mapped_column(Text)


class StageRun(Base):
    __tablename__ = "stage_runs"
    __table_args__ = (
        CheckConstraint("status IN ('succeeded', 'failed')", name="ck_stage_runs_status"),
        Index("ix_stage_runs_started_at", "started_at"),
        Index("ix_stage_runs_attempt_id", "attempt_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    attempt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_attempts.id"))
    stage: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    error_type: Mapped[str | None] = mapped_column(Text)


class JobEvent(Base):
    """Append-only audit trail; one row per status change, written in the same transaction."""

    __tablename__ = "job_events"
    __table_args__ = (Index("ix_job_events_job_id_id", "job_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"))
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job_attempts.id"))
    from_status: Mapped[str | None] = mapped_column(Text)
    to_status: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StoredObject(Base):
    """Object storage in Postgres, for deployments without an object store (P2S_STORAGE_BACKEND
    =postgres). Uploads are capped by max_upload_bytes, so values stay small enough for bytea."""

    __tablename__ = "stored_objects"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    content_type: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
