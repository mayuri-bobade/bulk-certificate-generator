"""SQLAlchemy ORM models."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_ERRORS = "COMPLETED_WITH_ERRORS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


#: Job statuses from which no further work happens.
TERMINAL_JOB_STATUSES = frozenset(
    {
        JobStatus.COMPLETED,
        JobStatus.COMPLETED_WITH_ERRORS,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    }
)


class RecipientStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    subtitle: Mapped[str | None] = mapped_column(String(300), nullable=True)
    issuer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    signatory_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    signatory_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    issue_date: Mapped[str | None] = mapped_column(String(100), nullable=True)

    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=32),
        default=JobStatus.PENDING,
        nullable=False,
        index=True,
    )

    # Optional client-supplied key so retries of the same request are idempotent.
    idempotency_key: Mapped[str | None] = mapped_column(
        String(200), nullable=True, unique=True, index=True
    )

    # Set by the cancel endpoint; the worker observes it between recipients.
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    total_recipients: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    succeeded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    recipients: Mapped[list["Recipient"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="Recipient.index",
    )
    certificates: Mapped[list["Certificate"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
    )


class Recipient(Base):
    __tablename__ = "recipients"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )

    index: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    identifier: Mapped[str | None] = mapped_column(String(200), nullable=True)
    course: Mapped[str | None] = mapped_column(String(200), nullable=True)

    status: Mapped[RecipientStatus] = mapped_column(
        Enum(RecipientStatus, native_enum=False, length=32),
        default=RecipientStatus.PENDING,
        nullable=False,
        index=True,
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    job: Mapped["Job"] = relationship(back_populates="recipients")
    certificate: Mapped["Certificate | None"] = relationship(
        back_populates="recipient",
        cascade="all, delete-orphan",
        uselist=False,
    )


class Certificate(Base):
    __tablename__ = "certificates"
    __table_args__ = (
        UniqueConstraint("recipient_id", name="uq_certificate_recipient"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recipient_id: Mapped[str] = mapped_column(
        ForeignKey("recipients.id", ondelete="CASCADE"), nullable=False
    )

    file_name: Mapped[str] = mapped_column(String(300), nullable=False)
    # Path relative to the configured storage directory.
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Integrity / trust features.
    verification_code: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True, index=True
    )
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    job: Mapped["Job"] = relationship(back_populates="certificates")
    recipient: Mapped["Recipient"] = relationship(back_populates="certificate")
