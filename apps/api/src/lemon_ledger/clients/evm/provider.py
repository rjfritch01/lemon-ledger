from __future__ import annotations

from typing import Any

import httpx


class EVMProvider:
    """Read-only EVM JSON-RPC wrapper.

    Wraps a single HTTP RPC endpoint.  Callers own the httpx.Client so
    connection pooling and timeouts are configured centrally.
    """

    def __init__(self, rpc_url: str, *, http: httpx.Client) -> None:
        self._rpc_url = rpc_url
        self._http = http

    def eth_call(self, to: str, data: str, block: str = "latest") -> str:
        """Execute a read-only eth_call and return the raw hex-encoded result."""
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": "eth_call",
            "params": [{"to": to, "data": data}, block],
            "id": 1,
        }
        resp = self._http.post(self._rpc_url, json=payload)
        resp.raise_for_status()
        result: str = resp.json()["result"]
        return result


def build_evm_provider(rpc_url: str, *, http: httpx.Client) -> EVMProvider:
    """Construct a read-only EVMProvider."""
    return EVMProvider(rpc_url, http=http)
