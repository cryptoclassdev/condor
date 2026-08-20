"""Core data provider: active executors with PnL and volume.

Delegates number-crunching to ``condor.agents.performance`` so that
live ticks and the web API always agree.
"""

from __future__ import annotations

from typing import Any

from . import register_provider
from .base import BaseProvider, ProviderResult


class ExecutorsProvider(BaseProvider):
    name = "executors"
    is_core = True

    async def execute(
        self,
        client: Any,
        config: dict,
        agent_id: str = "",
        bot_names: list[str] | None = None,
        since: float = 0.0,
    ) -> ProviderResult:
        from condor.agents.performance import (
            fetch_agent_performance,
            fetch_sibling_running,
        )

        if not agent_id:
            return ProviderResult(
                name=self.name,
                data={"executors": [], "total_pnl": 0, "total_volume": 0},
                summary="Active Executors: no agent_id provided",
            )

        # The ledger's bases are what this session actually owns — several, if it
        # deployed several. Without one (executor mode, tests) fall back to the
        # configured name, which is what the session would deploy under anyway.
        bases = list(bot_names or []) or [config.get("bot_name", "")]

        try:
            # ``since`` slices an adopted bot's history to this session's window,
            # so what the agent is told it earned is what the dashboard attributes
            # to it — inherited PnL is not reported as its own.
            perf = await fetch_agent_performance(
                client, agent_id, bot_names=bases, since=since
            )
        except Exception as e:
            return ProviderResult(
                name=self.name,
                data={"error": str(e)},
                summary=f"Active Executors: failed to fetch ({e})",
            )

        running = [e for e in perf.executors if e["status"] == "RUNNING"]

        # Positions this session ADOPTED from an earlier session of the same
        # strategy. They carry the creating session's controller_id, so every
        # attribution-filtered query misses them — including the one whose
        # open_count/total_exposure the risk engine enforces its caps against.
        # Owning a position without counting it is the dangerous direction.
        adopted = []
        try:
            owned_ids = {e["id"] for e in perf.executors if e.get("id")}
            adopted = [
                r for r in await fetch_sibling_running(client, agent_id)
                if r.get("id") not in owned_ids
            ]
        except Exception as e:
            adopted = []
            log_note = f"  ⚠️ Could not scan for adopted positions ({e}) — exposure below may be UNDERSTATED."
        else:
            log_note = ""
        running = running + adopted
        lines = [
            (
                f"Active Executors ({len(running)}) [agent: {agent_id}]:"
                if running
                else f"Active Executors: none running (agent: {agent_id})"
            )
        ]
        from condor.fetchers.executors import is_lp_executor

        any_lp = False
        for r in running:
            side = r.get("side") or ""
            tag = " [ADOPTED]" if r.get("adopted") else ""
            if is_lp_executor(r):
                # An LP row's "volume" is the quote deposited into the range, not
                # what traded through it. Labelling it V: read as trading volume
                # and made a dormant, out-of-range position look busy.
                any_lp = True
                metric = f"deployed:${r['volume']:,.0f}"
            else:
                metric = f"V:${r['volume']:,.0f}"
            fee_part = f" fees:${r['fees']:+.4f}" if r.get("fees") else ""
            lines.append(f"  {r['pair']} {side}{tag} ${r['pnl']:+.2f} ({metric}{fee_part})")
        if perf.bot_names:
            lines.append(f"  Bots operated: {', '.join(perf.bot_names)}")
        fees_label = (
            f"${perf.fees:+.4f}" if perf.fees_known else f"${perf.fees:+.4f} (incomplete)"
        )
        lines.append(
            f"  Realized: ${perf.realized_pnl:+.2f} | "
            f"Unrealized: ${perf.unrealized_pnl:+.2f} | "
            f"Total PnL: ${perf.total_pnl:+.2f} | "
            f"Fees earned: {fees_label} | "
            f"Volume/Deployed: ${perf.volume:,.0f}"
        )
        if adopted:
            lines.append(
                f"  ↩️ {len(adopted)} position(s) marked [ADOPTED] were opened by an earlier "
                "session of this strategy. They ARE your responsibility and DO count toward "
                "exposure and the open-executor limit, but their PnL is credited to the "
                "session that opened them, so the PnL line above excludes them."
            )
        if log_note:
            lines.append(log_note)
        if any_lp:
            # Fees are the product of an LP position; without them on this line the
            # agent was optimising a number it could not observe.
            lines.append(
                "  ℹ️ LP rows: the figure above is capital DEPLOYED into ranges, not "
                "swap volume traded through them — it does not move as the pool trades. "
                "Judge LP performance on 'Fees earned' and PnL, and read traded volume "
                "from the pool's own data, never from this line."
            )
        # Say so rather than letting a missing bot read as a flat one. The figures
        # above are then a floor, and an agent told a floor can go look; an agent
        # told "$0.00" has no reason to.
        if perf.unresolved_bases:
            lines.append(
                "  ⚠️ No live or archived instance found for: "
                f"{', '.join(perf.unresolved_bases)} — the PnL above EXCLUDES "
                "them and is incomplete, not flat. Verify with manage_bots before "
                "concluding this session is break-even."
            )

        # Exposure is NOT a single number when the book spans quote assets. Each
        # executor's `amount` is denominated in ITS OWN quote, so summing a
        # USDC-quoted core with a SOL-quoted satellite adds 20 to 0.244 and calls
        # the result "$20.24" — observed live Aug 19. The consequence is not
        # cosmetic: TickEngine feeds this straight into risk_state.total_exposure,
        # which is checked against `max_position_size_quote`, so a $20 SOL-quoted
        # position was being counted as if it were 24 cents. The cap was, in
        # effect, unenforced for every SOL-quoted sleeve.
        #
        # There is no price feed at this layer, so this does not invent a
        # conversion. It reports the breakdown, and takes the LARGEST single-quote
        # exposure as the headline figure rather than a meaningless sum — an
        # under-count on a risk limit is the dangerous direction, and a sum across
        # incompatible units under-counts every non-headline currency.
        from condor.fetchers.executors import executor_quote

        by_quote: dict[str, float] = {}
        for r in running:
            q = executor_quote(r.get("pair", "")) or "?"
            by_quote[q] = by_quote.get(q, 0.0) + float(r.get("amount", 0) or 0)
        total_exposure = max(by_quote.values()) if by_quote else 0.0
        if len(by_quote) > 1:
            lines.append(
                "  ⚠️ MIXED-QUOTE BOOK: "
                + ", ".join(f"{v:,.4g} {q}" for q, v in sorted(by_quote.items()))
                + ". These are different units and cannot be added. The risk engine's "
                "position-size limit is enforced against the largest single-quote figure "
                "only — treat per-entry sizing as YOUR responsibility, not the cap's."
            )

        return ProviderResult(
            name=self.name,
            data={
                "executors": running,
                "all_executors": perf.executors,
                "total_pnl": perf.total_pnl,
                "realized_pnl": perf.realized_pnl,
                "unrealized_pnl": perf.unrealized_pnl,
                "total_volume": perf.volume,
                "total_fees": perf.fees,
                "total_exposure": total_exposure,
                "exposure_by_quote": by_quote,
                # Includes adopted rows: this is what the risk engine counts
                # against max_open_executors, and a position you are responsible
                # for has to be in that number.
                "open_count": len(running),
                "owned_open_count": perf.open_count,
                "adopted_open_count": len(adopted),
                "closed_count": perf.closed_count,
                "win_rate": perf.win_rate,
                # Provenance travels with the figures. Everything downstream —
                # get_info(), the session report, the journal snapshot — reads
                # this dict, so a consumer that shows a total can show what the
                # total is made of and whether any of it is missing.
                "bot_names": perf.bot_names,
                "bot_instances": perf.bot_instances,
                "unresolved_bases": perf.unresolved_bases,
                "controllers": perf.controllers,
                "close_type_counts": perf.close_type_counts,
                "fees_known": perf.fees_known,
            },
            summary="\n".join(lines),
        )


register_provider(ExecutorsProvider())
