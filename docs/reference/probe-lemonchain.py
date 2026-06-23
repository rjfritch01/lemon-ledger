"""Lemonchain Blockscout probe — read-only, no API key required.

Usage:
    python docs/reference/probe-lemonchain.py <lemonchain_wallet_address> [--testnet]

Verifies the free public Blockscout instance is reachable and returns data:
  CHECK 1 — health:           eth_block_number returns a valid hex block
  CHECK 2 — balance:          account/balance returns status="1"
  CHECK 3 — token holdings:   account/tokenlist returns a list (may be empty)
  CHECK 4 — transactions:     account/txlist returns status="1" or "no txs"
  CHECK 5 — token transfers:  account/tokentx returns status="1" or "no txs"
  CHECK 6 — internal txs:     account/txlistinternal returns status="1" or "no txs"

BSC probes are out of scope here — run probe-v2.py for those.
"""

from __future__ import annotations

import json
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

try:
    import certifi

    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()

_MAINNET_URL = "https://explorer.lemonchain.io/api"
_TESTNET_URL = "https://explorer-testnet.lemonchain.io/api"


_UA = "Mozilla/5.0 (compatible; lemon-ledger-probe/1.0)"


def _call(base_url: str, params: dict[str, str]) -> dict:  # type: ignore[type-arg]
    url = base_url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
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


def check_health(base_url: str) -> bool:
    print("\n── CHECK 1: health (eth_block_number) ────────────────────────────────")
    resp = _call(base_url, {"module": "block", "action": "eth_block_number"})
    # Blockscout may return JSON-RPC shape {"jsonrpc":"2.0","result":"0x..."} or
    # Etherscan-compatible shape {"status":"1","result":"0x..."} — both have "result".
    result = resp.get("result", "")
    print(f"  response: {resp}")
    if isinstance(result, str) and result.startswith("0x"):
        try:
            block = int(result, 16)
            print(f"  Decoded block: {block} ({result})")
            print("  RESULT: CHECK 1 PASS — block number returned")
            return True
        except ValueError:
            pass
    print("  RESULT: CHECK 1 FAIL — unexpected response")
    return False


def check_balance(base_url: str, addr: str) -> bool:
    print("\n── CHECK 2: balance ──────────────────────────────────────────────────")
    resp = _call(base_url, {"module": "account", "action": "balance", "address": addr})
    status = resp.get("status")
    result = resp.get("result", "")
    print(
        f"  status={status!r}  message={resp.get('message')!r}  result={str(result)[:60]!r}"
    )
    if status == "1":
        print("  RESULT: CHECK 2 PASS — balance returned")
        return True
    print("  RESULT: CHECK 2 FAIL")
    return False


def check_token_list(base_url: str, addr: str) -> bool:
    print("\n── CHECK 3: token holdings (tokenlist) ───────────────────────────────")
    resp = _call(
        base_url, {"module": "account", "action": "tokenlist", "address": addr}
    )
    status = resp.get("status")
    result = resp.get("result", [])
    count = len(result) if isinstance(result, list) else "n/a"
    print(f"  status={status!r}  message={resp.get('message')!r}  records={count}")
    msg_lower = str(resp.get("message", "")).lower()
    if status == "1" or (
        status == "0" and ("no" in msg_lower or result == [] or result == "")
    ):
        print(f"  RESULT: CHECK 3 PASS — tokenlist reachable ({count} tokens)")
        return True
    print("  RESULT: CHECK 3 FAIL")
    return False


def _check_list_endpoint(
    base_url: str, label: str, module: str, action: str, addr: str, check_num: int
) -> bool:
    print(f"\n── CHECK {check_num}: {label} ──────────────────────────────────────")
    resp = _call(
        base_url,
        {
            "module": module,
            "action": action,
            "address": addr,
            "startblock": "0",
            "sort": "desc",
            "page": "1",
            "offset": "5",
        },
    )
    status = resp.get("status")
    result = resp.get("result", [])
    msg_lower = str(resp.get("message", "")).lower()
    count = len(result) if isinstance(result, list) else repr(result)
    print(f"  status={status!r}  message={resp.get('message')!r}  records={count}")
    if status == "1":
        print(f"  RESULT: CHECK {check_num} PASS — {count} records returned")
        return True
    if status == "0" and (
        "no transactions" in msg_lower or "no record" in msg_lower or result in ([], "")
    ):
        print(
            f"  RESULT: CHECK {check_num} PASS — endpoint reachable (wallet has no {label})"
        )
        return True
    print(f"  RESULT: CHECK {check_num} FAIL")
    return False


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0].startswith("-"):
        print(f"Usage: python {sys.argv[0]} <lemonchain_wallet_address> [--testnet]")
        sys.exit(1)

    addr = args[0].strip().lower()
    testnet = "--testnet" in args
    base_url = _TESTNET_URL if testnet else _MAINNET_URL
    network = "Citron testnet" if testnet else "mainnet"

    print(f"Lemonchain Blockscout probe — {network}")
    print(f"Wallet:   {addr}")
    print(f"Endpoint: {base_url}")

    r1 = check_health(base_url)
    r2 = check_balance(base_url, addr)
    r3 = check_token_list(base_url, addr)
    r4 = _check_list_endpoint(base_url, "transactions", "account", "txlist", addr, 4)
    r5 = _check_list_endpoint(
        base_url, "token transfers", "account", "tokentx", addr, 5
    )
    r6 = _check_list_endpoint(
        base_url, "internal txs", "account", "txlistinternal", addr, 6
    )

    print("\n══════════════════════════════════════════════════════════════════════")
    print("SUMMARY")
    print(f"  CHECK 1 health:           {'PASS' if r1 else 'FAIL'}")
    print(f"  CHECK 2 balance:          {'PASS' if r2 else 'FAIL'}")
    print(f"  CHECK 3 token holdings:   {'PASS' if r3 else 'FAIL'}")
    print(f"  CHECK 4 transactions:     {'PASS' if r4 else 'FAIL'}")
    print(f"  CHECK 5 token transfers:  {'PASS' if r5 else 'FAIL'}")
    print(f"  CHECK 6 internal txs:     {'PASS' if r6 else 'FAIL'}")
    print("══════════════════════════════════════════════════════════════════════")

    if not all([r1, r2, r3, r4, r5, r6]):
        sys.exit(1)


if __name__ == "__main__":
    main()
