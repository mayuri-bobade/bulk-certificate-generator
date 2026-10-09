"""Pydantic request/response schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models import JobStatus, RecipientStatus


class RecipientIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=500)
    email: EmailStr | None = None
    identifier: str | None = Field(default=None, max_length=200)
    course: str | None = Field(default=None, max_length=200)

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class JobCreate(BaseModel):
    title: str = Field(default="Certificate of Completion", max_length=200)
    subtitle: str | None = Field(default=None, max_length=300)
    issuer: str | None = Field(default=None, max_length=200)
    signatory_name: str | None = Field(default=None, max_length=200)
    signatory_title: str | None = Field(default=None, max_length=200)
    issue_date: str | None = Field(default=None, max_length=100)

    recipients: list[RecipientIn] = Field(..., min_length=1)


class JobSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: JobStatus
    title: str
    cancel_requested: bool = False
    total_recipients: int
    processed: int
    succeeded: int
    failed: int
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class RecipientResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    index: int
    name: str
    email: str | None = None
    identifier: str | None = None
    course: str | None = None
    status: RecipientStatus
    attempts: int = 0
    error: str | None = None
    certificate_id: str | None = None
    download_url: str | None = None


class JobDetailOut(JobSummaryOut):
    subtitle: str | None = None
    issuer: str | None = None
    signatory_name: str | None = None
    signatory_title: str | None = None
    issue_date: str | None = None
    error: str | None = None
    recipients: list[RecipientResultOut] = Field(default_factory=list)


class RecipientsPageOut(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[RecipientResultOut]


class CertificateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    job_id: str
    recipient_id: str
    file_name: str
    size_bytes: int
    verification_code: str
    sha256: str | None = None
    revoked: bool = False
    created_at: datetime
    download_url: str | None = None
    verify_url: str | None = None


class VerifyOut(BaseModel):
    valid: bool
    reason: str | None = None
    certificate_id: str | None = None
    verification_code: str
    recipient_name: str | None = None
    job_title: str | None = None
    issue_date: str | None = None
    issued_at: datetime | None = None
    revoked: bool = False
    download_url: str | None = None


class JobListOut(BaseModel):
    total: int
    items: list[JobSummaryOut]
