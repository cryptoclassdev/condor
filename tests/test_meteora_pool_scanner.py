import asyncio

from agents.meteora_regime_lp.routines import meteora_pool_scanner


def test_native_pool_fetch_uses_official_filtered_ranking(monkeypatch):
    captured = {}

    async def fake_get_json(_session, path, params):
        captured.update({"path": path, "params": params})
        return {
            "data": [
                {
                    "address": "pool",
                    "created_at": 1_787_600_000_000,
                    "tvl": 50_000,
                    "token_x": {
                        "address": "base-mint",
                        "symbol": "BASE",
                        "price": 0.1,
                    },
                    "token_y": {
                        "address": meteora_pool_scanner._QUOTE_MINTS["USDC"],
                        "symbol": "USDC",
                        "price": 1,
                    },
                    "volume": {"1h": 10_000, "4h": 30_000, "24h": 100_000},
                }
            ]
        }

    monkeypatch.setattr(meteora_pool_scanner, "meteora_get_json", fake_get_json)
    config = meteora_pool_scanner.Config(
        ranking_window="24h", min_tvl_usd=25_000, native_page_size=100
    )

    rows = asyncio.run(meteora_pool_scanner._fetch_native_pools(object(), config))

    assert captured["path"] == "pools"
    assert captured["params"]["sort_by"] == "fee_tvl_ratio_24h:desc"
    assert "is_blacklisted=false" in captured["params"]["filter_by"]
    assert rows[0]["attributes"]["volume_usd"]["h24"] == 100_000
