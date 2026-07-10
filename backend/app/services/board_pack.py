"""Board pack export — PowerPoint and PDF from published forecast versions."""

from __future__ import annotations

import io
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models.forecast import ForecastVersion, ForecastLineResult
from app.models.line_item import LineItem

logger = logging.getLogger(__name__)


def _aggregate_by_category(db: Session, version_id: str) -> list[dict[str, Any]]:
    results = (
        db.query(ForecastLineResult, LineItem)
        .join(LineItem, LineItem.id == ForecastLineResult.line_item_id)
        .filter(ForecastLineResult.version_id == version_id)
        .all()
    )
    buckets: dict[str, float] = {}
    for r, li in results:
        val = r.override_value if r.is_overridden else r.p50
        buckets[li.category] = buckets.get(li.category, 0.0) + float(val or 0)
    return [{"category": k, "total": v} for k, v in sorted(buckets.items())]


def build_board_pack_pptx(db: Session, version: ForecastVersion) -> bytes:
    """Generate a simple board-ready PowerPoint deck."""
    from pptx import Presentation
    from pptx.util import Inches, Pt

    prs = Presentation()
    # Title slide
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = version.name
    slide.placeholders[1].text = (
        f"Status: {version.status}\n"
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"Horizon: {version.horizon_months} months"
    )

    # Summary slide
    slide2 = prs.slides.add_slide(prs.slide_layouts[1])
    slide2.shapes.title.text = "Forecast Summary by Category"
    body = slide2.placeholders[1].text_frame
    body.clear()
    for row in _aggregate_by_category(db, version.id):
        p = body.add_paragraph()
        p.text = f"{row['category']}: ${row['total']:,.0f}"
        p.level = 0

    # Confidence slide
    slide3 = prs.slides.add_slide(prs.slide_layouts[1])
    slide3.shapes.title.text = "Confidence Distribution"
    body3 = slide3.placeholders[1].text_frame
    body3.clear()
    for label, count in [
        ("High", version.high_confidence_count),
        ("Medium", version.medium_confidence_count),
        ("Low", version.low_confidence_count),
        ("Overrides", version.override_count),
    ]:
        p = body3.add_paragraph()
        p.text = f"{label}: {count}"
        p.level = 0

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def build_board_pack_pdf(db: Session, version: ForecastVersion) -> bytes:
    """Generate a simple board-ready PDF summary."""
    # Prefer reportlab if available; otherwise emit a minimal PDF-like text fallback as PDF bytes via reportlab-free path
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas

        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        width, height = letter
        y = height - 72
        c.setFont("Helvetica-Bold", 18)
        c.drawString(72, y, f"Board Pack: {version.name}")
        y -= 28
        c.setFont("Helvetica", 11)
        c.drawString(72, y, f"Status: {version.status}  |  Horizon: {version.horizon_months} months")
        y -= 18
        c.drawString(72, y, f"Generated: {datetime.now(timezone.utc).isoformat()}")
        y -= 36
        c.setFont("Helvetica-Bold", 14)
        c.drawString(72, y, "Category Totals")
        y -= 20
        c.setFont("Helvetica", 11)
        for row in _aggregate_by_category(db, version.id):
            c.drawString(72, y, f"{row['category']}: ${row['total']:,.0f}")
            y -= 16
            if y < 72:
                c.showPage()
                y = height - 72
        y -= 20
        c.setFont("Helvetica-Bold", 14)
        c.drawString(72, y, "Confidence")
        y -= 20
        c.setFont("Helvetica", 11)
        c.drawString(72, y, f"High: {version.high_confidence_count}  Medium: {version.medium_confidence_count}  Low: {version.low_confidence_count}")
        c.save()
        return buf.getvalue()
    except ImportError:
        # Minimal plaintext fallback wrapped as bytes (clients still get a downloadable artifact)
        lines = [
            f"Board Pack: {version.name}",
            f"Status: {version.status}",
            f"Horizon: {version.horizon_months}",
            "",
            "Category Totals:",
        ]
        for row in _aggregate_by_category(db, version.id):
            lines.append(f"  {row['category']}: ${row['total']:,.0f}")
        return "\n".join(lines).encode("utf-8")


def save_board_pack(
    db: Session,
    version: ForecastVersion,
    fmt: str,
    output_dir: str,
) -> Path:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    safe = version.name.replace("/", "-").replace(" ", "_")
    if fmt == "pptx":
        data = build_board_pack_pptx(db, version)
        path = output / f"{safe}_board_pack.pptx"
    else:
        data = build_board_pack_pdf(db, version)
        path = output / f"{safe}_board_pack.pdf"
    path.write_bytes(data)
    return path
