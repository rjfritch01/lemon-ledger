"""Render Form8949Result to a substitute-form PDF (IRS Pub 1179)."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, Spacer, Table

from lemon_ledger.domain.forms.form_8949 import BoxSubtotals, Form8949Result
from lemon_ledger.domain.forms.render.pdf_base import (
    FONT_BOLD,
    FONT_NORMAL,
    FONT_SIZE,
    HEADER_SIZE,
    TITLE_SIZE,
    build_doc,
    disclaimer_paragraph,
    fmt_dollar,
    header_style,
    totals_style,
)

_AVAIL_WIDTH = 7.5 * inch

# Column widths: (a) desc, (b) acq, (c) disp, (d) proc, (e) basis, (f) code, (g) adj, (h) net
_COL_WIDTHS = [
    2.4 * inch,
    0.7 * inch,
    0.7 * inch,
    0.85 * inch,
    0.85 * inch,
    0.35 * inch,
    0.6 * inch,
    0.85 * inch,
]
_COL_HEADERS = [
    "(a) Description",
    "(b) Acq",
    "(c) Disp",
    "(d) Proceeds",
    "(e) Basis",
    "(f)",
    "(g) Adj",
    "(h) Gain/(Loss)",
]

_BOX_LABEL = {
    "A": "Box A — Short-term; basis reported to IRS",
    "B": "Box B — Short-term; basis NOT reported to IRS",
    "C": "Box C — Short-term; no 1099-DA issued",
    "D": "Box D — Long-term; basis reported to IRS",
    "E": "Box E — Long-term; basis NOT reported to IRS",
    "F": "Box F — Long-term; no 1099-DA issued",
}


def _row_data(subs: BoxSubtotals) -> list[list[str]]:
    data = [_COL_HEADERS]
    for r in subs.rows:
        data.append(
            [
                r.description,
                r.acquired_at.strftime("%m/%d/%Y"),
                r.disposed_at.strftime("%m/%d/%Y"),
                fmt_dollar(r.proceeds_usd),
                fmt_dollar(r.cost_basis_usd),
                r.adjustment_code or "",
                fmt_dollar(r.adjustment_usd) if r.adjustment_usd is not None else "",
                fmt_dollar(r.gain_loss_net),
            ]
        )
    return data


def render_form_8949(result: Form8949Result, out_path: Path) -> Path:
    """Render Form 8949 to *out_path*. Returns the path written."""
    title_style = ParagraphStyle("title", fontName=FONT_BOLD, fontSize=TITLE_SIZE, spaceAfter=4)
    sub_style = ParagraphStyle("sub", fontName=FONT_NORMAL, fontSize=FONT_SIZE, spaceAfter=2)
    box_style = ParagraphStyle(
        "box", fontName=FONT_BOLD, fontSize=HEADER_SIZE, spaceBefore=8, spaceAfter=4
    )

    story = []

    draft_tag = " [DRAFT — PENDING GATE RESOLUTION]" if result.is_draft else ""
    title = f"Form 8949 — Sales and Other Dispositions of Capital Assets{draft_tag}"
    story.append(Paragraph(title, title_style))
    sub = f"Tax Year: {result.tax_year}  |  Entity: {result.entity_id}"
    story.append(Paragraph(sub, sub_style))
    story.append(Paragraph("Substitute form prepared under IRS Pub 1179", sub_style))
    story.append(Spacer(1, 0.1 * inch))

    for box in ("A", "B", "C", "D", "E", "F"):
        subs: BoxSubtotals = result.boxes[box]
        if not subs.rows:
            continue

        story.append(Paragraph(_BOX_LABEL[box], box_style))

        data = _row_data(subs)
        t = Table(data, colWidths=_COL_WIDTHS, repeatRows=1)
        t.setStyle(header_style())
        story.append(t)

        # Box subtotals row
        totals = Table(
            [
                [
                    f"Box {box} Totals",
                    "",
                    "",
                    fmt_dollar(subs.total_proceeds),
                    fmt_dollar(subs.total_basis),
                    "",
                    fmt_dollar(subs.total_adjustment) if subs.total_adjustment else "",
                    fmt_dollar(subs.total_gain_loss_net),
                ]
            ],
            colWidths=_COL_WIDTHS,
        )
        totals.setStyle(totals_style())
        story.append(totals)
        story.append(Spacer(1, 0.05 * inch))

    story.append(disclaimer_paragraph())
    doc = build_doc(out_path)
    doc.build(story)
    return out_path
