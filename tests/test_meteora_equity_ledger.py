import json

import pytest

from agents.meteora_regime_lp.equity_ledger import (
    build_equity_snapshot,
    record_equity_snapshot,
    session_equity_change,
)
from agents.meteora_regime_lp.lifecycle import SOL_MINT


def _portfolio(wallet_value, sol_price=100.0):
    return {
        "main": {
            "solana": [
                {
                    "token": "SOL",
                    "units": wallet_value / sol_price,
                    "value": wallet_value,
                    "price": sol_price,
                }
            ]
        }
    }


def test_equity_snapshot_adds_wallet_and_open_lp_in_usd():
    snapshot = build_equity_snapshot(
        agent_id="meteora_regime_lp.operator_33",
        tick=10,
        portfolio_state=_portfolio(30.0),
        executors=[
            {
                "executor_id": "satellite",
                "status": "RUNNING",
                "executor_type": "lp_executor",
                "trading_pair": f"TokenMint111111111111111111111111111111-{SOL_MINT}",
                "custom_info": {"total_value_quote": 0.5},
            }
        ],
        timestamp="2026-08-23T12:00:00+00:00",
    )

    assert snapshot.wallet_usd == 30.0
    assert snapshot.open_lp_usd == 50.0
    assert snapshot.equity_usd == 80.0
    assert snapshot.known is True


def test_equity_ledger_reports_session_delta_from_first_to_latest(tmp_path):
    path = tmp_path / "equity.jsonl"
    first = build_equity_snapshot(
        agent_id="agent_33",
        tick=1,
        portfolio_state=_portfolio(100.0),
        executors=[],
        timestamp="2026-08-23T01:00:00+00:00",
    )
    latest = build_equity_snapshot(
        agent_id="agent_33",
        tick=2,
        portfolio_state=_portfolio(94.0),
        executors=[],
        timestamp="2026-08-23T02:00:00+00:00",
    )

    record_equity_snapshot(path, first)
    record_equity_snapshot(path, latest)

    assert len(path.read_text().splitlines()) == 2
    assert json.loads(path.read_text().splitlines()[1])["equity_usd"] == 94.0
    assert session_equity_change(path) == pytest.approx(-6.0)
