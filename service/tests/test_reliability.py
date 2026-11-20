import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path

import fake_pipelines
import pytest
from sqlalchemy import func, select, update

from conftest import TEST_DATABASE_URL, make_pdf
from p2s_service import queue
from p2s_service.db.models import Job, JobAttempt, JobEvent, StageRun
from p2s_service.pipeline_adapter import OUTPUT_FILES
from p2s_service.worker import result_prefix

TESTS_DIR = Path(__file__).resolve().parent


def submit(client, variant: str) -> uuid.UUID:
    response = client.post("/jobs", files={"file": ("p.pdf", make_pdf(variant), "application/pdf")})
    assert response.status_code == 201
    return uuid.UUID(response.json()["id"])


def load_job(session_factory, job_id) -> Job:
    with session_factory() as session:
        return session.get(Job, job_id)


def load_attempts(session_factory, job_id) -> list[JobAttempt]:
    with session_factory() as session:
        return session.scalars(
            select(JobAttempt).where(JobAttempt.job_id == job_id).order_by(JobAttempt.attempt_no)
        ).all()


def load_transitions(session_factory, job_id) -> list[tuple]:
    with session_factory() as session:
        events = session.scalars(
            select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.id)
        )
        return [(e.from_status, e.to_status, e.actor.split(":")[0]) for e in events]


def load_stage_runs(session_factory, attempt_id) -> list[StageRun]:
    with session_factory() as session:
        return session.scalars(
            select(StageRun).where(StageRun.attempt_id == attempt_id).order_by(StageRun.id)
        ).all()


def make_ready(session_factory, job_id) -> None:
    with session_factory() as session:
        session.execute(update(Job).where(Job.id == job_id).values(run_after=func.now()))
        session.commit()


def wait_for(condition, timeout: float, interval: float = 0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if result := condition():
            return result
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


class FlakyStorage:
    """Wraps a storage backend; the first `failures` downloads raise ConnectionError."""

    def __init__(self, inner, failures: int):
        self.inner = inner
        self.failures = failures

    def download(self, key, path):
        if self.failures > 0:
            self.failures -= 1
            raise ConnectionError("object store unreachable")
        self.inner.download(key, path)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def test_backoff_delay_grows_exponentially_with_jitter_and_cap():
    policy = queue.RetryPolicy(base_seconds=2, max_seconds=300)
    for _ in range(50):
        assert 1 <= policy.delay(1) <= 2
        assert 4 <= policy.delay(3) <= 8
        assert 150 <= policy.delay(20) <= 300


def test_transient_error_is_retried_after_backoff(client, make_worker, storage, session_factory):
    worker = make_worker(storage_override=FlakyStorage(storage, failures=1), retry_base_seconds=30)
    job_id = submit(client, "flaky")

    assert worker.run_once() is True
    with session_factory() as session:
        job = session.get(Job, job_id)
        wait = session.scalar(select(func.extract("epoch", Job.run_after - func.now())))
    assert job.status == "queued"
    assert job.last_error_type == "ConnectionError"
    assert 13 <= float(wait) <= 30  # delay(1) is in [15, 30]; allow for elapsed time
    assert worker.run_once() is False  # not ready before the backoff expires

    make_ready(session_factory, job_id)
    assert worker.run_once() is True
    job = load_job(session_factory, job_id)
    assert (job.status, job.attempts, job.last_error_type) == ("succeeded", 2, None)
    assert [a.outcome for a in load_attempts(session_factory, job_id)] == ["failed", "succeeded"]
    assert load_transitions(session_factory, job_id) == [
        (None, "queued", "api"),
        ("queued", "running", "worker"),
        ("running", "queued", "worker"),
        ("queued", "running", "worker"),
        ("running", "succeeded", "worker"),
    ]


def test_retries_stop_at_max_attempts(client, make_worker, storage, session_factory):
    worker = make_worker(storage_override=FlakyStorage(storage, failures=99))
    job_id = submit(client, "always-fails")
    for _ in range(3):
        assert worker.run_once() is True
        make_ready(session_factory, job_id)
    assert worker.run_once() is False

    job = load_job(session_factory, job_id)
    assert (job.status, job.attempts, job.last_error_type) == ("dead", 3, "ConnectionError")
    assert [a.outcome for a in load_attempts(session_factory, job_id)] == ["failed"] * 3
    with session_factory() as session:
        last = session.scalars(
            select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.id.desc())
        ).first()
    assert last.to_status == "dead"
    assert "attempts exhausted" in last.reason


