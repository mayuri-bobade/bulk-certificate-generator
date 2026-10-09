"""Background certificate generation worker.

A single request can contain many recipients, so generation is processed by a
thread pool. Each job is split into independent per-recipient units of work:
if one certificate fails, the remaining recipients are still processed and the
failure is recorded on that recipient only.

Each recipient is processed in its own short-lived session so that a failure /
rollback never affects other recipients and transient errors can be retried.
"""
from __future__ import annotations

import logging
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from app.certificate import CertificateGenerationError, build_certificate, sha256_of_file
from app.config import get_settings
from app.database import SessionLocal
from app.models import (
    TERMINAL_JOB_STATUSES,
    Certificate,
    Job,
    JobStatus,
    Recipient,
    RecipientStatus,
    utcnow,
)

logger = logging.getLogger("app.worker")

settings = get_settings()

_executor: ThreadPoolExecutor | None = None


def get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(
            max_workers=settings.max_workers, thread_name_prefix="certgen"
        )
    return _executor


def dispatch(job_id: str, *, inline: bool = False) -> None:
    """Schedule a job for processing.

    In inline mode the job is processed synchronously (used by tests and very
    small deployments); otherwise it is handed to the background pool.
    """
    if inline or settings.generation_mode == "inline":
        process_job(job_id)
    else:
        get_executor().submit(process_job, job_id)


def _new_verification_code() -> str:
    return secrets.token_hex(8).upper()


def generate_certificate(db, job: Job, recipient: Recipient) -> Certificate:
    """Generate a PDF for a single recipient and persist its metadata.

    Any exception from the renderer is re-raised so the caller can record it
    against the individual recipient.
    """
    certificate_id = uuid4().hex
    verification_code = _new_verification_code()
    verify_url = f"{settings.verification_base_url}/{verification_code}"

    relative_dir = Path("certificates") / job.id
    file_name = f"{certificate_id}.pdf"
    absolute_path = settings.storage_path / relative_dir / file_name

    try:
        build_certificate(
            recipient_name=recipient.name,
            output_path=absolute_path,
            title=job.title,
            subtitle=job.subtitle,
            issuer=job.issuer,
            signatory_name=job.signatory_name,
            signatory_title=job.signatory_title,
            issue_date=job.issue_date,
            course=recipient.course,
            identifier=recipient.identifier,
            verification_code=verification_code,
            verify_url=verify_url,
        )
    except Exception:
        # Never leave a partially written file behind.
        if absolute_path.exists():
            absolute_path.unlink(missing_ok=True)
        raise

    certificate = Certificate(
        id=certificate_id,
        job_id=job.id,
        recipient_id=recipient.id,
        file_name=file_name,
        file_path=str(relative_dir / file_name).replace("\\", "/"),
        size_bytes=absolute_path.stat().st_size,
        verification_code=verification_code,
        sha256=sha256_of_file(absolute_path),
        created_at=utcnow(),
    )
    db.add(certificate)
    return certificate


def _next_status(job: Job) -> JobStatus:
    if job.failed == 0:
        return JobStatus.COMPLETED
    if job.succeeded > 0:
        return JobStatus.COMPLETED_WITH_ERRORS
    return JobStatus.FAILED


def _is_cancel_requested(job_id: str) -> bool:
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        return bool(job and job.cancel_requested)
    finally:
        db.close()


