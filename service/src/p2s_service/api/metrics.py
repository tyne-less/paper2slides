import logging

from prometheus_client import CollectorRegistry, Counter, Histogram
from prometheus_client.core import GaugeMetricFamily
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from ..db.models import JOB_STATUSES

log = logging.getLogger("p2s.metrics")


class QueueCollector:
    """Job-level gauges read from Postgres at scrape time, so they include work done by
    every worker process and survive worker restarts."""

    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def collect(self):
        db_up = GaugeMetricFamily("p2s_metrics_db_up", "1 if the queue query succeeded")
        try:
            with self.session_factory() as session:
                by_status = dict(
                    session.execute(text("SELECT status, count(*) FROM jobs GROUP BY status")).all()
                )
                backlog, oldest_age = session.execute(
                    text(
                        "SELECT count(*), "
                        "coalesce(extract(epoch FROM now() - min(run_after)), 0) "
                        "FROM jobs WHERE status = 'queued' AND run_after <= now()"
                    )
                ).one()
        except Exception:
            log.exception("queue metrics query failed")
            db_up.add_metric([], 0)
            yield db_up
            return

        db_up.add_metric([], 1)
        yield db_up
        jobs = GaugeMetricFamily("p2s_jobs", "Jobs by current status", labels=["status"])
        for status in JOB_STATUSES:
            jobs.add_metric([status], by_status.get(status, 0))
        yield jobs
        yield GaugeMetricFamily("p2s_queue_backlog", "Queued jobs ready to run", value=backlog)
        yield GaugeMetricFamily(
            "p2s_queue_oldest_ready_age_seconds",
            "Age of the oldest ready queued job",
            value=float(oldest_age),
        )


class HttpMetrics:
    def __init__(self, registry: CollectorRegistry):
        self.requests = Counter(
            "p2s_http_requests_total",
            "HTTP requests",
            ["method", "route", "status"],
            registry=registry,
        )
        self.latency = Histogram(
            "p2s_http_request_duration_seconds",
            "HTTP request latency",
            ["method", "route"],
            registry=registry,
        )
