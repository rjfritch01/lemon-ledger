# Lemon Ledger · Data Layer R&D Report v2

**Date:** May 30, 2026
**Status:** R&D complete. Data layer fully specified. Zero remaining blockers for SOW.
**Replaces:** R&D Report v1 (May 12, 2026)

---

## What this document is

This is the technical justification document for the Lemon Ledger build. It captures every architectural decision made during the R&D phase, with the evidence behind each one. A developer reading this should be able to start the build with no remaining unknowns about how the data layer works.

The R&D phase consumed roughly three weeks of analysis across:
- Live API verification against Lemonchain mainnet
- Reading the source code of the on-chain price oracle
- Mapping the full token registry across two chains
- Identifying every contract address Lemon Ledger will need to read from
- Documenting bridge mechanics, tokenomics, and edge cases

The v2 of this report supersedes the v1 (which was a preliminary feasibility assessment). Major changes from v1:
- The ecosystem is 19 L2 projects, not 10
- The data layer spans two chains (Lemonchain + BSC), not one
- Pricing comes from a sophisticated on-chain oracle, not just DEX reserves
- The bridge is centralized custody, not a smart contract
- LUSD is the ecosystem native stablecoin, not USDT
- LMLN has deflationary buy/burn tokenomics, not standard emission
- NFT-only collections (LemQuest, Swap Credit) are first-class assets

---

## Executive summary

**The data layer is buildable, end-to-end, with the existing on-chain infrastructure.** Every category of data the product needs has been verified accessible:

1. **Transaction history** — Blockscout API on Lemonchain + BscScan API on BSC. Both expose the same Etherscan-compatible interface.
2. **Token balances and metadata** — Standard ERC-20/BEP-20 calls work natively on both chains.
3. **Pricing** — On-chain `PriceDataFeed` oracle at `0xf290928bA4457240C150c8AAa6B0A697819Cfe9D` covers all 21 Lemonchain tokens with confidence-weighted daily TWAP. PancakeSwap on-chain reserves provide BSC-side pricing and Lemonchain edge-case fallback.
4. **NFT detection** — Standard ERC-721 transfer events captured through the same APIs as fungible tokens.
5. **Reward emissions** — Each L2 token's transfer events from the staking contract to user wallet are detectable and classifiable.
6. **Historical pricing** — Combination of on-chain oracle reads (current + 30-day) and `DailyAverageFinalized` event log archaeology (pre-30-day backfill).

**Three known constraints, all manageable:**

1. **The price oracle keeps only 30 days of daily averages on-chain.** Older history must be reconstructed from chain event logs and persisted to our own database. Adds 1-2 weeks of dedicated archival/backfill work.
2. **There is no smart-contract bridge.** Cross-chain transfers use centralized custody (LEMX Bank Group operated). Bridge events require heuristic correlation between chain-pair transactions. Adds 2-3 weeks of classifier engineering.
3. **Card swipe data (LemPay/TMX card) is off-chain.** Either requires a partnership integration with LemPay, CSV import as fallback, or the Card Spend feature deferred to v1.1.

None of these are blockers — they're tractable engineering problems with known patterns and budgeted into the SOW timeline.

---

## Chain infrastructure

### Lemonchain mainnet via Blockscout

`https://explorer.lemonchain.io` runs Blockscout v7.0.2 with frontend v1.38.2. This is the most widely-deployed open-source EVM block explorer, used by 100+ chains. We inherit a mature API and ecosystem rather than building bespoke infrastructure.

Four independent API surfaces are exposed:

| Surface | Use case |
|---|---|
| **REST v1 (Etherscan-compatible)** at `/api?module=...&action=...` | Bulk wallet queries, transaction history, token transfers |
| **REST v2 (Blockscout-native)** at `/api/v2/...` | Modern endpoints, better pagination, structured responses |
| **GraphQL** at `/graphiql` | Flexible queries combining multiple data types |
| **Eth JSON-RPC** | Direct contract calls (`eth_call`, `eth_getLogs`, `eth_blockNumber`) |

We use all four. REST v1 for bulk wallet sync (every endpoint is paginated and supports 10k results per call). REST v2 for richer responses on individual transactions. GraphQL for combined queries during analytical work. Eth JSON-RPC for direct contract reads (specifically the PriceDataFeed oracle).

**Confirmed endpoints powering the data layer:**

