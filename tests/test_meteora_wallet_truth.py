import asyncio
from types import SimpleNamespace

import pytest

from agents.meteora_regime_lp.wallet_truth import (
    SOL_MINT,
    TokenHolding,
    _parse_holdings,
    _parse_prices,
    build_wallet_state,
    authoritative_wallet_state,
)


def test_wallet_truth_parses_legacy_and_token_2022_rows():
    payload = {
        "value": [
            {
                "account": {
                    "data": {
                        "parsed": {
                            "info": {
                                "mint": "token_mint",
                                "tokenAmount": {"uiAmountString": "12.5"},
                            }
                        }
                    }
                }
            }
        ]
    }

    assert _parse_holdings(payload, "spl-token-2022") == [
        TokenHolding("token_mint", 12.5, "spl-token-2022")
    ]


def test_wallet_truth_builds_a_complete_priced_state():
    holding = TokenHolding("cate_mint", 400, "spl-token-2022")
    state = build_wallet_state(
        native_sol=0.25,
        holdings=[holding],
        prices={SOL_MINT: ("SOL", 100), "cate_mint": ("CATE", 0.04)},
    )

    rows = state["gateway_wallet"]["solana-mainnet-beta"]
    assert rows[0]["value"] == 25
    assert rows[1]["token"] == "CATE"
    assert rows[1]["value"] == 16
    assert rows[1]["source"] == "solana-rpc/spl-token-2022"


def test_wallet_truth_fails_closed_when_a_holding_has_no_price():
    with pytest.raises(RuntimeError, match="no USD price"):
        build_wallet_state(
            native_sol=0.25,
            holdings=[TokenHolding("unknown_mint", 1, "spl-token")],
            prices={SOL_MINT: ("SOL", 100)},
        )


def test_wallet_truth_parses_gecko_multi_response():
    prices = _parse_prices(
        {
            "data": [
                {
                    "id": "solana_cate_mint",
                    "attributes": {"symbol": "CATE", "price_usd": "0.041"},
                }
            ]
        }
    )

    assert prices == {"cate_mint": ("CATE", 0.041)}


def test_wallet_truth_prefers_configured_gateway_rpc(monkeypatch):
    observed = {}
    monkeypatch.delenv("SOLANA_RPC_URL", raising=False)
    monkeypatch.delenv("RPC_URL", raising=False)

    class Accounts:
        async def list_gateway_wallets(self):
            return [
                {
                    "chain": "solana",
                    "default_address": "wallet_address",
                    "walletAddresses": ["wallet_address"],
                }
            ]

    class Gateway:
        async def get_network_config(self, network):
            assert network == "solana-mainnet-beta"
            return {"node_url": "https://private-rpc.invalid"}

    async def fake_rpc(session, url, method, params):
        observed.setdefault("urls", set()).add(url)
        if method == "getBalance":
            return {"value": 250_000_000}
        return {"value": []}

    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def json(self):
            return {
                "data": [
                    {
                        "id": f"solana_{SOL_MINT}",
                        "attributes": {"symbol": "SOL", "price_usd": "100"},
                    }
                ]
            }

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def get(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr("agents.meteora_regime_lp.wallet_truth._rpc", fake_rpc)
    monkeypatch.setattr(
        "agents.meteora_regime_lp.wallet_truth.aiohttp.ClientSession", Session
    )
    monkeypatch.setattr("agents.meteora_regime_lp.wallet_truth._CACHE", {})

    truth = asyncio.run(
        authoritative_wallet_state(
            SimpleNamespace(accounts=Accounts(), gateway=Gateway())
        )
    )

    assert truth is not None
    assert observed["urls"] == {"https://private-rpc.invalid"}
