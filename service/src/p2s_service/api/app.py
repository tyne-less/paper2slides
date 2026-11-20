import hashlib
import json
import logging
import time
import uuid
from datetime import datetime
from typing import Annotated

import pymupdf
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from .. import queue
from ..config import Settings, get_settings
from ..db.models import Job, JobEvent
from ..db.session import make_engine, make_session_factory
from ..observability import configure_logging, request_id_var
from ..pipeline_adapter import OUTPUT_FILES, PDFTOTEXT_MODES
from ..storage import ObjectNotFound, Storage, make_storage
from .metrics import HttpMetrics, QueueCollector

log = logging.getLogger("p2s.api")

RESULT_FILES = set(OUTPUT_FILES.values())


class JobOut(BaseModel):
    id: uuid.UUID
    status: str
    attempts: int
    max_attempts: int
    pipeline_version: str
    options: dict
    last_error_type: str | None
    last_error_message: str | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None

    @classmethod
    def from_job(cls, job: Job) -> "JobOut":
        return cls.model_validate(job, from_attributes=True)


class EventOut(BaseModel):
    id: int
    from_status: str | None
    to_status: str
    actor: str
    reason: str | None
    attempt_id: uuid.UUID | None
    created_at: datetime


def create_app(
    settings: Settings | None = None,
    session_factory: sessionmaker[Session] | None = None,
    storage: Storage | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    session_factory = session_factory or make_session_factory(make_engine(settings.database_url))
    # Only the API creates the bucket; the worker reads objects the API has written, so it
    # can start (and retry jobs) while the object store is down.
    storage = storage or make_storage(settings, session_factory, create_bucket=True)

    registry = CollectorRegistry()
    http_metrics = HttpMetrics(registry)
    registry.register(QueueCollector(session_factory))

    app = FastAPI(title="paper2slides service", version="0.1.0")

    # Endpoints open a session only around their database calls instead of using a yield
    # dependency. A sync yield dependency is cleaned up on a threadpool thread after the
    # response; under load every thread was blocked waiting for a pooled connection while the
    # connections were held by sessions waiting for a thread to close them (load test w4-r20).

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["x-request-id"] = request_id
            return response
        finally:
            elapsed = time.perf_counter() - start
            route = request.scope.get("route")
            route_path = route.path if route is not None else "unmatched"
            http_metrics.requests.labels(request.method, route_path, str(status)).inc()
            http_metrics.latency.labels(request.method, route_path).observe(elapsed)
            log.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": status,
                    "duration_ms": round(elapsed * 1000, 1),
                },
            )
            request_id_var.reset(token)

    def load_job(job_id: uuid.UUID) -> Job:
        with session_factory() as session:
            job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        return job

    @app.post("/jobs", response_model=JobOut, status_code=201)
    def create_job(
        response: Response,
        file: Annotated[UploadFile, File()],
        pdftotext_mode: Annotated[str, Form()] = "default",
    ):
        if pdftotext_mode not in PDFTOTEXT_MODES:
            raise HTTPException(422, f"pdftotext_mode must be one of {list(PDFTOTEXT_MODES)}")
        data = file.file.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(413, f"file exceeds {settings.max_upload_bytes} bytes")
        if not data.startswith(b"%PDF-"):
            raise HTTPException(415, "file is not a PDF")
        try:
            with pymupdf.open(stream=data, filetype="pdf") as pdf:
                page_count = pdf.page_count
        except Exception as exc:
            raise HTTPException(422, "PDF could not be opened") from exc
        if page_count > settings.max_pages:
            raise HTTPException(422, f"PDF has {page_count} pages; limit is {settings.max_pages}")

        sha256 = hashlib.sha256(data).hexdigest()
        storage_key = f"documents/{sha256}.pdf"
        storage.put_bytes(storage_key, data, "application/pdf")
        with session_factory() as session:
            job, created = queue.submit(
                session,
                queue.NewDocument(
                    sha256=sha256,
                    size_bytes=len(data),
                    page_count=page_count,
                    original_filename=file.filename or "upload.pdf",
                    storage_key=storage_key,
                ),
                options={"pdftotext_mode": pdftotext_mode},
                max_attempts=settings.max_attempts,
                request_id=request_id_var.get(),
            )
        log.info("job submitted", extra={"job_id": str(job.id), "new_job": created})
        if not created:
            response.status_code = 200
        return JobOut.from_job(job)

    @app.get("/jobs/{job_id}", response_model=JobOut)
    def get_job(job_id: uuid.UUID):
        return JobOut.from_job(load_job(job_id))

    @app.get("/jobs/{job_id}/events", response_model=list[EventOut])
    def get_job_events(job_id: uuid.UUID):
        load_job(job_id)
        with session_factory() as session:
            events = session.scalars(
                select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.id)
            ).all()
        return [EventOut.model_validate(e, from_attributes=True) for e in events]

    @app.get("/jobs/{job_id}/result")
    def get_result(job_id: uuid.UUID):
        job = load_job(job_id)
        if job.status != "succeeded":
            return JSONResponse(
                status_code=409, content={"detail": "job has no result", "status": job.status}
            )
        deck = json.loads(storage.get_bytes(job.result_prefix + OUTPUT_FILES["slide_deck"]))
        return {
            "job_id": str(job.id),
            "slide_deck": deck,
            "files": {name: f"/jobs/{job.id}/result/files/{name}" for name in sorted(RESULT_FILES)},
        }

    @app.get("/jobs/{job_id}/result/files/{name}")
    def get_result_file(job_id: uuid.UUID, name: str):
        job = load_job(job_id)
        if name not in RESULT_FILES:
            raise HTTPException(404, "unknown result file")
        if job.status != "succeeded":
            raise HTTPException(409, "job has no result")
        try:
            data = storage.get_bytes(job.result_prefix + name)
        except ObjectNotFound as exc:
            raise HTTPException(404, "result file missing from storage") from exc
        media_type = "application/json" if name.endswith(".json") else "text/plain"
        return Response(
            data,
            media_type=media_type,
            headers={"content-disposition": f'attachment; filename="{name}"'},
        )

    # Not /healthz: Cloud Run reserves paths ending in "z" and answers them with its own 404.
    @app.get("/health")
    def health():
        try:
            with session_factory() as session:
                session.execute(text("SELECT 1"))
        except Exception:
            log.exception("health check failed")
            return JSONResponse(status_code=503, content={"status": "db_unavailable"})
        return {"status": "ok"}

    @app.get("/metrics")
    def metrics():
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

    return app


def build_default_app() -> FastAPI:
    configure_logging(get_settings().log_level)
    return create_app()
