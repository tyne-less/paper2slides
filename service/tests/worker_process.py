"""Runs a real worker process for the kill and SIGTERM tests.
Usage: python worker_process.py <fake_pipelines function> <worker id>; settings from P2S_* env."""

import sys

import fake_pipelines

from p2s_service.config import Settings
from p2s_service.db.session import make_engine, make_session_factory
from p2s_service.observability import configure_logging
from p2s_service.storage import LocalStorage
from p2s_service.worker import Worker

if __name__ == "__main__":
    settings = Settings()
    configure_logging("INFO")
    Worker(
        settings,
        make_session_factory(make_engine(settings.database_url)),
        LocalStorage(settings.storage_local_root),
        worker_id=sys.argv[2],
        pipeline=getattr(fake_pipelines, sys.argv[1]),
    ).run_forever()
