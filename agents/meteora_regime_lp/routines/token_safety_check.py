"""Objective on-chain safety gates for a satellite candidate's base mint.

Agent-local routine for meteora_regime_lp. Checks, via plain Solana JSON-RPC
(use the configured private rpc_url — public RPC throttles the holder query):

  1. Mint authority renounced   (no infinite dilution)
  2. Freeze authority disabled  (can't freeze your tokens — honeypot vector)
  3. Top-10 holder concentration <= max_top10_holder_pct (whale-dump risk)

Returns PASS/FAIL with per-gate detail. Reject the candidate on ANY fail.
Self-contained: aiohttp only, no Gateway/client dependency.
"""

import logging
import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

PUBLIC_RPC = "https://api.mainnet-beta.solana.com"


class Config(BaseModel):
    """PASS/FAIL safety gates (mint/freeze authority, holder concentration) for a mint."""

    base_mint: str = Field(description="SPL token mint address to check")
    rpc_url: str = Field(default="", description="Private Solana RPC URL (public throttles holder query)")
    require_mint_renounced: bool = Field(default=True)
    require_freeze_disabled: bool = Field(default=True)
    max_top10_holder_pct: float = Field(default=60.0, description="Max %% of supply held by top 10 accounts")


async def _rpc(session: aiohttp.ClientSession, url: str, method: str, params: list) -> dict:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=25)) as resp:
        if resp.status != 200:
            raise RuntimeError(f"{method} -> HTTP {resp.status}")
        data = await resp.json()
    if "error" in data:
        raise RuntimeError(f"{method} -> {data['error']}")
    return data.get("result")


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    mint = config.base_mint.strip()
    if not mint:
        return "token_safety_check: base_mint is required."
    url = config.rpc_url.strip() or PUBLIC_RPC

    gates: list[tuple[str, bool, str]] = []  # (gate, passed, detail)
    try:
        async with aiohttp.ClientSession() as session:
            # Gate 1+2: mint account (jsonParsed) → mintAuthority / freezeAuthority / supply.
            acct = await _rpc(session, url, "getAccountInfo", [mint, {"encoding": "jsonParsed"}])
            info = (((acct or {}).get("value") or {}).get("data") or {}).get("parsed", {}).get("info", {})
            if not info:
                return f"token_safety_check: FAIL — could not parse mint account {mint} (wrong address or RPC issue)."
            mint_auth = info.get("mintAuthority")
            freeze_auth = info.get("freezeAuthority")
            supply = float(info.get("supply") or 0)
            decimals = int(info.get("decimals") or 0)
            supply_ui = supply / (10 ** decimals) if decimals else supply

            if config.require_mint_renounced:
                gates.append(("mint_authority_renounced", mint_auth is None,
                              "renounced" if mint_auth is None else f"ACTIVE: {mint_auth}"))
            if config.require_freeze_disabled:
                gates.append(("freeze_authority_disabled", freeze_auth is None,
                              "disabled" if freeze_auth is None else f"ACTIVE: {freeze_auth}"))

            # Gate 3: top-10 holder concentration.
            largest = await _rpc(session, url, "getTokenLargestAccounts", [mint])
            vals = (largest or {}).get("value") or []
            top10 = sum(float(v.get("uiAmount") or 0) for v in vals[:10])
            pct = (top10 / supply_ui * 100) if supply_ui > 0 else 100.0
            gates.append(("top10_holder_concentration", pct <= config.max_top10_holder_pct,
                          f"{pct:.1f}% (max {config.max_top10_holder_pct:.0f}%)"))
    except Exception as e:
        return f"token_safety_check: ERROR — {e}. Treat as FAIL (do not enter without a completed check)."

    passed = all(ok for _, ok, _ in gates)
    verdict = "PASS" if passed else "FAIL"
    lines = [f"token_safety_check: **{verdict}** for {mint}"]
    for gate, ok, detail in gates:
        lines.append(f"- {'✓' if ok else '✗'} {gate}: {detail}")
    if not passed:
        lines.append("Reject this candidate — one failed gate is a full reject.")
    return "\n".join(lines)
