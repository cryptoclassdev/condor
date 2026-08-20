import asyncio
from types import SimpleNamespace

from agents.meteora_regime_lp.capital_math import build_capital_plan
from agents.meteora_regime_lp.routines import capital_guard


def test_capital_plan_caps_configured_budget_to_authoritative_book():
    plan = build_capital_plan(
        configured_capital_usd=800,
        wallet_total_usd=40.61,
        liquid_quote_usd=40.61,
        open_lp_usd=36.50,
        native_price_usd=87.50,
        min_native_reserve=0.06,
        next_position_rent_native=0.0574,
        daily_loss_limit_pct=6,
        sleeve_percentages={"core": 60, "satellite": 20, "runner": 20},
    )

    assert plan.effective_equity_usd == 77.11
    assert plan.risk_capital_usd == 77.11
    assert plan.daily_loss_limit_usd == 4.63
    assert plan.gas_reserve_usd == 5.25
    assert plan.next_position_rent_usd == 5.02
    assert plan.max_new_deposit_usd == 30.34
    assert plan.sleeve_budgets_usd == {
        "core": 46.27,
        "satellite": 15.42,
        "runner": 15.42,
    }


def test_capital_plan_never_counts_stranded_inventory_as_liquid():
    plan = build_capital_plan(
        configured_capital_usd=100,
        wallet_total_usd=50,
        liquid_quote_usd=20,
        open_lp_usd=30,
        native_price_usd=100,
        min_native_reserve=0.06,
        next_position_rent_native=0.0574,
        daily_loss_limit_pct=6,
        sleeve_percentages={"core": 60, "satellite": 20, "runner": 20},
    )

    assert plan.effective_equity_usd == 80
    assert plan.max_new_deposit_usd == 8.26


def test_capital_guard_values_wallet_and_chain_positions_in_usd(monkeypatch):
    class Portfolio:
        async def get_state(self, **kwargs):
            return {
                "main": {
                    "solana": [
                        {
                            "token": "SOL",
                            "units": 0.4286,
                            "value": 37.57,
                            "price": 87.65,
                        },
                        {"token": "USDC", "units": 3.04, "value": 3.04, "price": 1.0},
                    ]
                }
            }

    class Executors:
        async def search_executors(self, **kwargs):
            return {
                "data": [
                    {
                        "config": {
                            "pool_address": "core_pool_address_which_is_long_enough_123",
                            "trading_pair": f"{capital_guard.SOL_MINT}-{capital_guard.USDC_MINT}",
                        }
                    },
                    {
                        "config": {
                            "pool_address": "sat_pool_address_which_is_long_enough_456",
                            "trading_pair": f"base_mint_which_is_long_enough_789-{capital_guard.SOL_MINT}",
                        }
                    },
                ]
            }

    class Gateway:
        async def get_positions_owned(self, *, pool_address, **kwargs):
            # The live gateway currently ignores pool_address and returns the
            # wallet-wide set on every call. The guard must deduplicate by
            # position and value each row using its own pool's quote asset.
            return [
                {
                    "position_address": "core_position_address_which_is_long_123",
                    "pool_address": "core_pool_address_which_is_long_enough_123",
                    "base_token_amount": 0,
                    "quote_token_amount": 20,
                },
                {
                    "position_address": "sat_position_address_which_is_long_456",
                    "pool_address": "sat_pool_address_which_is_long_enough_456",
                    "base_token_amount": 200,
                    "current_price": 0.0004,
                    "quote_token_amount": 0.108,
                },
            ]

    client = SimpleNamespace(
        portfolio=Portfolio(), executors=Executors(), gateway_clmm=Gateway()
    )

    async def fake_get_client(*args, **kwargs):
        return client

    monkeypatch.setattr(capital_guard, "get_client", fake_get_client)
    context = SimpleNamespace(_chat_id=1)
    result = asyncio.run(capital_guard.run(capital_guard.Config(), context))

    assert "effective equity $77.09" in str(result)
    assert "2 on-chain LP(s)" in str(result)
    assert "risk capital $77.09 (configured ceiling $800.00)" in str(result)
    assert "daily loss $4.63" in str(result)
    assert "liquid quote before reserves $40.61" in str(result)
