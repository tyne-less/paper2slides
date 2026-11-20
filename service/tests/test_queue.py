import threading

import fake_pipelines
from sqlalchemy import select, update

from conftest import make_pdf
from p2s_service import queue
from p2s_service.db.models import Job, JobAttempt, JobEvent, StageRun


def submit_fake(session_factory, n: int) -> list:
    ids = []
    for i in range(n):
        with session_factory() as session:
            job, _ = queue.submit(
                session,
                queue.NewDocument(f"{i:064x}", 1, 1, f"f{i}.pdf", f"documents/{i}.pdf"),
                options={},
                max_attempts=3,
                request_id=None,
            )
            ids.append(job.id)
    return ids


def test_concurrent_workers_never_claim_the_same_job(session_factory):
    job_ids = submit_fake(session_factory, 40)
    claimed: list = []
    lock = threading.Lock()
    barrier = threading.Barrier(6)

    def drain(worker_id: str):
        barrier.wait()
        while True:
            with session_factory() as session:
                job = queue.claim(session, worker_id, lease_seconds=60)
            if job is None:
                return
            with lock:
                claimed.append(job.job_id)

    threads = [threading.Thread(target=drain, args=(f"w{i}",)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(claimed) == sorted(job_ids)
    with session_factory() as session:
        assert session.scalar(select(JobAttempt.id).where(JobAttempt.attempt_no > 1)) is None


def test_stale_fence_token_cannot_complete(session_factory):
    [job_id] = submit_fake(session_factory, 1)
    with session_factory() as session:
        claimed = queue.claim(session, "w1", lease_seconds=60)
    # Simulate the job having been reclaimed by another attempt.
    with session_factory() as session:
        session.execute(update(Job).where(Job.id == job_id).values(fence_token=Job.fence_token + 1))
        session.commit()

    with session_factory() as session:
        assert queue.complete(session, claimed, "results/x/", []) is False
    with session_factory() as session:
        assert session.get(Job, job_id).status == "running"
        assert session.get(JobAttempt, claimed.attempt_id).outcome == "lost"


def test_stage_error_is_permanent_and_marks_job_dead(client, make_worker, session_factory):
    # max_attempts is 3, but an error raised by a pipeline stage is not retried.
    worker = make_worker(pipeline=fake_pipelines.fail_in_extract)
    pdf = make_pdf("fail")
    job_id = client.post("/jobs", files={"file": ("p.pdf", pdf, "application/pdf")}).json()["id"]
    worker.run_once()

    job = client.get(f"/jobs/{job_id}").json()
    assert job["status"] == "dead"
    assert job["last_error_type"] == "ZeroDivisionError"
    assert "extract_text" in job["last_error_message"]
    with session_factory() as session:
        attempt = session.scalars(select(JobAttempt).where(JobAttempt.job_id == job_id)).one()
        assert attempt.outcome == "failed"
        assert "ZeroDivisionError" in attempt.traceback_tail
        stage = session.scalars(select(StageRun).where(StageRun.attempt_id == attempt.id)).one()
        assert (stage.stage, stage.status, stage.error_type) == (
            "extract_text",
            "failed",
            "ZeroDivisionError",
        )
        last_event = session.scalars(
            select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.id.desc())
        ).first()
        assert (last_event.from_status, last_event.to_status) == ("running", "dead")

    # A dead job does not block resubmission of the same file.
    again = client.post("/jobs", files={"file": ("p.pdf", pdf, "application/pdf")})
    assert again.status_code == 201
    assert again.json()["id"] != job_id


def test_submit_keeps_working_once_psycopg_prepares_the_statement(session_factory):
    # psycopg switches a statement to a server-side prepared statement after 5 executions on
    # one connection. The ON CONFLICT clause must still match the partial unique index then.
    with session_factory() as session:
        for i in range(12):
            _, created = queue.submit(
                session,
                queue.NewDocument(f"{i:064x}", 1, 1, f"f{i}.pdf", f"documents/{i}.pdf"),
                options={},
                max_attempts=3,
                request_id=None,
            )
            assert created
