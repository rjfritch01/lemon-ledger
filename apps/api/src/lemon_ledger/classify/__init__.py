"""Transaction classification layer.

Entry points:
  classify_bundle  — classify a single TxBundle, returns ClassifiedTransaction rows.
  classify_wallet  — Celery task that walks a wallet's settled block range.
"""