| Need | Endpoint | Notes |
|---|---|---|
| Native LEMX balance | `?module=account&action=balance&address=...` | Returns wei. Divide by 10^18. |
| All transactions for wallet | `?module=account&action=txlist&address=...` | Up to 10k records, paginated. |
| Token transfers (ERC-20/721) | `?module=account&action=tokentx&address=...` | The firehose for L2 reward detection. |
| Tokens held by wallet | `?module=account&action=tokenlist&address=...` | Returns all token contracts + balances. |
| Internal transactions | `?module=account&action=txlistinternal&address=...` | Catches stake/unstake/claim contract calls. |
| Event logs | `?module=logs&action=getLogs&fromBlock=...&toBlock=...&address=...&topic0=...` | For `DailyAverageFinalized` backfill. |
| Token metadata | `?module=token&action=getToken&contractaddress=...` | Type, decimals, supply. |
| Contract ABI (if verified) | `?module=contract&action=getabi&address=...` | Decode method calls. |
| Block by timestamp | `?module=block&action=getblocknobytime&timestamp=...&closest=before` | Block-pinned historical queries. |
| Native coin price | `?module=stats&action=coinprice` | Returns LEMX/USD. |

**Rate limits:** Blockscout default is ~5 req/sec on shared instances. For production with hundreds of users, we'd request a higher rate limit from the Lemonchain team or eventually mirror the chain data into our own Blockscout instance. For v1, the public API is sufficient with our aggressive caching strategy (90% cache hit ratio expected).

### Lemonchain testnet (Citron) via Blockscout

`https://explorer-testnet.lemonchain.io` runs the same Blockscout v7.0.2. Identical API surface. **Development happens against testnet first** — same code, different base URL. This significantly reduces the cost of build mistakes and lets us test reward classification, lot tracking, and bridge correlation without real money at stake.

### BSC via BscScan

The BSC chain uses BscScan (`https://api.bscscan.com/api`) which is also Etherscan-compatible. The API patterns mirror Blockscout almost exactly — same query parameters, same response shapes — so our Lemonchain client adapts to BSC with minimal code changes. The only meaningful difference is API key handling: BscScan requires a free API key (5 req/sec free tier, 30 req/sec at $50/mo paid tier).

The BSC chain hosts BEP-20 versions of 10 L2 tokens plus BEP-20 LEMX (the canonical reference for BSC-side trading), so it's not an optional integration — users with cross-chain holdings need BSC coverage from v1.

### Direct node access (JSON-RPC)

Beyond explorer APIs, both chains expose standard JSON-RPC endpoints. We use this for:

- **Real-time freshness:** Explorer APIs lag the chain head by 1-3 seconds. For features needing immediate state (live price reads, latest balance for a tax filing), we hit the node directly.
- **Bulk historical queries:** Calling `eth_getLogs` directly against the node is faster than the explorer API for large historical ranges (e.g., the one-time backfill of `DailyAverageFinalized` events from oracle deployment).
- **Direct contract calls:** Reading the PriceDataFeed oracle's methods (`getPrice`, `getDailyAverage`, etc.) goes through `eth_call`. Standard pattern for any EVM contract interaction.

The `web3.py` (Python) and `viem` (TypeScript) libraries handle this natively — no custom JSON-RPC code needed.

---

## Pricing layer — the architectural centerpiece

The pricing layer is the most sophisticated part of the data architecture and the one with the most material findings. This section covers it in depth.

### The PriceDataFeed oracle

**Contract:** `0xf290928bA4457240C150c8AAa6B0A697819Cfe9D` on Lemonchain mainnet
**Source code:** Verified, available on `explorer.lemonchain.io`
**Pattern:** UUPS upgradeable proxy (OpenZeppelin)
**Oracle type:** Multi-tier TWAP (Time-Weighted Average Price) with confidence-weighted submission

This is genuinely sophisticated infrastructure for an emerging ecosystem. The team built a proper price oracle rather than relying on external feeds, which gives us authoritative pricing for every ecosystem token without depending on third-party data providers.

**How the oracle works internally** (extracted from the source):

1. **Authorized parties submit spot prices** via `setSpotPrice(token, price, confidence)`. The `confidence` is 0-100, set by the submitter, reflecting how confident they are in the price (e.g., from a DEX with deep liquidity = high confidence, from a thin pool = lower).

2. **Submissions are weighted by confidence** in the daily accumulator. A high-confidence submission counts more than a low-confidence one when computing the day's average.

3. **Each authorized submitter is rate-limited** to one update per 5 minutes per token. This caps the maximum daily data points at 288 per token (24h × 12 per hour) — which is exactly what the on-chain recent history buffer holds.

4. **Price deviation guards block anomalies.** A new price more than 50% different from the previous (5000 BPS, defined as `MAX_PRICE_DEVIATION_BPS`) is rejected. This protects against flash-loan-style oracle manipulation. During the initial seeding window (now expired), the limit was 3x normal (150%) to allow for legitimate price discovery.

5. **At day boundaries, the day's weighted average is finalized** and stored in a rolling 30-day buffer. The current-day accumulator resets and starts building the next day's average.

6. **Weekly and monthly averages are computed on-demand** from the stored daily averages, weighted by each day's confidence × data points.

7. **A 24-hour staleness guard** means `getPrice()` reverts if the most recent submission is older than 24 hours. Forces us to handle the staleness case gracefully.

8. **Emergency controls** (`paused`, `emergencyMode`) let the team take the oracle offline if needed. Our client handles this by falling back to cached values and surfacing a warning.

