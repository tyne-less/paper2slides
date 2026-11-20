import pytest
from fastapi.testclient import TestClient

from conftest import make_pdf
from p2s_service.api.app import create_app
from p2s_service.storage import LocalStorage, ObjectNotFound, PostgresStorage
from p2s_service.worker import Worker


@pytest.fixture(params=["local", "postgres"])
def any_storage(request, tmp_path):
    if request.param == "local":
        return LocalStorage(tmp_path / "objects")
    return PostgresStorage(request.getfixturevalue("session_factory"))


def test_put_get_overwrite_and_missing_key(any_storage, tmp_path):
    any_storage.put_bytes("a/b.pdf", b"first", "application/pdf")
    assert any_storage.get_bytes("a/b.pdf") == b"first"

    any_storage.put_bytes("a/b.pdf", b"second", "application/pdf")  # retried attempts rewrite
    assert any_storage.get_bytes("a/b.pdf") == b"second"

    source = tmp_path / "source.txt"
    source.write_bytes(b"from file")
    any_storage.put_file("c.txt", source, "text/plain")
    target = tmp_path / "target.txt"
    any_storage.download("c.txt", target)
    assert target.read_bytes() == b"from file"

    with pytest.raises(ObjectNotFound):
        any_storage.get_bytes("missing")


def test_happy_path_with_postgres_storage(settings, session_factory):
    storage = PostgresStorage(session_factory)
    worker = Worker(settings, session_factory, storage, worker_id="pg-worker")
    with TestClient(create_app(settings, session_factory, storage)) as client:
        job_id = client.post(
            "/jobs", files={"file": ("p.pdf", make_pdf("pg-storage"), "application/pdf")}
        ).json()["id"]
        assert worker.run_once() is True
        result = client.get(f"/jobs/{job_id}/result").json()
        assert result["slide_deck"]["slides"]
        tex = client.get(result["files"]["06_slides.tex"])
        assert "\\begin{document}" in tex.text
