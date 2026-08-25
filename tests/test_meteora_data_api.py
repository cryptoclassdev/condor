from agents.meteora_regime_lp.meteora_data_api import (
    aggregate_five_minute_history,
    pool_to_gecko_shape,
)


def test_native_pool_normalizes_into_existing_scanner_shape():
    raw = pool_to_gecko_shape(
        {
            "address": "pool-address",
            "created_at": 1_787_600_000_000,
            "tvl": 42_000,
            "token_x": {"address": "runner-mint", "symbol": "RUN", "price": 0.02},
            "token_y": {"address": "sol-mint", "symbol": "SOL", "price": 98},
            "volume": {"1h": 12_000, "4h": 40_000, "24h": 180_000},
        },
        m5_volume=3_000,
        h1_volume=15_000,
    )

    attrs = raw["attributes"]
    assert attrs["address"] == "pool-address"
    assert attrs["volume_usd"] == {
        "m5": 3_000,
        "h1": 15_000,
        "h6": 40_000,
        "h24": 180_000,
    }
    assert raw["relationships"]["dex"]["data"]["id"] == "meteora"
    assert raw["relationships"]["base_token"]["data"]["id"] == "solana_runner-mint"


def test_five_minute_history_uses_latest_bucket_and_latest_hour():
    rows = [
        {"timestamp": index, "volume": index * 100}
        for index in range(1, 15)
    ]

    m5, h1 = aggregate_five_minute_history(rows)

    assert m5 == 1_400
    assert h1 == sum(index * 100 for index in range(3, 15))