**Critical decimal convention:** All prices are returned in **8 decimals (Chainlink convention)**. A price of $13.07 is represented as `1307000000`. Every price read divides by 10^8 to get USD.

### What the oracle covers — verified May 30, 2026

`getSupportedTokens()` returned 22 addresses. Cross-referenced against the project token list, this is:

| Category | Count | Tokens |
|---|---|---|
| Native gas | 1 | Native LEMX (represented as `0x000...000`) |
| Wrapped native | 1 | WLEMX |
| L2 project tokens | 19 | All 19 L2s on Lemonchain |
| Stablecoin | 1 | LUSD (LemonUSD) |

**Every Lemonchain token a user might transact with is priced by the oracle.** Zero gap on the pricing side for Lemonchain assets. This is the strongest finding from the R&D phase.

### Methods we use, with full signatures

Extracted from the verified source code:

**For current prices (called constantly during normal operation):**

```solidity
function getPrice(address token) returns (uint256)
// Wrapper around getSpotPrice. Returns price in 8 decimals.
// Reverts: TokenNotSupported, PriceStale

function getSpotPrice(address token) returns (uint128)
// Same data as getPrice, typed as uint128 (the actual storage type).
// Reverts: TokenNotSupported, PriceStale
```

**For tax FMV (called during transaction classification):**

```solidity
function getDailyAverage(address token) returns (uint128)
// Current day's confidence-weighted average, including partial accumulator.
// Used for FMV-at-receipt when the receiving block falls within today.

function getDailyAveragesHistory(address token, uint8 maxEntries)
  returns (DailyAverageEntry[])
// Returns up to maxEntries (capped at 30) most recent daily averages.
// Each entry: { averagePrice, timestamp, dataPoints, confidence }
// Used for nightly archival sync.

function getAverageWithMetadata(address token, uint8 period)
  returns (uint128 value, uint64 oldestDataTimestamp, uint8 validDays, bool hasSufficientData)
// Returns average + data quality flag. period must be 1, 7, or 30.
// hasSufficientData is the safety signal — if false, the UI surfaces a warning.
```

**For oracle discovery and health:**

```solidity
function getSupportedTokens() returns (address[])
function isSupportedToken(address token) returns (bool)
function getTokenInfo(address token) returns (
  uint64 lastUpdateTime,
  uint256 updateCount,
  bool isActive,
  uint16 historyCount,
  uint8 dailyAverageCount,
  uint64 currentDayStart
)
function paused() returns (bool)
function emergencyMode() returns (bool)
function getSeedingStatus() returns (uint256 deadline, bool isComplete, bool isActive, uint256 timeRemaining)
```

**Critical events for our indexer:**

```solidity
event SpotPriceUpdated(
  address indexed token,
  uint128 oldPrice,
  uint128 newPrice,
  uint64 timestamp,
  uint32 confidence,
  address indexed updater
);
// Real-time price stream. We subscribe for any token of interest.

event DailyAverageFinalized(
  address indexed token,
  uint64 dayTimestamp,
  uint128 dailyAverage,
  uint32 dataPoints,
  uint32 confidence
);
// Emitted every time a day finalizes. This is how we backfill historical
// prices beyond the on-chain 30-day buffer — events live in chain logs forever.

event TokenAdded(address indexed token, uint256 timestamp);
event TokenRemoved(address indexed token, uint256 timestamp);
// Tells us when the supported token set changes.

event Upgraded(address indexed implementation);
// Proxy upgrade — alert and verify interface compatibility.
```

### The 30-day storage constraint and how we work around it

The oracle keeps **only the last 30 daily averages in on-chain storage**. Older entries are overwritten in the circular buffer. This is a deliberate gas-efficiency choice — fine for the oracle's primary use cases (swap pricing, lending oracles, current FMV) but insufficient for a tax product that needs historical FMV for transactions throughout the year.

**Our solution:** maintain our own historical price database, populated from chain events.

The `DailyAverageFinalized` event is emitted every time a day's average is finalized in storage. These events live forever in the chain's event log — they're not subject to the same overwriting as the storage buffer. By querying historical logs of this event from the oracle deployment block (~September 2025) forward, we reconstruct the entire price history for every supported token.

**Implementation pattern:**

1. **One-time backfill at deployment.** A migration job pages through `DailyAverageFinalized` events from `block_start = oracle_deployment_block` to `block_end = latest_block`, filtered to the oracle contract address. Each event becomes a row in our `historical_prices` table indexed by `(token_address, day_timestamp)`. For a year of history × 22 tokens × ~1 event per day per token = ~8000 events to backfill. Takes hours, not days, with proper pagination.

2. **Nightly incremental sync.** A cron job queries new `DailyAverageFinalized` events emitted in the last 24 hours and appends to our table.

3. **Real-time read path.** UI queries our `historical_prices` table directly — fast indexed lookup, no oracle round-trip needed for any historical date.

