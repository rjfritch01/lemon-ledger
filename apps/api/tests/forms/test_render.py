"""Smoke tests: PDF rendering produces a valid PDF file.

No assertion on content — just verifies the render pipeline doesn't crash
and produces a non-empty PDF-magic-number file.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from lemon_ledger.domain.forms.form_8949 import build_8949
from lemon_ledger.domain.forms.read_model import DisposalRow, RewardIncomeRow
from lemon_ledger.domain.forms.render.pdf_8949 import render_form_8949
from lemon_ledger.domain.forms.render.pdf_base import fmt_dollar, whole_dollar
from lemon_ledger.domain.forms.render.pdf_schedule_1 import render_schedule_1
from lemon_ledger.domain.forms.render.pdf_schedule_d import render_schedule_d
from lemon_ledger.domain.forms.schedule_1 import build_schedule_1
from lemon_ledger.domain.forms.schedule_d import build_schedule_d

_ENT = uuid.uuid4()
_PDF_MAGIC = b"%PDF"


def _row(proceeds: str, basis: str, holding: str = "short") -> DisposalRow:
    return DisposalRow(
        lot_id=uuid.uuid4(),
        disposal_tx_id=uuid.uuid4(),
        description="10 LEMX",
        acquired_at=date(2024, 1, 1),
        disposed_at=date(2025, 6, 1),
        proceeds_usd=Decimal(proceeds),
        cost_basis_usd=Decimal(basis),
        adjustment_code=None,
        adjustment_usd=None,
        holding_period=holding,
        covered_status="no-1099-da",
        asset_class="fungible",
        entity_id=_ENT,
    )


@pytest.fixture
def tmp_out(tmp_path: Path) -> Path:
    return tmp_path


def test_render_form_8949_produces_pdf(tmp_out: Path) -> None:
    rows = [
        _row("200", "100", "short"),
        _row("500", "300", "long"),
    ]
    form = build_8949(rows, _ENT, 2025)
    out = render_form_8949(form, tmp_out / "8949.pdf")
    assert out.exists()
    assert out.stat().st_size > 0
    assert out.read_bytes()[:4] == _PDF_MAGIC


def test_render_form_8949_draft_watermark(tmp_out: Path) -> None:
    form = build_8949([], _ENT, 2025, is_draft=True)
    out = render_form_8949(form, tmp_out / "8949_draft.pdf")
    assert out.exists()
    assert out.stat().st_size > 0


def test_render_schedule_d_produces_pdf(tmp_out: Path) -> None:
    rows = [_row("300", "100", "short"), _row("400", "200", "long")]
    form = build_8949(rows, _ENT, 2025)
    sched = build_schedule_d(form)
    out = render_schedule_d(sched, tmp_out / "sched_d.pdf")
    assert out.exists()
    assert out.read_bytes()[:4] == _PDF_MAGIC


def test_render_schedule_1_produces_pdf(tmp_out: Path) -> None:
    income = RewardIncomeRow(entity_id=_ENT, tax_year=2025, total_income_usd=Decimal("75"))
    sched = build_schedule_1(income)
    out = render_schedule_1(sched, tmp_out / "sched_1.pdf")
    assert out.exists()
    assert out.read_bytes()[:4] == _PDF_MAGIC


def test_whole_dollar_rounding() -> None:
    assert whole_dollar(Decimal("99.50")) == 100
    assert whole_dollar(Decimal("99.49")) == 99
    assert whole_dollar(Decimal("-10.51")) == -11


def test_fmt_dollar_positive() -> None:
    assert fmt_dollar(Decimal("1234.56")) == "1,235"


def test_fmt_dollar_negative() -> None:
    assert fmt_dollar(Decimal("-500")) == "(500)"


def test_render_8949_with_l_adjustment(tmp_out: Path) -> None:
    """Render a row with adjustment_code='L' (related-party disallowed loss)."""
    row = DisposalRow(
        lot_id=uuid.uuid4(),
        disposal_tx_id=uuid.uuid4(),
        description="5 LEMX",
        acquired_at=date(2023, 6, 1),
        disposed_at=date(2025, 1, 1),
        proceeds_usd=Decimal("100"),
        cost_basis_usd=Decimal("200"),
        adjustment_code="L",
        adjustment_usd=Decimal("100"),
        holding_period="long",
        covered_status="no-1099-da",
        asset_class="fungible",
        entity_id=_ENT,
    )
    form = build_8949([row], _ENT, 2025)
    out = render_form_8949(form, tmp_out / "8949_L.pdf")
    assert out.exists()
