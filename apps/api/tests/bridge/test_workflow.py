"""Unit tests for bridge workflow resolution functions.

Uses MagicMock sessions.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from lemon_ledger.domain.bridge.workflow import (
    resolve_pair,
    resolve_unmatched,
    set_classification_signal,
)
from lemon_ledger.models.bridge import BridgeCorrelation, BridgeStatus, UserResolution


def _corr(
    status: str = "needs_confirmation",
    resolved_by: str | None = None,
    out_id: uuid.UUID | None = None,
    in_id: uuid.UUID | None = None,
) -> BridgeCorrelation:
    corr = MagicMock(spec=BridgeCorrelation)
    corr.id = uuid.uuid4()
    corr.status = status
    corr.resolved_by = resolved_by
    corr.resolved_at = None
    corr.confidence_level = None
    corr.confidence_score = None
    corr.user_resolution = None
    corr.outflow_classified_event_id = out_id or uuid.uuid4()
    corr.inflow_classified_event_id = in_id or uuid.uuid4()
    return corr


def _session_with_corr(corr: BridgeCorrelation) -> MagicMock:
    session = MagicMock()
    session.get.return_value = corr
    session.flush.return_value = None
    session.add.return_value = None
    session.scalar.return_value = None
    session.scalars.return_value.all.return_value = []
    return session


# ── resolve_pair ──────────────────────────────────────────────────────────────


def test_resolve_pair_confirm_sets_status() -> None:
    corr = _corr()
    session = _session_with_corr(corr)
    with patch("lemon_ledger.domain.bridge.workflow.set_classification_signal"):
        resolve_pair(session, corr.id, decision="confirm", actor="test_user")
    assert corr.status == BridgeStatus.CONFIRMED
    assert corr.resolved_by == "user"
    assert corr.resolved_at is not None


def test_resolve_pair_reject_sets_status() -> None:
    corr = _corr()
    session = _session_with_corr(corr)
    with patch("lemon_ledger.domain.bridge.workflow.set_classification_signal"):
        resolve_pair(session, corr.id, decision="reject", actor="test_user")
    assert corr.status == BridgeStatus.REJECTED
    assert corr.resolved_by == "user"


def test_resolve_pair_noop_if_already_user_resolved_same_outcome() -> None:
    corr = _corr(status="confirmed", resolved_by="user")
    session = _session_with_corr(corr)
    with patch("lemon_ledger.domain.bridge.workflow.set_classification_signal") as mock_sig:
        resolve_pair(session, corr.id, decision="confirm", actor="test_user")
    mock_sig.assert_not_called()


def test_resolve_pair_user_can_override_auto_confirmed() -> None:
    corr = _corr(status="confirmed", resolved_by="auto")
    session = _session_with_corr(corr)
    with patch("lemon_ledger.domain.bridge.workflow.set_classification_signal"):
        resolve_pair(session, corr.id, decision="reject", actor="test_user")
    # Should proceed and flip to rejected.
    assert corr.status == BridgeStatus.REJECTED
    assert corr.resolved_by == "user"


def test_resolve_pair_not_found_raises() -> None:
    session = MagicMock()
    session.get.return_value = None
    with pytest.raises(ValueError, match="not found"):
        resolve_pair(session, uuid.uuid4(), decision="confirm", actor="user")


def test_resolve_pair_writes_audit_log() -> None:
    corr = _corr()
    session = _session_with_corr(corr)
    with patch("lemon_ledger.domain.bridge.workflow.set_classification_signal"):
        resolve_pair(session, corr.id, decision="confirm", actor="test_user")
    session.add.assert_called()


# ── resolve_unmatched ─────────────────────────────────────────────────────────


def test_resolve_unmatched_sale_sets_rejected() -> None:
    corr = _corr(status="unmatched")
    session = _session_with_corr(corr)
    with patch("lemon_ledger.domain.bridge.workflow.set_classification_signal"):
        resolve_unmatched(session, corr.id, user_resolution=UserResolution.SALE, actor="user")
    assert corr.status == BridgeStatus.REJECTED
    assert corr.resolved_by == "user"


def test_resolve_unmatched_third_party_sets_rejected() -> None:
    corr = _corr(status="unmatched")
    session = _session_with_corr(corr)
    with patch("lemon_ledger.domain.bridge.workflow.set_classification_signal"):
        resolve_unmatched(
            session, corr.id, user_resolution=UserResolution.THIRD_PARTY, actor="user"
        )
    assert corr.status == BridgeStatus.REJECTED


def test_resolve_unmatched_bridge_pending_stays_unmatched() -> None:
    corr = _corr(status="unmatched")
    session = _session_with_corr(corr)
    resolve_unmatched(session, corr.id, user_resolution=UserResolution.BRIDGE_PENDING, actor="user")
    assert corr.status == BridgeStatus.UNMATCHED


def test_resolve_unmatched_other_sets_needs_review() -> None:
    corr = _corr(status="unmatched")
    ct = MagicMock()
    ct.needs_review = False
    session = _session_with_corr(corr)
    session.get.side_effect = lambda model, pk: corr if model is BridgeCorrelation else ct
    resolve_unmatched(session, corr.id, user_resolution=UserResolution.OTHER, actor="user")
    assert ct.needs_review is True


# ── set_classification_signal ─────────────────────────────────────────────────


def test_set_classification_signal_confirmed_relocate() -> None:
    out_id = uuid.uuid4()
    in_id = uuid.uuid4()
    corr = _corr(status="confirmed", out_id=out_id, in_id=in_id)

    out_ct = MagicMock()
    out_ct.wallet_id = uuid.uuid4()
    out_ct.occurred_at = datetime(2024, 6, 1, tzinfo=UTC)
    out_ct.classification = "transfer-out"

    in_ct = MagicMock()
    in_ct.classification = "transfer-in"
    in_ct.relocation_source_event_id = None

    session = MagicMock()
    session.scalar.return_value = None  # entity lookup → no assignment → default relocate

    def _get_side(model: object, pk: object) -> object:
        if pk == corr.id:
            return corr
        if pk == out_id:
            return out_ct
        if pk == in_id:
            return in_ct
        return MagicMock()

    session.get.side_effect = _get_side

    set_classification_signal(session, corr, confirmed=True)

    assert out_ct.classification == "bridge-out"
    assert in_ct.classification == "bridge-in"
    assert in_ct.relocation_source_event_id == out_id


def test_set_classification_signal_rejected_restores() -> None:
    out_id = uuid.uuid4()
    in_id = uuid.uuid4()
    corr = _corr(status="rejected", out_id=out_id, in_id=in_id)

    out_ct = MagicMock()
    out_ct.bridge_correlation_id = corr.id

    in_ct = MagicMock()
    in_ct.bridge_correlation_id = corr.id
    in_ct.relocation_source_event_id = out_id

    session = MagicMock()

    def _get_side(model: object, pk: object) -> object:
        if pk == corr.id:
            return corr
        if pk == out_id:
            return out_ct
        if pk == in_id:
            return in_ct
        return None

    session.get.side_effect = _get_side

    set_classification_signal(session, corr, confirmed=False)

    assert out_ct.classification == "transfer-out"
    assert in_ct.classification == "transfer-in"
    assert in_ct.relocation_source_event_id is None
