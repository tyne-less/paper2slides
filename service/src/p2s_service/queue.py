"""Postgres-backed job queue. Every job status change goes through this module and is
written together with its job_events row in one transaction."""

import hashlib
import json
import random
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .db.models import Document, Job, JobAttempt, JobEvent, StageRun
from .pipeline_adapter import PIPELINE_VERSION, StageTiming

MAX_ERROR_MESSAGE_CHARS = 2000
MAX_TRACEBACK_CHARS = 4000
# Attempt outcomes that count towards max_attempts. "released" (graceful shutdown) does not.
FAILED_OUTCOMES = ("failed", "timeout", "lost")


@dataclass(frozen=True)
class RetryPolicy:
    base_seconds: float
    max_seconds: float

    def delay(self, failures: int) -> float:
        """Exponential backoff with jitter: a value in [d/2, d], d = min(max, base * 2^(n-1))."""
        capped = min(self.max_seconds, self.base_seconds * 2 ** (failures - 1))
        return capped / 2 + random.uniform(0, capped / 2)


@dataclass(frozen=True)
class NewDocument:
    sha256: str
    size_bytes: int
    page_count: int
    original_filename: str
    storage_key: str


@dataclass(frozen=True)
class ClaimedJob:
    job_id: uuid.UUID
    attempt_id: uuid.UUID
    attempt_no: int
    fence_token: int
    worker_id: str
    dedup_key: str
    options: dict
    request_id: str | None
    document_sha256: str
    document_key: str