def _process_one_recipient(job_id: str, recipient_id: str) -> None:
    """Process a single recipient with bounded retries, in its own sessions.

    The attempt counter is committed *before* rendering so that it accurately
    counts every attempt, including ones that are rolled back on retry.
    """
    for attempt in range(1, settings.max_attempts + 1):
        db = SessionLocal()
        try:
            job = db.get(Job, job_id)
            recipient = db.get(Recipient, recipient_id)
            if job is None or recipient is None:
                return

            # Already done (e.g. a retry pass) - never regenerate.
            if recipient.status == RecipientStatus.COMPLETED:
                return

            recipient.attempts += 1
            db.commit()  # persist the attempt counter independently

            try:
                generate_certificate(db, job, recipient)
                recipient.status = RecipientStatus.COMPLETED
                recipient.error = None
                job.succeeded += 1
                job.processed += 1
                db.commit()
                return
            except CertificateGenerationError as exc:
                # Permanent, recipient-specific validation failure: no retry.
                recipient.status = RecipientStatus.FAILED
                recipient.error = str(exc)
                job.failed += 1
                job.processed += 1
                db.commit()
                logger.warning(
                    "Recipient %s in job %s failed validation: %s",
                    recipient_id,
                    job_id,
                    exc,
                )
                return
            except Exception as exc:  # noqa: BLE001 - transient, retry
                db.rollback()
                if attempt >= settings.max_attempts:
                    recipient = db.get(Recipient, recipient_id)
                    job = db.get(Job, job_id)
                    recipient.status = RecipientStatus.FAILED
                    recipient.error = (
                        f"unexpected error after {attempt} attempt(s): {exc}"
                    )
                    job.failed += 1
                    job.processed += 1
                    db.commit()
                    logger.exception(
                        "Recipient %s in job %s failed after %s attempts",
                        recipient_id,
                        job_id,
                        attempt,
                    )
                    return
                logger.warning(
                    "Recipient %s in job %s attempt %s failed (%s); retrying",
                    recipient_id,
                    job_id,
                    attempt,
                    exc,
                )
                time.sleep(settings.retry_backoff_seconds * attempt)
        finally:
            db.close()


def process_job(job_id: str) -> None:
    """Process every recipient of a job, isolating per-recipient failures."""
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None:
            logger.warning("Job %s not found; nothing to process", job_id)
            return
        if job.status in TERMINAL_JOB_STATUSES:
            return
        if job.cancel_requested:
            _finalize_job(job_id, cancelled=True)
            return

        job.status = JobStatus.PROCESSING
        if job.started_at is None:
            job.started_at = utcnow()
        db.commit()

        recipient_ids = list(
            db.scalars(
                select(Recipient.id)
                .where(Recipient.job_id == job_id)
                .order_by(Recipient.index)
            ).all()
        )
    finally:
        db.close()

    cancelled = False
    for recipient_id in recipient_ids:
        if _is_cancel_requested(job_id):
            cancelled = True
            break
        if settings.generation_delay_ms:
            time.sleep(settings.generation_delay_ms / 1000.0)
        _process_one_recipient(job_id, recipient_id)

    _finalize_job(job_id, cancelled=cancelled)

    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is not None:
            logger.info(
                "Job %s finished with status %s (%s ok, %s failed)",
                job_id,
                job.status,
                job.succeeded,
                job.failed,
            )
    finally:
        db.close()


def _finalize_job(job_id: str, *, cancelled: bool = False) -> None:
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None:
            return

        if cancelled or job.cancel_requested:
            remaining = db.scalars(
                select(Recipient).where(
                    Recipient.job_id == job_id,
                    Recipient.status.in_(
                        [RecipientStatus.PENDING, RecipientStatus.PROCESSING]
                    ),
                )
            ).all()
            for recipient in remaining:
                recipient.status = RecipientStatus.CANCELLED
            job.status = JobStatus.CANCELLED
        else:
            job.status = _next_status(job)

        job.completed_at = utcnow()
        db.commit()
    finally:
        db.close()


def recover_incomplete_jobs() -> int:
    """Re-dispatch jobs left in PENDING/PROCESSING (e.g. after a crash)."""
    if not settings.recover_incomplete_jobs:
        return 0

    db = SessionLocal()
    try:
        job_ids = list(
            db.scalars(
                select(Job.id).where(
                    Job.status.in_([JobStatus.PENDING, JobStatus.PROCESSING])
                )
            ).all()
        )
    finally:
        db.close()

    for job_id in job_ids:
        logger.info("Recovering incomplete job %s", job_id)
        dispatch(job_id)
    return len(job_ids)


def shutdown_executor() -> None:
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=True)
        _executor = None
