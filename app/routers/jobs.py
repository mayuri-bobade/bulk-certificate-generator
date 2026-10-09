"""Job endpoints: submit bulk generation, track progress, cancel/retry, export."""
from __future__ import annotations

import json
import time
import zipfile
from collections.abc import Iterator
from tempfile import SpooledTemporaryFile

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal, get_db
from app.models import (
    TERMINAL_JOB_STATUSES,
    Certificate,
    Job,
    RecipientStatus,
)
from app.schemas import (
    CertificateOut,
    JobCreate,
    JobDetailOut,
    JobListOut,
    RecipientsPageOut,
)
from app.services import (
    create_job,
    get_job_by_idempotency_key,
    list_jobs,
    list_recipients,
    request_cancel,
    reset_failed_recipients,
    serialize_certificate,
    serialize_detail,
    serialize_recipient,
    serialize_summary,
)
from app.worker import dispatch

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


@router.post(
    "",
    response_model=JobDetailOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a bulk certificate generation request",
)
def submit_job(
    payload: JobCreate,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
) -> JobDetailOut:
    settings = get_settings()
    if len(payload.recipients) > settings.max_recipients_per_job:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Too many recipients: {len(payload.recipients)} "
                f"(max {settings.max_recipients_per_job})"
            ),
        )

    # Idempotent replay: return the original job instead of creating a duplicate.
    if idempotency_key:
        existing = get_job_by_idempotency_key(db, idempotency_key)
        if existing is not None:
            response.status_code = status.HTTP_200_OK
            return serialize_detail(existing)

    try:
        job = create_job(db, payload, idempotency_key=idempotency_key)
    except IntegrityError:
        # Lost a race on the unique idempotency key; return the winner.
        db.rollback()
        existing = get_job_by_idempotency_key(db, idempotency_key)
        if existing is None:
            raise
        response.status_code = status.HTTP_200_OK
        return serialize_detail(existing)

    dispatch(job.id)

    # Refresh so an inline (synchronous) run is reflected immediately.
    db.expire_all()
    job = db.get(Job, job.id)
    return serialize_detail(job)


@router.get("", response_model=JobListOut, summary="List generation jobs")
def get_jobs(
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> JobListOut:
    total, jobs = list_jobs(db, limit=limit, offset=offset)
    return JobListOut(total=total, items=[serialize_summary(job) for job in jobs])


@router.get("/{job_id}", response_model=JobDetailOut, summary="Get job status")
def get_job(
    job_id: str,
    include_recipients: bool = Query(
        True, description="Include per-recipient results in the response."
    ),
    db: Session = Depends(get_db),
) -> JobDetailOut:
    job = _get_job_or_404(db, job_id)
    return serialize_detail(job, include_recipients=include_recipients)


@router.get(
    "/{job_id}/recipients",
    response_model=RecipientsPageOut,
    summary="Paginated per-recipient results",
)
def get_job_recipients(
    job_id: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    recipient_status: RecipientStatus | None = Query(
        None, alias="status", description="Filter by recipient status."
    ),
    db: Session = Depends(get_db),
) -> RecipientsPageOut:
    _get_job_or_404(db, job_id)
    total, recipients = list_recipients(
        db, job_id, limit=limit, offset=offset, status=recipient_status
    )
    return RecipientsPageOut(
        total=total,
        limit=limit,
        offset=offset,
        items=[serialize_recipient(recipient) for recipient in recipients],
    )


@router.post("/{job_id}/cancel", response_model=JobDetailOut, summary="Cancel a job")
def cancel_job(job_id: str, db: Session = Depends(get_db)) -> JobDetailOut:
    job = _get_job_or_404(db, job_id)
    if not request_cancel(db, job):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job already finished with status {job.status}",
        )
    db.refresh(job)
    return serialize_detail(job)


@router.post(
    "/{job_id}/retry", response_model=JobDetailOut, summary="Retry failed recipients"
)
def retry_job(job_id: str, db: Session = Depends(get_db)) -> JobDetailOut:
    job = _get_job_or_404(db, job_id)
    if job.status not in TERMINAL_JOB_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Job is still running; wait for it to finish before retrying.",
        )

    reset_count = reset_failed_recipients(db, job)
    if reset_count == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Job has no failed recipients to retry.",
        )

    dispatch(job.id)
    db.expire_all()
    job = db.get(Job, job.id)
    return serialize_detail(job)


@router.get(
    "/{job_id}/certificates",
    response_model=list[CertificateOut],
    summary="List certificates generated for a job",
)
def get_job_certificates(
    job_id: str, db: Session = Depends(get_db)
) -> list[CertificateOut]:
    _get_job_or_404(db, job_id)
    certificates = db.scalars(
        select(Certificate)
        .where(Certificate.job_id == job_id)
        .order_by(Certificate.created_at)
    ).all()
    return [serialize_certificate(certificate) for certificate in certificates]


@router.get("/{job_id}/download", summary="Download all certificates as a ZIP archive")
def download_job_zip(job_id: str, db: Session = Depends(get_db)) -> StreamingResponse:
    job = _get_job_or_404(db, job_id)
    settings = get_settings()
    certificates = db.scalars(
        select(Certificate).where(
            Certificate.job_id == job_id, Certificate.revoked.is_(False)
        )
    ).all()

    spool = SpooledTemporaryFile(max_size=64 * 1024 * 1024)
    written = 0
    with zipfile.ZipFile(spool, "w", zipfile.ZIP_DEFLATED) as archive:
        for certificate in certificates:
            file_path = settings.storage_path / certificate.file_path
            if file_path.is_file():
                archive.write(file_path, arcname=certificate.file_name)
                written += 1

    if written == 0:
        spool.close()
        raise HTTPException(status_code=404, detail="No certificates available yet")

    spool.seek(0)

    def _iter() -> Iterator[bytes]:
        try:
            while chunk := spool.read(1024 * 1024):
                yield chunk
        finally:
            spool.close()

    return StreamingResponse(
        _iter(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{job_id}.zip"'},
    )


@router.get("/{job_id}/events", summary="Server-Sent Events progress stream")
def job_events(job_id: str, request: Request) -> StreamingResponse:
    # Validate the job exists before opening the stream.
    db = SessionLocal()
    try:
        if db.get(Job, job_id) is None:
            raise HTTPException(status_code=404, detail="Job not found")
    finally:
        db.close()

    return StreamingResponse(
        _event_stream(job_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _event_stream(job_id: str) -> Iterator[str]:
    last_payload: str | None = None
    while True:
        db = SessionLocal()
        try:
            job = db.get(Job, job_id)
            if job is None:
                yield _sse("error", {"detail": "Job not found"})
                return
            progress = {
                "id": job.id,
                "status": str(job.status),
                "total": job.total_recipients,
                "processed": job.processed,
                "succeeded": job.succeeded,
                "failed": job.failed,
                "cancel_requested": job.cancel_requested,
            }
            terminal = job.status in TERMINAL_JOB_STATUSES
        finally:
            db.close()

        payload = json.dumps(progress)
        if payload != last_payload:
            yield _sse("progress", progress)
            last_payload = payload

        if terminal:
            yield _sse("done", progress)
            return

        time.sleep(0.5)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _get_job_or_404(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
