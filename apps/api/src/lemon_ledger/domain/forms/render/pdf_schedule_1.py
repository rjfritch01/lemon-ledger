"""Render Schedule1Result to PDF."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, Spacer, Table

from lemon_ledger.domain.forms.render.pdf_base import (
    FONT_BOLD,
    FONT_NORMAL,
    FONT_SIZE,
    TITLE_SIZE,
    build_doc,
    fmt_dollar,
    totals_style,
)
from lemon_ledger.domain.forms.schedule_1 import Schedule1Result


def render_schedule_1(result: Schedule1Result, out_path: Path) -> Path:
    title_style = ParagraphStyle("title", fontName=FONT_BOLD, fontSize=TITLE_SIZE, spaceAfter=4)
    sub_style = ParagraphStyle("sub", fontName=FONT_NORMAL, fontSize=FONT_SIZE, spaceAfter=2)

    story = []
    draft_tag = " [DRAFT]" if result.is_draft else ""
    story.append(Paragraph(f"Schedule 1 — Additional Income{draft_tag}", title_style))
    story.append(
        Paragraph(f"Tax Year: {result.tax_year}  |  Entity: {result.entity_id}", sub_style)
    )
    story.append(Spacer(1, 0.15 * inch))

    col_w = [5.0 * inch, 2.5 * inch]
    data = [
        [
            "Line 8z — Other Income: Cryptocurrency Staking / Reward Income",
            fmt_dollar(result.line_8z_income),
        ],
    ]
    t = Table(data, colWidths=col_w)
    t.setStyle(totals_style())
    story.append(t)

    doc = build_doc(out_path)
    doc.build(story)
    return out_path
