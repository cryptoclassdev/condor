"""Every token the wallet holds — including the ones the agent cannot otherwise see.

Agent-local routine for meteora_regime_lp.

The agent's normal wallet read reports SOL and USDC. That is fine until an LP
position fills into its base token, because closing such a position returns
**base tokens, not quote**. The proceeds then sit in the wallet, invisible, and
every downstream figure is wrong in the same direction:

  Observed Aug 20, session 12: the agent reported "wallet $43.10, unchanged since
  t8" and declared a funding swap blocked for FIVE consecutive ticks, while the
  wallet actually held **$17.17 of Intismeran** — 21% of the book — recovered
  from a stopped-out satellite. It believed it could not fund a $20 probe while
  sitting on most of one.

  Observed Aug 19: the same blindness hid a position that had gone 100% base and
  ~29% under water. The table read "$0.00" and said nothing.

`portfolio.get_state()` has always returned every token with units, value and
price. Nothing needed to be added to the platform — it just was not being read.

This routine reports the FULL book and splits it in two:

  LIQUID QUOTE the quote assets the sleeves can spend (SOL, USDC), before gas,
               rent, and strategy reserves
  STRANDED     everything else — base-token inventory left over from closed LP
               positions. This is capital, not noise, and not missing money. It
               is one swap away from being deployable, and until it is swapped
               it carries full directional risk with no stop-loss on it.

Read-only. It never swaps; recovering stranded inventory is a deliberate act.
"""

import logging

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

# What the sleeves can actually deploy. Everything else is inventory.
DEFAULT_QUOTES = ["SOL", "USDC", "USDT", "WSOL"]


