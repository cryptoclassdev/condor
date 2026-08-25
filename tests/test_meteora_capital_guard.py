import asyncio
from pathlib import Path
from types import SimpleNamespace

from agents.meteora_regime_lp.capital_math import (
    build_capital_plan,
    build_spendable_quote_caps,
)
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


def test_capital_plan_reserves_probe_and_rent_for_every_future_target_slot():
    plan = build_capital_plan(
        configured_capital_usd=800,
        wallet_total_usd=137.99,
        liquid_quote_usd=137.99,
        open_lp_usd=0,
        native_price_usd=100,
        min_native_reserve=0.06,
        next_position_rent_native=0.0574,
        daily_loss_limit_pct=6,
        sleeve_percentages={"core": 60, "satellite": 20, "runner": 20},
        target_open_slots=3,
        current_open_slots=0,
        min_position_deposit_usd=20,
        slot_reserve_buffer_usd=2,
    )

    # Before opening the core, preserve two future $20 probes, two rents,
    # the gas floor, and a small execution buffer.
    assert plan.future_slot_reserve_usd == 53.48
    assert plan.max_new_deposit_usd == 72.77


def test_capital_plan_blocks_new_entry_when_target_slot_count_is_full():
    plan = build_capital_plan(
        configured_capital_usd=800,
        wallet_total_usd=100,
        liquid_quote_usd=100,
        open_lp_usd=100,
        native_price_usd=100,
        min_native_reserve=0.06,
        next_position_rent_native=0.0574,
        daily_loss_limit_pct=6,
        sleeve_percentages={"core": 60, "satellite": 20, "runner": 20},
        target_open_slots=3,
        current_open_slots=3,
        min_position_deposit_usd=20,
        slot_reserve_buffer_usd=2,
    )

    assert plan.max_new_deposit_usd == 0


def test_quote_caps_never_fund_usdc_entry_from_sol_value():
    caps = build_spendable_quote_caps(
        quote_values_usd={"SOL": 65.72, "USDC": 23.09},
        native_symbols={"SOL", "WSOL"},
        gas_reserve_usd=5.33,
        next_position_rent_usd=5.10,
    )

    assert caps == {"SOL": 55.29, "USDC": 23.09}
    assert caps["USDC"] < 53.29


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
    assert "per-asset max deposits: SOL $27.28, USDC $3.04" in str(result)


def test_capital_guard_counts_orphan_position_without_running_executor(monkeypatch):
    class Portfolio:
        async def get_state(self, **kwargs):
            return {
                "main": {
                    "solana": [
                        {"token": "SOL", "value": 60.75, "price": 90.4},
                    ]
                }
            }

    class Executors:
        async def search_executors(self, **kwargs):
            return {"data": []}

    class Gateway:
        async def get_positions_owned(self, **kwargs):
            return {
                "data": [
                    {
                        "position_address": "orphan_position_address_which_is_long_123",
                        "pool_address": capital_guard.CORE_SOL_USDC_POOL,
                        "quote_token_address": capital_guard.USDC_MINT,
                        "base_token_amount": 0,
                        "quote_token_amount": 22.97,
                        "current_price": 90.4,
                    }
                ]
            }

    client = SimpleNamespace(
        portfolio=Portfolio(), executors=Executors(), gateway_clmm=Gateway()
    )

    async def fake_get_client(*args, **kwargs):
        return client

    monkeypatch.setattr(capital_guard, "get_client", fake_get_client)
    result = asyncio.run(
        capital_guard.run(capital_guard.Config(), SimpleNamespace(_chat_id=1))
    )

    assert "effective equity $83.72" in str(result)
    assert "1 on-chain LP(s) $22.97" in str(result)