def test_timeout_kills_a_hung_stage_and_schedules_a_retry(client, make_worker, session_factory):
    worker = make_worker(pipeline=fake_pipelines.hang_after_extract, job_timeout_seconds=1)
    job_id = submit(client, "hang")

    started = time.monotonic()
    worker.run_once()
    assert time.monotonic() - started < 10  # the pipeline itself would sleep 600 s

    assert load_job(session_factory, job_id).status == "queued"
    [attempt] = load_attempts(session_factory, job_id)
    assert (attempt.outcome, attempt.error_type) == ("timeout", "Timeout")
    runs = load_stage_runs(session_factory, attempt.id)
    assert [(r.stage, r.status, r.error_type) for r in runs] == [
        ("extract_text", "succeeded", None),
        ("split_pages", "failed", "Timeout"),
    ]
    assert runs[1].duration_ms >= 500


def test_child_process_crash_is_recorded_and_retried(client, make_worker, session_factory):
    worker = make_worker(pipeline=fake_pipelines.crash)
    job_id = submit(client, "crash")
    worker.run_once()

    assert load_job(session_factory, job_id).status == "queued"
    [attempt] = load_attempts(session_factory, job_id)
    assert (attempt.outcome, attempt.error_type) == ("failed", "ChildCrashed")
    assert "137" in attempt.error_message


def test_lease_lost_mid_run_abandons_the_attempt(
    client, make_worker, storage, session_factory, monkeypatch
):
    monkeypatch.setenv("P2S_TEST_PIPELINE_DELAY", "5")
    worker = make_worker(pipeline=fake_pipelines.slow, lease_seconds=0.6)
    job_id = submit(client, "lease-lost")

    thread = threading.Thread(target=worker.run_once)
    thread.start()
    wait_for(lambda: load_job(session_factory, job_id).status == "running", timeout=5)
    # Another attempt takes over (as if this worker had stalled past its lease).
    with session_factory() as session:
        session.execute(update(Job).where(Job.id == job_id).values(fence_token=Job.fence_token + 1))
        session.commit()
    thread.join(timeout=3)
    assert not thread.is_alive()  # stopped at the next heartbeat, not after the 5 s pipeline

    [attempt] = load_attempts(session_factory, job_id)
    assert (attempt.outcome, attempt.error_type) == ("lost", "LeaseLost")
    job = load_job(session_factory, job_id)
    assert job.status == "running" and job.result_prefix is None
    assert not (storage.root / result_prefix(job.dedup_key)).exists()


def test_reaper_requeues_job_of_a_worker_that_stopped_heartbeating(
    client, make_worker, session_factory
):
    job_id = submit(client, "orphan")
    with session_factory() as session:
        queue.claim(session, "vanished-worker", lease_seconds=0.3)
    time.sleep(0.5)

    worker = make_worker(reap_interval_seconds=0, retry_base_seconds=0.01)
    wait_for(lambda: worker.run_once() and load_job(session_factory, job_id).status, timeout=5)

    assert load_job(session_factory, job_id).status == "succeeded"
    attempts = load_attempts(session_factory, job_id)
    assert [(a.worker_id, a.outcome, a.error_type) for a in attempts] == [
        ("vanished-worker", "lost", "LeaseExpired"),
        ("test-worker", "succeeded", None),
    ]
    assert ("running", "queued", "reaper") in load_transitions(session_factory, job_id)


