import pytest
import asyncio

from agents.meteora_regime_lp.performance import normalize_executor_book
from agents.meteora_regime_lp.lifecycle import SOL_MINT, USDC_MINT
from condor.agents.performance import AgentPerformance
from condor.agents.providers.executors import ExecutorsProvider


def test_mixed_quote_pnl_and_deployed_capital_are_normalized_to_usd():
    rows = [
        {
            "id": "core",
            "status": "TERMINATED",
            "pair": f"{SOL_MINT}-{USDC_MINT}",
            "pnl": 0.78,
            "volume": 68.0,
            "fees": 0.02,
        },
        {
            "id": "satellite",
            "status": "TERMINATED",
            "pair": f"TokenMint111111111111111111111111111111-{SOL_MINT}",
            "pnl": -0.01,
            "volume": 0.20,
            "fees": 0.0004,
        },
    ]
    portfolio = {
        "main": {
            "solana": [
                {"token": "SOL", "units": 0.5, "value": 50.0, "price": 100.0},
                {"token": "USDC", "units": 10.0, "value": 10.0, "price": 1.0},
            ]
        }
    }

    result = normalize_executor_book(rows, portfolio)

    assert result.known is True
    assert result.realized_pnl_usd == pytest.approx(-0.22)
    assert result.total_pnl_usd == pytest.approx(-0.22)
    assert result.deployed_usd == pytest.approx(88.0)
    assert result.fees_usd == pytest.approx(0.06)
    assert result.by_quote["SOL"]["rate_usd"] == 100.0


def test_unknown_non_stable_quote_fails_closed_instead_of_adding_units():
    result = normalize_executor_book(
        [
            {
                "id": "unknown",
                "status": "RUNNING",
                "pair": "TokenMint111111111111111111111111111111-UNKNOWN_QUOTE",
                "pnl": 4.0,
                "volume": 3.0,
                "fees": 1.0,
            }
        ],
        {},
    )

    assert result.known is False
    assert result.total_pnl_usd is None
    assert result.deployed_usd is None
    assert result.missing_quotes == ("UNKNOWN_QUOTE",)


def test_meteora_provider_uses_normalized_usd_totals(monkeypatch):
    rows = [
        {
            "id": "core",
            "status": "TERMINATED",
            "pair": f"{SOL_MINT}-{USDC_MINT}",
            "pnl": 0.78,
            "volume": 68.0,
            "fees": 0.02,
            "amount": 68.0,
        },
        {
            "id": "sat",
            "status": "TERMINATED",
            "pair": f"TokenMint111111111111111111111111111111-{SOL_MINT}",
            "pnl": -0.01,
            "volume": 0.20,
            "fees": 0.0004,
            "amount": 0.20,
        },
    ]

    async def fake_performance(*args, **kwargs):
        return AgentPerformance(
            agent_id="meteora_regime_lp.operator_33",
            realized_pnl=0.77,
            total_pnl=0.77,
            volume=68.2,
            fees=0.0204,
            closed_count=2,
            trade_count=2,
            executors=rows,
        )

    monkeypatch.setattr(
        "condor.agents.performance.fetch_agent_performance", fake_performance
    )

    class Portfolio:
        async def get_state(self, **kwargs):
            return {
                "main": {
                    "solana": [
                        {"token": "SOL", "units": 0.5, "price": 100.0},
                        {"token": "USDC", "units": 10.0, "price": 1.0},
                    ]
                }
            }

    class Executors:
        async def search_executors(self, **kwargs):
            return {"data": []}

    client = type("Client", (), {"portfolio": Portfolio(), "executors": Executors()})()
    result = asyncio.run(
        ExecutorsProvider().execute(
            client,
            {"bot_name": ""},
            agent_id="meteora_regime_lp.operator_33",
        )
    )

    assert result.data["pnl_known"] is True
    assert result.data["total_pnl"] == pytest.approx(-0.22)
    assert result.data["total_volume"] == pytest.approx(88.0)
    assert "USD-normalized" in result.summary