4. **Live price path.** Current prices read directly from the oracle's `getPrice()` method with a 60-second cache TTL.

This is the standard pattern used by professional crypto tax tools — treat the oracle as a current-data firehose, maintain our own archival store. Total additional engineering: 1-2 weeks during build.

### Seeding status — confirmed May 30, 2026

`getSeedingStatus()` returned:
- `deadline = 1757454892` (Sep 9, 2025 UTC)
- `isComplete = false`
- `isActive = false`
- `timeRemaining = 0`

Interpretation: the contract was deployed approximately **September 2, 2025**, with a 7-day seeding window. The team did not explicitly call `completeSeedingPhase()`, but the deadline expired (263 days ago as of this report), so batch seeding is now locked out by the `onlyDuringSeeding` modifier.

**Implication:** The oracle has been collecting daily averages organically for ~8 months. Our event log backfill recovers all of that data. We don't have pre-September 2025 prices, but the product's first active tax year is 2026, so this is sufficient.

### Pricing for BSC tokens

BSC-side tokens (11 BEP-20 contracts) are not covered by the Lemonchain oracle. For BSC pricing, we use PancakeSwap v2 on-chain reserves directly.

The pattern is well-established and identical across every Uniswap-style AMM:

1. **Find the trading pair pool** for each BSC token. Most pair against `BSC-USD` (BUSD) or BNB. Pool addresses are derivable from the PancakeSwap factory contract via `getPair(tokenA, tokenB)`.

2. **Read the pool's reserves** via `getReserves()`. Returns `(reserve0, reserve1, blockTimestampLast)`.

3. **Compute spot price** as the ratio of reserves, normalized for token decimals. If `reserve0` is the L2 token (24,000 LFLX scaled) and `reserve1` is BUSD (10,000 BUSD scaled), then 1 LFLX = 10000/24000 = 0.4167 BUSD ≈ 0.42 USD.

4. **For historical prices**, call `getReserves()` via `eth_call` with a `blockNumber` parameter — gives us the reserve state at any past block. Combined with the explorer's block-by-timestamp lookup, we can get FMV at any historical moment.

