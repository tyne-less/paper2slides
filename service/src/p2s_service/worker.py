import logging
import multiprocessing
import os
import signal
import socket
import tempfile
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from prometheus_client import Counter, Histogram, start_http_server
from sqlalchemy.orm import Session, sessionmaker

from . import pipeline_child, queue
from .config import Settings, get_settings
from .db.session import make_engine, make_session_factory
from .observability import configure_logging, job_id_var, request_id_var
from .pipeline_adapter import STAGE_NAMES, StageTiming, run_stages
from .storage import Storage, make_storage

log = logging.getLogger("p2s.worker")

CONTENT_TYPES = {".json": "application/json", ".tex": "application/x-tex", ".txt": "text/plain"}

_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300)
ATTEMPTS = Counter("p2s_worker_attempts", "Finished attempts by outcome", ["outcome"])
PHASE_SECONDS = Histogram(
    "p2s_worker_phase_seconds",
    "Time spent per processing phase of an attempt",
    ["phase"],
    buckets=_BUCKETS,
)
STAGE_SECONDS = Histogram(
    "p2s_worker_stage_seconds", "Pipeline stage duration", ["stage"], buckets=_BUCKETS
)

# forkserver: each pipeline process is forked from a small server process that is started once,
# has the pipeline modules already imported, and holds no DB connections or threads (the
# reason not to fork the worker itself). With spawn, every attempt started a new interpreter
# and re-imported the worker's modules, which took about 0.5 s per attempt.
_MP = multiprocessing.get_context("forkserver")
_MP.set_forkserver_preload(["p2s_service.pipeline_child"])


def result_prefix(dedup_key: str) -> str:
    return f"results/{dedup_key}/"


@dataclass(frozen=True)
class PipelineOutcome:
    # ok | error (raised by a stage) | timeout | crashed | lost (lease) | shutdown
    kind: str
    paths: dict[str, Path] | None = None
    error_type: str | None = None
    message: str | None = None
    traceback: str | None = None


