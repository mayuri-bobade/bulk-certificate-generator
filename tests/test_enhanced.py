"""Tests for the enhanced features (v2).

Covers idempotency, pagination, ZIP export, SSE progress, verification/revoke,
cancellation, retry, crash recovery, readiness and request correlation.
"""
from __future__ import annotations

import io
import time
import uuid
import zipfile

from app.config import get_settings
from app.database import SessionLocal
from app.models import (
    Job,
    JobStatus,
    Recipient,
    RecipientStatus,
    utcnow,
)
from app.worker import recover_incomplete_jobs

TERMINAL = {"COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "CANCELLED"}


def _create(client, recipients, **overrides):
    payload = {"title": "Certificate", "recipients": recipients}
    payload.update(overrides)
    response = client.post("/api/v1/jobs", json=payload)
    assert response.status_code in (200, 202), response.text
    return response.json()


def _wait_terminal(client, job_id, timeout=15.0):
    deadline = time.time() + timeout
    job = None
    while time.time() < deadline:
        job = client.get(f"/api/v1/jobs/{job_id}").json()
        if job["status"] in TERMINAL:
            return job
        time.sleep(0.05)
    raise AssertionError(f"Job did not finish: {job}")


# --------------------------------------------------------------------------- #
# Idempotency
# --------------------------------------------------------------------------- #
def test_idempotency_key_replays_existing_job(client):
    payload = {"title": "Idem", "recipients": [{"name": "Alice"}]}
    headers = {"Idempotency-Key": "key-abc"}

    first = client.post("/api/v1/jobs", json=payload, headers=headers)
    assert first.status_code == 202

    second = client.post("/api/v1/jobs", json=payload, headers=headers)
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]


def test_missing_idempotency_key_creates_new_job(client):
    payload = {"title": "No-Idem", "recipients": [{"name": "Bob"}]}
    first = client.post("/api/v1/jobs", json=payload)
    second = client.post("/api/v1/jobs", json=payload)
    assert first.json()["id"] != second.json()["id"]


