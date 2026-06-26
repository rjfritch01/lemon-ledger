"""Render ScheduleDResult to PDF."""

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
    disclaimer_paragraph,
    fmt_dollar,
    totals_style,
)
from lemon_ledger.domain.forms.schedule_d import ScheduleDResult

_AVAIL_WIDTH = 7.5 * inch


def render_schedule_d(result: ScheduleDResult, out_path: Path) -> Path:
    title_style = ParagraphStyle("title", fontName=FONT_BOLD, fontSize=TITLE_SIZE, spaceAfter=4)
    sub_style = ParagraphStyle("sub", fontName=FONT_NORMAL, fontSize=FONT_SIZE, spaceAfter=2)

    story = []
    draft_tag = " [DRAFT]" if result.is_draft else ""
    story.append(Paragraph(f"Schedule D — Capital Gains and Losses{draft_tag}", title_style))
    story.append(
        Paragraph(f"Tax Year: {result.tax_year}  |  Entity: {result.entity_id}", sub_style)
    )
    story.append(
        Paragraph(
            "Values from Form 8949 box subtotals (anti-drift: no direct ledger read)",
            sub_style,
        )
    )
    story.append(Spacer(1, 0.15 * inch))

    col_w = [4.0 * inch, 3.5 * inch]
    data = [
        [
            "Part I — Short-Term Capital Gains and Losses (Boxes A, B, C)",
            fmt_dollar(result.short_term_net),
        ],
        [
            "Part II — Long-Term Capital Gains and Losses (Boxes D, E, F)",
            fmt_dollar(result.long_term_net),
        ],
        ["Line 16 — Net Capital Gain or (Loss)", fmt_dollar(result.total_net)],
    ]
    t = Table(data, colWidths=col_w)
    t.setStyle(totals_style())
    story.append(t)

    story.append(disclaimer_paragraph())
    doc = build_doc(out_path)
    doc.build(story)
    return out_path
