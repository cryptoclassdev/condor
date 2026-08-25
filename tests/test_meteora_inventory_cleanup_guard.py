import asyncio
from types import SimpleNamespace

from agents.meteora_regime_lp.routines import inventory_cleanup_guard
from agents.meteora_regime_lp.wallet_truth import WalletTruth


def _state(*rows):
    return {"gateway_wallet": {"solana-mainnet-beta": list(rows)}}


def _row(token, mint, units, price):
    return {
        "token": token,
        "mint": mint,
        "units": units,
        "price": price,
        "value": units * price,
    }


def _run(monkeypatch, rows, executors=()):
    class Executors:
        async def search_executors(self, **kwargs):
            return {"data": list(executors)}

    client = SimpleNamespace(executors=Executors())

    async def fake_get_client(*args, **kwargs):
        return client

    async def fake_wallet_state(*args, **kwargs):
        return WalletTruth("wallet", _state(*rows))

    monkeypatch.setattr(inventory_cleanup_guard, "get_client", fake_get_client)
    monkeypatch.setattr(
        inventory_cleanup_guard, "authoritative_wallet_state", fake_wallet_state
    )
    return str(
        asyncio.run(
            inventory_cleanup_guard.run(
                inventory_cleanup_guard.Config(), SimpleNamespace(_chat_id=1)
            )
        )
    )


def test_cleanup_guard_plans_largest_stranded_balance_to_sol(monkeypatch):
    result = _run(
        monkeypatch,
        [
            _row("SOL", inventory_cleanup_guard.SOL_MINT, 0.2, 100),
            _row("USDC", inventory_cleanup_guard.USDC_MINT, 5, 1),
            _row("CATE", "cate_mint_which_is_long_enough_123456789", 400, 0.04),
            _row("MADE", "made_mint_which_is_long_enough_123456789", 100, 0.03),
        ],
    )

    assert "CLEANUP_REQUIRED" in result
    assert "CATE" in result
    assert "cate_mint_which_is_long_enough_123456789" in result
    assert f"-{inventory_cleanup_guard.SOL_MINT}" in result
    assert "amount=400" in result
    assert "token=USDC" not in result


def test_cleanup_guard_is_clean_when_only_deployable_quotes_remain(monkeypatch):
    result = _run(
        monkeypatch,
        [
            _row("SOL", inventory_cleanup_guard.SOL_MINT, 0.25, 100),
            _row("USDC", inventory_cleanup_guard.USDC_MINT, 10, 1),
        ],
    )

    assert "CLEAN" in result
    assert "no stranded inventory" in result


def test_cleanup_guard_sweeps_sub_dollar_stablecoin_dust_to_sol(monkeypatch):
    result = _run(
        monkeypatch,
        [
            _row("SOL", inventory_cleanup_guard.SOL_MINT, 0.55, 100),
            _row("USDC", inventory_cleanup_guard.USDC_MINT, 0.84, 1),
        ],
    )

    assert "CLEANUP_REQUIRED" in result
    assert "token=USDC" in result
    assert "amount=0.84" in result


def test_cleanup_guard_waits_for_existing_cleanup_executor(monkeypatch):
    mint = "cate_mint_which_is_long_enough_123456789"
    result = _run(
        monkeypatch,
        [
            _row("SOL", inventory_cleanup_guard.SOL_MINT, 0.2, 100),
            _row("CATE", mint, 400, 0.04),
        ],
        executors=[
            {
                "type": "order_executor",
                "config": {
                    "trading_pair": f"{mint}-{inventory_cleanup_guard.SOL_MINT}",
                },
            }
        ],
    )

    assert "WAIT" in result
    assert "cleanup executor already RUNNING" in result


def test_cleanup_guard_blocks_unidentified_stranded_token(monkeypatch):
    result = _run(
        monkeypatch,
        [
            _row("SOL", inventory_cleanup_guard.SOL_MINT, 0.2, 100),
            _row("MYSTERY", "", 10, 2),
        ],
    )

    assert "BLOCKED" in result
    assert "missing mint" in result


def test_cleanup_guard_catches_visible_cent_level_residual(monkeypatch):
    result = _run(
        monkeypatch,
        [
            _row("SOL", inventory_cleanup_guard.SOL_MINT, 0.55, 100),
            _row("CATE", "cate_mint_which_is_long_enough_123456789", 1.1, 0.067),
        ],
    )

    assert "CLEANUP_REQUIRED" in result
    assert "value_usd=$0.07" in result


def test_cleanup_guard_never_sells_token_used_by_running_lp(monkeypatch):
    mint = "cate_mint_which_is_long_enough_123456789"
    result = _run(
        monkeypatch,
        [
            _row("SOL", inventory_cleanup_guard.SOL_MINT, 0.2, 100),
            _row("CATE", mint, 400, 0.04),
        ],
        executors=[
            {
                "type": "lp_executor",
                "config": {
                    "trading_pair": f"{mint}-{inventory_cleanup_guard.SOL_MINT}",
                },
            }
        ],
    )

    assert "WAIT" in result
    assert "token belongs to a RUNNING LP" in result
    assert "CLEANUP_REQUIRED" not in result