class Worker:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        storage: Storage,
        worker_id: str | None = None,
        pipeline: Callable = run_stages,
    ):
        self.settings = settings
        self.session_factory = session_factory
        self.storage = storage
        # In a Cloud Run job the hostname is "localhost"; the execution name tells runs apart.
        host = os.environ.get("CLOUD_RUN_EXECUTION") or socket.gethostname()
        self.worker_id = worker_id or f"{host}-{os.getpid()}"
        self.pipeline = pipeline
        self.retry = queue.RetryPolicy(settings.retry_base_seconds, settings.retry_max_seconds)
        self.stop_event = threading.Event()
        self._stop_requested_at: float | None = None
        self._last_reap = float("-inf")

    def request_stop(self) -> None:
        if self._stop_requested_at is None:
            self._stop_requested_at = time.monotonic()
        self.stop_event.set()

    def run_once(self) -> bool:
        """Reap expired leases if due, then claim and process one job.
        Returns False when the queue had nothing ready."""
        self._maybe_reap()
        with self.session_factory() as session:
            claimed = queue.claim(session, self.worker_id, self.settings.lease_seconds)
        if claimed is None:
            return False
        job_token = job_id_var.set(str(claimed.job_id))
        request_token = request_id_var.set(claimed.request_id)
        try:
            log.info("job claimed", extra={"attempt_no": claimed.attempt_no})
            self._process(claimed)
        finally:
            job_id_var.reset(job_token)
            request_id_var.reset(request_token)
        return True

    def _maybe_reap(self) -> None:
        now = time.monotonic()
        if now - self._last_reap < self.settings.reap_interval_seconds:
            return
        self._last_reap = now
        with self.session_factory() as session:
            count = queue.reap(session, f"reaper:{self.worker_id}", self.retry)
        if count:
            log.warning("requeued jobs with expired leases", extra={"count": count})

    def _fail(self, claimed, timings, outcome, error_type, message, tb, retryable) -> None:
        with self.session_factory() as session:
            status = queue.fail(
                session,
                claimed,
                outcome=outcome,
                error_type=error_type,
                error_message=message,
                traceback_tail=tb,
                timings=timings,
                retryable=retryable,
                retry=self.retry,
            )
        log.warning(
            "attempt failed",
            extra={"outcome": outcome, "error_type": error_type, "error": message, "next": status},
        )

    def _record(self, outcome: str, phases: dict[str, float], timings: list[StageTiming]) -> None:
        ATTEMPTS.labels(outcome).inc()
        for phase, seconds in phases.items():
            PHASE_SECONDS.labels(phase).observe(seconds)
        for t in timings:
            if t.status == "succeeded":
                STAGE_SECONDS.labels(t.stage).observe(t.duration_ms / 1000)

    def _process(self, claimed: queue.ClaimedJob) -> None:
        timings: list[StageTiming] = []
        # Wall-clock seconds per phase of this attempt; logged and exported as metrics.
        phases: dict[str, float] = {}
        outcome = "failed"
        try:
            outcome = self._process_attempt(claimed, timings, phases)
        finally:
            self._record(outcome, phases, timings)

    def _process_attempt(
        self, claimed: queue.ClaimedJob, timings: list[StageTiming], phases: dict[str, float]
    ) -> str:
        """Process one claimed attempt and return its outcome."""
        try:
            with tempfile.TemporaryDirectory(prefix="p2s-") as tmp:
                input_pdf = Path(tmp) / f"{claimed.document_sha256}.pdf"
                started = time.monotonic()
                self.storage.download(claimed.document_key, input_pdf)
                phases["download"] = time.monotonic() - started
                result = self._run_pipeline(claimed, input_pdf, Path(tmp) / "run", timings, phases)

                if result.kind == "lost":
                    with self.session_factory() as session:
                        queue.abandon(session, claimed, timings)
                    log.warning("lease lost; attempt abandoned")
                    return "lost"
                if result.kind == "shutdown":
                    with self.session_factory() as session:
                        queue.release(session, claimed, timings)
                    log.info("job released on shutdown")
                    return "released"
                if result.kind != "ok":
                    outcome = "timeout" if result.kind == "timeout" else "failed"
                    # A stage error is deterministic for the same input and code, so it is
                    # not retried. Timeouts and crashes may be environmental and are retried.
                    self._fail(
                        claimed,
                        timings,
                        outcome,
                        result.error_type,
                        result.message,
                        result.traceback,
                        retryable=result.kind != "error",
                    )
                    return outcome

                prefix = result_prefix(claimed.dedup_key)
                started = time.monotonic()
                for path in result.paths.values():
                    self.storage.put_file(prefix + path.name, path, CONTENT_TYPES[path.suffix])
                phases["upload"] = time.monotonic() - started
        except Exception as exc:
            # Storage or other infrastructure errors outside the pipeline: retried.
            self._fail(
                claimed,
                timings,
                "failed",
                type(exc).__name__,
                str(exc),
                traceback.format_exc(),
                retryable=True,
            )
            return "failed"

        started = time.monotonic()
        with self.session_factory() as session:
            owned = queue.complete(session, claimed, prefix, timings)
        phases["complete"] = time.monotonic() - started
        log.info(
            "job succeeded" if owned else "job finished but lease was lost",
            extra={
                "stages_ms": sum(t.duration_ms for t in timings),
                "phases_ms": {k: round(v * 1000, 1) for k, v in phases.items()},
                "owned": owned,
            },
        )
        return "succeeded" if owned else "lost"

    def _run_pipeline(
        self,
        claimed: queue.ClaimedJob,
        input_pdf: Path,
        run_dir: Path,
        timings: list[StageTiming],
        phases: dict[str, float],
    ) -> PipelineOutcome:
        settings = self.settings
        parent_conn, child_conn = _MP.Pipe(duplex=False)
        process = _MP.Process(
            target=pipeline_child.main,
            args=(
                self.pipeline,
                str(input_pdf),
                str(run_dir),
                claimed.options.get("pdftotext_mode", "default"),
                child_conn,
            ),
            daemon=True,
        )
        start = time.monotonic()
        process.start()
        child_conn.close()

        deadline = start + settings.job_timeout_seconds
        heartbeat_every = max(settings.lease_seconds / 3, 0.1)
        next_heartbeat = start + heartbeat_every
        stage_started = (datetime.now(UTC), start)

        def in_progress_stage(error_type: str) -> None:
            if len(timings) < len(STAGE_NAMES):
                started_at, started = stage_started
                duration_ms = round((time.monotonic() - started) * 1000)
                timings.append(
                    StageTiming(
                        STAGE_NAMES[len(timings)], started_at, duration_ms, "failed", error_type
                    )
                )

        try:
            while True:
                now = time.monotonic()
                if now >= deadline:
                    in_progress_stage("Timeout")
                    return PipelineOutcome(
                        "timeout",
                        error_type="Timeout",
                        message=f"attempt exceeded {settings.job_timeout_seconds}s",
                    )
                if (
                    self._stop_requested_at is not None
                    and now - self._stop_requested_at >= settings.shutdown_grace_seconds
                ):
                    return PipelineOutcome("shutdown")
                if now >= next_heartbeat:
                    with self.session_factory() as session:
                        if not queue.heartbeat(session, claimed, settings.lease_seconds):
                            return PipelineOutcome("lost")
                    next_heartbeat = now + heartbeat_every

                if not parent_conn.poll(min(0.2, max(deadline - now, 0))):
                    continue
                try:
                    kind, payload = parent_conn.recv()
                except EOFError:
                    process.join(5)
                    in_progress_stage("ChildCrashed")
                    return PipelineOutcome(
                        "crashed",
                        error_type="ChildCrashed",
                        message=f"pipeline process exited with code {process.exitcode}",
                    )
                if kind == "ready":
                    phases["process_start"] = time.monotonic() - start
                    stage_started = (datetime.now(UTC), time.monotonic())
                elif kind == "stage":
                    timings.append(payload)
                    stage_started = (datetime.now(UTC), time.monotonic())
                elif kind == "ok":
                    phases["pipeline"] = time.monotonic() - start
                    return PipelineOutcome("ok", paths={k: Path(v) for k, v in payload.items()})
                else:
                    error_type, message, tb = payload
                    return PipelineOutcome("error", None, error_type, message, tb)
        finally:
            exit_started = time.monotonic()
            if "pipeline" in phases:
                # Finished normally: let the process exit, but never wait on it indefinitely.
                process.join(5)
            if process.is_alive():
                process.kill()
            process.join()
            phases["process_exit"] = time.monotonic() - exit_started
            parent_conn.close()

    def run_forever(self, drain: bool = False) -> None:
        """Process jobs until stopped. With drain=True, return as soon as no job is ready
        (one Cloud Run Job execution); errors then propagate so the execution fails visibly."""

        def on_signal(signum, _frame):
            log.info("stop requested", extra={"signal": signal.Signals(signum).name})
            self.request_stop()

        signal.signal(signal.SIGTERM, on_signal)
        signal.signal(signal.SIGINT, on_signal)
        log.info("worker started", extra={"worker_id": self.worker_id, "drain": drain})
        # The stop flag is checked between jobs; a job in progress gets shutdown_grace_seconds
        # to finish and is otherwise released back to the queue.
        while not self.stop_event.is_set():
            try:
                worked = self.run_once()
            except Exception:
                # e.g. the database is briefly unreachable. A job held at this point is
                # recovered by the reaper once its lease expires.
                log.exception("worker iteration failed")
                if drain:
                    raise
                worked = False
            if not worked:
                if drain:
                    break
                self.stop_event.wait(self.settings.poll_interval_seconds)
        log.info("worker stopped", extra={"worker_id": self.worker_id})


def main(drain: bool = False) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.worker_metrics_port:
        start_http_server(settings.worker_metrics_port)
    session_factory = make_session_factory(make_engine(settings.database_url))
    storage = make_storage(settings, session_factory)
    Worker(settings, session_factory, storage).run_forever(drain=drain)
