"""BSC probe against Etherscan V2 API (read-only — signs/sends nothing).

Usage:
    ETHERSCAN_API_KEY=<key> python docs/reference/probe-v2.py <bsc_wallet_address>

Or with Doppler:
    doppler run -- python docs/reference/probe-v2.py <bsc_wallet_address>

Clears three assumptions before building the BSC client:
  CHECK 1 — free-tier access: balance + txlist return status="1" with data
  CHECK 2 — traceId presence: every internal tx record has a non-null traceId
  CHECK 3 — decimals: BSC LEMX (0x2Da91257961b87e69Fa13b2e20931D517dc97597)
             decimals() returns 18 via eth_call on the free tier
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

# Use certifi's CA bundle when available (fixes macOS system-Python SSL issue).
try:
    import certifi

    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()

# ── config ────────────────────────────────────────────────────────────────────

_BASE_URL = "https://api.etherscan.io/v2/api"
_CHAIN_ID = "56"
_BSC_LEMX = "0x2Da91257961b87e69Fa13b2e20931D517dc97597"
_DECIMALS_SELECTOR = "0x313ce567"


def _get_api_key() -> str:
    key = os.environ.get("ETHERSCAN_API_KEY", "")
    if not key:
        print("FATAL: ETHERSCAN_API_KEY env var is not set.")
        print("  Run with:  ETHERSCAN_API_KEY=<key> python probe-v2.py <bsc_addr>")
        print("  Or:        doppler run -- python probe-v2.py <bsc_addr>")
        sys.exit(1)
    return key


def _call(params: dict[str, str], api_key: str) -> dict:  # type: ignore[type-arg]
    """Single GET against Etherscan V2, always including chainid + apikey."""
    full_params = {**params, "chainid": _CHAIN_ID, "apikey": api_key}
    url = _BASE_URL + "?" + urllib.parse.urlencode(full_params)
    try:
        with urllib.request.urlopen(url, timeout=15, context=_SSL_CTX) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return {
            "_http_error": exc.code,
            "status": "0",
            "message": str(exc),
            "result": "",
        }
    except Exception as exc:
        return {
            "_exception": str(exc),
            "status": "0",
            "message": str(exc),
            "result": "",
        }


# ── individual checks ─────────────────────────────────────────────────────────


def check_free_tier(addr: str, api_key: str) -> bool:
    """CHECK 1: balance + txlist on the free tier return usable data."""
    print("\n── CHECK 1: free-tier access (balance + txlist) ──────────────────────")

    bal_resp = _call(
        {"module": "account", "action": "balance", "address": addr}, api_key
    )
    print(
        f"  balance response:  status={bal_resp.get('status')!r}  "
        f"message={bal_resp.get('message')!r}  "
        f"result={str(bal_resp.get('result', ''))[:60]!r}"
    )

    if bal_resp.get("status") != "1":
        msg = str(bal_resp.get("message", "")).lower()
        result_str = str(bal_resp.get("result", "")).lower()
        if "missing" in msg and "chainid" in msg:
            print("  DETAIL: 'Missing chainid' error — V2 endpoint rejected chainid=56")
        elif (
            "pro" in msg
            or "pro" in result_str
            or "plan" in msg
            or "plan" in result_str
            or "upgrade" in result_str
        ):
            print("  DETAIL: Pro/paid-tier gate detected")
        elif "deprecat" in msg or "deprecat" in result_str:
            print("  DETAIL: Deprecation response (old endpoint?)")
        print("  RESULT: CHECK 1 FAIL — balance returned status != '1'")
        return False

    tx_resp = _call(
        {
            "module": "account",
            "action": "txlist",
            "address": addr,
            "startblock": "0",
            "sort": "desc",
            "page": "1",
            "offset": "5",
        },
        api_key,
    )
    tx_status = tx_resp.get("status")
    tx_msg = str(tx_resp.get("message", "")).lower()
    tx_result = tx_resp.get("result", [])
    print(
        f"  txlist  response:  status={tx_status!r}  message={tx_resp.get('message')!r}  "
        f"records={len(tx_result) if isinstance(tx_result, list) else tx_result!r}"
    )

    if tx_status == "1" or (tx_status == "0" and "no transactions" in tx_msg):
        count = len(tx_result) if isinstance(tx_result, list) else 0
        print(
            f"  RESULT: CHECK 1 PASS — balance OK, txlist returned {count} records "
            f"({'no txs in wallet' if count == 0 else 'data present'})"
        )
        return True

    print("  RESULT: CHECK 1 FAIL — txlist returned unexpected status")
    return False


def check_trace_id(addr: str, api_key: str) -> bool | None:
    """CHECK 2: internal txs carry traceId. Returns None if inconclusive (zero records)."""
    print("\n── CHECK 2: traceId presence on internal txs ─────────────────────────")

    resp = _call(
        {
            "module": "account",
            "action": "txlistinternal",
            "address": addr,
            "startblock": "0",
            "sort": "desc",
            "page": "1",
            "offset": "25",
        },
        api_key,
    )
    status = resp.get("status")
    msg = str(resp.get("message", "")).lower()
    records = resp.get("result", [])

    print(
        f"  txlistinternal:    status={status!r}  message={resp.get('message')!r}  "
        f"records={len(records) if isinstance(records, list) else records!r}"
    )

    # Check for Pro-gate before the "no records" empty-wallet path.
    if (
        status == "0"
        and isinstance(records, str)
        and ("plan" in records.lower() or "upgrade" in records.lower())
    ):
        print(f"  DETAIL: Pro/paid-tier gate: {records[:120]!r}")
        print("  RESULT: CHECK 2 FAIL — paid-plan required for BSC on Etherscan V2")
        return False

    if status == "0" and (
        msg in ("no transactions found",) or records == [] or records == ""
    ):
        print(
            "  RESULT: CHECK 2 INCONCLUSIVE — wallet has zero internal txs; "
            "cannot verify traceId field"
        )
        return None

    if status != "1" or not isinstance(records, list):
        print("  RESULT: CHECK 2 FAIL — unexpected response from txlistinternal")
        return False

    missing = [r for r in records if not r.get("traceId") and not r.get("trace_id")]
    if missing:
        sample = missing[0]
        print(f"  SAMPLE missing traceId keys: {sorted(sample.keys())}")
        print(
            f"  RESULT: CHECK 2 FAIL — {len(missing)}/{len(records)} records lack traceId"
        )
        return False

    sample = records[0]
    trace_val = sample.get("traceId") or sample.get("trace_id")
    print(f"  Sample traceId value: {trace_val!r}")
    print(f"  RESULT: CHECK 2 PASS — all {len(records)} records have traceId")
    return True


def check_decimals(api_key: str) -> bool:
    """CHECK 3: BSC LEMX decimals() via eth_call returns 18 on the free tier."""
    print("\n── CHECK 3: BSC LEMX decimals() via eth_call ─────────────────────────")
    print(f"  Contract: {_BSC_LEMX}  selector: {_DECIMALS_SELECTOR}")

    resp = _call(
        {
            "module": "proxy",
            "action": "eth_call",
            "to": _BSC_LEMX,
            "data": _DECIMALS_SELECTOR,
            "tag": "latest",
        },
        api_key,
    )
    raw_result = resp.get("result", "")
    print(
        f"  eth_call response: status={resp.get('status')!r}  "
        f"message={resp.get('message')!r}  result={str(raw_result)[:80]!r}"
    )

    # eth_call via proxy module returns {"jsonrpc":"2.0","id":1,"result":"0x..."} or
    # wraps it in the Etherscan envelope — handle both shapes
    if isinstance(raw_result, str) and raw_result.startswith("0x"):
        hex_val = raw_result
    elif isinstance(raw_result, str) and (
        "plan" in raw_result.lower() or "upgrade" in raw_result.lower()
    ):
        print(f"  DETAIL: Pro/paid-tier gate: {raw_result[:120]!r}")
        print("  RESULT: CHECK 3 FAIL — paid-plan required for BSC on Etherscan V2")
        return False
    elif isinstance(resp.get("error"), dict):
        print(f"  DETAIL: JSON-RPC error: {resp['error']}")
        print("  RESULT: CHECK 3 FAIL — eth_call returned a JSON-RPC error")
        return False
    else:
        print("  RESULT: CHECK 3 FAIL — unexpected response shape")
        return False

    if len(hex_val) < 2:
        print("  RESULT: CHECK 3 FAIL — empty hex result")
        return False

    try:
        decoded = int(hex_val, 16)
    except ValueError:
        print(f"  RESULT: CHECK 3 FAIL — cannot parse hex: {hex_val!r}")
        return False

    print(f"  Decoded decimals: {decoded} (hex {hex(decoded)})")
    if decoded == 18:
        print("  RESULT: CHECK 3 PASS — BSC LEMX decimals() == 18, matches seed value")
        return True
    else:
        print(f"  RESULT: CHECK 3 FAIL — got {decoded}, expected 18")
        return False


# ── main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <bsc_wallet_address>")
        sys.exit(1)

    bsc_addr = sys.argv[1].strip()
    api_key = _get_api_key()

    print(f"BSC probe — Etherscan V2  (chainid={_CHAIN_ID})")
    print(f"Wallet: {bsc_addr}")
    print(f"Endpoint: {_BASE_URL}")

    r1 = check_free_tier(bsc_addr, api_key)
    r2 = check_trace_id(bsc_addr, api_key)
    r3 = check_decimals(api_key)

    print("\n══════════════════════════════════════════════════════════════════════")
    print("SUMMARY")
    print(f"  CHECK 1 free-tier access:   {'PASS' if r1 else 'FAIL'}")
    print(
        f"  CHECK 2 traceId presence:   {'PASS' if r2 is True else 'INCONCLUSIVE (zero internal txs)' if r2 is None else 'FAIL'}"
    )
    print(f"  CHECK 3 BSC LEMX decimals:  {'PASS' if r3 else 'FAIL'}")
    print("══════════════════════════════════════════════════════════════════════")

    # Exit non-zero if any definite failure
    if r1 is False or r2 is False or r3 is False:
        sys.exit(1)


if __name__ == "__main__":
    main()
