"""Synthetic-fixture reconciliation harness.

Expected values in BUILTIN_FIXTURES are LITERAL CONSTANTS computed by hand from
first principles (see Phase 1 review).  They are never derived from engine output.
This anti-circularity rule is load-bearing: the harness detects engine regressions
only because the expected values are independent of the code under test.

Tolerance: per-figure absolute delta ≤ $5.00 (whole-dollar rounding at render).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

_TOLERANCE = Decimal("5.00")


# ── Fixture definition ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BoxExpected:
    rows: int
    proceeds: Decimal
    basis: Decimal
    gain_loss_net: Decimal


@dataclass(frozen=True)
class FixtureExpected:
    fixture_id: str
    description: str
    boxes: dict[str, BoxExpected]  # only non-empty boxes need be listed
    short_term_net: Decimal
    long_term_net: Decimal
    total_net: Decimal
    line_8z: Decimal
    gate_held: bool  # True iff the fixture represents a gate-held scenario


# ── S1–S8 literal constants (hand-computed, Phase 1 verified) ────────────────

S1 = FixtureExpected(
    fixture_id="S1",
    description="Simple buy then full sale, short-term",
    boxes={
        "C": BoxExpected(
            rows=1, proceeds=Decimal("500"), basis=Decimal("200"), gain_loss_net=Decimal("300")
        ),
    },
    short_term_net=Decimal("300"),
    long_term_net=Decimal("0"),
    total_net=Decimal("300"),
    line_8z=Decimal("0"),
    gate_held=False,
)

S2 = FixtureExpected(
    fixture_id="S2",
    description="Partial sale FIFO two lots — long from 2024 and short from 2025",
    boxes={
        "F": BoxExpected(
            rows=1, proceeds=Decimal("500"), basis=Decimal("200"), gain_loss_net=Decimal("300")
        ),
        "C": BoxExpected(
            rows=1, proceeds=Decimal("250"), basis=Decimal("200"), gain_loss_net=Decimal("50")
        ),
    },
    short_term_net=Decimal("50"),
    long_term_net=Decimal("300"),
    total_net=Decimal("350"),
    line_8z=Decimal("0"),
    gate_held=False,
)

S3 = FixtureExpected(
    fixture_id="S3",
    description="Long-term vs short-term split — same disposal date",
    boxes={
        "F": BoxExpected(
            rows=1, proceeds=Decimal("400"), basis=Decimal("150"), gain_loss_net=Decimal("250")
        ),
        "C": BoxExpected(
            rows=1, proceeds=Decimal("400"), basis=Decimal("300"), gain_loss_net=Decimal("100")
        ),
    },
    short_term_net=Decimal("100"),
    long_term_net=Decimal("250"),
    total_net=Decimal("350"),
    line_8z=Decimal("0"),
    gate_held=False,
)

S4 = FixtureExpected(
    fixture_id="S4",
    description="Reward income then sale — no double-count",
    boxes={
        "C": BoxExpected(
            rows=1, proceeds=Decimal("250"), basis=Decimal("150"), gain_loss_net=Decimal("100")
        ),
    },
    short_term_net=Decimal("100"),
    long_term_net=Decimal("0"),
    total_net=Decimal("100"),
    line_8z=Decimal("150"),
    gate_held=False,
)

# S5 has two sub-fixtures: entity A (zero rows) and entity B (one long row).
S5_ENTITY_A = FixtureExpected(
    fixture_id="S5-A",
    description="Cross-entity cap-contribution — entity A view (no disposals)",
    boxes={},
    short_term_net=Decimal("0"),
    long_term_net=Decimal("0"),
    total_net=Decimal("0"),
    line_8z=Decimal("0"),
    gate_held=False,
)

S5_ENTITY_B = FixtureExpected(
    fixture_id="S5-B",
    description="Cross-entity cap-contribution — entity B view (LONG disposal post-relocation)",
    boxes={
        "F": BoxExpected(
            rows=1, proceeds=Decimal("500"), basis=Decimal("200"), gain_loss_net=Decimal("300")
        ),
    },
    short_term_net=Decimal("0"),
    long_term_net=Decimal("300"),
    total_net=Decimal("300"),
    line_8z=Decimal("0"),
    gate_held=False,
)

S6 = FixtureExpected(
    fixture_id="S6",
    description="Related-party disposal §267 loss disallowed — col(h) = $0",
    boxes={
        "F": BoxExpected(
            rows=1, proceeds=Decimal("300"), basis=Decimal("500"), gain_loss_net=Decimal("0")
        ),
    },
    short_term_net=Decimal("0"),
    long_term_net=Decimal("0"),
    total_net=Decimal("0"),
    line_8z=Decimal("0"),
    gate_held=False,
)

S7 = FixtureExpected(
    fixture_id="S7",
    description="Gift-out to third party — no disposal row written",
    boxes={},
    short_term_net=Decimal("0"),
    long_term_net=Decimal("0"),
    total_net=Decimal("0"),
    line_8z=Decimal("0"),
    gate_held=False,
)

S8 = FixtureExpected(
    fixture_id="S8",
    description="Gate-held unresolved pending CT — generate-8949 must exit 2 without --draft",
    boxes={},
    short_term_net=Decimal("0"),
    long_term_net=Decimal("0"),
    total_net=Decimal("0"),
    line_8z=Decimal("0"),
    gate_held=True,
)

BUILTIN_FIXTURES: dict[str, FixtureExpected] = {
    "S1": S1,
    "S2": S2,
    "S3": S3,
    "S4": S4,
    "S5-A": S5_ENTITY_A,
    "S5-B": S5_ENTITY_B,
    "S6": S6,
    "S7": S7,
    "S8": S8,
}


# ── Reconcile result ──────────────────────────────────────────────────────────


@dataclass
class FigureDiff:
    name: str
    expected: Decimal
    actual: Decimal

    @property
    def delta(self) -> Decimal:
        return abs(self.actual - self.expected)

    @property
    def within_tolerance(self) -> bool:
        return self.delta <= _TOLERANCE


@dataclass
class ReconcileResult:
    fixture_id: str
    gate_verdict: bool  # True = gate is clear for this fixture (or is_draft)
    diffs: list[FigureDiff] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.gate_verdict and all(d.within_tolerance for d in self.diffs)

    def summary_lines(self) -> list[str]:
        lines = [f"Fixture {self.fixture_id}: {'PASS' if self.passed else 'FAIL'}"]
        lines.append(f"  gate_verdict={self.gate_verdict}")
        for d in self.diffs:
            mark = "OK" if d.within_tolerance else "FAIL"
            lines.append(
                f"  [{mark}] {d.name}: expected={d.expected} actual={d.actual} delta={d.delta}"
            )
        return lines


# ── Load fixture ──────────────────────────────────────────────────────────────


def load_fixture(fixture_id_or_path: str) -> FixtureExpected:
    """Return a FixtureExpected by built-in ID or by deserialising a JSON file."""
    if fixture_id_or_path in BUILTIN_FIXTURES:
        return BUILTIN_FIXTURES[fixture_id_or_path]

    path = Path(fixture_id_or_path)
    if path.exists():
        raw = json.loads(path.read_text())
        boxes = {
            k: BoxExpected(
                rows=v["rows"],
                proceeds=Decimal(str(v["proceeds"])),
                basis=Decimal(str(v["basis"])),
                gain_loss_net=Decimal(str(v["gain_loss_net"])),
            )
            for k, v in raw.get("boxes", {}).items()
        }
        return FixtureExpected(
            fixture_id=raw["fixture_id"],
            description=raw.get("description", ""),
            boxes=boxes,
            short_term_net=Decimal(str(raw["short_term_net"])),
            long_term_net=Decimal(str(raw["long_term_net"])),
            total_net=Decimal(str(raw["total_net"])),
            line_8z=Decimal(str(raw["line_8z"])),
            gate_held=bool(raw.get("gate_held", False)),
        )

    known = ", ".join(sorted(BUILTIN_FIXTURES))
    raise ValueError(
        f"Unknown fixture {fixture_id_or_path!r}. Known built-ins: {known}. "
        "Or pass a path to a JSON fixture file."
    )


# ── Run reconcile ─────────────────────────────────────────────────────────────


def run_reconcile(
    session: Session,
    entity_id: uuid.UUID,
    tax_year: int,
    expected: FixtureExpected,
    *,
    is_draft: bool = False,
) -> ReconcileResult:
    """Compare actual form output against *expected* fixture, per-figure within $5 tolerance.

    Gate check is performed first.  If the gate is held and the fixture expects
    gate_held=True, gate_verdict=True.  If gate is held and not draft, diffs are
    empty and gate_verdict=False (harness cannot run against a held gate).
    """
    from lemon_ledger.domain.forms.form_8949 import build_8949
    from lemon_ledger.domain.forms.gate import check_gate
    from lemon_ledger.domain.forms.read_model import fetch_disposal_rows, fetch_reward_income
    from lemon_ledger.domain.forms.schedule_1 import build_schedule_1
    from lemon_ledger.domain.forms.schedule_d import build_schedule_d

    gate = check_gate(session, entity_id, tax_year)

    if expected.gate_held:
        return ReconcileResult(
            fixture_id=expected.fixture_id,
            gate_verdict=gate.is_held,
            diffs=[],
        )

    if gate.is_held and not is_draft:
        return ReconcileResult(
            fixture_id=expected.fixture_id,
            gate_verdict=False,
            diffs=[],
        )

    disposal_rows = fetch_disposal_rows(session, entity_id, tax_year)
    reward = fetch_reward_income(session, entity_id, tax_year)
    form = build_8949(disposal_rows, entity_id, tax_year, is_draft=gate.is_held and is_draft)
    sched_d = build_schedule_d(form)
    sched_1 = build_schedule_1(reward, is_draft=gate.is_held and is_draft)

    diffs: list[FigureDiff] = []

    # Per-box row count and totals
    for box_id, exp_box in expected.boxes.items():
        actual_box = form.boxes.get(box_id)  # type: ignore[call-overload]
        actual_rows = len(actual_box.rows) if actual_box else 0
        actual_proceeds = actual_box.total_proceeds if actual_box else Decimal(0)
        actual_basis = actual_box.total_basis if actual_box else Decimal(0)
        actual_gl = actual_box.total_gain_loss_net if actual_box else Decimal(0)

        diffs.append(FigureDiff(f"box_{box_id}_rows", Decimal(exp_box.rows), Decimal(actual_rows)))
        diffs.append(FigureDiff(f"box_{box_id}_proceeds", exp_box.proceeds, actual_proceeds))
        diffs.append(FigureDiff(f"box_{box_id}_basis", exp_box.basis, actual_basis))
        diffs.append(FigureDiff(f"box_{box_id}_gain_loss_net", exp_box.gain_loss_net, actual_gl))

    diffs.append(FigureDiff("short_term_net", expected.short_term_net, sched_d.short_term_net))
    diffs.append(FigureDiff("long_term_net", expected.long_term_net, sched_d.long_term_net))
    diffs.append(FigureDiff("total_net", expected.total_net, sched_d.total_net))
    diffs.append(FigureDiff("line_8z", expected.line_8z, sched_1.line_8z_income))

    return ReconcileResult(
        fixture_id=expected.fixture_id,
        gate_verdict=True,
        diffs=diffs,
    )