5. **Cross-validate** against CoinGecko and CoinMarketCap when the BSC token is listed there (most BEP-20 L2 tokens won't be initially, but LEMX BEP-20 already is via `lemon-2` listing).

**Why this is reliable:** Even when an L2 token has thin liquidity, the AMM math is deterministic. The price might be volatile or wide-spread, but it's not invented. Our pricing service surfaces the liquidity depth alongside the price so users can see when a price is "thin."

### Pricing strategy — full architecture

Pulling it all together:

**For LEMX (the gas token):**
1. Primary: `getPrice(0x000...000)` on the Lemonchain oracle
2. Cross-validate: BEP-20 LEMX on PancakeSwap pool reserves
3. Tertiary check: CoinGecko `lemon-2` and CoinMarketCap LEMON listings
4. Disagreement handling: if sources diverge >5%, flag and use the more recent value

**For Lemonchain L2 tokens (19 tokens):**
1. Primary: `getPrice(token_address)` on the Lemonchain oracle
2. Fallback: HEXDEX pool reserves (Lemonchain DEX) if oracle returns `PriceStale`
3. Edge case (no liquidity): record cost basis as $0, defer tax to first disposal

**For BSC-side BEP-20 L2 tokens (11 tokens):**
1. Primary: PancakeSwap v2 pool reserves
2. Cross-validate: CoinGecko/CoinMarketCap when listed
3. Edge case (no liquidity): same as above

**For LUSD (native stablecoin):**
1. Pinned at $1.00 (oracle confirms, but functionally treated as stable)
2. Depeg monitoring: alert if oracle reports >2% deviation from $1.00

**For historical FMV at receipt (tax-critical):**
1. Look up our `historical_prices` table for the day of receipt
2. If not present (rare edge case), block-pinned query against the oracle or DEX
3. Cache forever — historical prices don't change once recorded

**Caching strategy:**
- Block numbers, timestamps: cache forever (immutable)
- Token metadata (name/symbol/decimals): 24 hours
- Supported token list: 24 hours
- Current prices: 60 seconds
- Historical prices: cache forever (not changeable)
- Wallet balances: 60 seconds
- Transaction lists: 60 seconds with cursor-based invalidation

Estimated cache hit ratio: 90%+ for active users, reducing API calls accordingly.

---

## Token registry — the canonical list

The full set of tokens Lemon Ledger needs to track, as of May 30, 2026.

### Lemonchain native (21 tokens — all priced by the oracle)

| Symbol | Name | Contract Address | Type | Max Supply |
|---|---|---|---|---|
| LEMX | Native Lemon (gas) | `0x000...000` | Native | 50M |
| WLEMX | Wrapped LEMX | `0x84862e65EBF37aF91a8b85283B58505dE3352588` | ERC-20 | 14.1M current |
| LUSD | LemonUSD (stablecoin) | `0x8DE60f88f19DAD42dde0D9ED2eebA68269722a99` | ERC-20 | 5.2B |
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

*LMLN is deflationary: 10% of LemLoans origination fees are burned, reducing supply over time.

### BSC-side BEP-20 (12 tokens — priced via PancakeSwap)

| Symbol | Name | Contract Address |
|---|---|---|
| LEMX | BEP 20 Lemon | `0x2Da91257961b87e69Fa13b2e20931D517dc97597` |
| LFLX | BEP 20 LemonFlix | `0x545C1aFBdF28b67F06c47Af6803ea4E87f507155` |
| LBNK | BEP 20 LemonBank | `0x848C93ADA05241e138E76768031FB1C0070dd69b` |
| LLOT | BEP 20 LemLotto | `0x101F0b4D86b7428D62588Bf8ebbaA98a328c90eB` |
| LPAY | BEP 20 LEMPay | `0xf56418572e5ceAc8f86ebA2518934CC5b27A1589` |
| LMED | BEP 20 LemCare | `0x2Ecc3d65472Ab0eED612944799725711df0355d0` |
| CTFZ | BEP 20 Catfiz | `0xd41e369EE546bbb14049fE3Ad08fa0C4C0780769` |
| LTVL | BEP 20 LemTravel | `0x31F8dD46AC2920c9C1E72474F4E3BA3c2BF52721` |
| LBST | BEP 20 LemBoost (BSC-only) | `0x1682c43e6ca82B6F2C3269670E9a9D3C182E4Add` |
| LMLN | BEP 20 LemLoans | `0xd968f212e16705c9Be569391c4b9bD98CD81D9B6` |
| LLUX | BEP 20 LemLux | `0x08936586605c9B5870e15800EF19aA3d27e0c631` |
| LSQZ | BEP 20 LemSqueeze | `0xC99d1FAf3c7c2a0627dD76957066A7748BD6F783` |

### NFT-only collections (3)

| Symbol | Name | Contract Address | Chain | Notes |
|---|---|---|---|---|
| LQST | LemQuest | `0x3e2f7F34ec743a78d4682DE27890E4dc2BA543A4` | Lemonchain | User-mintable collectible; future marketplace planned |
| SCDT | Swap Credit | `0xE20E3C6447B024A3FBF22D4803de1D910ADd7776` | Lemonchain | Earned via games; redeemed for L2 NFTs |
| SCDT | BEP 20 SwapCredit | `0xeF50C3b7A6de0d006779400e958ACa4360F14e5a` | BSC | BSC-side version of Swap Credit |

### Cross-chain coverage matrix

10 L2s exist on both Lemonchain and BSC: CTFZ, LBNK, LFLX, LLOT, LLUX, LMED, LMLN, LPAY, LSQZ, LTVL.
9 L2s are Lemonchain-only: HXBT, HXDX, MHSA, NXYS, PUP, RMC, SMART, STH, TIXA.
1 L2 is BSC-only: LBST.
LEMX itself: native gas on Lemonchain, BEP-20 token on BSC.
WLEMX and LUSD: Lemonchain-only.

---

## The bridge mechanic

This is one of the most architecturally consequential findings. **There is no smart-contract bridge between Lemonchain and BSC.** The mechanic is:

> "Token → custody → new issue → burn original"

This is a **centralized custody bridge** (sometimes called "trusted bridge" or "off-chain coordinated lock-and-mint"). Mechanically:

1. User sends tokens on source chain to a custody address operated by Lemon Bank Group
2. Off-chain operator confirms the deposit
3. Operator triggers a mint on the destination chain to the user's address
4. Original tokens are burned from custody to maintain 1:1 peg

### Implications for the data layer

**There is no fixed bridge contract address we can hardcode.** Custody may use multiple addresses, rotate them, or use operator-controlled EOAs (externally-owned accounts) rather than contracts.

**Detection must be heuristic, not deterministic.** We can't say "any transfer to address X is a bridge event." Instead, we look for the pattern:
- Large token outflow on chain A
- Corresponding inflow on chain B within a time window
- Same token symbol on both sides
- Same wallet on both sides (user address consistency)

**Classification requires cross-chain correlation.** The backend must read both chains simultaneously and look for paired events. This is more complex than a simple bridge-contract allowlist but is exactly what professional crypto tax tools do for centralized bridges.

**Ambiguous cases surface to user.** For pairs the classifier isn't fully confident in, the UI asks: "It looks like this 1,000 LFLX outflow on Lemonchain and this 1,000 LFLX inflow on BSC are the same bridge event. Confirm?" This gives users control over an inherently ambiguous classification.

**Custody address inference.** As we observe transactions, we build an empirical list of "addresses that move large amounts in both directions and never net-position" — these are almost certainly custody addresses, and we promote them to a higher-confidence allowlist over time. After ~30 days of operation, the system should auto-classify the vast majority of bridge events correctly.

### Why bridge correlation matters for tax

Without proper bridge correlation, every cross-chain transfer looks like two unrelated transactions: an outbound (taxable disposal at FMV) and an inbound (cost basis reset). This would create **phantom taxable events** — the user would owe tax on hundreds of bridge moves that aren't actually sales.

Correctly classified, a bridge event is a non-taxable transfer of the same asset between two ledger entries. No gain/loss recognized, original cost basis preserved. This is the tax-correct treatment under IRS guidance for wrapped/bridged assets.

**Building this correctly is a meaningful differentiator.** Generic crypto tax tools (Koinly, CoinTracker) handle smart-contract bridges well but struggle with centralized custody bridges precisely because there's no contract to recognize. Lemon Ledger's heuristic correlation approach handles this case natively.

### Engineering estimate

Building the bridge correlation module from scratch: **2-3 weeks of focused work.** Breakdown:
- Cross-chain transaction reader (parallel BSC + Lemonchain ingestion): 4 days
- Pattern matcher (time window, amount, token match): 3 days
- User confirmation UI for ambiguous cases: 3 days
- Empirical custody address learning: 3 days
- Testing against historical bridge events: 2 days

This goes in Phase 2 of the build (Data Layer), after the basic ingestion is working but before the UI layer needs it.

---

## NFT and reward classification

### Project NFTs (the L2 mining pattern)

Each L2 project has an NFT contract. Users mint NFTs for $25 each, stake them in the project's staking contract, and earn that L2's tokens 24/7 until the max supply cap is reached. The tax classification:

1. **Mint event:** Acquisition of NFT. Cost basis = mint fee + gas. Asset class: collectible NFT (28% capital gains on disposal under current IRS guidance).

2. **Stake event:** Transfer of NFT to staking contract. **Not a taxable event** — the user retains beneficial ownership. The classifier tags this as "stake" with the original NFT's cost basis preserved.

3. **Reward emissions:** Each token transfer from the staking contract to the user's wallet is an **ordinary income event** at FMV on receipt. The FMV becomes the cost basis of those tokens going forward. This is what flows to Schedule 1 (or Form 1120-S for business entities).

4. **Unstake event:** Transfer of NFT back to user wallet. Not a taxable event.

5. **Distribution completion:** When the L2 hits its supply cap and emissions stop, no further income events occur. The user still holds the NFT (collectible value) and the accumulated tokens (with the cost basis established at receipt).

### LemQuest (collectible NFTs)

LemQuest NFTs are user-mintable collectibles. Future marketplace planned. Tax classification:

1. **Mint event:** Acquisition. Cost basis = mint fee + gas. Asset class: collectible NFT.
2. **Disposal event:** Sale on the marketplace (when launched) or redemption for an item. FIFO/HIFO basis matching against the held NFT. Gain or loss recognized.
3. **No emission events** — LemQuest NFTs don't earn tokens.

### Swap Credit (consumable reward NFTs)

Swap Credits are earned through gameplay and point redemption, then consumed in exchange for L2 NFTs. The multi-step event chain:

1. **Earn event:** Receive a Swap Credit NFT. Cost basis = $0 (or game fee attributable). Ordinary income event at FMV at receipt. Goes to Schedule 1.

2. **Redeem event:** Two-part transaction in a single block:
   - **Swap Credit burn:** Disposal event. Gain/loss = (FMV at redemption) - (basis at receipt). Capital gain/loss.
   - **L2 NFT mint:** Acquisition event. Cost basis = FMV of the Swap Credit at moment of redemption (since that's what was given up to acquire it).

The classifier needs a specific rule: **"if Swap Credit NFT outflow + L2 NFT inflow in same transaction, classify as paired redemption."** Engineering time: 2 days.

### Buy/burn tokenomics (LMLN and others)

Several tokens have buy/burn contracts that progressively reduce supply. The most documented case is LMLN:

- 10% of LemLoans origination fees go to a buy/burn contract
- The contract uses the fee to buy LMLN from the DEX (price-supportive activity)
- The bought LMLN is burned (sent to a permanent burn address)
- Remaining 90% of fees go to a liquidity wallet that expands LemLoan capacity

**Classification implications:**

- **User transfers TO known burn addresses** are not "sales" in the conventional sense — they're protocol-mandated burns. Tax treatment: same as a destruction of property (potentially deductible as a capital loss equal to basis, but consult tax advisor).
- **Buy activity FROM the burn contract** appears on-chain as buys originating from a known protocol address. The classifier tags these as "protocol-driven, not user-driven" and excludes them from user activity feeds.
- **Total supply tracking matters.** Unlike fixed-supply tokens, LMLN's `totalSupply()` decreases over time. The UI should reflect this (e.g., "your share of 182.7B current supply" rather than "your share of original supply").

**Engineering effort:** "Burn-aware accounting" as a distinct module of the classification engine. ~1 week of work. Requires maintaining a config table of known burn addresses per token + a daily `totalSupply()` snapshot job.

---

## Multi-entity ledger and wallet management

### Wallet model

Wallets are stored in our database with the following schema:

```
wallets
├── id (UUID)
├── user_id (FK to users)
├── address (lowercase hex, 42 chars)
├── chain (enum: 'lemonchain-mainnet', 'bsc', 'lemonchain-testnet')
├── name (user-given friendly name)
├── entity_id (FK to entities at current point in time)
├── role (enum: VEST, LIVE, STAKE, NFT, COLD, BRIDGE, OTHER)
├── added_via (enum: 'address-paste', 'walletconnect', 'csv-import')
├── added_at, last_synced_at, is_active
└── notes (free text)
```

The same wallet address might exist on both chains (the user's seed phrase generates the same address on Lemonchain and BSC). The `chain` field disambiguates.

### Slowly-changing dimension for entity assignments

Wallet-to-entity assignments are tracked in a separate audit table:

```
wallet_entity_assignments
├── id
├── wallet_id (FK)
├── entity_id (FK)
├── effective_from (date)
├── effective_to (nullable; null = current)
├── classification (enum: capital-contribution, sale, gift, loan, initial-assignment)
├── note
└── created_at
```

When a wallet is added: row created with `classification = 'initial-assignment'`. When reassigned to a different entity: previous row's `effective_to` set, new row created with appropriate classification. Full history preserved for IRS audit defense.

### Wallet connection — WalletConnect vs address-paste

**Address-paste is the primary flow.** The user types or pastes a 42-character hex address, assigns it to an entity, gives it a friendly name. The system starts indexing the wallet's history. No signature required, no session state, no custody risk. This is what Lemon Ledger's read-only nature deserves.

**WalletConnect is an optional convenience shortcut.** For users with an active Lemon Zest, MetaMask, or other WalletConnect-compatible wallet, a "Quick Add via Wallet" button calls `eth_requestAccounts`, reads the connected address, and drops the session immediately. We get the public address and proceed exactly as if it had been typed.

Implementation: WalletConnect v2 with `@walletconnect/web3modal` (full-featured) or `@walletconnect/sign-client` (minimal). Free tier from `cloud.walletconnect.com` covers 100k connections/month.

### Multi-chain awareness in the wallet model

When a user adds a wallet, the UI prompts for the chain (Lemonchain mainnet / BSC). Some addresses are used on both chains (same private key, same derived address) — in which case the user adds the same address twice with different chain values. The backend treats these as separate wallet records but the holdings UI can optionally consolidate them under a single "logical wallet" view.

---

## Lot tracking, basis methods, and tax form generation

### Lot tracking architecture

Each acquisition (buy, mint, reward receipt, bridge inbound) creates a **tax lot**:

```
tax_lots
├── id
├── wallet_id, token_address, chain
├── acquired_at (timestamp)
├── acquisition_type (buy, mint, reward, bridge-in, gift, ...)
├── quantity, quantity_remaining
├── cost_basis_usd (FMV at acquisition + fees)
├── source_transaction_hash
└── notes
```

Each disposal (sell, redeem, burn, bridge-out) consumes lots according to the configured basis method (FIFO, HIFO, Specific ID, Average Cost). A `lot_disposals` table records which lots were consumed for each disposal event.

This is the same pattern used by every professional crypto tax tool. Building it is well-trodden territory.

### Tax form generation

Final outputs:

- **Form 8949** (Sales and Other Dispositions of Capital Assets) — every disposal event as a line item
- **Schedule D** rollup — short-term and long-term totals
- **Schedule 1, Line 8z** — total of all ordinary income from reward emissions
- **Schedule E** (if rental/RE activity) — pass-through entity treatment
- **Form 1120-S** lines — for the LLC entity's S-Corp filing

Each form is generated on-demand from the underlying ledger tables. The forms are PDFs (filled-in IRS-compliant) plus CSVs (for tax pro hand-off).

---

## Architectural recommendations for the SOW

These decisions are now informed by R&D findings and should go straight into the SOW with no further debate.

### Tech stack

**Backend:** Python + FastAPI + Celery + Redis + Postgres. `web3.py` for direct contract calls, `eth_abi` for ABI decoding. Alternative: Node.js + TypeScript with `viem` and `BullMQ` — both ecosystems work, choose by developer comfort.

**Frontend:** React + TypeScript, sharing design tokens with the existing Ledger product. Single-page application served from a CDN with API calls to the FastAPI backend.

**Auth:** Clerk or Auth0 — don't build this ourselves.

**Hosting:** Railway (same as Ledger) for backend, Vercel for frontend, Postgres on Railway or Supabase, Redis on Upstash.

**Monitoring:** Sentry for errors, Grafana + Prometheus for metrics, alerts to Discord/Slack for oracle pause events.

### Three-tier data architecture

**Tier 1 — Chain ingestion.** Worker processes that poll Blockscout (Lemonchain) and BscScan (BSC) on a schedule. Decoded events written to raw tables. Independently scalable.

**Tier 2 — Classification & lots.** Transformation layer that takes raw events and produces classified events plus tax lots. Includes the bridge correlation module, burn-aware accounting, and per-L2 decoder logic.

**Tier 3 — Query & presentation.** Read-optimized views over the ledger tables. Powers all UI and tax form generation. No direct chain queries from this layer.

### Testnet-first development

All development happens against Citron testnet first. Same API surface, same patterns, no real money. Production deployment switches the base URL — that's the only difference.

### Phased build with clear gates

Each phase ends with a demo and explicit go/no-go before the next phase starts:

**Phase 1: Data layer (5 weeks).** Wallet ingestion (both chains), classification, lot tracking, bridge correlation. No UI. Output: CLI command produces correct Form 8949 from real chain data. Goal: prove the engine works against real wallets.

**Phase 2: Core UI (4 weeks).** Portfolio, Holdings, Transactions, Settings. Entity switching. The minimum surface for a user to see their data and manage wallets.

**Phase 3: L2-specific UI (4 weeks).** L2 Projects page, Staking & Rewards page, income runway forecasting. The differentiating layer.

**Phase 4: Tax & polish (5 weeks).** Tax & Reports page with form generation. Card Spend if surviving prioritization. Onboarding flow. Public launch readiness.

**Total: 18 weeks** (~4.5 months) with one experienced full-stack developer. Compress to ~11 weeks with two developers (one backend, one frontend).

### Budget estimate

For contractor builds:
- Single experienced full-stack developer at $90-130/hr for 18 weeks @ 40h/week: **$65k - $94k**
- Two developers compressed to 11 weeks: **$80k - $115k**
- DIY with Claude Code: meaningfully less, with your time as the primary cost

These are working estimates. Final numbers come from actual developer conversations during SOW hand-off.

---

## What we still need to confirm during build week 1

A few items that don't block the SOW but should be locked down in the first week of build:

1. **HEXDEX factory contract address** — for the rare cases where the oracle returns stale and we need DEX-reserve fallback pricing on Lemonchain. Findable by inspecting any HEXDEX swap transaction.

2. **The custody/bridge addresses, empirically.** Watch the first few bridge events real users perform and identify the recurring custody addresses.

3. **Staking contract addresses per L2.** The 19 L2 token contracts emit `Transfer` events from the staking contract to user wallets. Inspecting one reward event for each L2 reveals the staking address.

4. **Buy/burn contract addresses.** Same drill — inspect a known burn event for tokens with the deflationary mechanic.

None of these block scoping or estimating. They're "during build" details that the developer extracts from real transactions in the first sprint.

---

## Risk assessment

**Low-risk areas (high confidence):**
- Chain ingestion (standard Blockscout/BscScan pattern)
- Token registry (fully identified)
- Pricing for Lemonchain tokens (sophisticated oracle in place)
- Standard lot tracking (well-known patterns)
- Wallet management (address-paste primary, WalletConnect convenience)
- UI build (existing prototype validates the design space)

**Medium-risk areas (manageable with budgeted time):**
- Bridge correlation (heuristic, requires empirical custody address learning)
- BSC pricing for low-liquidity tokens (PancakeSwap reserves can be thin)
- Per-L2 decoder logic (19 L2s × small amount of customization each)
- Historical price backfill (event log archaeology is tedious but tractable)

**Higher-risk areas (defer or partner):**
- TMX Card / LemPay swipe data integration — off-chain, requires partnership or CSV fallback
- IRS guidance evolution for crypto tax (1099-DA is rolling out; tax form templates may shift)

**Out of scope for v1:**
- Other EVM chains (Polygon, Arbitrum, etc.)
- DeFi positions on non-Lemonchain chains
- Tax filing software integration (TurboTax export comes in v1.1)
- Mobile apps (web-responsive first)

---

## Conclusion

The R&D phase is complete. We've answered every architectural question that affects the SOW. The data layer is buildable end-to-end with the infrastructure that exists today. The 18-week build estimate is grounded in concrete engineering tasks, not guesses.

Three sources of confidence:

1. **Every claim in this report is verifiable.** The probe script we shipped earlier hits the real APIs against any wallet address you provide. Independent reproduction takes 5 minutes.

2. **The most sophisticated piece (pricing) turned out to be the most well-supported.** The team built a proper TWAP oracle rather than relying on third parties. This is the strongest competitive moat for Lemon Ledger and the most reassuring signal about the ecosystem's longevity.

3. **The known constraints are budgeted into the timeline.** Cross-chain handling, bridge correlation, deflationary tokenomics, multi-entity ledger — these are all real engineering work, but they're scoped work, not unknowns.

The next document in the package is the technical SOW. That's where this R&D becomes a build contract.

---

*Appendix: probe.py shipped alongside this report can independently verify every chain data claim. Token registry was extracted from the user's own dApp configuration as of May 30, 2026.*