@pytest.fixture
def worker_process(settings, tmp_path):
    """Start tests/worker_process.py as a real OS process in its own process group."""
    started: list[subprocess.Popen] = []

    def start(pipeline: str, **env_overrides) -> subprocess.Popen:
        env = {
            **os.environ,
            "P2S_DATABASE_URL": TEST_DATABASE_URL,
            "P2S_STORAGE_LOCAL_ROOT": str(settings.storage_local_root),
            "P2S_LEASE_SECONDS": "1",
            "P2S_POLL_INTERVAL_SECONDS": "0.05",
            "P2S_REAP_INTERVAL_SECONDS": "3600",
            **env_overrides,
        }
        log = open(tmp_path / f"worker-{len(started)}.log", "w")
        proc = subprocess.Popen(
            [sys.executable, str(TESTS_DIR / "worker_process.py"), pipeline, "proc-worker"],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        started.append(proc)
        return proc

    yield start
    for proc in started:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


def test_sigkill_mid_run_is_recovered_by_another_worker(
    client, make_worker, worker_process, storage, session_factory
):
    job_id = submit(client, "sigkill")
    proc = worker_process("slow", P2S_TEST_PIPELINE_DELAY="30")
    wait_for(lambda: load_job(session_factory, job_id).status == "running", timeout=15)

    # While the process is alive, heartbeats keep the 1 s lease from expiring.
    time.sleep(2)
    with session_factory() as session:
        remaining = session.scalar(
            select(func.extract("epoch", Job.lease_expires_at - func.now())).where(Job.id == job_id)
        )
    assert float(remaining) > 0

    # Kill the worker and its pipeline child at once, as when a container is killed.
    os.killpg(proc.pid, signal.SIGKILL)
    proc.wait()

    rescuer = make_worker(worker_id="rescuer", reap_interval_seconds=0, retry_base_seconds=0.01)
    wait_for(
        lambda: rescuer.run_once() or load_job(session_factory, job_id).status == "succeeded",
        timeout=10,
    )
    job = load_job(session_factory, job_id)
    assert (job.status, job.attempts) == ("succeeded", 2)
    attempts = load_attempts(session_factory, job_id)
    assert [(a.worker_id, a.outcome) for a in attempts] == [
        ("proc-worker", "lost"),
        ("rescuer", "succeeded"),
    ]
    # The killed attempt never finished a stage, so it wrote no outputs.
    assert load_stage_runs(session_factory, attempts[0].id) == []
    for name in OUTPUT_FILES.values():
        assert (storage.root / job.result_prefix / name).is_file()


def test_sigterm_releases_a_job_that_cannot_finish_within_grace(
    client, make_worker, worker_process, storage, session_factory
):
    job_id = submit(client, "sigterm-release")
    proc = worker_process("slow", P2S_TEST_PIPELINE_DELAY="30", P2S_SHUTDOWN_GRACE_SECONDS="1")
    wait_for(lambda: load_job(session_factory, job_id).status == "running", timeout=15)

    proc.send_signal(signal.SIGTERM)  # to the worker only, like `docker stop`
    assert proc.wait(timeout=10) == 0

    job = load_job(session_factory, job_id)
    assert job.status == "queued"
    [attempt] = load_attempts(session_factory, job_id)
    assert attempt.outcome == "released"
    assert load_transitions(session_factory, job_id)[-1] == ("running", "queued", "worker")

    # A released attempt does not count towards max_attempts: with max_attempts=2, one real
    # failure after the release must still leave the job queued for another try.
    with session_factory() as session:
        session.execute(update(Job).where(Job.id == job_id).values(max_attempts=2))
        session.commit()
    flaky = make_worker(storage_override=FlakyStorage(storage, failures=1))
    assert flaky.run_once() is True
    assert load_job(session_factory, job_id).status == "queued"


def test_sigterm_lets_a_short_job_finish(client, worker_process, session_factory):
    job_id = submit(client, "sigterm-finish")
    proc = worker_process("slow", P2S_TEST_PIPELINE_DELAY="1", P2S_SHUTDOWN_GRACE_SECONDS="8")
    wait_for(lambda: load_job(session_factory, job_id).status == "running", timeout=15)

    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=15) == 0
    assert load_job(session_factory, job_id).status == "succeeded"


@pytest.fixture
def restore_signal_handlers():
    # run_forever installs SIGTERM/SIGINT handlers; put pytest's back afterwards.
    saved = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    yield
    for sig, handler in saved.items():
        signal.signal(sig, handler)


def test_drain_mode_processes_ready_jobs_then_returns(
    client, make_worker, session_factory, restore_signal_handlers
):
    ready = [submit(client, f"drain-{i}") for i in range(3)]
    later = submit(client, "drain-later")
    with session_factory() as session:
        session.execute(
            update(Job).where(Job.id == later).values(run_after=func.now() + timedelta(hours=1))
        )
        session.commit()

    started = time.monotonic()
    make_worker(poll_interval_seconds=30).run_forever(drain=True)
    assert time.monotonic() - started < 20  # returned without waiting for the poll interval

    assert [load_job(session_factory, j).status for j in ready] == ["succeeded"] * 3
    assert load_job(session_factory, later).status == "queued"  # left for the next execution


def test_drain_mode_fails_loudly_on_infrastructure_errors(
    make_worker, monkeypatch, restore_signal_handlers
):
    worker = make_worker()

    def broken():
        raise ConnectionError("database unreachable")

    monkeypatch.setattr(worker, "run_once", broken)
    with pytest.raises(ConnectionError):
        worker.run_forever(drain=True)


def test_worker_cli_drain_exits_when_the_queue_is_empty(settings, session_factory):
    env = {
        **os.environ,
        "P2S_DATABASE_URL": TEST_DATABASE_URL,
        "P2S_STORAGE_LOCAL_ROOT": str(settings.storage_local_root),
        "P2S_WORKER_METRICS_PORT": "0",
    }
    done = subprocess.run(
        [sys.executable, "-m", "p2s_service", "worker", "--drain"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert '"drain": true' in done.stdout
    assert "worker stopped" in done.stdout
