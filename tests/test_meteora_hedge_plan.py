from agents.meteora_regime_lp.hedge_math import plan_sol_hedge
from condor.agents.strategy import StrategyStore


def test_stale_price_blocks_hedge_recommendation():
    plan = plan_sol_hedge(
        sol_amount=2.0,
        sol_price_usd=100.0,
        price_age_sec=181,
        max_price_age_sec=180,
        portfolio_equity_usd=800.0,
        current_short_usd=0.0,
    )

    assert plan.action == "BLOCKED"
    assert "stale" in plan.reason.lower()
    assert plan.adjustment_usd == 0.0


def test_target_is_capped_by_real_portfolio_equity():
    plan = plan_sol_hedge(
        sol_amount=3.0,
        sol_price_usd=100.0,
        price_age_sec=5,
        max_price_age_sec=180,
        portfolio_equity_usd=800.0,
        current_short_usd=0.0,
        max_hedge_pct_equity=25.0,
    )

    assert plan.action == "INCREASE_SHORT"
    assert plan.sol_exposure_usd == 300.0
    assert plan.hedge_cap_usd == 200.0
    assert plan.target_short_usd == 200.0
    assert plan.adjustment_usd == 200.0


def test_small_live_lp_exposure_does_not_create_dust_hedge():
    plan = plan_sol_hedge(
        sol_amount=0.008393792,
        sol_price_usd=87.38,
        price_age_sec=5,
        max_price_age_sec=180,
        portfolio_equity_usd=81.70,
        current_short_usd=0.0,
        min_order_usd=10.0,
    )

    assert plan.sol_exposure_usd == 0.73
    assert plan.action == "HOLD"
    assert plan.adjustment_usd == 0.0
    assert "minimum order" in plan.reason.lower()


def test_missing_price_or_equity_blocks_hedge_recommendation():
    plan = plan_sol_hedge(
        sol_amount=2.0,
        sol_price_usd=0.0,
        price_age_sec=5,
        max_price_age_sec=180,
        portfolio_equity_usd=0.0,
        current_short_usd=0.0,
    )

    assert plan.action == "BLOCKED"
    assert "positive" in plan.reason.lower()


def test_live_hedging_is_disabled_until_a_perpetual_venue_is_connected():
    strategy = StrategyStore().get("meteora_regime_lp", "regime_lp_operator")

    assert strategy is not None
    assert strategy.default_config["hedge"] == {
        "enabled": False,
        "connector": "bitget_perpetual",
        "trading_pair": "SOL-USDT",
        "coverage_pct": 100,
        "max_hedge_pct_equity": 25,
        "min_order_usd": 10,
        "rebalance_band_usd": 2,
        "max_price_age_sec": 180,
    }
