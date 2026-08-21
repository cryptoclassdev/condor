"""Optional deterministic outcome hooks for autonomous agents.

An agent may expose ``agents.<slug>.host_outcomes`` with async ``reconcile`` and
``capture`` functions.  The host calls them outside the model/tool loop, so
durable execution evidence does not depend on prompt adherence.  Hook failures
are logged and fail open with respect to supervision: they never stop a tick or
authorize a transaction.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from types import ModuleType
from typing import Any

log = logging.getLogger(__name__)


def load(agent_slug: str) -> ModuleType | None:
    module_name = f"agents.{agent_slug}.host_outcomes"
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            return None
        log.exception("Outcome hook %s has a missing dependency", module_name)
        return None
    except Exception:
        log.exception("Could not load outcome hook %s", module_name)
        return None


async def reconcile(
    module: ModuleType | None,
    *,
    client: Any,
    agent_id: str,
    tick: int,
) -> None:
    callback = getattr(module, "reconcile", None) if module else None
    if callback is None:
        return
    try:
        async with asyncio.timeout(30):
            await callback(client=client, agent_id=agent_id, tick=tick)
    except TimeoutError:
        log.warning("Outcome reconciliation timed out for %s", agent_id)
    except Exception:
        log.exception("Outcome reconciliation failed for %s", agent_id)


async def prepare(
    module: ModuleType | None,
    *,
    client: Any,
    agent_id: str,
    tick: int,
) -> dict[str, Any]:
    """Capture pre-model evidence needed to identify post-action changes."""
    callback = getattr(module, "prepare", None) if module else None
    if callback is None:
        return {}
    try:
        async with asyncio.timeout(20):
            evidence = await callback(client=client, agent_id=agent_id, tick=tick)
        return evidence if isinstance(evidence, dict) else {}
    except TimeoutError:
        log.warning("Outcome preparation timed out for %s", agent_id)
    except Exception:
        log.exception("Outcome preparation failed for %s", agent_id)
    return {}


async def capture(
    module: ModuleType | None,
    *,
    client: Any,
    agent_id: str,
    tick: int,
    tool_calls: list[dict[str, Any]],
    evidence_before: dict[str, Any] | None = None,
) -> None:
    callback = getattr(module, "capture", None) if module else None
    if callback is None:
        return
    try:
        async with asyncio.timeout(20):
            await callback(
                client=client,
                agent_id=agent_id,
                tick=tick,
                tool_calls=tool_calls,
                evidence_before=evidence_before or {},
            )
    except TimeoutError:
        log.warning("Outcome capture timed out for %s tick %s", agent_id, tick)
    except Exception:
        log.exception("Outcome capture failed for %s tick %s", agent_id, tick)
