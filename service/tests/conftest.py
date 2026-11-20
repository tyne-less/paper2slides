import os
from pathlib import Path

import pymupdf
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from p2s_service.api.app import create_app
from p2s_service.config import Settings
from p2s_service.db.session import make_engine, make_session_factory
from p2s_service.observability import configure_logging
from p2s_service.storage import LocalStorage
from p2s_service.worker import Worker

SERVICE_DIR = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.environ.get("P2S_TEST_DATABASE_URL")


def make_pdf(variant: str = "a") -> bytes:
    """A small two-page paper-like PDF; `variant` changes the content (and so the hash)."""
    doc = pymupdf.open()
    page = doc.new_page()
    lines = [
        f"Robust Parsing of Scholarly Documents {variant}",
        "Abstract",
        "We study how parsing errors propagate to generated slides.",
        "Our pipeline recovers reading order and section structure.",
        "1 Introduction",
        "Academic PDFs mix columns, figures and formulas.",
        "Errors in early stages affect every downstream stage.",
    ]
    for i, line in enumerate(lines):
        page.insert_text((72, 72 + i * 24), line, fontsize=11)
    page = doc.new_page()
    for i, line in enumerate(["2 Method", "We time each stage of the pipeline separately."]):
        page.insert_text((72, 72 + i * 24), line, fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture(autouse=True)
def json_logging_at_info():
    # Emit INFO records so every log call (and its `extra` keys) is exercised by the tests.
    configure_logging("INFO")


@pytest.fixture(scope="session")
def engine():
    if not TEST_DATABASE_URL:
        pytest.skip("P2S_TEST_DATABASE_URL is not set")
    config = Config(str(SERVICE_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = make_engine(TEST_DATABASE_URL)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(engine):
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE job_events, stage_runs, job_attempts, jobs, documents"))
    return make_session_factory(engine)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_url=TEST_DATABASE_URL or "",
        storage_backend="local",
        storage_local_root=tmp_path / "storage",
        max_upload_bytes=1024 * 1024,
        max_pages=10,
        poll_interval_seconds=0.01,
    )


@pytest.fixture
def storage(settings):
    return LocalStorage(settings.storage_local_root)


@pytest.fixture
def client(settings, session_factory, storage):
    with TestClient(create_app(settings, session_factory, storage)) as client:
        yield client


@pytest.fixture
def worker(settings, session_factory, storage):
    return Worker(settings, session_factory, storage, worker_id="test-worker")


@pytest.fixture
def make_worker(settings, session_factory, storage):
    def make(pipeline=None, storage_override=None, worker_id="test-worker", **overrides):
        kwargs = {"pipeline": pipeline} if pipeline else {}
        return Worker(
            settings.model_copy(update=overrides),
            session_factory,
            storage_override or storage,
            worker_id=worker_id,
            **kwargs,
        )

    return make
