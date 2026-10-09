"""Predefined certificate template and PDF rendering.

The template is intentionally a single fixed design (as required by the
assignment). Recipient-specific information is injected at generation time.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

#: Names longer than this cannot be laid out sensibly on the template and are
#: treated as a per-recipient generation failure.
MAX_NAME_LENGTH = 120

_ILLEGAL_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

PAGE_WIDTH, PAGE_HEIGHT = landscape(A4)

PRIMARY = HexColor("#1b3b5f")
ACCENT = HexColor("#b8860b")
BACKGROUND = HexColor("#fbf8f1")
MUTED = HexColor("#5a6b7b")


class CertificateGenerationError(Exception):
    """Raised when a single recipient's certificate cannot be generated."""


def validate_recipient(name: str) -> str:
    """Validate recipient data at generation time.

    Returns the cleaned name or raises :class:`CertificateGenerationError`.
    This is a second, stricter validation layer (the first one lives in the
    Pydantic request schema) so a single bad record never aborts a whole job.
    """
    if name is None:
        raise CertificateGenerationError("recipient name is required")

    cleaned = name.strip()
    if not cleaned:
        raise CertificateGenerationError("recipient name must not be blank")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise CertificateGenerationError(
            f"recipient name exceeds the maximum length of {MAX_NAME_LENGTH} characters"
        )
    if _ILLEGAL_CONTROL_CHARS.search(cleaned):
        raise CertificateGenerationError("recipient name contains control characters")
    return cleaned


def sha256_of_file(path: Path) -> str:
    """Return the hex SHA-256 digest of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fit_font_size(text: str, max_width: float, base_size: float) -> float:
    """Shrink the font size until ``text`` fits within ``max_width`` points."""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    size = base_size
    while size > 10 and stringWidth(text, "Helvetica-Bold", size) > max_width:
        size -= 1
    return size


def _qr_image_reader(verify_url: str) -> ImageReader | None:
    """Build a QR code image for the verification URL (best effort)."""
    try:
        import qrcode  # imported lazily so the core has no hard dependency
    except Exception:  # pragma: no cover - optional dependency
        return None
    try:
        image = qrcode.make(verify_url)
        return ImageReader(image)
    except Exception:  # pragma: no cover - never fail generation for a QR
        return None


def build_certificate(
    *,
    recipient_name: str,
    output_path: Path,
    title: str,
    subtitle: str | None = None,
    issuer: str | None = None,
    signatory_name: str | None = None,
    signatory_title: str | None = None,
    issue_date: str | None = None,
    course: str | None = None,
    identifier: str | None = None,
    verification_code: str | None = None,
    verify_url: str | None = None,
) -> Path:
    """Render a single certificate PDF and return its path."""
    name = validate_recipient(recipient_name)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(output_path), pagesize=(PAGE_WIDTH, PAGE_HEIGHT))
    pdf.setTitle(f"{title} - {name}")

    # Background and decorative border.
    pdf.setFillColor(BACKGROUND)
    pdf.rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT, fill=1, stroke=0)

    pdf.setStrokeColor(PRIMARY)
    pdf.setLineWidth(6)
    pdf.rect(24, 24, PAGE_WIDTH - 48, PAGE_HEIGHT - 48, fill=0, stroke=1)
    pdf.setStrokeColor(ACCENT)
    pdf.setLineWidth(1.5)
    pdf.rect(38, 38, PAGE_WIDTH - 76, PAGE_HEIGHT - 76, fill=0, stroke=1)

    center_x = PAGE_WIDTH / 2

    # Title.
    pdf.setFillColor(PRIMARY)
    pdf.setFont("Helvetica-Bold", 40)
    pdf.drawCentredString(center_x, PAGE_HEIGHT - 140, title)

    if subtitle:
        pdf.setFillColor(MUTED)
        pdf.setFont("Helvetica", 16)
        pdf.drawCentredString(center_x, PAGE_HEIGHT - 170, subtitle)

    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 15)
    pdf.drawCentredString(center_x, PAGE_HEIGHT / 2 + 60, "This is to certify that")

    # Recipient name, auto-sized to fit.
    name_size = _fit_font_size(name, PAGE_WIDTH - 200, 38)
    pdf.setFillColor(PRIMARY)
    pdf.setFont("Helvetica-Bold", name_size)
    pdf.drawCentredString(center_x, PAGE_HEIGHT / 2 + 5, name)

    # Underline beneath the name.
    underline_half = max(120, min((PAGE_WIDTH - 200) / 2, 320))
    pdf.setStrokeColor(ACCENT)
    pdf.setLineWidth(1.2)
    pdf.line(
        center_x - underline_half,
        PAGE_HEIGHT / 2 - 12,
        center_x + underline_half,
        PAGE_HEIGHT / 2 - 12,
    )

    detail_parts: list[str] = []
    if course:
        detail_parts.append(course)
    if identifier:
        detail_parts.append(f"ID: {identifier}")
    if detail_parts:
        pdf.setFillColor(MUTED)
        pdf.setFont("Helvetica", 14)
        pdf.drawCentredString(center_x, PAGE_HEIGHT / 2 - 45, "  |  ".join(detail_parts))

    # Footer with date (left) and signatory (right).
    footer_y = 90
    pdf.setStrokeColor(MUTED)
    pdf.setLineWidth(1)
    if issue_date:
        pdf.line(90, footer_y, 290, footer_y)
        pdf.setFillColor(PRIMARY)
        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawCentredString(190, footer_y + 8, issue_date)
        pdf.setFillColor(MUTED)
        pdf.setFont("Helvetica", 11)
        pdf.drawCentredString(190, footer_y - 14, "Date")

    if signatory_name:
        pdf.line(PAGE_WIDTH - 290, footer_y, PAGE_WIDTH - 90, footer_y)
        pdf.setFillColor(PRIMARY)
        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawCentredString(PAGE_WIDTH - 190, footer_y + 8, signatory_name)
        pdf.setFillColor(MUTED)
        pdf.setFont("Helvetica", 11)
        pdf.drawCentredString(PAGE_WIDTH - 190, footer_y - 14, signatory_title or "Signature")

    if issuer:
        pdf.setFillColor(MUTED)
        pdf.setFont("Helvetica-Oblique", 11)
        pdf.drawCentredString(center_x, 60, issuer)

    # Verification code + QR (integrity/trust).
    if verification_code:
        pdf.setFillColor(MUTED)
        pdf.setFont("Helvetica", 9)
        pdf.drawCentredString(
            center_x, 44, f"Verify at {verify_url or 'the issuer portal'}  •  {verification_code}"
        )

    if verify_url:
        reader = _qr_image_reader(verify_url)
        if reader is not None:
            pdf.drawImage(
                reader,
                PAGE_WIDTH - 150,
                52,
                width=64,
                height=64,
                preserveAspectRatio=True,
                mask="auto",
            )

    pdf.showPage()
    pdf.save()
    return output_path
