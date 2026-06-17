# Lemon Ledger — Technical Statement of Work

**Document version:** 1.2
**Date:** May 31, 2026
**Project sponsor:** Ryan Fritzsche
**Status:** Ready for developer engagement
**Prerequisite document:** Lemon Ledger · Data Layer R&D Report v2
**Changes from v1.1:** Card Spend page formally removed from v1 scope due to absence of a LemPay API data source. Page deferred to v1.1+ when data source materializes. Phase 4 deliverables and timeline adjusted accordingly.
**Changes from v1.0:** Added mandatory WalletConnect-based authorization model (§3.11), design system as Phase 1 deliverable (§4.0.1), and Security & Quality Assurance Program (§9). Revised timeline to 20 weeks single-dev / 13 weeks two-devs.

---

## 1. Executive summary

Lemon Ledger is a multi-entity crypto portfolio and tax accounting product purpose-built for the Lemonchain ecosystem. It tracks holdings, classifies transactions, computes cost basis with lot tracking, generates IRS-compliant tax forms, and surfaces ecosystem-specific insights (L2 emission income forecasts, distribution status, cross-chain holdings) that no general-purpose crypto tax tool can provide.

The product targets two user segments simultaneously: **individual community members** with multiple wallets and L2 NFT positions, and **business entities** (S-Corps, LLCs) that hold crypto for operational or treasury purposes and need entity-segregated bookkeeping with proper audit trails.

This SOW scopes the v1 build at **20 weeks** of focused engineering with one experienced full-stack developer, or **13 weeks** with two developers in parallel. The build is structured in four phases with explicit go/no-go gates between phases. Budget estimates: $72k–$104k for one developer, $94k–$135k for two, plus $5-10k for a pre-launch penetration test and ~$300/month for AI-assisted adversarial code review during active development.

The R&D phase that preceded this SOW resolved every major architectural unknown: the chain data layer, the pricing oracle, the multi-chain coverage, the bridge mechanic, the token registry, the NFT taxonomy, and the external token handling strategy. The SOW that follows is therefore a build contract — scoped work with known inputs, not a research project.

**This document v1.1 incorporates three additions that were not present in v1.0:** (a) a mandatory WalletConnect-based wallet authorization model treating signature-proof-of-ownership as a security invariant rather than a convenience, (b) a design system as a Phase 1 deliverable enforcing consistent visual language across the entire build, and (c) a formal Security and Quality Assurance Program including automated scanning, adversarial LLM code review, and pre-launch penetration testing.

---

## 2. Scope summary

### 2.1 In scope for v1

**Wallet management**
- Multi-wallet support with per-wallet entity assignment
- Address-paste primary flow, WalletConnect v2 convenience shortcut
- Multi-chain awareness (Lemonchain mainnet + BSC)
- Slowly-changing-dimension audit trail for wallet reassignments between entities

**Multi-entity ledger**
- Up to 5 entities per user (Personal + multiple business entities)
- Entity-segregated cost basis pools and tax form generation
- Cross-entity transfer classification with user confirmation
- Per-entity books that roll up into a consolidated view

**Holdings tracking**
- Full balance and basis tracking for all 33 identified Tier-1 token contracts across Lemonchain + BSC
- Plus 3 NFT collections (LemQuest, Swap Credit on Lemonchain, Swap Credit on BSC)
- User-curated Tier-2 token registry (external ERC-20s/BEP-20s like USDT, USDC, ETH, BNB)
- Wallet-discovered token presentation with user classification (include / spam / review later)

**Transaction classification**
- Buy, sell, swap, mint, stake, unstake, claim, reward, transfer (internal), transfer (external)
- Bridge-in, bridge-out (heuristic cross-chain correlation)
- Swap Credit redemption (paired burn + mint)
- Buy/burn protocol events (LMLN and other deflationary tokens)
- Cross-entity transfer with user confirmation dialog
- User override / manual reclassification for edge cases

**Pricing service**
- Tier-1 (Lemonchain ecosystem): PriceDataFeed oracle primary, HEXDEX reserves fallback
- Tier-1 (BSC ecosystem): PancakeSwap v2 reserves primary, CoinGecko cross-validation
- Tier-2 (external tokens): CoinGecko primary, CoinMarketCap secondary, DEX reserves fallback
- Historical pricing archive from `DailyAverageFinalized` event log backfill
- Configurable per-token pricing source override

**Lot tracking and basis methods**
- FIFO (default), HIFO, Specific ID, Average Cost
- Per-entity lot pools (no mixing across entities)
- Lot disposal optimizer (suggest tax-efficient disposal choices)
- Manual override for specific lot selection on disposals

**Tax form generation**
- Form 8949 (per entity, both short-term and long-term sections)
- Schedule D rollup
- Schedule 1 Line 8z (ordinary income from reward emissions)
- Schedule E line items (when entity is configured for rental/passthrough)
- Form 1120-S relevant lines (for S-Corp entities)
- PDF generation (IRS-compliant formatting)
- CSV exports compatible with TurboTax, TaxAct, and generic CPA hand-off
- JSON exports for power users / external tools

**L2-specific insights**
- Distribution progress per L2 (current supply / max supply)
- Income runway forecast (per-L2 completion estimate)
- Per-L2 emission rate, cumulative income, YTD totals
- Cross-chain holdings reconciliation (e.g., LFLX on Lemonchain + LFLX on BSC)
- NFT collection views for LemQuest and Swap Credit

**User experience**
- Single-page web application, responsive design (desktop primary, tablet/mobile functional)
- Modern auth (Clerk integration with MFA, social login, magic link)
- Real-time data freshness via WebSocket push for active sessions
- Empty states, error states, and onboarding flows for first-time users
- Settings for tax configuration, classification rules, integrations, and profile

### 2.2 Deferred to v1.1 or v2 (with rationale)

**Deferred to v1.1 (within 6 months of v1 launch):**

- **TMX Card / LemPay swipe data integration.** Explicitly deferred to v1.1 or later. Requires either (a) a published API from LemPay/Lemon Bank Group, or (b) a partnership negotiation that produces such an API, or (c) a CSV import flow once an export format exists. None of these are in place today, and building UI for data we can't yet pull is not productive. Card Spend page is **removed from v1 scope entirely** — when the data source materializes, it becomes a clean v1.1 addition that benefits from all the infrastructure (classifier, lot tracking, tax form integration) already built in v1.
- **TurboTax / TaxAct direct API integration.** CSV exports are sufficient for v1; native integrations come after launch.
- **Read-only sharing with CPAs.** Sub-account access for tax preparers. Useful but not core.
- **Lemon Ledger ↔ Ledger (fiat bookkeeping) API integration.** Separate SOW after Lemon Ledger has validated product-market fit.

**Deferred to v2:**

- **Mobile native apps (iOS / Android).** Web-responsive design serves v1; native apps after demand validation.
- **Other EVM chains (Polygon, Arbitrum, Optimism).** Architecture supports trivially; defer until Lemonchain + BSC alone has validated product-market fit.
- **DeFi position tracking on non-Lemonchain chains.** Uniswap LP positions, Aave deposits, etc. on other chains.
- **Real-time tax loss harvesting alerts.** Useful but expensive to build correctly.
- **Multi-user team collaboration features.** For accounting firms using Lemon Ledger across multiple clients.

### 2.3 Explicitly out of scope

- **Trading / order execution.** Lemon Ledger is read-only. We never request signatures or move funds. No exceptions.
- **Custody of any kind.** Users connect wallets via public address; we never hold private keys or assets.
- **Investment advice.** We surface tax-strategic information (lot disposal optimizer suggestions) but do not provide investment recommendations.
- **Tax filing.** We generate forms; users file them (directly or via CPA). We are not a registered tax preparer.
- **Fiat bookkeeping.** The existing Ledger product handles this. Lemon Ledger focuses on crypto.

---

## 3. Architecture

### 3.1 System topology

```
┌──────────────────────────────────────────────────────────────┐
│                    USER (browser / mobile web)               │
└───────────────────────────────┬──────────────────────────────┘
                                │ HTTPS
                                ▼
┌──────────────────────────────────────────────────────────────┐
│           FRONTEND (Vercel-hosted React SPA)                 │
│   Auth (Clerk) · Portfolio · Holdings · L2 · Tax · Settings  │
└───────────────────────────────┬──────────────────────────────┘
                                │ REST + WebSocket
                                ▼
┌──────────────────────────────────────────────────────────────┐
│           API LAYER (FastAPI on Railway)                     │
│        Query endpoints · Mutations · WebSocket events        │
└────────────────────┬────────────────────────┬────────────────┘
                     │                        │
                     ▼                        ▼
       ┌────────────────────────┐  ┌────────────────────────┐
       │   POSTGRES             │  │   REDIS                │
       │   - wallets, entities  │  │   - price cache (60s)  │
       │   - tax_lots           │  │   - balance cache      │
       │   - classified_txs     │  │   - tx cache           │
       │   - historical_prices  │  │   - Celery queue       │
       │   - audit logs         │  │   - WebSocket pub/sub  │
       └────────────────────────┘  └────────────────────────┘
                                 ▲
                                 │
              ┌──────────────────┴──────────────────┐
              ▼                                     ▼
   ┌────────────────────────┐         ┌────────────────────────┐
   │   CELERY WORKERS       │         │   CELERY BEAT (cron)   │
   │   - chain ingestion    │         │   - nightly sync       │
   │   - classification     │         │   - oracle backfill    │
   │   - lot tracking       │         │   - cache warming      │
   │   - bridge correlation │         │   - supply snapshots   │
   └──────────┬─────────────┘         └────────────────────────┘
              │
              ▼
   ┌────────────────────────────────────────────────────────────┐
   │              EXTERNAL DATA SOURCES                         │
   │                                                            │
   │  Lemonchain Blockscout (mainnet + testnet)   ─┐            │
   │  BSC BscScan API                              ─┤            │
   │  PriceDataFeed Oracle (via JSON-RPC)          ─┼─→ via     │
   │  PancakeSwap V2 (via BSC RPC)                 ─┤  web3.py   │
   │  CoinGecko API                                ─┤            │
   │  CoinMarketCap API (cross-validation)         ─┘            │
   └────────────────────────────────────────────────────────────┘
```

