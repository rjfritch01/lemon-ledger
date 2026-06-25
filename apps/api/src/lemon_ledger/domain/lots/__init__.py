"""Lot tracking and tax math engine.

Entry points:
  apply_event(session, event)      — process one ClassifiedTransaction
  apply_lots_for_wallet(wallet_id) — Celery task
  rebuild_wallet(session, wallet_id) — wipe + full replay
"""