def compute_dedup_key(sha256: str, pipeline_version: str, options: dict) -> str:
    payload = json.dumps(
        {"sha256": sha256, "pipeline_version": pipeline_version, "options": options},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _event(
    session: Session,
    job_id: uuid.UUID,
    from_status: str | None,
    to_status: str,
    actor: str,
    reason: str | None = None,
    attempt_id: uuid.UUID | None = None,
) -> None:
    session.add(
        JobEvent(
            job_id=job_id,
            attempt_id=attempt_id,
            from_status=from_status,
            to_status=to_status,
            actor=actor,
            reason=reason,
        )
    )


def submit(
    session: Session,
    document: NewDocument,
    options: dict,
    max_attempts: int,
    request_id: str | None,
) -> tuple[Job, bool]:
    """Return (job, created). An identical active job is returned instead of a new one."""
    session.execute(
        pg_insert(Document)
        .values(id=uuid.uuid4(), **document.__dict__)
        .on_conflict_do_nothing(index_elements=["sha256"])
    )
    document_id = session.scalar(select(Document.id).where(Document.sha256 == document.sha256))

    dedup_key = compute_dedup_key(document.sha256, PIPELINE_VERSION, options)
    job_id = session.scalar(
        pg_insert(Job)
        .values(
            id=uuid.uuid4(),
            document_id=document_id,
            pipeline_version=PIPELINE_VERSION,
            options=options,
            dedup_key=dedup_key,
            status="queued",
            max_attempts=max_attempts,
            request_id=request_id,
        )
        # The predicate must be literal SQL: a bound parameter (status != $1) no longer matches
        # the partial unique index once psycopg prepares the statement (after 5 executions).
        .on_conflict_do_nothing(index_elements=["dedup_key"], index_where=text("status <> 'dead'"))
        .returning(Job.id)
    )
    if job_id is None:
        job = session.scalars(
            select(Job).where(Job.dedup_key == dedup_key, Job.status != "dead")
        ).one()
        session.commit()
        return job, False

    _event(session, job_id, None, "queued", actor="api", reason="submitted")
    session.commit()
    return session.get(Job, job_id), True


def claim(session: Session, worker_id: str, lease_seconds: float) -> ClaimedJob | None:
    job = session.scalars(
        select(Job)
        .where(Job.status == "queued", Job.run_after <= func.now())
        .order_by(Job.run_after)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).one_or_none()
    if job is None:
        session.rollback()
        return None

    job.status = "running"
    job.attempts += 1
    job.fence_token += 1
    job.lease_owner = worker_id
    job.lease_expires_at = func.now() + timedelta(seconds=lease_seconds)
    job.updated_at = func.now()
    attempt = JobAttempt(
        id=uuid.uuid4(),
        job_id=job.id,
        attempt_no=job.attempts,
        worker_id=worker_id,
        fence_token=job.fence_token,
    )
    session.add(attempt)
    session.flush()
    _event(session, job.id, "queued", "running", f"worker:{worker_id}", "claimed", attempt.id)
    document = session.get(Document, job.document_id)
    claimed = ClaimedJob(
        job_id=job.id,
        attempt_id=attempt.id,
        attempt_no=attempt.attempt_no,
        fence_token=attempt.fence_token,
        worker_id=worker_id,
        dedup_key=job.dedup_key,
        options=dict(job.options),
        request_id=job.request_id,
        document_sha256=document.sha256,
        document_key=document.storage_key,
    )
    session.commit()
    return claimed


def _finish_attempt(
    session: Session,
    attempt_id: uuid.UUID,
    outcome: str,
    timings: list[StageTiming],
    error_type: str | None = None,
    error_message: str | None = None,
    traceback_tail: str | None = None,
) -> None:
    session.execute(
        update(JobAttempt)
        .where(JobAttempt.id == attempt_id)
        .values(
            outcome=outcome,
            finished_at=func.now(),
            error_type=error_type,
            error_message=error_message[:MAX_ERROR_MESSAGE_CHARS] if error_message else None,
            traceback_tail=traceback_tail[-MAX_TRACEBACK_CHARS:] if traceback_tail else None,
        )
    )
    session.add_all(
        StageRun(
            attempt_id=attempt_id,
            stage=t.stage,
            started_at=t.started_at,
            duration_ms=t.duration_ms,
            status=t.status,
            error_type=t.error_type,
        )
        for t in timings
    )


def _owned_job(session: Session, claimed: ClaimedJob) -> Job | None:
    """Lock and return the job only if this attempt still holds it (running, same fence token)."""
    return session.scalars(
        select(Job)
        .where(
            Job.id == claimed.job_id,
            Job.status == "running",
            Job.fence_token == claimed.fence_token,
        )
        .with_for_update()
    ).one_or_none()


def abandon(session: Session, claimed: ClaimedJob, timings: list[StageTiming]) -> None:
    session.rollback()
    _finish_attempt(
        session,
        claimed.attempt_id,
        "lost",
        timings,
        error_type="LeaseLost",
        error_message="job no longer owned by this attempt",
    )
    session.commit()


def _release_lease(job: Job) -> None:
    job.lease_owner = None
    job.lease_expires_at = None
    job.updated_at = func.now()


def _requeue_or_bury(
    session: Session,
    job: Job,
    attempt_id: uuid.UUID,
    actor: str,
    error_type: str,
    error_message: str,
    retryable: bool,
    retry: RetryPolicy,
) -> str:
    """After a failed attempt has been recorded: schedule a retry, or mark the job dead."""
    session.flush()
    failures = session.scalar(
        select(func.count())
        .select_from(JobAttempt)
        .where(JobAttempt.job_id == job.id, JobAttempt.outcome.in_(FAILED_OUTCOMES))
    )
    _release_lease(job)
    job.last_error_type = error_type
    job.last_error_message = error_message[:MAX_ERROR_MESSAGE_CHARS]
    if retryable and failures < job.max_attempts:
        delay = retry.delay(failures)
        job.status = "queued"
        job.run_after = func.now() + timedelta(seconds=delay)
        reason = f"retry in {delay:.1f}s after {error_type} ({failures}/{job.max_attempts})"
    else:
        job.status = "dead"
        job.finished_at = func.now()
        reason = error_type if not retryable else f"{error_type}; attempts exhausted"
    _event(session, job.id, "running", job.status, actor, reason, attempt_id)
    return job.status


def heartbeat(session: Session, claimed: ClaimedJob, lease_seconds: float) -> bool:
    """Extend the lease. False means this attempt no longer owns the job."""
    row = session.execute(
        update(Job)
        .where(
            Job.id == claimed.job_id,
            Job.status == "running",
            Job.fence_token == claimed.fence_token,
        )
        .values(lease_expires_at=func.now() + timedelta(seconds=lease_seconds))
        .returning(Job.id)
        .execution_options(synchronize_session=False)
    ).one_or_none()
    session.commit()
    return row is not None


def complete(
    session: Session, claimed: ClaimedJob, result_prefix: str, timings: list[StageTiming]
) -> bool:
    job = _owned_job(session, claimed)
    if job is None:
        abandon(session, claimed, timings)
        return False
    _release_lease(job)
    job.status = "succeeded"
    job.result_prefix = result_prefix
    job.finished_at = func.now()
    job.last_error_type = None
    job.last_error_message = None
    _finish_attempt(session, claimed.attempt_id, "succeeded", timings)
    _event(
        session,
        claimed.job_id,
        "running",
        "succeeded",
        f"worker:{claimed.worker_id}",
        attempt_id=claimed.attempt_id,
    )
    session.commit()
    return True


def fail(
    session: Session,
    claimed: ClaimedJob,
    *,
    outcome: str,
    error_type: str,
    error_message: str,
    traceback_tail: str | None,
    timings: list[StageTiming],
    retryable: bool,
    retry: RetryPolicy,
) -> str | None:
    """Record a failed attempt. Returns the new job status, or None if the job was lost."""
    job = _owned_job(session, claimed)
    if job is None:
        abandon(session, claimed, timings)
        return None
    _finish_attempt(
        session, claimed.attempt_id, outcome, timings, error_type, error_message, traceback_tail
    )
    status = _requeue_or_bury(
        session,
        job,
        claimed.attempt_id,
        f"worker:{claimed.worker_id}",
        error_type,
        error_message,
        retryable,
        retry,
    )
    session.commit()
    return status


def release(session: Session, claimed: ClaimedJob, timings: list[StageTiming]) -> bool:
    """Give an unfinished job back to the queue without counting a failure (shutdown)."""
    job = _owned_job(session, claimed)
    if job is None:
        abandon(session, claimed, timings)
        return False
    _release_lease(job)
    job.status = "queued"
    job.run_after = func.now()
    _finish_attempt(session, claimed.attempt_id, "released", timings)
    _event(
        session,
        job.id,
        "running",
        "queued",
        f"worker:{claimed.worker_id}",
        "released on shutdown",
        claimed.attempt_id,
    )
    session.commit()
    return True


def reap(session: Session, actor: str, retry: RetryPolicy, limit: int = 100) -> int:
    """Requeue (or bury) running jobs whose lease expired, e.g. because the worker was killed."""
    jobs = session.scalars(
        select(Job)
        .where(Job.status == "running", Job.lease_expires_at < func.now())
        .order_by(Job.lease_expires_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    for job in jobs:
        attempt_id = session.scalar(
            select(JobAttempt.id).where(
                JobAttempt.job_id == job.id, JobAttempt.fence_token == job.fence_token
            )
        )
        message = f"lease held by {job.lease_owner} expired"
        _finish_attempt(session, attempt_id, "lost", [], "LeaseExpired", message)
        _requeue_or_bury(session, job, attempt_id, actor, "LeaseExpired", message, True, retry)
    session.commit()
    return len(jobs)
