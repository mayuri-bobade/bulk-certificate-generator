"""Tests for asynchronous (background thread) processing and progress tracking."""
from __future__ import annotations

import time

from app.config import get_settings

TERMINAL_STATUSES = {"COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"}


def _wait_for_terminal(client, job_id, timeout=15.0):
    deadline = time.time() + timeout
    job = None
    while time.time() < deadline:
        response = client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        job = response.json()
        if job["status"] in TERMINAL_STATUSES:
            return job
        time.sleep(0.05)
    raise AssertionError(f"Job did not finish in time: {job}")


def test_background_thread_processing(client):
    settings = get_settings()
    original_mode = settings.generation_mode
    settings.generation_mode = "thread"
    try:
        recipients = [{"name": f"Background {i}"} for i in range(5)]
        response = client.post(
            "/api/v1/jobs",
            json={"title": "Async Certificate", "recipients": recipients},
        )
        assert response.status_code == 202
        job_id = response.json()["id"]

        job = _wait_for_terminal(client, job_id)

        assert job["status"] == "COMPLETED"
        assert job["processed"] == 5
        assert job["succeeded"] == 5
        assert job["failed"] == 0
        assert job["started_at"] is not None
        assert job["completed_at"] is not None
    finally:
        settings.generation_mode = original_mode
