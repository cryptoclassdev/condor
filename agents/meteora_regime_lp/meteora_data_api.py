"""Read-only helpers for Meteora's official DLMM Data API.

The indexed API is a discovery source, never transaction or position authority.
Pool ownership and execution verification continue to come from Solana/Gateway.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import aiohttp


METEORA_DATA_BASE = "https://dlmm.datapi.meteora.ag"


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _created_at_iso(value: Any) -> str | None:
    timestamp = _num(value)
    if timestamp <= 0:
        return None
    if timestamp > 1e11:
        timestamp /= 1000.0
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


async def get_json(
    session: aiohttp.ClientSession,
    path: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    async with session.get(
        f"{METEORA_DATA_BASE}/{path.lstrip('/')}",
        params=params,
        headers={"Accept": "application/json"},
        timeout=aiohttp.ClientTimeout(total=20),
    ) as response:
        payload = await response.json()
        if response.status != 200:
            message = payload.get("message") if isinstance(payload, dict) else None
            raise RuntimeError(
                f"Meteora DLMM Data API {path} -> HTTP {response.status}"
                + (f" ({message})" if message else "")
            )
    if not isinstance(payload, dict):
        raise RuntimeError(f"Meteora DLMM Data API {path} returned non-object data")
    return payload


def pool_to_gecko_shape(
    pool: dict[str, Any],
    *,
    m5_volume: float | None = None,
    h1_volume: float | None = None,
) -> dict[str, Any]:
    """Normalize a native Meteora pool into the scanner's existing raw shape."""

    token_x = pool.get("token_x") if isinstance(pool.get("token_x"), dict) else {}
    token_y = pool.get("token_y") if isinstance(pool.get("token_y"), dict) else {}
    volume = pool.get("volume") if isinstance(pool.get("volume"), dict) else {}
    x_mint = str(token_x.get("address") or "")
    y_mint = str(token_y.get("address") or "")
    x_symbol = str(token_x.get("symbol") or "?")
    y_symbol = str(token_y.get("symbol") or "?")
    h1 = _num(volume.get("1h")) if h1_volume is None else float(h1_volume)
    volume_usd = {
        "m5": 0.0 if m5_volume is None else float(m5_volume),
        "h1": h1,
        # The API exposes 4h/12h rather than 6h. The sustained-volume gate only
        # requires a non-zero intermediate window, so 4h is the conservative fit.
        "h6": _num(volume.get("4h")),
        "h24": _num(volume.get("24h")),
    }
    return {
        "id": f"solana_{pool.get('address') or ''}",
        "attributes": {
            "address": str(pool.get("address") or ""),
            "name": f"{x_symbol} / {y_symbol}",
            "base_token_symbol": x_symbol,
            "quote_token_symbol": y_symbol,
            "base_token_price_usd": _num(token_x.get("price")),
            "quote_token_price_usd": _num(token_y.get("price")),
            "reserve_in_usd": _num(pool.get("tvl")),
            "volume_usd": volume_usd,
            "pool_created_at": _created_at_iso(pool.get("created_at")),
        },
        "relationships": {
            "dex": {"data": {"id": "meteora"}},
            "base_token": {"data": {"id": f"solana_{x_mint}"}},
            "quote_token": {"data": {"id": f"solana_{y_mint}"}},
        },
    }


def aggregate_five_minute_history(rows: list[dict[str, Any]]) -> tuple[float, float]:
    """Return latest 5m volume and the sum of the latest twelve 5m buckets."""

    ordered = sorted(
        (row for row in rows if isinstance(row, dict)),
        key=lambda row: _num(row.get("timestamp")),
    )
    volumes = [_num(row.get("volume")) for row in ordered[-12:]]
    if not volumes:
        return 0.0, 0.0
    return volumes[-1], sum(volumes)
