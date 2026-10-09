"""Pytest fixtures.

Environment variables are configured *before* the application is imported so
that the cached settings, database engine and storage paths point at a
throwaway temp directory.
"""
from __future__ import annotations

import os
import shutil
import tempfile

_TMP_DIR = tempfile.mkdtemp(prefix="certgen-tests-")

os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DIR}/test.db".replace("\\", "/")
os.environ["STORAGE_DIR"] = os.path.join(_TMP_DIR, "storage")
# Run generation synchronously by default so tests are deterministic.
os.environ["GENERATION_MODE"] = "inline"
os.environ["MAX_WORKERS"] = "2"
os.environ["MAX_RECIPIENTS_PER_JOB"] = "50"
os.environ["LOG_LEVEL"] = "WARNING"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def cleanup_temp_dir():
    yield
    shutil.rmtree(_TMP_DIR, ignore_errors=True)


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def settings():
    return get_settings()