### 3.2 Three-tier data architecture

**Tier 1 — Chain ingestion (raw data layer).** Celery workers poll Blockscout (Lemonchain) and BscScan (BSC) every 60 seconds for active wallets. Decoded events written to raw tables. Idempotent — re-running ingestion produces same data without duplication. Tracks `last_synced_block` per (wallet, chain) for efficient incremental sync.

**Tier 2 — Classification and lot tracking (transformation layer).** Worker processes consume raw events and produce classified events plus tax lots. Houses the per-L2 decoder logic, bridge correlation module, burn-aware accounting, Swap Credit redemption matcher, and cross-entity transfer detector. Each classifier module is independently testable and deployable.

**Tier 3 — Query and presentation (read layer).** Read-optimized views over the ledger tables. Powers all UI rendering and tax form generation. No direct chain queries from this tier — everything served from the database with Redis caching in front. Maintains separation: if Tier 1 fails (Blockscout downtime), users still see their last-known-good state.

### 3.3 Tech stack decisions

| Component | Choice | Rationale |
|---|---|---|
| Backend language | Python 3.12 + FastAPI | Mature crypto ecosystem (`web3.py`, `eth_abi`); fast async; good developer pool |
| Frontend framework | React 18 + TypeScript | Existing Ledger product uses React; shared design tokens; broad expertise |
| Database | PostgreSQL 16 | ACID compliance for ledger correctness; mature multi-table reporting |
| Cache + queue | Redis 7 + Celery | De facto standard; Celery integration; pub-sub for WebSocket events |
| Auth | Clerk | Handles MFA, social, magic links out of the box; don't reinvent |
| Frontend hosting | Vercel | Best DX for React SPAs; CDN; existing team familiarity |
| Backend hosting | Railway | Existing Ledger deployment; simple Postgres + Redis; reasonable pricing |
| Web3 (backend) | web3.py | Mature; covers all chain interactions |
| Web3 (frontend) | viem + wagmi + WalletConnect v2 | Modern, type-safe; React hooks; standard WalletConnect flow |
| Monitoring | Sentry + Grafana | Sentry for errors; Grafana for metrics; alerts to Discord/Slack |
| CI/CD | GitHub Actions | Free for our size; integrates with Railway and Vercel |

**Alternative considered:** Node.js + TypeScript + viem for full-stack TS consistency. Equally valid. Choose by developer comfort. Both achieve the same outcomes within similar timelines.

### 3.4 Database schema (key tables)

Full schema in Appendix A. Highlights:

**`users`** — Auth identity (managed by Clerk; we store the Clerk user_id and any profile preferences)

**`entities`** — User's entities. Fields: id, user_id, name, type (personal / s-corp / llc-passthrough / partnership / sole-prop), tax_id, formation_date, fiscal_year_end, default_basis_method

**`wallets`** — Wallet records. Fields: id, user_id, chain, address (lowercase), name, role (vest/live/stake/nft/cold/bridge/other), added_via, added_at, last_synced_at, last_synced_block, is_active, notes. **Composite unique:** (user_id, chain, address)

**`wallet_entity_assignments`** — SCD-Type-2 audit trail. Fields: id, wallet_id, entity_id, effective_from, effective_to (nullable; null = current), classification (initial-assignment / capital-contribution / sale / gift / loan / reassignment), note, created_at

**`token_registry`** — Tier-1 (system-managed) + Tier-2 (user-managed) token definitions. Fields: id, chain, contract_address, symbol, name, decimals, tier (1 or 2), category (ecosystem-l2 / ecosystem-stablecoin / ecosystem-native / external-stablecoin / external-major / external-other), pricing_source_primary, pricing_source_fallback, is_deflationary, max_supply, project_metadata (jsonb)

**`user_token_classifications`** — Per-user decisions on Tier-2 tokens. Fields: user_id, token_id, classification (include / spam / pending-review), classified_at, note. Tokens not in this table default to "pending-review" when first discovered.

**`raw_transactions`, `raw_token_transfers`, `raw_internal_txs`, `raw_logs`** — Tier-1 ingested chain data, indexed by (chain, wallet, block_number) for fast incremental queries

**`classified_transactions`** — Tier-2 output. One row per logical event (a single tx may produce multiple classified events). Fields: id, chain, tx_hash, block_number, occurred_at, wallet_id, classification, token_id, amount, value_usd_at_event, related_lots (array of lot ids), bridge_correlation_id (nullable), notes, manual_override

**`tax_lots`** — Each acquisition that creates basis. Fields: id, entity_id, wallet_id, token_id, acquired_at, acquisition_type (buy / mint / reward / bridge-in / gift / cap-contribution), quantity, quantity_remaining, cost_basis_usd, source_classified_tx_id

**`lot_disposals`** — Each disposal event with consumed-lot references. Fields: id, lot_id, disposal_tx_id, quantity_consumed, proceeds_usd, gain_loss_usd, holding_period (short / long), disposed_at

**`historical_prices`** — Archived oracle daily averages. Composite primary key: (chain, token_id, day_timestamp). Fields: average_price_usd, data_points, confidence, source (oracle / pancakeswap / coingecko / manual)

**`bridge_correlations`** — Confirmed cross-chain bridge event pairings. Fields: id, outflow_classified_tx_id, inflow_classified_tx_id, user_confirmed (bool), confidence_score, created_at

**`l2_emission_summaries`** — Materialized view of per-L2 reward emission totals per entity per period. Refreshed nightly.

**`audit_log`** — Append-only log of all user-initiated changes (wallet add/remove, classification override, entity reassignment, lot adjustment). Fields: id, user_id, action_type, target_id, before_state, after_state, occurred_at, ip_address

All tables have appropriate indexes for dominant query patterns. Foreign key relationships enforce referential integrity. The schema is migration-managed via Alembic.

### 3.5 The Tier-1 / Tier-2 token model (architectural detail)

This subsection elaborates the token registry decision because it has implications across multiple subsystems.

**Tier-1 tokens** are system-managed and ship with the product. They are pre-populated in the `token_registry` table at deployment time. The Tier-1 set includes:

- 21 Lemonchain tokens: native LEMX, WLEMX, LUSD, 19 L2 tokens
- 12 BSC tokens: BEP-20 LEMX, 10 BEP-20 L2 versions, LBST (BSC-only)
- 3 NFT collections: LemQuest, Swap Credit (Lemonchain + BSC)

Tier-1 tokens get:
- Oracle-based pricing (Lemonchain) or DEX-based pricing (BSC)
- Ecosystem context in the UI (project descriptions, distribution status, etc.)
- L2-specific classification (rewards, stakes, etc.)
- Full participation in L2 emission income tracking

**Tier-2 tokens** are user-curated. The system discovers them by scanning each newly-added wallet for token transfer events involving contract addresses not in Tier-1. The discovered tokens enter a "pending classification" state, surfaced in the Settings → Tokens panel with a count badge.

