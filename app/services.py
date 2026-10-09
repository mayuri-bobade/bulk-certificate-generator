"""Domain services: job creation, state transitions and serialization."""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    TERMINAL_JOB_STATUSES,
    Certificate,
    Job,
    JobStatus,
    Recipient,
    RecipientStatus,
    utcnow,
)
from app.schemas import (
    CertificateOut,
    JobCreate,
    JobDetailOut,
    JobSummaryOut,
    RecipientResultOut,
)


def project_id() -> str:
    return uuid4().hex


def certificate_download_url(certificate_id: str) -> str:
    return f"/api/v1/certificates/{certificate_id}/download"


def certificate_verify_url(verification_code: str) -> str:
    return f"/api/v1/verify/{verification_code}"


def create_job(
    db: Session, payload: JobCreate, idempotency_key: str | None = None
) -> Job:
    """Persist a job and its recipients in one transaction."""
    job = Job(
        id=project_id(),
        title=payload.title,
        subtitle=payload.subtitle,
        issuer=payload.issuer,
        signatory_name=payload.signatory_name,
        signatory_title=payload.signatory_title,
        issue_date=payload.issue_date,
        status=JobStatus.PENDING,
        idempotency_key=idempotency_key,
        cancel_requested=False,
        total_recipients=len(payload.recipients),
        processed=0,
        succeeded=0,
        failed=0,
        created_at=utcnow(),
    )
    db.add(job)

    for index, recipient in enumerate(payload.recipients):
        db.add(
            Recipient(
                id=project_id(),
                job_id=job.id,
                index=index,
                name=recipient.name,
                email=str(recipient.email) if recipient.email else None,
                identifier=recipient.identifier,
                course=recipient.course,
                status=RecipientStatus.PENDING,
                attempts=0,
            )
        )

    db.commit()
    db.refresh(job)
    return job


def get_job_by_idempotency_key(db: Session, key: str) -> Job | None:
    return db.scalar(select(Job).where(Job.idempotency_key == key))


def serialize_summary(job: Job) -> JobSummaryOut:
    return JobSummaryOut.model_validate(job)


def serialize_recipient(recipient: Recipient) -> RecipientResultOut:
    return RecipientResultOut(
        id=recipient.id,
        index=recipient.index,
        name=recipient.name,
        email=recipient.email,
        identifier=recipient.identifier,
        course=recipient.course,
        status=recipient.status,
        attempts=recipient.attempts,
        error=recipient.error,
        certificate_id=recipient.certificate.id if recipient.certificate else None,
        download_url=(
            certificate_download_url(recipient.certificate.id)
            if recipient.certificate
            else None
        ),
    )


def serialize_detail(job: Job, *, include_recipients: bool = True) -> JobDetailOut:
    recipients: list[RecipientResultOut] = []
    if include_recipients:
        recipients = [
            serialize_recipient(recipient)
            for recipient in sorted(job.recipients, key=lambda r: r.index)
        ]
    return JobDetailOut(
        id=job.id,
        status=job.status,
        title=job.title,
        subtitle=job.subtitle,
        issuer=job.issuer,
        signatory_name=job.signatory_name,
        signatory_title=job.signatory_title,
        issue_date=job.issue_date,
        cancel_requested=job.cancel_requested,
        error=job.error,
        total_recipients=job.total_recipients,
        processed=job.processed,
        succeeded=job.succeeded,
        failed=job.failed,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        recipients=recipients,
    )


def serialize_certificate(certificate: Certificate) -> CertificateOut:
    data = CertificateOut.model_validate(certificate)
    data.download_url = certificate_download_url(certificate.id)
    data.verify_url = certificate_verify_url(certificate.verification_code)
    return data


def list_jobs(db: Session, limit: int, offset: int) -> tuple[int, list[Job]]:
    total = db.scalar(select(func.count()).select_from(Job)) or 0
    jobs = db.scalars(
        select(Job).order_by(Job.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return total, list(jobs)


def list_recipients(
    db: Session,
    job_id: str,
    *,
    limit: int,
    offset: int,
    status: RecipientStatus | None = None,
) -> tuple[int, list[Recipient]]:
    filters = [Recipient.job_id == job_id]
    if status is not None:
        filters.append(Recipient.status == status)

    total = db.scalar(
        select(func.count()).select_from(Recipient).where(*filters)
    ) or 0
    recipients = db.scalars(
        select(Recipient)
        .where(*filters)
        .order_by(Recipient.index)
        .limit(limit)
        .offset(offset)
    ).all()
    return total, list(recipients)


def request_cancel(db: Session, job: Job) -> bool:
    """Flag a job for cancellation. Returns False if it already finished."""
    if job.status in TERMINAL_JOB_STATUSES:
        return False
    job.cancel_requested = True
    db.commit()
    return True


def reset_failed_recipients(db: Session, job: Job) -> int:
    """Reset FAILED recipients so the job can be re-dispatched."""
    failed = [
        recipient
        for recipient in job.recipients
        if recipient.status == RecipientStatus.FAILED
    ]
    for recipient in failed:
        recipient.status = RecipientStatus.PENDING
        recipient.error = None

    job.failed = 0
    job.processed = job.succeeded
    job.status = JobStatus.PENDING
    job.error = None
    job.started_at = None
    job.completed_at = None
    job.cancel_requested = False
    db.commit()
    return len(failed)