class Config(BaseModel):
    """Full wallet inventory: liquid quote before reserves vs stranded base tokens."""

    quote_tokens: list[str] = Field(
        default=DEFAULT_QUOTES,
        description="Tokens the sleeves can deploy directly; all others are stranded inventory",
    )
    dust_usd: float = Field(
        default=0.50, description="Ignore balances worth less than this"
    )
    refresh: bool = Field(
        default=True, description="Force a balance refresh rather than trusting cache"
    )
    account_name: str = Field(
        default="", description="Restrict to one account (blank = all)"
    )


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "wallet_audit: no server available."

    try:
        state = await client.portfolio.get_state(
            account_names=[config.account_name] if config.account_name else None,
            refresh=config.refresh,
        )
    except Exception as e:
        # Do NOT return a partial or empty book — a wallet that reads as empty is
        # indistinguishable from a wallet that is empty, and this routine exists
        # precisely because under-reporting the wallet drives bad decisions.
        return (
            f"wallet_audit: FAILED to read portfolio state ({e}). Treat the wallet as "
            f"UNKNOWN this tick, not empty. Do not conclude anything is missing, and do "
            f"not size an entry off the last known figure."
        )

    quotes = {q.strip().upper() for q in config.quote_tokens}
    rows, liquid_quote, stranded = [], 0.0, 0.0

    if not isinstance(state, dict):
        return (
            f"wallet_audit: unexpected portfolio payload type {type(state).__name__}."
        )

    for account, account_data in state.items():
        if not isinstance(account_data, dict):
            continue
        for connector, balances in account_data.items():
            for b in balances or []:
                if not isinstance(b, dict):
                    continue
                token = str(b.get("token") or "?").upper()
                units = _num(b.get("units"))
                value = _num(b.get("value"))
                if abs(value) < config.dust_usd and abs(units) <= 0:
                    continue
                if abs(value) < config.dust_usd:
                    continue
                is_quote = token in quotes
                if is_quote:
                    liquid_quote += value
                else:
                    stranded += value
                rows.append(
                    {
                        "Token": token,
                        "Class": "liquid quote" if is_quote else "STRANDED",
                        "Units": f"{units:,.6g}",
                        "Value($)": f"{value:,.2f}",
                        "Price": f"{_num(b.get('price')):,.8g}",
                        "Where": f"{account}/{connector}",
                    }
                )

    if not rows:
        return (
            "wallet_audit: no balances above the dust threshold. If that is unexpected, "
            "treat it as a read problem, not an empty wallet."
        )

    rows.sort(key=lambda r: -abs(float(r["Value($)"].replace(",", ""))))
    total = liquid_quote + stranded

    # PARTIAL-READ GUARD. get_state() can SUCCEED and still return an incomplete
    # book — a connector missing from the payload raises nothing, so the wallet
    # simply reads smaller and every downstream figure is wrong in the dangerous
    # direction. Observed Aug 20, session 15 tick 1: the read returned $3.04 of
    # USDC and no SOL at all, fourteen minutes after the same routine read
    # 0.372 SOL + $3.04. The chain (Phantom) held the SOL the whole time. The
    # agent then froze for want of capital it already had.
    #
    # The invariant that catches it: this is a Solana LP agent. Gas is paid in
    # SOL and every DLMM position locks ~0.0574 SOL of rent. A book with no
    # native SOL row AT ALL is therefore not a poor wallet, it is a broken read
    # — a genuinely SOL-less wallet could not have opened a position or paid for
    # this very call. Absence of the gas token is evidence about the READ, never
    # about the balance.
    native = [r for r in rows if r["Token"] in ("SOL", "WSOL")]
    where = sorted({r["Where"] for r in rows})
    if not native:
        return (
            f"wallet_audit: ⚠️ PARTIAL READ — the portfolio call SUCCEEDED but returned NO "
            f"native SOL balance at all, only {', '.join(r['Token'] for r in rows)} "
            f"(${total:,.2f}) across {len(where)} connector(s): {', '.join(where)}. "
            f"A Solana LP wallet cannot hold zero SOL: gas is paid in SOL and every open "
            f"DLMM position locks ~0.0574 SOL of rent. This is a READ FAILURE, not a small "
            f"wallet. Treat the wallet as UNKNOWN this tick: do NOT report it as "
            f"under-funded, do NOT declare a sleeve unfundable, do NOT size an entry off "
            f"the figure above, and do NOT close anything to 'free up' capital. Re-run next "
            f"tick; if it persists for three consecutive ticks, notify the operator and "
            f"verify the balance on-chain before acting."
        )

    parts = [
        f"wallet_audit: total wallet ${total:,.2f} = ${liquid_quote:,.2f} liquid quote "
        f"before gas/rent/strategy reserves + ${stranded:,.2f} stranded across "
        f"{len(rows)} token(s). This is NOT the amount safe to deploy; use capital_guard "
        f"for the reserve-adjusted entry budget."
    ]
    if stranded >= config.dust_usd:
        pct = 100.0 * stranded / total if total else 0.0
        names = ", ".join(r["Token"] for r in rows if r["Class"] == "STRANDED")
        parts.append(
            f"⚠️ ${stranded:,.2f} ({pct:.0f}% of the book) is STRANDED base-token "
            f"inventory: {names}. This is left over from LP positions that filled into "
            f"their base token and were then closed — a DLMM close returns base tokens, "
            f"not quote. It is NOT missing money and NOT dust: it is capital that is one "
            f"swap away from being deployable. Two consequences to act on: (1) do not "
            f"report the wallet as under-funded, or a sleeve as unfundable, without "
            f"counting this — journal the stranded figure explicitly alongside the quote "
            f"balances; (2) it carries full directional risk with NO stop-loss on it, so "
            f"the longer it sits the more it is an unmanaged position rather than a cash "
            f"balance. Recovering it is a deliberate swap, never automatic."
        )
    else:
        parts.append(
            "No stranded inventory — the whole wallet is liquid quote before reserves."
        )

    summary = " ".join(parts)
    columns = ["Token", "Class", "Units", "Value($)", "Price", "Where"]

    try:
        from condor.reports import ReportBuilder

        b = ReportBuilder("Wallet Audit — liquid quote vs stranded")
        b.source("routine", "wallet_audit").tags(["wallet", "inventory", "meteora"])
        b.kpi("Total", f"${total:,.2f}")
        b.kpi("Liquid quote (pre-reserve)", f"${liquid_quote:,.2f}")
        b.kpi("Stranded", f"${stranded:,.2f}")
        b.markdown(summary)
        b.table(rows, columns)
        b.manual_order()
        await b.save()
    except Exception as e:
        logger.info(f"wallet_audit: report save skipped: {e}")

    try:
        from routines.base import RoutineResult

        return RoutineResult(text=summary, table_data=rows, table_columns=columns)
    except Exception:
        lines = [summary, ""]
        for r in rows:
            lines.append(
                f"{r['Token']:<10} {r['Class']:<11} {r['Units']:>16} units  "
                f"${r['Value($)']:>10}  @ {r['Price']}  [{r['Where']}]"
            )
        return "\n".join(lines)
