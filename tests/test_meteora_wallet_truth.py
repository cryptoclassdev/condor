import pytest

from agents.meteora_regime_lp.wallet_truth import (
    SOL_MINT,
    TokenHolding,
    _parse_holdings,
    _parse_prices,
    build_wallet_state,
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
