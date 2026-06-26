"""Render ActivityReport to PDF."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, Spacer, Table

from lemon_ledger.domain.forms.activity_report import ActivityReport
from lemon_ledger.domain.forms.render.pdf_base import (
    FONT_BOLD,
    FONT_NORMAL,
    FONT_SIZE,
    TITLE_SIZE,
    build_doc,
    disclaimer_paragraph,
    fmt_dollar,
    header_style,
    totals_style,
)

_ACQ_HEADERS = ["Date", "Description", "Cost Basis"]
_ACQ_WIDTHS = [1.0 * inch, 4.5 * inch, 2.0 * inch]

_DISP_HEADERS = ["Date", "Description", "Proceeds", "Basis", "HP", "Adj", "Gain/(Loss)"]
_DISP_WIDTHS = [
    0.9 * inch,
    2.5 * inch,
    0.9 * inch,
    0.9 * inch,
    0.45 * inch,
    0.35 * inch,
    1.0 * inch,
]


def render_activity_report(report: ActivityReport, out_path: Path) -> Path:
    """Render the activity report PDF to *out_path*. Returns path written."""
    title_style = ParagraphStyle("title", fontName=FONT_BOLD, fontSize=TITLE_SIZE, spaceAfter=4)
    sub_style = ParagraphStyle("sub", fontName=FONT_NORMAL, fontSize=FONT_SIZE, spaceAfter=2)
    section_style = ParagraphStyle(
        "section", fontName=FONT_BOLD, fontSize=FONT_SIZE + 1, spaceBefore=8, spaceAfter=4
    )

    story = []
    draft_tag = " [DRAFT — PENDING GATE RESOLUTION]" if report.is_draft else ""
    story.append(Paragraph(f"Lemon Ledger — Activity & Gain-Loss Report{draft_tag}", title_style))
    story.append(
        Paragraph(f"Tax Year: {report.tax_year}  |  Entity: {report.entity_id}", sub_style)
    )
    story.append(Spacer(1, 0.1 * inch))

    # ── Acquisitions ──────────────────────────────────────────────────────────
    story.append(Paragraph("Acquisitions", section_style))
    if report.acquisitions:
        acq_data = [_ACQ_HEADERS]
        for acq in report.acquisitions:
            acq_data.append(
                [
                    acq.acquired_at.strftime("%m/%d/%Y"),
                    acq.description,
                    fmt_dollar(acq.cost_basis_usd),
                ]
            )
        acq_table = Table(acq_data, colWidths=_ACQ_WIDTHS, repeatRows=1)
        acq_table.setStyle(header_style())
        story.append(acq_table)
    else:
        story.append(Paragraph("No acquisitions in tax year.", sub_style))
    story.append(Spacer(1, 0.1 * inch))

    # ── Disposals ─────────────────────────────────────────────────────────────
    story.append(Paragraph("Disposals", section_style))
    if report.disposals:
        disp_data = [_DISP_HEADERS]
        for disp in report.disposals:
            adj = disp.adjustment_code or ""
            if adj and disp.adjustment_usd is not None:
                adj = f"{adj}/{fmt_dollar(disp.adjustment_usd)}"
            disp_data.append(
                [
                    disp.disposed_at.strftime("%m/%d/%Y"),
                    disp.description,
                    fmt_dollar(disp.proceeds_usd),
                    fmt_dollar(disp.cost_basis_usd),
                    disp.holding_period[:1].upper(),
                    adj,
                    fmt_dollar(disp.gain_loss_net),
                ]
            )
        disp_table = Table(disp_data, colWidths=_DISP_WIDTHS, repeatRows=1)
        disp_table.setStyle(header_style())
        story.append(disp_table)
    else:
        story.append(Paragraph("No disposals in tax year.", sub_style))
    story.append(Spacer(1, 0.1 * inch))

    # ── Summary ───────────────────────────────────────────────────────────────
    story.append(Paragraph("Summary", section_style))
    summary_data = [
        ["Total Proceeds", fmt_dollar(report.total_proceeds)],
        ["Total Cost Basis (disposed)", fmt_dollar(report.total_cost_basis_disposed)],
        ["Total Net Gain / (Loss)", fmt_dollar(report.total_gain_loss)],
        ["Reward Income (Schedule 1 Line 8z)", fmt_dollar(report.total_reward_income)],
    ]
    summary_table = Table(summary_data, colWidths=[5.0 * inch, 2.5 * inch])
    summary_table.setStyle(totals_style())
    story.append(summary_table)

    story.append(disclaimer_paragraph())
    doc = build_doc(out_path)
    doc.build(story)
    return out_path
