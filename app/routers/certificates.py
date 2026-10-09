"""Certificate endpoints: metadata, download and revocation."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Certificate
from app.schemas import CertificateOut
from app.services import serialize_certificate

router = APIRouter(prefix="/api/v1/certificates", tags=["certificates"])


def _get_certificate_or_404(db: Session, certificate_id: str) -> Certificate:
    certificate = db.get(Certificate, certificate_id)
    if certificate is None:
        raise HTTPException(status_code=404, detail="Certificate not found")
    return certificate


@router.get("/{certificate_id}", response_model=CertificateOut, summary="Certificate metadata")
def get_certificate(
    certificate_id: str, db: Session = Depends(get_db)
) -> CertificateOut:
    return serialize_certificate(_get_certificate_or_404(db, certificate_id))


@router.get("/{certificate_id}/download", summary="Download a generated certificate PDF")
def download_certificate(
    certificate_id: str, db: Session = Depends(get_db)
) -> FileResponse:
    certificate = _get_certificate_or_404(db, certificate_id)
    settings = get_settings()
    file_path = settings.storage_path / certificate.file_path
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Certificate file is missing")

    return FileResponse(
        path=file_path,
        media_type="application/pdf",
        filename=certificate.file_name,
    )


@router.post(
    "/{certificate_id}/revoke",
    response_model=CertificateOut,
    summary="Revoke a certificate (fails verification)",
)
def revoke_certificate(
    certificate_id: str, db: Session = Depends(get_db)
) -> CertificateOut:
    certificate = _get_certificate_or_404(db, certificate_id)
    certificate.revoked = True
    db.commit()
    db.refresh(certificate)
    return serialize_certificate(certificate)