# --------------------------------------------------------------------------- #
# Observability
# --------------------------------------------------------------------------- #
def test_request_id_header_is_added(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers.get("X-Request-ID")


def test_request_id_header_is_propagated(client):
    response = client.get("/health", headers={"X-Request-ID": "trace-42"})
    assert response.headers["X-Request-ID"] == "trace-42"


def test_readiness_probe(client):
    response = client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["storage"] == "ok"


# --------------------------------------------------------------------------- #
# Pagination
# --------------------------------------------------------------------------- #
def test_paginated_recipients_and_filtering(client):
    job = _create(client, [{"name": f"Person {i}"} for i in range(5)])

    page = client.get(
        f"/api/v1/jobs/{job['id']}/recipients", params={"limit": 2, "offset": 0}
    ).json()
    assert page["total"] == 5
    assert len(page["items"]) == 2
    assert [item["index"] for item in page["items"]] == [0, 1]

    completed = client.get(
        f"/api/v1/jobs/{job['id']}/recipients", params={"status": "COMPLETED"}
    ).json()
    assert completed["total"] == 5

    failed = client.get(
        f"/api/v1/jobs/{job['id']}/recipients", params={"status": "FAILED"}
    ).json()
    assert failed["total"] == 0


def test_job_detail_can_omit_recipients(client):
    job = _create(client, [{"name": "Solo"}])
    detail = client.get(
        f"/api/v1/jobs/{job['id']}", params={"include_recipients": False}
    ).json()
    assert detail["recipients"] == []
    assert detail["total_recipients"] == 1


# --------------------------------------------------------------------------- #
# ZIP export
# --------------------------------------------------------------------------- #
def test_download_all_certificates_as_zip(client):
    job = _create(client, [{"name": "One"}, {"name": "Two"}, {"name": "Three"}])
    response = client.get(f"/api/v1/jobs/{job['id']}/download")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"

    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = archive.namelist()
    assert len(names) == 3
    assert all(name.endswith(".pdf") for name in names)
    assert archive.read(names[0]).startswith(b"%PDF")


# --------------------------------------------------------------------------- #
# Verification & revocation
# --------------------------------------------------------------------------- #
def test_verify_and_revoke_certificate(client):
    job = _create(client, [{"name": "Verified Person"}])
    recipient = job["recipients"][0]
    cert_id = recipient["certificate_id"]

    metadata = client.get(f"/api/v1/certificates/{cert_id}").json()
    code = metadata["verification_code"]
    assert metadata["sha256"]
    assert metadata["verify_url"].endswith(code)

    verified = client.get(f"/api/v1/verify/{code}").json()
    assert verified["valid"] is True
    assert verified["recipient_name"] == "Verified Person"
    assert client.get(f"/api/v1/verify/{code}/download").status_code == 200

    revoked = client.post(f"/api/v1/certificates/{cert_id}/revoke").json()
    assert revoked["revoked"] is True

    after = client.get(f"/api/v1/verify/{code}").json()
    assert after["valid"] is False
    assert after["revoked"] is True
    assert client.get(f"/api/v1/verify/{code}/download").status_code == 410

    unknown = client.get("/api/v1/verify/DOESNOTEXIST").json()
    assert unknown["valid"] is False


# --------------------------------------------------------------------------- #
# Cancellation
# --------------------------------------------------------------------------- #
def test_cancel_running_job(client):
    settings = get_settings()
    original_mode = settings.generation_mode
    original_delay = settings.generation_delay_ms
    settings.generation_mode = "thread"
    settings.generation_delay_ms = 120
    try:
        job = _create(client, [{"name": f"Cancel {i}"} for i in range(8)])
        job_id = job["id"]

        deadline = time.time() + 10
        while time.time() < deadline:
            current = client.get(f"/api/v1/jobs/{job_id}").json()
            if current["processed"] >= 1:
                break
            time.sleep(0.03)

        cancel = client.post(f"/api/v1/jobs/{job_id}/cancel")
        assert cancel.status_code == 200

        final = _wait_terminal(client, job_id)
        assert final["status"] == "CANCELLED"
        assert final["succeeded"] < final["total_recipients"]
        assert any(r["status"] == "CANCELLED" for r in final["recipients"])
    finally:
        settings.generation_mode = original_mode
        settings.generation_delay_ms = original_delay


def test_cancel_finished_job_returns_conflict(client):
    job = _create(client, [{"name": "Done"}])
    response = client.post(f"/api/v1/jobs/{job['id']}/cancel")
    assert response.status_code == 409


# --------------------------------------------------------------------------- #
# Retry
# --------------------------------------------------------------------------- #
def test_retry_failed_recipients(client):
    long_name = "Z" * 130
    job = _create(client, [{"name": "Good"}, {"name": long_name}])
    assert job["status"] == "COMPLETED_WITH_ERRORS"

    retried = client.post(f"/api/v1/jobs/{job['id']}/retry")
    assert retried.status_code == 200
    body = retried.json()
    assert body["status"] == "COMPLETED_WITH_ERRORS"
    assert body["failed"] == 1

    failed = [r for r in body["recipients"] if r["status"] == "FAILED"][0]
    assert failed["attempts"] == 2


def test_retry_with_no_failures_returns_conflict(client):
    job = _create(client, [{"name": "All Good"}])
    response = client.post(f"/api/v1/jobs/{job['id']}/retry")
    assert response.status_code == 409


# --------------------------------------------------------------------------- #
# Crash recovery
# --------------------------------------------------------------------------- #
def test_recover_incomplete_jobs(client):
    job_id = uuid.uuid4().hex
    recipient_id = uuid.uuid4().hex

    db = SessionLocal()
    try:
        db.add(
            Job(
                id=job_id,
                title="Recovered",
                status=JobStatus.PENDING,
                cancel_requested=False,
                total_recipients=1,
                processed=0,
                succeeded=0,
                failed=0,
                created_at=utcnow(),
            )
        )
        db.add(
            Recipient(
                id=recipient_id,
                job_id=job_id,
                index=0,
                name="Recovered Person",
                status=RecipientStatus.PENDING,
                attempts=0,
            )
        )
        db.commit()
    finally:
        db.close()

    recovered = recover_incomplete_jobs()
    assert recovered >= 1

    detail = client.get(f"/api/v1/jobs/{job_id}").json()
    assert detail["status"] == "COMPLETED"
    assert detail["recipients"][0]["status"] == "COMPLETED"


# --------------------------------------------------------------------------- #
# SSE progress stream
# --------------------------------------------------------------------------- #
def test_sse_progress_stream_terminates(client):
    job = _create(client, [{"name": "Streamed"}])
    with client.stream("GET", f"/api/v1/jobs/{job['id']}/events") as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())

    assert "event:" in body
    assert "event: done" in body
    assert job["id"] in body