For each pending token, the user chooses:
- **Include** — track it as a real holding. The system fetches metadata (name, symbol, decimals) from the chain, looks up pricing on CoinGecko/CoinMarketCap, and starts treating it like a Tier-1 token for accounting purposes (lots, disposals, tax forms). Just without the ecosystem-specific context.
- **Spam** — hide from holdings UI. Balance is still tracked silently in the background (so we don't lose data), but no UI surfaces it, no tax forms include it, no income events generated. User can reverse this later.
- **Review later** — stays in the pending pile. Useful for "I'll deal with this when I have time."

Once a token is classified by one user, that classification is per-user. Other users will independently classify the same token. (We deliberately do NOT promote user-classified Tier-2 tokens to Tier-1 — that's a system-level decision made by the product team, not crowdsourced.)

For pricing of Tier-2 "include" tokens, the cascade is:
1. CoinGecko by contract address + chain (most reliable)
2. CoinMarketCap by contract address (secondary)
3. DEX reserve lookup (PancakeSwap on BSC, HEXDEX on Lemonchain) if the token has a known pool
4. Manual price entry by the user (final fallback)

The user can override the pricing source per token in Settings. Useful when CoinGecko has an outdated entry or a token has multiple listings.

### 3.6 Pricing service architecture (detailed)

The pricing service is consumed by the classifier (for FMV-at-receipt determination) and the UI (for current prices).

**Public interface:**

```python
class PricingService:
    def get_current_price(self, chain: str, token_id: int) -> Optional[Decimal]:
        """Returns USD price with 60s cache. None if unavailable."""

    def get_historical_price(self, chain: str, token_id: int, timestamp: datetime) -> Optional[Decimal]:
        """Returns USD price at the given timestamp (day-level resolution).
        None if pre-archive or no oracle data."""

    def get_supported_tokens(self, chain: str) -> List[TokenInfo]:
        """Returns all Tier-1 + user's Tier-2 'include' tokens."""

    def is_priceable(self, chain: str, token_id: int) -> bool:
        """Check if we have any price source for this token."""

    def health_check(self) -> PricingHealthReport:
        """Status of all upstream sources (oracle paused?, CoinGecko available?, etc.)"""
```

**Internal cascade — Tier-1 Lemonchain tokens:**

```
1. Check Redis cache (60s TTL) → return if hit
2. Call PriceDataFeed.getPrice(token_addr) via web3.py
   - On success: convert from 8 decimals, cache, return
   - On PriceStale revert: fall through to DEX
   - On TokenNotSupported revert: fall through to DEX (shouldn't happen for Tier-1)
   - On paused() == true: fall through to last cached value with stale-flag
3. HEXDEX fallback:
   - Find pool via factory.getPair(token_addr, LEMX_addr)
   - Read pool.getReserves(), compute spot price in LEMX
   - Multiply by LEMX/USD (which we have authoritatively from oracle)
   - Cache, return
4. Final fallback: return None
```

**Internal cascade — Tier-1 BSC tokens:**

```
1. Check Redis cache
2. PancakeSwap V2 reserves
   - Call factory.getPair(token_addr, BUSD_ADDR or BNB_ADDR)
   - Read pool.getReserves()
   - Compute spot price
3. If CoinGecko listing exists: cross-validate
   - Divergence >5%: log warning, use more recent value
4. Cache, return
```

**Internal cascade — Tier-2 (external) tokens:**

```
1. Check Redis cache
2. CoinGecko by contract address: /api/v3/simple/token_price/<chain>?contract_addresses=<addr>
3. CoinMarketCap by contract address: /v2/cryptocurrency/quotes/latest?contract_address=<addr>
4. DEX reserves (PancakeSwap if BSC, HEXDEX if Lemonchain) if pool exists
5. User-supplied override (from token_settings table) if set
6. Return None — UI surfaces "no price available, treat as cost-basis $0"
```

**Historical price path (tax-critical):**

```
1. Round timestamp to day boundary (UTC midnight)
2. Query historical_prices table where (chain, token_id, day) matches → return if hit
3. Live fallback: query DailyAverageFinalized events around that block (Tier-1 oracle tokens)
4. Live fallback for Tier-2: query CoinGecko historical price API for that date
5. Pre-archive: return None
```

**Nightly archival job (Celery beat):**

```python
@app.task
def nightly_oracle_sync():
    for token in Tier1Tokens.lemonchain():
        history = oracle.getDailyAveragesHistory(token.address, max_entries=30)
        for entry in history:
            historical_prices.upsert(
                chain='lemonchain',
                token_id=token.id,
                day=entry.timestamp,
                price_usd=entry.average_price / 10**8,
                data_points=entry.data_points,
                confidence=entry.confidence,
                source='oracle'
            )
    alert_if_oracle_stale_or_paused()

@app.task
def nightly_tier2_sync():
    """For user-included Tier-2 tokens, archive yesterday's CoinGecko price."""
    yesterday = today() - timedelta(days=1)
    for token in user_included_tier2_tokens():
        price = coingecko.historical_price(token, yesterday)
        if price:
            historical_prices.upsert(...)

@app.task
def nightly_supply_snapshot():
    """For deflationary tokens, record totalSupply() to track burn progress."""
    for token in Tier1Tokens.deflationary():
        supply = ERC20(token.address).totalSupply()
        token_supply_history.insert(chain, token_id, today(), supply)
```

**Estimated implementation effort for the pricing service:** ~1,800 lines of code including comprehensive tests. ~1.5 weeks of focused work.

### 3.7 Bridge correlation module

Cross-chain transfer pairs are detected using the following signals:

**Candidate identification:**
- An outflow on chain A: classified_transaction with type = `transfer-out`, `to` = unrecognized address, material amount
- An inflow on chain B: classified_transaction with type = `transfer-in`, `from` = unrecognized address, material amount

**Pairing criteria (all must match):**
- Time window: inflow occurs within ±2 hours of outflow
- Token match: same symbol on both chains (looked up via cross-chain mapping table)
- Amount match: within 1% (accounting for any bridge fee)
- Same user (i.e., the inflow wallet and outflow wallet both belong to the same user)

**Confidence levels:**
- **High confidence** (auto-classify): amount match within 0.5%, time window <30 min, custody address recognized
- **Medium confidence** (default + user notify): amount match within 2%, time window <2 hours, custody address inferred
- **Low confidence** (require user confirmation): amount match within 5%, time window <4 hours, custody address unknown

**User confirmation UI:** For medium and low confidence pairs, the Transactions page shows a "Possible bridge event" prompt: *"We found a 1,000 LFLX outflow on Lemonchain at 14:23 and a 998.5 LFLX inflow on BSC at 14:48. Are these the same bridge transfer?"* User confirms (auto-pair) or rejects (treat as separate events).

**Empirical custody address learning:** A nightly job analyzes confirmed bridge pairs. Addresses that appear in many confirmed pairings (e.g., 5+ unique users' confirmed bridges) get promoted to the known-custody list, raising future confidence scores.

**Unmatched candidates:** After 7 days of no match found, the system surfaces the transaction to the user: *"This 1,000 LFLX outflow looks like it might be a bridge but we didn't find a matching inflow. Treat as: bridge (no inflow yet) / sale / transfer to third party / other?"*

**Tax implications correctly handled:**
- Confirmed bridge pair: **non-taxable** transfer. Cost basis preserved across chains. No Form 8949 entry.
- Rejected pair (separate events): outflow = disposal (Form 8949 entry, gain/loss recognized); inflow = acquisition (new lot at receipt FMV).
- Unmatched: defaults to taxable until user resolves.

**Estimated implementation:** ~2,000 lines of code including tests and UI. ~2.5 weeks.

### 3.8 Per-L2 decoder framework

The 19 Lemonchain L2 tokens each have somewhat different reward emission patterns. Rather than 19 unique implementations, we use a **decoder framework** with:

**Base class `L2Decoder`** providing common logic:
- Detect mint events (NFT acquisition with mint fee → cost basis)
- Detect stake events (NFT transfer to staking contract → non-taxable)
- Detect reward emission events (token transfer from staking contract → ordinary income at FMV)
- Detect unstake events (NFT transfer back to user → non-taxable)
- Detect completion events (distribution cap reached → flag in L2 metadata)

**Per-L2 subclass** overrides specific behaviors as needed:
- Staking contract address (configured per L2)
- Reward emission event signature (most use standard ERC-20 Transfer, but some may use custom events)
- Mint fee calculation (some L2s have variable fees)
- Burn-and-mint vs straight-mint behavior

For each L2, the per-L2 customization is small (typically 50-100 lines of code). Most of the logic lives in the base class.

**Configuration table `l2_decoder_config`** stores per-L2 settings (staking contract, mint contract, fee structure, etc.). This is initially populated from the SOW's appendix B (contract addresses).

**Test coverage:** Each L2 decoder gets test cases covering: a real mint event from the chain, a real reward emission, a stake/unstake pair, the boundary case at completion. Total test fixtures: ~80 (19 L2s × ~4 events each).

**Estimated implementation:** ~3,500 lines of code (base + 19 subclasses + tests). ~3.5 weeks. Parallelizable across multiple developers.

### 3.9 Multi-entity ledger and SCD assignment

Wallets are assigned to entities. The assignment can change over time (e.g., a wallet is created in your personal name, then later "contributed" to an LLC). Both the current state and the historical assignment matter for tax purposes.

**Pattern:** SCD Type 2 (slowly changing dimension) on `wallet_entity_assignments`.

**Initial assignment:** When a wallet is added to the system, one row is created:
```
{wallet_id: X, entity_id: Personal, effective_from: today, effective_to: null, classification: 'initial-assignment'}
```

**Reassignment:** When the user changes a wallet's entity (Personal → LLC), two operations occur in a single transaction:
1. Close the current row: `UPDATE wallet_entity_assignments SET effective_to = today WHERE wallet_id = X AND effective_to IS NULL`
2. Insert new row: `{wallet_id: X, entity_id: LLC, effective_from: today, effective_to: null, classification: 'capital-contribution'}`

The user is prompted to choose the classification at reassignment time: capital-contribution (most common), sale (rare), gift, loan, or simple-reassignment.

**Tax implications:**
- **Capital contribution:** Non-taxable transfer of holdings from owner to entity. Cost basis carries over.
- **Sale:** Taxable event. Disposal from old entity at FMV, acquisition by new entity at FMV.
- **Gift:** Taxable to recipient (gift tax form 709 may apply for owner above annual exclusion).
- **Loan:** Non-taxable; creates loan receivable/payable on entity books.
- **Simple reassignment:** Used only for record-correction (no economic substance).

**Cross-entity transfer detection:** When a transaction is classified, the classifier checks: is this a transfer between two wallets owned by the same user but assigned to different entities? If yes: surface the cross-entity transfer dialog requiring user classification before completing the classification.

**Reports query by effective date:** All tax form generation queries the SCD table to determine which wallet belonged to which entity at any given point. Form 8949 for tax year 2025 reports the entity assignment as of each transaction's date, not the current assignment.

### 3.10 NFT classification (project NFTs, LemQuest, Swap Credit)

Each NFT type has distinct tax treatment.

**Project NFTs (the 19 L2s):** Mint = acquisition with cost basis (mint fee + gas). Stake/unstake = non-taxable transfers preserving basis. Rewards emitted are ordinary income (Schedule 1 Line 8z) at FMV on receipt. No taxable event on the NFT itself unless sold or burned.

**LemQuest NFTs:** Mint = acquisition with cost basis. Hold = no taxable events. Disposal (sale on future marketplace, or redemption for items) = taxable event with gain/loss = (proceeds) - (basis).

**Swap Credit NFTs:** Earn (via gameplay) = ordinary income at FMV at receipt (Schedule 1). Hold = no taxable events. Redemption (consumed in exchange for L2 NFT) = paired event:
- Disposal of Swap Credit (gain/loss vs basis)
- Acquisition of L2 NFT with cost basis = FMV of Swap Credit at redemption

The classifier needs a specific rule for the redemption pairing: detect a Swap Credit outflow + L2 NFT inflow in the same transaction hash, classify as redemption.

### 3.11 Wallet authorization and security model

Lemon Ledger's most security-sensitive operation is **adding a wallet to a user account**. Once a wallet is associated with an account, the user can see its full transaction history, classifications, lot tracking, income totals, and inferred tax position — a substantially richer view than browsing the public block explorer. The aggregation is the product, but the aggregation also creates a security risk that must be addressed at the authorization layer.

**The core principle:** every wallet addition must be cryptographically proven to be controlled by the user adding it. No wallet enters the system on the basis of a typed address alone.

#### 3.11.1 The signature-based ownership proof

Wallet addition uses **EIP-4361 "Sign-In with Ethereum" (SIWE)** message signing. The flow:

1. User initiates "Add Wallet" in the UI
2. Backend generates a challenge message of the form:
   ```
   Lemon Ledger wants you to sign in with your Ethereum account:
   0x7A3f...91Ed

   Authorize this wallet for read-only accounting in Lemon Ledger
   account {user_id}.

   URI: https://lemonledger.app
   Version: 1
   Chain ID: {chain_id_of_target_wallet}
   Nonce: {32-byte cryptographically random string}
   Issued At: {ISO 8601 timestamp}
   Expiration Time: {issued_at + 10 minutes}
   ```
3. The challenge nonce is stored in a short-lived (10 minute TTL) Redis key
4. Challenge is sent to the user's wallet via WalletConnect v2 `personal_sign` method
5. User reviews the message in their wallet UI and approves (signs) it
6. Signed message returns to Lemon Ledger backend
7. Backend uses `eth_account.recover` (Python) / `viem.recoverAddress` (TypeScript) to recover the signing address from the signature
8. If the recovered address matches the address being added: wallet is verified and added to the account
9. The nonce is invalidated immediately after use (single-use)
10. If verification fails for any reason: addition is rejected with an explicit error explaining why

**This proves wallet ownership at one point in time.** Crucially, this signature **cannot be used to authorize a transaction** — message signing and transaction signing are different operations in every EVM wallet. A signed message has no executable bytecode, no nonce, no gas, and cannot move tokens. The user retains full custody.

#### 3.11.2 WalletConnect v2 as the primary integration

WalletConnect v2 is the canonical Web3 wallet-to-dApp communication protocol. It supports:

- **Browser-based wallets** (MetaMask, Trust Wallet, Coinbase Wallet, Rainbow, Lemon Zest)
- **Mobile-based wallets** (same list, via QR code)
- **Hardware wallets** through Lemon Zest or compatible interfaces (Ledger, Trezor)
- **Multi-sig wallets** (Gnosis Safe via SafeAuthKit)

Implementation: `@walletconnect/web3modal` (with `wagmi` adapters) on the frontend, no WalletConnect-specific backend dependencies (the backend just verifies signatures using standard libraries).

WalletConnect requires a Project ID from `cloud.walletconnect.com`. Free tier covers 100k connections/month, more than sufficient for v1.

#### 3.11.3 Hardware wallet considerations

For users with hardware wallets (Ledger, Trezor), the signing flow involves the physical device. The challenge message is displayed on the hardware screen; the user approves on the device itself. **This is the most secure path** — Lemon Ledger never sees the private key, and the device confirms exactly what is being signed. The UI should highlight this option as "Most secure: cold storage wallet signing" in the onboarding flow.

#### 3.11.4 Multi-signature wallet handling

Multi-sig wallets (Gnosis Safe primarily, but also other implementations) are common for business entities holding significant crypto. These wallets require multiple signers to authorize transactions. For Lemon Ledger's read-only ownership proof, the question is: what level of multi-sig consent is required?

**Recommendation:** **Any one signer** can authorize a multi-sig wallet for read-only Lemon Ledger access. Rationale:

- Read-only access doesn't move funds — the security model of the multi-sig (preventing unauthorized transfers) is not violated
- Requiring all signers to coordinate for every wallet addition would make the bookkeeping flow impractical for legitimate business use
- Any signer being able to authorize doesn't grant any signer additional power they don't already have (they could already view the wallet on any explorer)

Implementation: when adding a multi-sig wallet, detect that the address is a contract (not an EOA). Look up the Safe's signer list. The challenge message is then signed by any one of the listed signers using their own EOA — Safe's `isValidSignature` mechanism verifies that the signer is authorized for the Safe. The result: any signer can authorize read access; spending requires the full multi-sig as before.

#### 3.11.5 Fallback for unsupported wallet types

Some wallets the user controls may not support WalletConnect: legacy paper wallets, hand-managed seed phrases, exchange-derived addresses that the user owns the seed for but doesn't actively use in any wallet UI, etc.

**Fallback flow:** the UI provides the challenge message text and a copy-button. The user signs it manually using whatever tool they prefer (MyEtherWallet's "Sign Message", a CLI tool, etc.) and pastes the signature into Lemon Ledger. Backend verifies the same way. Less polished UX, enables every wallet type.

**This is the only path** that doesn't involve a real-time signing interaction. It must be explicitly chosen by the user (a "Manual signature" option in the Add Wallet flow), and we should document the procedure with screenshots/examples for the most common scenarios.

#### 3.11.6 Re-verification cadence

A signature proves ownership at one point in time. Ownership can change (wallet keys can be compromised, multisig signers can be removed, accounts can be sold). To keep the proof reasonably fresh:

- **Every 90 days**, the system requires re-verification. The user is notified two weeks before expiry. They re-sign a new challenge message via the same flow as initial addition.
- **On suspicious activity** (e.g., the wallet starts behaving in ways inconsistent with its history; a new device tries to access an account that has this wallet linked), the wallet is **temporarily quarantined** and re-verification is required before any read access is restored.

This is more conservative than most Web3 dApps (which often verify once and trust forever), but it's appropriate for a financial product. The UX overhead is one signing request every quarter.

#### 3.11.7 What we do NOT do

Explicit non-features to clarify the security model:

- **We never request transaction-signing authority.** WalletConnect supports both `personal_sign` (read-only proof) and `eth_sendTransaction` (transaction signing). We only ever request the former.
- **We never persist the signature beyond verification.** Once a signature is verified and the wallet is added, we discard the signature itself. Only the resulting "wallet X is authorized for user Y as of timestamp Z" record is persisted.
- **We never use a wallet's session to query other dApps or services.** The WalletConnect session is used only for the initial signature exchange, then disconnected.
- **We never store private keys, seed phrases, or any wallet credential.** This is fundamental and non-negotiable.
- **We never request "account abstraction" or smart-account permissions** that would grant ongoing operational authority.

#### 3.11.8 Audit logging

All wallet authorization events are logged to the `audit_log` table:

- Wallet addition attempts (successful and failed)
- Signature verification details (signing address recovered, nonce used, timestamp)
- Re-verification events
- Wallet removal events
- Failed re-verification leading to quarantine
- Quarantine release events

The audit log is append-only, indexed, and surfaced in Settings → Security so users can review their own wallet authorization history. This both supports user trust and creates a defensible audit trail if questions arise later.

#### 3.11.9 Implementation effort and timeline

The wallet authorization module is approximately **1,200 lines of code** (frontend signature flow + backend verification + audit logging + UI). Estimated **1 week of focused work** added to Phase 2 of the build. This is the security-critical foundation that everything else builds on, so it's positioned as a Phase 2 priority deliverable.

---

## 4. Build phases

The build is structured in four phases with explicit go/no-go gates between them. Each phase ends with a demo and acceptance review before the next phase commences.

### 4.0.1 Foundational deliverable: Design System (Phase 1, Week 1)

Before any feature UI work begins, a formalized **Design System** must be established as a Phase 1 deliverable. This is non-negotiable: building feature UI without a design system in place leads to inconsistent visual language, parallel implementations of the same primitives, and expensive rework. The prototype's visual language exists in CSS variables but is not yet a formal system — converting it is the first UI task.

**Required artifacts:**

1. **Design tokens file** (`/packages/design-tokens/`) as the single source of truth. Defined in TypeScript, with a build step that generates parallel CSS variable definitions for runtime use. All color values, typography sizes, spacing scale, border radii, shadows, and motion timings live here. **No hardcoded values anywhere else in the codebase.**

2. **Component library** (`/packages/ui/`) implementing every UI primitive used by the application. The component inventory includes:
   - Form controls: `Button`, `IconButton`, `Input`, `TextArea`, `Select`, `Checkbox`, `Radio`, `Toggle`, `DatePicker`
   - Display: `KPI`, `Pill`, `Tag`, `Badge`, `Card`, `Panel`, `Divider`, `Tooltip`, `Avatar`
   - Layout: `Stack`, `Cluster`, `Grid`, `Sidebar`, `Header`, `Footer`, `PageHeader`
   - Data: `Table` (with sortable columns, row hover, click-through), `EmptyState`, `Skeleton`, `Sparkline`
   - Feedback: `Toast`, `Dialog`, `Drawer`, `Spinner`, `ProgressBar`
   - Crypto-specific: `AddressDisplay` (truncated, copy-to-clipboard), `TokenIcon`, `EntityPill`, `WalletChip`, `ChainBadge`, `ClassificationBadge`

3. **Styleguide page** at `/styleguide` (gated to internal users only in production). A live, rendered catalog of every token and every component with usage examples and prop documentation. New components get added here first, then used in features.

4. **Linting rules** that prevent design-token bypass:
   - ESLint plugin flagging any hardcoded color hex/rgb values, hardcoded font families, hardcoded spacing values outside the defined scale
   - Stylelint rules enforcing the same for any CSS files
   - CI failure if linting fails — same gate as type checking and tests

**Required design tokens** (this is the starting baseline pulled from the v3 prototype; the developer may refine but not arbitrarily change):

**Colors:**

```typescript
export const colors = {
  // Surface
  bg: {
    base: '#0a0a0b',
    card: '#141416',
    elev: '#1f1f23',
    overlay: 'rgba(10, 10, 11, 0.85)',
  },

  // Borders
  border: {
    subtle: '#26262b',
    default: '#3a3a42',
    strong: '#52525a',
  },

  // Text
  text: {
    primary: '#fafafa',
    dim: '#a8a8b0',
    muted: '#737380',
    inverse: '#1a1a1a',
  },

  // Brand
  brand: {
    lemon: '#fdd835',       // Primary brand color
    lemonGlow: 'rgba(253, 216, 53, 0.08)',
    gold: '#f5b800',        // Secondary brand color
    lemonBright: '#ffe55c', // Accent for emphasis
  },

  // Semantic
  semantic: {
    green: '#2ecc71',       // Positive deltas, success
    red: '#e74c3c',         // Negative deltas, errors
    orange: '#ff9a3c',      // Warnings, attention
    blue: '#4a9eff',        // Information
    purple: '#a374ff',      // Special states (vesting)
    teal: '#2ec4b6',        // Subtle highlight
  },

  // Entity color system
  entity: {
    personal: '#fdd835',    // Lemon
    businessA: '#4a9eff',   // Blue
    businessB: '#a374ff',   // Purple
    businessC: '#2ec4b6',   // Teal (reserved for additional entities)
  },

  // L2 brand palette (used on L2 cards)
  l2Brand: {
    lemFlix: { from: '#ff4d6d', to: '#c9184a' },
    lemonBank: { from: '#2ec4b6', to: '#20a39e' },
    lemPay: { from: '#fb8500', to: '#ffb703' },
    lemCare: { from: '#06d6a0', to: '#08b894' },
    hexDex: { from: '#8338ec', to: '#5e2ca5' },
    lemTravel: { from: '#00b4d8', to: '#0077b6' },
    catfiz: { from: '#f72585', to: '#b5179e' },
    // ... full mapping for all 19 L2s in the actual file
  },
};
```

**Typography:**

```typescript
export const typography = {
  fonts: {
    display: '"Sora", system-ui, sans-serif',     // Headers, titles
    body: '"Inter", system-ui, sans-serif',       // Body text, UI
    mono: '"JetBrains Mono", "Menlo", monospace', // Numbers, addresses, code
  },
  sizes: {
    xs: '10px',   // Small labels, footnotes
    sm: '11px',   // Secondary text
    base: '12px', // Body
    md: '13px',   // Standard UI text
    lg: '14px',   // Emphasized body
    xl: '16px',   // Subheadings
    '2xl': '18px',
    '3xl': '22px', // KPI values
    '4xl': '28px', // Page titles
    '5xl': '38px', // Hero values
  },
  weights: { regular: 400, medium: 500, semibold: 600, bold: 700 },
  letterSpacing: { tight: '-0.025em', normal: '0', wide: '0.1em', wider: '0.12em' },
};
```

**Spacing scale** (use only these values for padding, margin, gap):
```typescript
export const spacing = {
  0: '0',  1: '4px',  2: '8px',  3: '12px', 4: '16px',
  5: '20px', 6: '24px', 8: '32px', 10: '40px',
  12: '48px', 16: '64px', 20: '80px', 24: '96px',
};
```

**Border radius:**
```typescript
export const radius = {
  sm: '4px', md: '6px', lg: '10px', xl: '14px', pill: '100px', full: '9999px',
};
```

**Motion:**
```typescript
export const motion = {
  fast: '120ms ease-out',     // Hover states
  base: '180ms ease-out',     // Standard transitions
  slow: '320ms ease-in-out',  // Page transitions, dialogs
};
```

**Spacing scale enforcement:** Components and pages use these tokens via Tailwind-style utility classes generated from the tokens, OR direct CSS variable references. Either is fine; mixing is not. The developer chooses one approach in Phase 1 and applies it consistently.

**Acceptance criteria for the Design System deliverable:**
- All tokens defined in code with TypeScript types
- All UI primitives implemented in the component library
- Styleguide page renders all components and tokens with usage examples
- Linting rules enforce token usage (CI fails on hardcoded values)
- A developer adding a new feature can complete it without writing any custom CSS — only using existing components and tokens
- Visual regression tests cover the styleguide page

**Estimated implementation:** Approximately 800-1200 lines of code spread across the tokens file, ~30 component primitives, the styleguide page, and lint configuration. **Approximately 1 week of focused work**, completed before any feature pages are built.

---

### 4.1 Phase 1 — Data Layer (Weeks 1–5)

**Objective:** Prove the engine works against real wallets. No UI work in this phase.

**Deliverables:**

1. Backend project scaffolding: FastAPI + Celery + Postgres + Redis + GitHub Actions CI
2. Auth integration (Clerk) at the API layer (UI auth comes in Phase 2)
3. Database schema with Alembic migrations
4. Wallet management API endpoints (CRUD + SCD entity assignment)
5. Chain ingestion workers: Lemonchain (Blockscout) + BSC (BscScan)
6. Token registry seeded with all Tier-1 tokens (33 contracts + 3 NFT collections)
7. Tier-2 token discovery + pending-classification workflow (API only)
8. Pricing service (oracle + DEX fallback + CoinGecko + CoinMarketCap)
9. Historical price backfill (one-time job for `DailyAverageFinalized` events)
10. Per-L2 decoder framework + 19 L2 subclasses
11. Bridge correlation module (heuristic pairing + custody learning)
12. Lot tracking engine (FIFO, HIFO, Specific ID, Average Cost)
13. Cross-entity transfer detection
14. CLI: `lemon-ledger sync --wallet <addr>` / `lemon-ledger generate-8949 --year 2026 --entity personal`
15. Form 8949 generator producing valid IRS-compliant output (PDF + CSV)

**Acceptance criteria for Phase 1:**
- Run CLI against the project sponsor's wallets (Lemonchain + BSC)
- Generated Form 8949 reconciles to manual classification with <2% deviation
- Bridge correlation correctly identifies all cross-chain transfers in observed historical data
- Per-L2 decoders correctly classify reward emissions for all 11 actively-held L2s
- Pricing service returns historical FMV for any past date with p95 <100ms (cached)
- Test coverage >80% across the classifier, pricer, and lot tracker
- All integration tests pass

**Go/no-go decision:** Acceptable error rate is <2% of classified transactions requiring manual override. If higher, fix before Phase 2.

### 4.2 Phase 2 — Core UI (Weeks 6–10)

**Objective:** Build the minimum UI for a user to manage wallets, see holdings, and review transactions. **Begin with the Design System and Wallet Authorization layer** as foundational deliverables before any feature UI.

**Deliverables (in order):**

1. **Design System foundation** (see §4.0.1) — design tokens, component library, styleguide, linting rules. Completed before any feature pages built.
2. **Wallet Authorization module** (see §3.11) — SIWE message signing, WalletConnect v2 integration, signature verification backend, hardware wallet path, multi-sig handling, manual signature fallback, audit logging. Completed before the Wallet Management screen is built (since the management screen depends on it).
3. Frontend project scaffolding: React + Vite + TypeScript + Vercel deployment
4. Auth UI via Clerk components (account-level auth, separate from wallet auth)
5. Onboarding flow (sign up → add first entity → add first wallet via signed authorization → see data)
6. Entity management screen (list, create, edit, delete with confirmation)
7. Wallet management screen with chain selector, entity assignment, **mandatory signature-based authorization**, re-verification scheduling
8. Portfolio dashboard: KPIs, top holdings table, allocation ring, entity scoping, insight cards
9. Holdings page with wallet-level breakdown grouped by entity
10. Transactions page with filter pills, day-grouped stream, classification badges, cross-entity prompts
11. Settings → Tokens panel for Tier-2 classification (include / spam / review)
12. Settings → Security panel showing wallet authorization audit log, re-verification status, quarantine state if applicable
13. Settings → Entities, Wallets, Tax Configuration, Profile sections

**Acceptance criteria for Phase 2:**
- All UI uses design tokens; CI lint fails on hardcoded values
- A new user can sign up, add 3 wallets across 2 entities **via signature proof**, and see correct holdings within 10 minutes
- Wallet addition with an invalid signature is rejected with a clear error
- Hardware wallet signing flow works end-to-end (tested against at least one Ledger or Trezor device)
- Multi-sig wallet addition works for at least one Gnosis Safe configuration
- Manual signature fallback works end-to-end
- Re-verification flow triggers correctly at 90-day anniversary
- Entity switching reflows all data correctly
- Cross-entity transfer dialog correctly classifies internal transfers
- Bridge events display as such (not as sales)
- Tier-2 token classification flow works end-to-end
- All UI works on desktop (Chrome, Firefox, Safari) at 1280px+
- Tablet/mobile-responsive but not fully optimized

### 4.3 Phase 3 — L2-Specific UI (Weeks 11–14)

**Objective:** Build the differentiating L2 features.

**Deliverables:**

1. L2 Projects page (19 L2 cards + 2 NFT collections + held/available sectioning)
2. Cross-chain holding visualization (Lemonchain + BSC reconciliation)
3. Staking & Rewards page (per-L2 reward streams, sparklines, calendar forecast)
4. Income runway forecast (completion timeline, drop forecasting, mint-to-maintain call-to-action)
5. YTD income breakdown by project (visual + table)
6. NFT collection detail views (LemQuest, Swap Credit positions, history)
7. L2 distribution progress bars driven by real `totalSupply()` data

**Acceptance criteria for Phase 3:**
- Every L2 the sponsor holds displays with correct token balances, NFT counts, and reward emission rates
- Income runway forecast matches manual calculation within 5%
- Cross-chain holdings reconcile correctly
- L2 distribution progress matches actual on-chain supply
- NFT collection pages correctly show positions

### 4.4 Phase 4 — Tax, Polish, Launch (Weeks 15–20)

**Objective:** Production-ready launch.

**Deliverables:**

1. Tax & Reports page (always-on tax position, live form preview, lot disposal optimizer)
2. PDF generation for Form 8949, Schedule D, Schedule 1, Schedule E, 1120-S lines
3. CSV exports for CPA hand-off (TurboTax-compatible, generic CPA format)
4. JSON export for power users
5. Lot disposal optimizer suggesting tax-efficient disposal lots
6. Onboarding flow polish (first-time user experience)
7. Empty states and error states across all pages
8. Settings → Integrations placeholder for future Ledger sync and future Card Spend integration (UI stubs only, no functionality)
9. Documentation (user-facing help docs, in-product tooltips)
10. Production deployment, monitoring, alerting, runbooks
11. Closed beta with 10-15 community members
12. Feedback incorporation cycle
13. Public launch readiness review (legal, privacy, security sign-off)

**Acceptance criteria for Phase 4:**
- Generated tax forms match manual calculation for sponsor's wallets within $5 across all entities
- Beta users (n=10) complete full onboarding without developer assistance
- All pages render <2s on first load (cached <500ms)
- Production environment fully monitored with alerts on failure modes
- Public launch checklist signed off

---

## 5. Timeline and resources

### 5.1 Single-developer timeline (20 weeks)

| Week | Phase | Focus |
|---|---|---|
| 1 | 1 | Project scaffold, schema, basic ingestion |
| 2 | 1 | Token registry, oracle integration, pricing service |
| 3 | 1 | Per-L2 decoders (10 of 19) |
| 4 | 1 | Per-L2 decoders (9 of 19), bridge correlation start |
| 5 | 1 | Bridge correlation complete, lot tracking, CLI 8949 generation. **Gate 1.** |
| 6 | 2 | **Design System** (tokens, components, styleguide, linting) |
| 7 | 2 | **Wallet Authorization** (SIWE signing, WalletConnect, signature verification, audit log) |
| 8 | 2 | Frontend auth scaffold, entity management, wallet management screen |
| 9 | 2 | Portfolio dashboard, holdings page |
| 10 | 2 | Transactions stream, Settings (Tokens, Security, Profile). **Gate 2.** |
| 11 | 3 | L2 Projects page (cards, layout, data binding) |
| 12 | 3 | Staking & Rewards page |
| 13 | 3 | Income runway forecast, NFT collections |
| 14 | 3 | Cross-chain reconciliation, L2 polish. **Gate 3.** |
| 15 | 4 | Tax & Reports page, form generation |
| 16 | 4 | PDF + CSV + JSON exports, lot disposal optimizer |
| 17 | 4 | Onboarding flow, empty states, integration stubs, documentation |
| 18 | 4 | Pre-launch security audit (external firm, see §9) |
| 19 | 4 | Closed beta, feedback incorporation |
| 20 | 4 | Production readiness, launch. **Gate 4.** |

### 5.2 Two-developer timeline (13 weeks)

Backend-focused developer takes Phase 1 + backend portions of subsequent phases (Wallet Authorization signature verification, classification, lot tracking, pricing, tax form generation). Frontend-focused developer starts on Design System in Week 2 (parallel with Phase 1) and owns the UI layer throughout, including Wallet Authorization UI integration in Week 4. Bridge correlation work happens in Weeks 3-5 by the backend developer. Clean handoffs at each gate. Total: roughly 13 weeks.

### 5.3 Budget estimates

**Working assumptions:**
- Mid-senior full-stack developer rate: $90-130/hr (US-based contractor)
- ~40 productive hours per week per developer

**Estimates:**

| Configuration | Total hours | Rate $90/hr | Rate $130/hr |
|---|---|---|---|
| 1 developer, 20 weeks | 800 | $72,000 | $104,000 |
| 2 developers, 13 weeks | 1,040 | $94,000 | $135,000 |

**Additional costs:**
- Pre-launch penetration test (external security firm): $5,000-$10,000
- AI-assisted adversarial code review during active development: ~$200-$500/month (Claude API costs for reviewer sessions)
- Bug bounty program post-launch: variable, budget $5,000-$10,000 reserve for first year

**Alternative: DIY with Claude Code.** Sponsor builds Phases 1-2 (or parts) with Claude Code assistance, then engages contractors for Phases 3-4. Trade-off: ~$35k-55k cash vs. ~250-350 hours of sponsor time (now including time for Design System and Wallet Auth foundational work). Best for sponsors who want maximum product understanding and have engineering capacity.

### 5.4 Infrastructure costs (monthly, post-launch)

| Service | Cost at launch | Cost at 1000 active users |
|---|---|---|
| Railway (backend + Postgres + Redis) | $50-100 | $400-800 |
| Vercel (frontend) | Free tier | $20-50 |
| Clerk auth | $25 + $0.02/MAU | $50-100 |
| WalletConnect Cloud | Free tier (100k connections/mo) | $0-50 |
| Sentry + Grafana | $25-50 | $100-200 |
| CoinGecko Pro (optional) | $0 (free tier) | $129 |
| BscScan Pro API key (optional) | $0 (free tier) | $50 |
| **Total** | **~$200-400/mo** | **~$800-1500/mo** |

---

## 6. Risks and mitigations

### 6.1 Technical risks

| Risk | Impact | Mitigation | Escalation trigger |
|---|---|---|---|
| Blockscout API rate limits constrain ingestion | Medium | Aggressive caching (90%+ hit ratio); request higher rate limit from Lemonchain team; long-term mirror chain data | >10% of ingestion runs hit rate limits |
| Bridge correlation false positives/negatives | Medium | User confirmation for ambiguous; empirical custody learning; manual override | >5% of cross-chain transfers require manual override after first 30 days |
| L2 contracts upgrade and break decoders | Low-medium | Subscribe to upgrade events; regression tests | Any L2 upgrade not detected within 24 hours |
| Price oracle goes paused or unhealthy | Low | Multi-source pricing; cached fallback; user-visible warnings | Oracle paused >2 hours |
| Per-L2 decoders take longer than 3.5 weeks | Medium | Parallelize across developers if available; defer non-held L2s to v1.1 | Decoder work exceeds 25% over budget |

### 6.2 Product risks

| Risk | Impact | Mitigation |
|---|---|---|
| Community demand insufficient | High | Run validation cycle in parallel with Phase 1 to confirm willingness to pay |
| LemPay partnership doesn't materialize | None (Card Spend removed from v1) | Card Spend page deferred to v1.1 or later; revisit when LemPay publishes an API or coordinates a partnership |
| IRS guidance changes mid-build | Medium | Build tax form templates modularly; track Form 1099-DA rollout |
| Lemonchain ecosystem stagnates | High (existential) | Architecture supports adding other chains; not all eggs in one basket |

### 6.3 Operational risks

| Risk | Impact | Mitigation |
|---|---|---|
| Developer attrition mid-build | High | Document everything; test coverage >80%; favor experienced developers |
| Production data loss | Critical | Daily Postgres backups; point-in-time restore; tested DR runbook |
| Security incident | High | Read-only design eliminates custody risk; SOC2-ready architecture; Sentry alerts on auth anomalies |

---

## 7. Success criteria

### 7.1 Launch readiness (Phase 4 acceptance)

- All 4 phase gates passed
- Closed beta complete with ≥10 users, ≥80% net promoter score
- Generated tax forms match manual calculation within $5 for sponsor's books
- Production monitoring active with alerts on all critical failure modes
- Documentation complete for users and operators
- Legal/privacy/security review signed off

### 7.2 Post-launch (90 days)

- 50+ active users
- <5% churn after first 30 days
- ≤1 critical bug per week in production
- p95 page load time <2s
- Successful tax season for ≥10 users (forms used in real filings)
- ≥2 community testimonials usable for marketing

### 7.3 12-month north star

- 500+ active users
- ≥1 business entity customer (S-Corp/LLC paying for entity-grade features)
- Net Revenue Retention >100% (existing users expanding or paying more)
- Lemon Ledger ↔ Ledger integration shipped (v1.1)
- Considered the standard tax tool for Lemonchain users

---

## 8. Out-of-scope explicit disclaimers

- **Trading or transaction signing.** Lemon Ledger is read-only.
- **Custody.** Wallets remain with the user; we read public chain data only.
- **Investment advice.** Suggestions are tax-strategic, not investment-strategic.
- **Tax filing.** We generate forms; users file them.
- **Fiat bookkeeping.** Use the existing Ledger product for non-crypto books.
- **Other chains beyond Lemonchain + BSC** in v1.
- **DeFi position tracking on non-Lemonchain chains** in v1.
- **Mobile native apps** in v1.

---

## 9. Security and Quality Assurance Program

Lemon Ledger handles financial data, generates tax forms that users will rely on for IRS filings, and aggregates highly personal economic information. The product's value depends entirely on user trust that the data is correct and confidential. The Security and QA Program defined here is not optional — it must be implemented in full as the build proceeds.

### 9.1 Three-layer quality assurance model

The program operates on three complementary layers that catch different categories of issue:

**Layer 1: Automated static analysis (catches: known vulnerabilities, common bug patterns, style violations, type errors)**

Runs on every commit and pull request. CI fails if any check fails — no exceptions. Specific tools:

- **Semgrep** with security rulesets (OWASP Top 10, Python Crypto, JavaScript Web3 patterns). Configured to flag: SQL injection patterns, command injection, hardcoded secrets, weak crypto algorithms, dangerous use of `eval`/`Function()`, untrusted user input flowing to dangerous sinks
- **Bandit** (Python) for backend-specific security scanning
- **ESLint** + `eslint-plugin-security` for frontend security patterns
- **TypeScript strict mode** + **mypy strict mode** for type safety — no `any`, no untyped function parameters, no implicit conversions
- **Dependency scanning** via Dependabot (GitHub-native) or Snyk for known CVEs in npm/pip dependencies
- **Secret scanning** via TruffleHog and git-secrets pre-commit hooks; CI also scans for committed secrets
- **Test coverage gate**: minimum 80% line coverage on backend, 70% on frontend. CI fails if coverage drops below threshold
- **Custom AST-level lint rule**: no Web3 library call that sends transactions (`.send()`, `eth_sendTransaction`, `eth_signTransaction`). Only `.call()` and read-only methods are permitted. This is enforced at build time, not at code review.

**Layer 2: LLM-based adversarial code review (catches: domain-specific logic errors, security issues requiring intent understanding, missing tests, code quality issues)**

For every pull request that touches security-sensitive paths, a **separate LLM session** (Claude or equivalent) reviews the diff before human merge approval. "Security-sensitive paths" include:

- Authentication and authorization (Clerk integration, session handling, JWT validation)
- Wallet authorization (SIWE signature verification, challenge nonce handling, multi-sig logic)
- Cost basis math, FMV-at-receipt calculation, lot disposal ordering
- Tax form generation and PDF rendering
- Cross-entity transfer detection and classification
- Bridge correlation logic
- External API calls (oracle, CoinGecko, BscScan, etc.)
- Database query construction (especially anywhere user-supplied input enters a query)
- Logging and error handling in any of the above

The reviewer LLM operates with explicit instructions to:

1. **Read the PR description** and understand the intent
2. **Review the diff** against three lenses: security (could this leak data, bypass auth, or be exploited?); correctness (does the implementation match the intent? does the crypto-tax math align with IRS guidance?); quality (are tests adequate? are edge cases handled? are there obvious bugs?)
3. **Produce a structured review** with: blocking issues (must fix before merge), recommended issues (should fix), and observations (consider for future)
4. **Be adversarial** — actively look for problems rather than passively reading the code. Assume the implementer might have missed something obvious.

**Critical operational requirement:** the reviewer LLM session must be **completely separate** from the implementer LLM session — different conversation, different context window, no shared memory. If the same Claude instance writes the code and reviews it, the review is performative. The reviewer must come in cold.

Output: each material PR gets an LLM review document attached. Human reviewer (typically the project sponsor or lead developer) reads both the diff and the LLM review before approving merge.

**Implementation:** This is operationalized via a GitHub Action that, on every PR touching the security-sensitive paths defined above, opens a new Claude API session with the diff and review instructions, captures the output, and posts it as a PR comment. Cost: ~$200-500/month during active development based on typical PR volume.

**Layer 3: Weekly human security review (catches: program-level issues, drift over time, things automated checks miss)**

Every Friday during active development, a structured 60-minute security review covers:

- All new dependencies added in the past week: are they reputable? Maintained? Pin or accept latest?
- All TODOs or `// XXX:` comments in security-sensitive code paths: are any tracking actual security debt?
- All test exemptions or coverage drops: justified?
- Audit log volume and pattern: any signs of anomaly?
- Outstanding LLM review observations: any that should be promoted to issues?
- Recent CVEs in our dependency tree: any patches available?

Attendees: project sponsor + lead developer + (optional) external security consultant on retainer. Output: a brief weekly memo logged in the project's documentation.

### 9.2 Pre-launch security audit (external firm)

Before the v1 public launch (Week 18 in the timeline), engage an external security firm specializing in Web3/dApp security. Recommended firms with relevant expertise: Trail of Bits, OpenZeppelin, Spearbit, Cure53, HackerOne. Scope of engagement:

1. **Authentication and authorization review** — Clerk integration, session handling, wallet authorization flow, signature verification correctness
2. **Wallet authorization deep-dive** — SIWE implementation, nonce handling, replay attack resistance, multi-sig handling, manual signature flow
3. **Cross-chain logic review** — bridge correlation potential exploits, custody address inference safety
4. **Tax math correctness review** (with optional CPA consultation) — lot tracking, FMV-at-receipt timing, entity segregation, form generation
5. **API surface review** — every endpoint reviewed for IDOR (insecure direct object references), authorization bypass, rate limiting
6. **Infrastructure review** — secrets management, database access patterns, backup integrity, log retention policies
7. **Penetration testing** — black-box and gray-box pen testing of the deployed staging environment

Typical engagement length: 2 weeks. Output: a written security report with severity-classified findings. Critical and high findings must be remediated before launch. Medium findings are remediated by the v1.1 milestone. Low findings tracked in the security backlog.

**Cost:** $5,000-$10,000 for a focused engagement at a small but reputable firm. More if scope expands. Budget reserved in §5.3.

### 9.3 Post-launch bug bounty program

After v1 launch, a public bug bounty program incentivizes ethical disclosure. Platform: HackerOne or Bugcrowd (or a self-hosted program if budget is tight). Initial bounty schedule:

- Critical (full account takeover, complete data leak, arbitrary code execution): $2,500-$5,000
- High (significant authorization bypass, partial data leak, privilege escalation): $1,000-$2,500
- Medium (minor data leak, business logic flaw, classification bypass): $250-$1,000
- Low (security misconfigurations, minor info leaks): $50-$250
- Informational (best-practice violations, no immediate impact): swag/credits

Reserve budget: $10,000 for the first year. Adjustable based on activity.

### 9.4 Incident response plan

Before launch, document a written incident response plan covering:

1. **Detection channels** — how a potential incident is reported (user reports, monitoring alerts, security researchers via bounty program, internal team discovery)
2. **Severity classification** — criteria for Sev-0 (active exploit, data leak in progress), Sev-1 (confirmed vulnerability not yet exploited), Sev-2 (suspected vulnerability), Sev-3 (security improvement opportunity)
3. **Response roles** — incident commander, technical lead, communications lead, legal contact
4. **Communication templates** — for affected users, the broader user base, and (if required) regulatory bodies
5. **Post-mortem process** — within 14 days of resolution, a blameless post-mortem documenting root cause, contributing factors, fix, and prevention measures
6. **Runbook** — step-by-step playbook for the most likely scenarios (auth bypass discovered, oracle compromise, third-party API breach, database leak)

The IR plan is reviewed quarterly and updated as the product evolves.

### 9.5 Privacy and data handling commitments

These commitments are encoded in the Privacy Policy that ships with v1 launch:

- **No third-party tracking** beyond essential analytics (which itself uses a privacy-respecting tool like Plausible, not Google Analytics)
- **No data sales, ever** — user data is never sold to third parties under any circumstances
- **Data minimization** — we collect only what's necessary for the product to function. Wallet addresses, transaction history, user-provided metadata. We do not collect SSNs, dates of birth, government IDs, or other identifiers beyond what Clerk requires for authentication
- **User-initiated deletion** — users can delete their account and all associated data at any time. Confirmation flow ensures intent. Deletion is permanent and unrecoverable after a 30-day grace period
- **Encryption at rest and in transit** — Postgres encryption at rest, TLS for all in-transit traffic, encrypted backups
- **Access controls** — only developers explicitly granted access can read production data, and only for incident response purposes. All access logged
- **Data retention** — transaction data retained as long as the user maintains an account. Audit logs retained for 7 years (tax statute of limitations). Deleted accounts purged after 30 days

### 9.6 Compliance posture

Lemon Ledger is not a registered financial advisor, tax preparer, or money transmitter. Compliance positioning:

- **Read-only design** removes us from the money transmitter regulatory framework — we never hold or move user assets
- **Tax form generation, not filing** keeps us out of registered tax preparer regulation — users file their own forms, with Lemon Ledger providing reference documents
- **No advice, only information** — the product surfaces tax-relevant information but does not give personalized tax advice
- **Terms of Service** explicitly disclaim financial/tax advice and direct users to consult licensed professionals for their specific situations
- **GDPR/CCPA compliance** — user data rights respected (access, deletion, portability) regardless of where the user lives, because complying with the stricter standard is cleaner than maintaining region-specific behavior

A compliance lawyer should review the Terms of Service and Privacy Policy before launch. Budget: $2,000-$5,000 for the initial review.

### 9.7 Summary of Security/QA program effort

| Item | Effort/Cost |
|---|---|
| Automated CI scanning setup | 3 days (Phase 1) |
| LLM adversarial review GitHub Action | 2 days (Phase 1) |
| Wallet authorization implementation (§3.11) | 1 week (Phase 2) |
| Security review weekly cadence | 60 min/week, ongoing |
| Pre-launch external security audit | $5,000-$10,000 + 2 calendar weeks (Phase 4) |
| Compliance lawyer review | $2,000-$5,000 (Phase 4) |
| LLM review ongoing API costs | ~$200-500/month during active dev |
| Bug bounty reserve (year 1) | $10,000 |

Total incremental cost of the Security/QA program on top of base development: roughly $25,000-$35,000 across pre-launch + first year. **This is appropriate spending for a financial product.** Cutting any of these items would be ill-advised.

---

## Appendix A — Database schema (full)

[Migrations file would be generated from the schema definitions outlined in section 3.4. Available on request before build start.]

---

## Appendix B — Contract address reference

### Lemonchain mainnet (21 Tier-1 tokens + 2 NFT collections)

| Symbol | Name | Contract Address | Type | Max Supply |
|---|---|---|---|---|
| LEMX | Native Lemon (gas) | `0x000...000` | Native | 50M |
| WLEMX | Wrapped LEMX | `0x84862e65EBF37aF91a8b85283B58505dE3352588` | ERC-20 | ~14.1M current |
| LUSD | LemonUSD | `0x8DE60f88f19DAD42dde0D9ED2eebA68269722a99` | ERC-20 stable | 5.2B current |
| LFLX | LemFlix | `0x1BACc825fCD91971E8dACA3104370380b4a981Be` | ERC-20 (L2) | 833.5M |
| LBNK | LemonBank | `0xc17eF640D7c34A8c684073d85d815539F66da3C7` | ERC-20 (L2) | 744M |
| LPAY | LemPay | `0x708Cf95b67f3DFfF16E1F48313425d0CFb629Ee7` | ERC-20 (L2) | 735M |
| LMED | LemCare | `0xF489e786cF6242B3c32cfE5372453b37b8f0Cc13` | ERC-20 (L2) | 725M |
| CTFZ | Catfiz | `0x83D4B4DB63C40846735860ce3B2aDF83Aa9EdC8E` | ERC-20 (L2) | 1.099B |
| LTVL | LemTravel | `0x02535cBC23c045134A481CF8b6a6645E7655EfB8` | ERC-20 (L2) | 1.097B |
| LLOT | LemLotto | `0xc8fa8354D6C6856dE3E3F7dA89f0ce4636E51710` | ERC-20 (L2) | 709M |
| LSQZ | LemSqueeze | `0xCE37EDD204DEdBC256A7F5d3622e82F5Fc031CD8` | ERC-20 (L2) | 507M |
| HXDX | HexDEX | `0x59100856DFbBb5A10bdAFC894B8f82c89a0aDC34` | ERC-20 (L2) | 329M |
| HXBT | Hexbit | `0xc9fD20a101f01EaC20e859645e91C9998aaa509B` | ERC-20 (L2) | 332M |
| SMART | DNSmart | `0x38374F0527e3320058c96AdCB57C6e78AfE9447E` | ERC-20 (L2) | 333M |
| RMC | RubicManagement | `0x5d59Ca7460b5e0C553e62B4B7B0197bF12aC1FB5` | ERC-20 (L2) | 335M |
| MHSA | MotivHSA | `0x8F9457a8dE85876951b3ac2843c09997B951C267` | ERC-20 (L2) | 334M |
| STH | StartHealth | `0x3ed3BFBAc6ECe65468b37Abb15091F346f1b8905` | ERC-20 (L2) | 335M |
| NXYS | NXCPodcast | `0x0f4Bb028EAa7f0d0545ddD24600C524c3E044962` | ERC-20 (L2) | 329M |
| TIXA | TixAccess | `0xe2677DA211265C092F1Bc4f018798AfBC20971DC` | ERC-20 (L2) | 328M |
| PUP | WasatchPup | `0xDD84A98F9f9e0Be193bfD91c123254d835cB3b32` | ERC-20 (L2) | 320M |
| LLUX | LemLux | `0x71E3A635763910bCcF5f979eBBf8c69Cb9704DB0` | ERC-20 (L2) | 172M |
| LMLN | LemLoans (deflationary) | `0x6cC7ee8f2F45782CBF376B4021D41960b814f321` | ERC-20 (L2) | 182.7B current * |
| LQST | LemQuest | `0x3e2f7F34ec743a78d4682DE27890E4dc2BA543A4` | ERC-721 (NFT) | n/a |
| SCDT | Swap Credit (LC) | `0xE20E3C6447B024A3FBF22D4803de1D910ADd7776` | ERC-721 (NFT) | n/a |

\* LMLN is deflationary: 10% of LemLoans origination fees are burned, reducing supply over time.

### BSC chain (12 Tier-1 BEP-20 tokens + 1 NFT collection)

| Symbol | Name | Contract Address |
|---|---|---|
| LEMX | BEP-20 Lemon | `0x2Da91257961b87e69Fa13b2e20931D517dc97597` |
| LFLX | BEP-20 LemonFlix | `0x545C1aFBdF28b67F06c47Af6803ea4E87f507155` |
| LBNK | BEP-20 LemonBank | `0x848C93ADA05241e138E76768031FB1C0070dd69b` |
| LLOT | BEP-20 LemLotto | `0x101F0b4D86b7428D62588Bf8ebbaA98a328c90eB` |
| LPAY | BEP-20 LEMPay | `0xf56418572e5ceAc8f86ebA2518934CC5b27A1589` |
| LMED | BEP-20 LemCare | `0x2Ecc3d65472Ab0eED612944799725711df0355d0` |
| CTFZ | BEP-20 Catfiz | `0xd41e369EE546bbb14049fE3Ad08fa0C4C0780769` |
| LTVL | BEP-20 LemTravel | `0x31F8dD46AC2920c9C1E72474F4E3BA3c2BF52721` |
| LBST | BEP-20 LemBoost (BSC-only) | `0x1682c43e6ca82B6F2C3269670E9a9D3C182E4Add` |
| LMLN | BEP-20 LemLoans | `0xd968f212e16705c9Be569391c4b9bD98CD81D9B6` |
| LLUX | BEP-20 LemLux | `0x08936586605c9B5870e15800EF19aA3d27e0c631` |
| LSQZ | BEP-20 LemSqueeze | `0xC99d1FAf3c7c2a0627dD76957066A7748BD6F783` |
| SCDT | BEP-20 SwapCredit | `0xeF50C3b7A6de0d006779400e958ACa4360F14e5a` |

### Infrastructure contracts

| Component | Contract Address | Chain |
|---|---|---|
| PriceDataFeed Oracle | `0xf290928bA4457240C150c8AAa6B0A697819Cfe9D` | Lemonchain |
| Lemonchain Blockscout Explorer | `https://explorer.lemonchain.io` | n/a |
| BSC Block Explorer (BscScan) | `https://api.bscscan.com/api` | n/a |
| PancakeSwap V2 Factory (BSC) | `0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73` | BSC |
| HEXDEX Factory (Lemonchain) | TBD week 1 of build | Lemonchain |

### To be confirmed week 1 of build

- HEXDEX factory contract address (from a HEXDEX swap transaction)
- Per-L2 staking contract addresses (from real reward emission transactions)
- Custody/bridge addresses (empirical from first observed bridge events)
- Per-token burn contract addresses (for deflationary tokens like LMLN)

---

## Appendix C — Pricing decimal conventions

Different sources use different decimal conventions. The build must normalize all to USD with arbitrary precision (`Decimal` in Python, `BigNumber` in JS).

| Source | Convention | Example for $13.07 |
|---|---|---|
| PriceDataFeed Oracle | 8 decimals | `1307000000` |
| Native LEMX (wei) | 18 decimals | `13070000000000000000` for 1 LEMX at $13.07 |
| ERC-20 token amounts | varies by token (mostly 18) | Stored raw, must divide by `decimals()` |
| CoinGecko response | Floating decimal as string | `"13.07"` |
| CoinMarketCap response | Floating decimal | `13.07` |
| PancakeSwap reserves | Token-specific decimals | Must divide by `decimals()` per token |

**Build standard:** All internal pricing uses Python `Decimal` (or JS `BigNumber`). Conversion happens at ingestion boundaries. UI formatting uses standard locale-aware decimal display.

---

## Appendix D — Glossary

- **L2:** Layer-2 project on Lemonchain. Each L2 has its own ERC-20 token, NFT collection, and staking contract. NFTs are minted by users for a fee and stake to earn tokens.
- **FMV:** Fair Market Value. The USD price of a token at a specific moment in time.
- **FIFO / HIFO / Specific ID:** Cost basis methods. FIFO = "first in, first out." HIFO = "highest in, first out." Specific ID = user picks the lot to dispose.
- **Schedule 1 Line 8z:** IRS tax form line for "Other income" — where crypto reward income gets reported.
- **Form 8949:** IRS tax form for capital asset disposals. One line per disposal event.
- **SCD Type 2:** Slowly Changing Dimension Type 2. Data modeling pattern that preserves historical state by versioning rows rather than overwriting.
- **TWAP:** Time-Weighted Average Price. A price averaging method that weighs each price submission by its duration in the period.
- **UUPS:** Universal Upgradeable Proxy Standard. An OpenZeppelin pattern for upgradeable smart contracts.
- **Buy/burn:** A deflationary tokenomic pattern where a portion of fees is used to buy back and burn the token, reducing supply over time.

---

**End of Statement of Work — Lemon Ledger v1.0**

*Prepared in response to R&D Report v2. Ready for developer review and engagement.*
