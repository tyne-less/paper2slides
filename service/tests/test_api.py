import uuid

from conftest import make_pdf


def upload(client, data: bytes, **form):
    return client.post("/jobs", files={"file": ("paper.pdf", data, "application/pdf")}, data=form)


def test_happy_path_upload_process_fetch_result(client, worker):
    response = upload(client, make_pdf(), pdftotext_mode="default")
    assert response.status_code == 201
    job_id = response.json()["id"]
    assert response.json()["status"] == "queued"
    assert response.headers["x-request-id"]

    assert worker.run_once() is True
    assert worker.run_once() is False

    job = client.get(f"/jobs/{job_id}").json()
    assert job["status"] == "succeeded"
    assert job["attempts"] == 1

    result = client.get(f"/jobs/{job_id}/result").json()
    assert result["slide_deck"]["slides"]
    tex = client.get(result["files"]["06_slides.tex"])
    assert tex.status_code == 200
    assert "\\begin{document}" in tex.text

    events = client.get(f"/jobs/{job_id}/events").json()
    assert [(e["from_status"], e["to_status"]) for e in events] == [
        (None, "queued"),
        ("queued", "running"),
        ("running", "succeeded"),
    ]


def test_same_file_returns_existing_job(client, worker):
    pdf = make_pdf("dup")
    first = upload(client, pdf)
    second = upload(client, pdf)
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]

    worker.run_once()
    third = upload(client, pdf)
    assert third.status_code == 200
    assert third.json()["status"] == "succeeded"


def test_same_file_with_different_options_is_a_new_job(client):
    pdf = make_pdf("opt")
    first = upload(client, pdf, pdftotext_mode="default")
    second = upload(client, pdf, pdftotext_mode="hybrid")
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


def test_request_id_header_is_propagated(client, session_factory):
    response = client.post(
        "/jobs",
        files={"file": ("paper.pdf", make_pdf("rid"), "application/pdf")},
        headers={"x-request-id": "req-123"},
    )
    assert response.headers["x-request-id"] == "req-123"
    from p2s_service.db.models import Job

    with session_factory() as session:
        assert session.get(Job, uuid.UUID(response.json()["id"])).request_id == "req-123"


def test_upload_validation(client, settings):
    assert upload(client, b"not a pdf").status_code == 415
    too_big = b"%PDF-" + b"0" * settings.max_upload_bytes
    assert upload(client, too_big).status_code == 413
    assert upload(client, b"%PDF-1.7 broken").status_code == 422
    assert upload(client, make_pdf(), pdftotext_mode="bogus").status_code == 422


def test_unknown_job_and_pending_result(client):
    assert client.get(f"/jobs/{uuid.uuid4()}").status_code == 404
    job_id = upload(client, make_pdf("pending")).json()["id"]
    response = client.get(f"/jobs/{job_id}/result")
    assert response.status_code == 409
    assert response.json()["status"] == "queued"
    assert client.get(f"/jobs/{job_id}/result/files/secret.txt").status_code == 404


def test_health_and_metrics(client, worker):
    assert client.get("/health").json() == {"status": "ok"}
    upload(client, make_pdf("m1"))
    upload(client, make_pdf("m2"))
    worker.run_once()

    body = client.get("/metrics").text
    assert 'p2s_jobs{status="succeeded"} 1.0' in body
    assert 'p2s_jobs{status="queued"} 1.0' in body
    assert "p2s_queue_backlog 1.0" in body
    assert "p2s_metrics_db_up 1.0" in body
    assert 'p2s_http_requests_total{method="POST",route="/jobs",status="201"} 2.0' in body


def test_worker_records_attempt_phase_and_stage_metrics(client, worker):
    from prometheus_client import REGISTRY

    def value(name, **labels):
        return REGISTRY.get_sample_value(name, labels) or 0

    phases = ["download", "process_start", "pipeline", "process_exit", "upload", "complete"]
    before = {p: value("p2s_worker_phase_seconds_count", phase=p) for p in phases}
    succeeded = value("p2s_worker_attempts_total", outcome="succeeded")
    extract = value("p2s_worker_stage_seconds_count", stage="extract_text")

    upload(client, make_pdf("worker-metrics"))
    worker.run_once()

    assert value("p2s_worker_attempts_total", outcome="succeeded") == succeeded + 1
    assert value("p2s_worker_stage_seconds_count", stage="extract_text") == extract + 1
    for phase in phases:
        assert value("p2s_worker_phase_seconds_count", phase=phase) == before[phase] + 1, phase
