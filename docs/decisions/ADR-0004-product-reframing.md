# ADR-0004 — Product Reframing: Informational Record-Keeping

**Status**: Accepted (Stage 6, 2026-06-26)

## Context

The original Lemon Ledger positioning described the product as "computing your taxes." This raised liability concerns: users might file directly from the output without involving a licensed tax professional, leading to errors or regulatory exposure for both users and the project author.

## Decision

Lemon Ledger is repositioned as **informational record-keeping for the user and their CPA**.

Claims change; the engine does not.

Specifically:
- Every generated PDF and CSV carries a mandatory disclaimer (see ADR-0006).
- Form 8949 / Schedule D / Schedule 1 / Activity Report PDFs are labelled "INFORMATIONAL DRAFT — NOT FILED TAX ADVICE."
- The CLI `generate-8949` and `activity-report` commands emit the disclaimer in all outputs.
- No changes to the lot engine, tax math, or gate logic are required by this reframing.

## Consequences

- **Product copy and docs** must not claim the tool "files" or "computes" taxes — it "generates informational drafts for CPA review."
- **Gate logic** is unchanged: the gate still blocks on unresolved events; the reframing changes what we say about the output, not when we refuse to produce it.
- **Disclaimer is non-optional**: it appears on every page of every form PDF and in every CSV header.
