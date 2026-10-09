"""Tests for job creation and input validation."""
from __future__ import annotations


def _job_payload(recipients=None, **overrides):
    payload = {
        "title": "Certificate of Achievement",
        "signatory_name": "Dr. Ada Lovelace",
        "signatory_title": "Director",
        "recipients": recipients
        if recipients is not None
        else [{"name": "Alice Example", "email": "alice@example.com"}],
    }
    payload.update(overrides)
    return payload


def test_create_job_returns_accepted_and_processes(client):
    recipients = [
        {"name": "Alice Example", "email": "alice@example.com", "course": "Python"},
        {"name": "Bob Example", "email": "bob@example.com"},
        {"name": "Carol Example"},
    ]
    response = client.post("/api/v1/jobs", json=_job_payload(recipients))

    assert response.status_code == 202
    body = response.json()
    assert body["id"]
    assert body["total_recipients"] == 3
    assert body["status"] == "COMPLETED"
    assert body["processed"] == 3
    assert body["succeeded"] == 3
    assert body["failed"] == 0
    assert len(body["recipients"]) == 3
    assert all(r["status"] == "COMPLETED" for r in body["recipients"])
    assert all(r["certificate_id"] for r in body["recipients"])


def test_create_job_rejects_empty_recipient_list(client):
    response = client.post("/api/v1/jobs", json=_job_payload(recipients=[]))
    assert response.status_code == 422


def test_create_job_rejects_missing_name(client):
    response = client.post(
        "/api/v1/jobs", json=_job_payload(recipients=[{"email": "x@example.com"}])
    )
    assert response.status_code == 422


def test_create_job_rejects_blank_name(client):
    response = client.post(
        "/api/v1/jobs", json=_job_payload(recipients=[{"name": "   "}])
    )
    assert response.status_code == 422


def test_create_job_rejects_invalid_email(client):
    response = client.post(
        "/api/v1/jobs",
        json=_job_payload(recipients=[{"name": "Alice", "email": "not-an-email"}]),
    )
    assert response.status_code == 422


def test_create_job_rejects_too_many_recipients(client):
    recipients = [{"name": f"Person {i}"} for i in range(51)]
    response = client.post("/api/v1/jobs", json=_job_payload(recipients=recipients))
    assert response.status_code == 422


def test_list_jobs(client):
    client.post("/api/v1/jobs", json=_job_payload())
    response = client.get("/api/v1/jobs", params={"limit": 5, "offset": 0})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert len(body["items"]) >= 1


def test_get_unknown_job_returns_404(client):
    response = client.get("/api/v1/jobs/does-not-exist")
    assert response.status_code == 404
