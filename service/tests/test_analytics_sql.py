from pathlib import Path

from sqlalchemy import text

from conftest import make_pdf

SQL_DIR = Path(__file__).resolve().parents[1] / "sql" / "analytics"


def run_sql(session_factory, name: str) -> list:
    with session_factory() as session:
        return session.execute(text((SQL_DIR / f"{name}.sql").read_text())).mappings().all()


def test_analytics_queries_run_against_current_schema(client, worker, session_factory):
    client.post("/jobs", files={"file": ("p.pdf", make_pdf("analytics"), "application/pdf")})
    worker.run_once()

    stages = run_sql(session_factory, "stage_latency")
    assert len(stages) == 7
    assert all(row["runs"] == 1 for row in stages)

    assert run_sql(session_factory, "failures_by_error_type") == []

    days = run_sql(session_factory, "daily_throughput")
    assert len(days) == 14
    assert (days[-1]["submitted"], days[-1]["succeeded"], days[-1]["dead"]) == (1, 1, 0)
