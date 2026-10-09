"""Public certificate verification endpoints.

These are intentionally unauthenticated: anyone holding a certificate (or its
verification code) can confirm it is genuine and not revoked.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Certificate
from app.schemas import VerifyOut
from app.services import certificate_download_url

router = APIRouter(prefix="/api/v1/verify", tags=["verification"])


def _get_certificate_by_code(db: Session, code: str) -> Certificate | None:
    return db.scalar(
        select(Certificate).where(Certificate.verification_code == code.strip().upper())
    )


@router.get("/{verification_code}", response_model=VerifyOut, summary="Verify a certificate")
def verify_certificate(
    verification_code: str, db: Session = Depends(get_db)
) -> VerifyOut:
    certificate = _get_certificate_by_code(db, verification_code)
    if certificate is None:
        return VerifyOut(
            valid=False,
            reason="Unknown verification code",
            verification_code=verification_code,
        )

    settings = get_settings()
    file_exists = (settings.storage_path / certificate.file_path).is_file()
    valid = not certificate.revoked and file_exists

    reason = None
    if certificate.revoked:
        reason = "Certificate has been revoked"
    elif not file_exists:
        reason = "Certificate file is unavailable"

    recipient = certificate.recipient
    job = certificate.job
    return VerifyOut(
        valid=valid,
        reason=reason,
        certificate_id=certificate.id,
        verification_code=certificate.verification_code,
        recipient_name=recipient.name if recipient else None,
        job_title=job.title if job else None,
        issue_date=job.issue_date if job else None,
        issued_at=certificate.created_at,
        revoked=certificate.revoked,
        download_url=(
            certificate_download_url(certificate.id) if valid else None
        ),
    )


@router.get("/{verification_code}/download", summary="Download a verified certificate")
def download_verified(
    verification_code: str, db: Session = Depends(get_db)
) -> FileResponse:
    certificate = _get_certificate_by_code(db, verification_code)
    if certificate is None:
        raise HTTPException(status_code=404, detail="Unknown verification code")
    if certificate.revoked:
        raise HTTPException(status_code=410, detail="Certificate has been revoked")

    settings = get_settings()
    file_path = settings.storage_path / certificate.file_path
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Certificate file is missing")

    return FileResponse(
        path=file_path,
        media_type="application/pdf",
        filename=certificate.file_name,
    )
