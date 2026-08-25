import asyncio
from types import SimpleNamespace

from agents.meteora_regime_lp.routines import wallet_audit
from agents.meteora_regime_lp.wallet_truth import WalletTruth


def test_wallet_audit_reports_visible_cent_level_stranded_inventory(monkeypatch):
    class Portfolio:
        async def get_state(self, **kwargs):
            return {}

    async def fake_get_client(*args, **kwargs):
        return SimpleNamespace(portfolio=Portfolio())

    async def fake_wallet_state(*args, **kwargs):
        return WalletTruth(
            "wallet",
            {
                "gateway_wallet": {
                    "solana-mainnet-beta": [
                        {
                            "token": "SOL",
                            "mint": "So11111111111111111111111111111111111111112",
                            "units": 0.55,
                            "price": 100,
                            "value": 55,
                        },
                        {
                            "token": "CATE",
                            "mint": "cate_mint_which_is_long_enough_123456789",
                            "units": 1.1,
                            "price": 0.067,
                            "value": 0.0737,
                        },
                    ]
                }
            },
        )

    monkeypatch.setattr(wallet_audit, "get_client", fake_get_client)
    monkeypatch.setattr(wallet_audit, "authoritative_wallet_state", fake_wallet_state)

    result = str(
        asyncio.run(wallet_audit.run(wallet_audit.Config(), SimpleNamespace(_chat_id=1)))
    )

    assert "$0.07 stranded" in result
    assert "CATE" in result
    assert "No stranded inventory" not in result