def test_capital_guard_resolves_missing_orphan_quote_from_pool_info(monkeypatch):
    """Replay the live t114 payload: owned rows omit both token addresses."""

    orphan_pool = "orphan_pool_address_which_is_long_enough_456"

    class Portfolio:
        async def get_state(self, **kwargs):
            return {
                "main": {
                    "solana": [
                        {"token": "SOL", "value": 35.93, "price": 91.75},
                        {"token": "USDC", "value": 51.41, "price": 1.0},
                    ]
                }
            }

    class Executors:
        async def search_executors(self, **kwargs):
            return {"data": []}

    class Gateway:
        async def get_positions_owned(self, **kwargs):
            return [
                {
                    "position_address": "core_position_address_which_is_long_123",
                    "pool_address": capital_guard.CORE_SOL_USDC_POOL,
                    "base_token_amount": 0,
                    "quote_token_amount": 23.31,
                },
                {
                    "position_address": "orphan_position_address_which_is_long_456",
                    "pool_address": orphan_pool,
                    "base_token_amount": 16_928.98,
                    "base_fee_amount": 4_414.47,
                    "current_price": 0.000004503,
                    "quote_token_amount": 0.1413,
                    "quote_fee_amount": 0.0185,
                },
            ]

        async def get_pool_info(self, *, pool_address, **kwargs):
            assert pool_address == orphan_pool
            return {
                "base_token_address": "base_mint_which_is_long_enough_789",
                "quote_token_address": capital_guard.SOL_MINT,
            }

    client = SimpleNamespace(
        portfolio=Portfolio(), executors=Executors(), gateway_clmm=Gateway()
    )

    async def fake_get_client(*args, **kwargs):
        return client

    monkeypatch.setattr(capital_guard, "get_client", fake_get_client)
    result = asyncio.run(
        capital_guard.run(capital_guard.Config(), SimpleNamespace(_chat_id=1))
    )

    assert "UNKNOWN" not in str(result)
    assert "effective equity $134.13" in str(result)
    assert "2 on-chain LP(s) $46.79" in str(result)


def test_capital_guard_rejects_inconsistent_partial_quote_token_reads(monkeypatch):
    class Portfolio:
        calls = 0

        async def get_state(self, **kwargs):
            self.calls += 1
            balances = [{"token": "SOL", "value": 60, "price": 90}]
            if self.calls > 1:
                balances.append({"token": "USDC", "value": 51.41, "price": 1})
            return {"main": {"solana": balances}}

    class Executors:
        async def search_executors(self, **kwargs):
            return {"data": []}

    class Gateway:
        async def get_positions_owned(self, **kwargs):
            return {"data": []}

    client = SimpleNamespace(
        portfolio=Portfolio(), executors=Executors(), gateway_clmm=Gateway()
    )

    async def fake_get_client(*args, **kwargs):
        return client

    monkeypatch.setattr(capital_guard, "get_client", fake_get_client)
    result = asyncio.run(
        capital_guard.run(capital_guard.Config(), SimpleNamespace(_chat_id=1))
    )

    assert "UNKNOWN" in str(result)
    assert "inconsistent quote-token sets" in str(result)


def test_capital_guard_pauses_when_running_executor_position_is_missing_from_authority(
    monkeypatch,
):
    class Portfolio:
        async def get_state(self, **kwargs):
            return {
                "main": {"solana": [{"token": "SOL", "value": 60, "price": 90}]}
            }

    class Executors:
        async def search_executors(self, **kwargs):
            return {
                "data": [
                    {
                        "id": "running-executor",
                        "config": {
                            "pool_address": capital_guard.CORE_SOL_USDC_POOL,
                            "trading_pair": f"{capital_guard.SOL_MINT}-{capital_guard.USDC_MINT}",
                        },
                        "custom_info": {
                            "position_address": "missing_position_address_which_is_long_123"
                        },
                    }
                ]
            }

    class Gateway:
        async def get_positions_owned(self, **kwargs):
            return {"data": []}

    client = SimpleNamespace(
        portfolio=Portfolio(), executors=Executors(), gateway_clmm=Gateway()
    )

    async def fake_get_client(*args, **kwargs):
        return client

    monkeypatch.setattr(capital_guard, "get_client", fake_get_client)
    result = asyncio.run(
        capital_guard.run(capital_guard.Config(), SimpleNamespace(_chat_id=1))
    )

    assert "UNKNOWN" in str(result)
    assert "RUNNING executor position" in str(result)


def test_strategy_recovers_from_insufficient_quote_without_indefinite_pause():
    strategy_text = (
        Path(capital_guard.__file__).resolve().parents[1]
        / "strategies/regime_lp_operator/strategy.md"
    ).read_text()

    assert "INSUFFICIENT_BALANCE" in strategy_text
    assert "per-asset max deposit" in strategy_text
    assert "next eligible deep tick" in strategy_text
