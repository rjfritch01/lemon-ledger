"""Classifier orchestrator.

classify_bundle   — classify a single TxBundle, returns ClassifiedTransaction rows.
common_transfer_events — conservative fallback for unclaimed raw rows.
replace_classified     — delete-replace with per-tx override pinning.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from lemon_ledger.classify.types import ClaimSet, ClassifiedEvent, TxBundle
from lemon_ledger.models.classified import ClassifiedTransaction
from lemon_ledger.models.enums import ClassificationKind

# Priority within a tx for deterministic event_seq ordering.
# Lower number = earlier in the sequence.
_KIND_PRIORITY: dict[ClassificationKind, int] = {
    ClassificationKind.MINT: 0,
    ClassificationKind.STAKE: 1,
    ClassificationKind.UNSTAKE: 2,
    ClassificationKind.REWARD: 3,
    ClassificationKind.TRANSFER_IN: 4,
    ClassificationKind.TRANSFER_OUT: 5,
    ClassificationKind.UNCLASSIFIED: 6,
}

ZERO_ADDR = "0x" + "0" * 40


def classify_bundle(
    bundle: TxBundle,
    ctx: object,  # WalletContext — avoid circular at module level
) -> list[ClassifiedTransaction]:
    """Classify one TxBundle and return persisted-model instances (unsaved)."""
    from lemon_ledger.classify.context import WalletContext

    if not isinstance(ctx, WalletContext):
        raise TypeError(f"ctx must be WalletContext, got {type(ctx)!r}")

    claims = ClaimSet()
    events: list[ClassifiedEvent] = []

    for decoder in ctx.decoders_for_bundle(bundle):
        events += decoder.decode(bundle, ctx, claims)

    events += common_transfer_events(bundle, ctx, claims)
    return _assign_event_seq(bundle, events)


def common_transfer_events(
    bundle: TxBundle,
    ctx: object,
    claims: ClaimSet,
) -> list[ClassifiedEvent]:
    """Conservative fallback for any raw rows not consumed by a decoder.

    Rules:
    - Unclaimed ERC-20 Transfer inbound → transfer-in
    - Unclaimed ERC-20 Transfer outbound → transfer-out
    - Native value in envelope/internals → transfer-in / transfer-out
    - Unclaimed ERC-721 from ZERO_ADDR → transfer-in (NOT mint; cold-start)
    - No valuation here; value_usd_at_event stays None.
    """
    from lemon_ledger.classify.context import WalletContext

    if not isinstance(ctx, WalletContext):
        raise TypeError(f"ctx must be WalletContext, got {type(ctx)!r}")

    events: list[ClassifiedEvent] = []
    wallet_addr = ctx.wallet_address

    # Token transfers (ERC-20 and ERC-721)
    for t in bundle.transfers:
        if claims.has(t):
            continue
        to_addr = t.raw.get("to", "").lower()
        from_addr = t.raw.get("from", "").lower()
        if to_addr == wallet_addr:
            kind = ClassificationKind.TRANSFER_IN
        elif from_addr == wallet_addr:
            kind = ClassificationKind.TRANSFER_OUT
        else:
            continue  # neither to nor from wallet; skip

        is_nft = "tokenID" in t.raw
        amount = Decimal(1) if is_nft else _token_amount(t)
        events.append(
            ClassifiedEvent(
                classification=kind,
                contract_address=t.contract_address,
                token_id=None,
                amount=amount,
                value_usd_at_event=None,
                _order_hint=t.log_index,
            )
        )
        claims.add(t)

    # Native value in the envelope
    if bundle.envelope:
        env = bundle.envelope
        env_from = env.raw.get("from", "").lower()
        env_to = env.raw.get("to", "").lower()
        native_value = int(env.raw.get("value", 0))
        if native_value > 0:
            if env_to == wallet_addr:
                events.append(
                    ClassifiedEvent(
                        classification=ClassificationKind.TRANSFER_IN,
                        contract_address=ZERO_ADDR,
                        token_id=None,
                        amount=Decimal(native_value).scaleb(-18),
                        value_usd_at_event=None,
                        _order_hint=-1,
                    )
                )
            elif env_from == wallet_addr:
                events.append(
                    ClassifiedEvent(
                        classification=ClassificationKind.TRANSFER_OUT,
                        contract_address=ZERO_ADDR,
                        token_id=None,
                        amount=Decimal(native_value).scaleb(-18),
                        value_usd_at_event=None,
                        _order_hint=-1,
                    )
                )

    # Internal transactions (native value)
    for itx in bundle.internals:
        itx_from = itx.raw.get("from", "").lower()
        itx_to = itx.raw.get("to", "").lower()
        itx_value = int(itx.raw.get("value", 0))
        if itx_value <= 0:
            continue
        if itx_to == wallet_addr:
            events.append(
                ClassifiedEvent(
                    classification=ClassificationKind.TRANSFER_IN,
                    contract_address=ZERO_ADDR,
                    token_id=None,
                    amount=Decimal(itx_value).scaleb(-18),
                    value_usd_at_event=None,
                    _order_hint=-1,
                )
            )
        elif itx_from == wallet_addr:
            events.append(
                ClassifiedEvent(
                    classification=ClassificationKind.TRANSFER_OUT,
                    contract_address=ZERO_ADDR,
                    token_id=None,
                    amount=Decimal(itx_value).scaleb(-18),
                    value_usd_at_event=None,
                    _order_hint=-1,
                )
            )

    return events


def _assign_event_seq(
    bundle: TxBundle, events: list[ClassifiedEvent]
) -> list[ClassifiedTransaction]:
    """Assign a deterministic event_seq and convert to ORM rows (unsaved)."""
    sorted_events = sorted(
        events,
        key=lambda e: (
            e._order_hint,
            _KIND_PRIORITY.get(e.classification, 99),
        ),
    )
    rows: list[ClassifiedTransaction] = []
    for seq, event in enumerate(sorted_events):
        row = ClassifiedTransaction(
            wallet_id=bundle.wallet_id,
            chain=str(bundle.chain),
            tx_hash=bundle.tx_hash,
            event_seq=seq,
            block_number=bundle.block_number,
            occurred_at=bundle.occurred_at,
            classification=event.classification,
            token_id=event.token_id,
            contract_address=event.contract_address,
            amount=event.amount,
            value_usd_at_event=event.value_usd_at_event,
            needs_review=event.needs_review,
            notes=event.notes,
            related_lots=None,
            bridge_correlation_id=None,
        )
        rows.append(row)
    return rows


def replace_classified(
    session: Session,
    wallet_id: uuid.UUID,
    tx_hash: str,
    events: list[ClassifiedTransaction],
) -> None:
    """Delete-replace classified rows for a tx, respecting manual overrides.

    Per-tx override pinning: if ANY row for this tx has manual_override=True,
    the entire tx is frozen and this call is a no-op. (1.9 will write those.)
    """
    pinned_count = session.scalar(
        select(func.count())
        .select_from(ClassifiedTransaction)
        .where(
            ClassifiedTransaction.wallet_id == wallet_id,
            ClassifiedTransaction.tx_hash == tx_hash,
            ClassifiedTransaction.manual_override.is_(True),
        )
    )
    if pinned_count:
        return

    session.execute(
        delete(ClassifiedTransaction).where(
            ClassifiedTransaction.wallet_id == wallet_id,
            ClassifiedTransaction.tx_hash == tx_hash,
            ClassifiedTransaction.manual_override.is_(False),
        )
    )
    session.add_all(events)


def _token_amount(transfer: object) -> Decimal:
    """Extract decimal-adjusted ERC-20 amount from a raw transfer row."""
    from lemon_ledger.models.raw import RawTokenTransfer

    t: RawTokenTransfer = transfer  # type: ignore[assignment]
    decimals_str = t.raw.get("tokenDecimal", "18")
    decimals = int(decimals_str) if decimals_str else 18
    raw_value = str(t.value)
    return Decimal(raw_value).scaleb(-decimals)
