"""Read-only SOL hedge readiness and sizing guard for Meteora LP inventory.

The routine never places an order. It converts measured SOL token inventory into a
bounded short-notional recommendation, then reports whether the configured perpetual
connector actually has credentials. Transaction cost is intentionally not part of the
proof; stale data, dust orders, and real-equity caps remain hard blockers.
"""

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from agents.meteora_regime_lp.hedge_math import plan_sol_hedge
from config_manager import get_client

CATEGORY = "Risk Management"


class Config(BaseModel):
    """Plan a SOL hedge from authoritative LP composition and capital inputs."""

    sol_amount: float = Field(
        description="Measured SOL in open LPs, including pending SOL-denominated fees"
    )
    sol_price_usd: float = Field(description="Fresh SOL/USD reference price")
    price_age_sec: float = Field(
        default=0, description="Age of the SOL/USD observation in seconds"
    )
    portfolio_equity_usd: float = Field(
        description="Actual wallet + on-chain LP equity from capital_guard"
    )
    current_short_usd: float = Field(
        default=0, description="Current SOL perpetual short notional"
    )
    connector: str = Field(default="bitget_perpetual")
    trading_pair: str = Field(default="SOL-USDT")
    account_name: str = Field(default="master_account")
    coverage_pct: float = Field(default=100, ge=0, le=100)
    max_hedge_pct_equity: float = Field(default=25, ge=0, le=100)
    min_order_usd: float = Field(default=10, ge=0)
    rebalance_band_usd: float = Field(default=2, ge=0)
    max_price_age_sec: float = Field(default=180, gt=0)


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    plan = plan_sol_hedge(
        sol_amount=config.sol_amount,
        sol_price_usd=config.sol_price_usd,
        price_age_sec=config.price_age_sec,
        max_price_age_sec=config.max_price_age_sec,
        portfolio_equity_usd=config.portfolio_equity_usd,
        current_short_usd=config.current_short_usd,
        coverage_pct=config.coverage_pct,
        max_hedge_pct_equity=config.max_hedge_pct_equity,
        min_order_usd=config.min_order_usd,
        rebalance_band_usd=config.rebalance_band_usd,
    )

    credentials: set[str] = set()
    credential_error = ""
    client = await get_client(context._chat_id, context=context)
    if client is None:
        credential_error = "no Hummingbot server available"
    else:
        try:
            credentials = set(
                await client.accounts.list_account_credentials(config.account_name)
            )
        except Exception as exc:
            credential_error = f"credential check failed: {type(exc).__name__}"

    venue_ready = config.connector in credentials
    if venue_ready:
        execution = "READY for a separately verified execution step"
    else:
        detail = credential_error or f"no {config.connector} credential"
        execution = f"READINESS ONLY — {detail}; do not place a hedge"

    return (
        f"hedge_guard: {plan.action}. SOL exposure ${plan.sol_exposure_usd:,.2f}; "
        f"target short ${plan.target_short_usd:,.2f}; adjustment "
        f"${plan.adjustment_usd:+,.2f}; cap ${plan.hedge_cap_usd:,.2f} "
        f"({config.max_hedge_pct_equity:g}% of real equity). "
        f"Venue {config.connector}/{config.trading_pair}: {execution}. {plan.reason}"
    )
