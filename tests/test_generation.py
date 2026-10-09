"""Tests for certificate generation and per-recipient failure isolation."""
from __future__ import annotations


def _create_job(client, recipients):
    response = client.post(
        "/api/v1/jobs",
        json={"title": "Certificate of Completion", "recipients": recipients},
    )
    assert response.status_code == 202
    return response.json()


def test_certificate_pdf_is_generated_and_downloadable(client):
    job = _create_job(client, [{"name": "Grace Hopper", "course": "Compilers"}])
    recipient = job["recipients"][0]
    assert recipient["status"] == "COMPLETED"
    assert recipient["certificate_id"]

    download = client.get(recipient["download_url"])
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/pdf"
    assert download.content.startswith(b"%PDF")
    assert len(download.content) > 1000

    metadata = client.get(f"/api/v1/certificates/{recipient['certificate_id']}")
    assert metadata.status_code == 200
    meta = metadata.json()
    assert meta["job_id"] == job["id"]
    assert meta["recipient_id"] == recipient["id"]
    assert meta["size_bytes"] > 0


def test_individual_failure_does_not_block_other_certificates(client):
    long_name = "A" * 130  # exceeds MAX_NAME_LENGTH (template constraint)
    recipients = [
        {"name": "Valid One"},
        {"name": long_name},
        {"name": "Valid Two"},
    ]
    job = _create_job(client, recipients)

    assert job["status"] == "COMPLETED_WITH_ERRORS"
    assert job["processed"] == 3
    assert job["succeeded"] == 2
    assert job["failed"] == 1

    by_name = {r["name"]: r for r in job["recipients"]}
    failed = by_name[long_name]
    assert failed["status"] == "FAILED"
    assert failed["error"]
    assert failed["certificate_id"] is None
    assert failed["download_url"] is None

    for name in ("Valid One", "Valid Two"):
        assert by_name[name]["status"] == "COMPLETED"
        assert by_name[name]["certificate_id"]
        assert client.get(by_name[name]["download_url"]).status_code == 200


def test_all_recipients_failing_marks_job_failed(client):
    recipients = [{"name": "B" * 130}, {"name": "C" * 140}]
    job = _create_job(client, recipients)
    assert job["status"] == "FAILED"
    assert job["succeeded"] == 0
    assert job["failed"] == 2


def test_job_certificates_listing(client):
    job = _create_job(client, [{"name": "One"}, {"name": "Two"}])
    response = client.get(f"/api/v1/jobs/{job['id']}/certificates")
    assert response.status_code == 200
    certificates = response.json()
    assert len(certificates) == 2
    assert all(c["download_url"] for c in certificates)


def test_download_unknown_certificate_returns_404(client):
    assert client.get("/api/v1/certificates/nope/download").status_code == 404
