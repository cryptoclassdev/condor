#!/usr/bin/env python3
"""Install a freshly revoked/reissued Telegram bot token without exposing it.

BotFather must perform the actual revoke/reissue because it changes an account
credential. Once the operator has the replacement, this helper reads it from a
hidden terminal prompt, atomically updates Condor's ignored ``.env``, and keeps
the file owner-only. The token never appears in argv or normal output.
"""

from __future__ import annotations

import getpass
import json
import re
import urllib.error
import urllib.request
from argparse import ArgumentParser
from pathlib import Path
from typing import Any, Callable

if __package__:
    from scripts.rotate_hummingbot_api_password import (
        _atomic_owner_only,
        _replace_env_value,
    )
else:  # Direct ``python scripts/...`` invocation.
    from rotate_hummingbot_api_password import (  # type: ignore[no-redef]
        _atomic_owner_only,
        _replace_env_value,
    )


CONDOR_ENV = Path(__file__).resolve().parents[1] / ".env"
TOKEN_PATTERN = re.compile(r"^[0-9]{6,12}:[A-Za-z0-9_-]{30,}$")


def install(token: str, *, env_path: Path = CONDOR_ENV) -> None:
    token = token.strip()
    if not TOKEN_PATTERN.fullmatch(token):
        raise ValueError("replacement does not have Telegram bot-token format")
    current = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    _atomic_owner_only(
        env_path,
        _replace_env_value(current, "TELEGRAM_TOKEN", token),
    )


def _current_token(env_path: Path = CONDOR_ENV) -> str:
    if not env_path.exists():
        raise RuntimeError(f"credential file does not exist: {env_path}")
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        key, separator, value = raw_line.partition("=")
        if separator and key.strip() == "TELEGRAM_TOKEN":
            token = value.strip().strip('"').strip("'")
            if TOKEN_PATTERN.fullmatch(token):
                return token
            raise RuntimeError("TELEGRAM_TOKEN is present but malformed")
    raise RuntimeError("TELEGRAM_TOKEN is not configured")


def verify_current(
    *,
    env_path: Path = CONDOR_ENV,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    """Verify bot identity and webhook state without printing the credential.

    This deliberately avoids ``getUpdates`` because the running Condor process owns
    the long-poll stream.  Calling that method from a health check would itself create
    a competing poller and could produce the conflict we are trying to diagnose.
    """

    token = _current_token(env_path)

    def call(method: str) -> dict[str, Any]:
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/{method}",
            data=b"",
            method="POST",
        )
        try:
            with opener(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"Telegram {method} verification failed with HTTP {exc.code}"
            ) from None
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                f"Telegram {method} verification failed: {type(exc).__name__}"
            ) from None
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram {method} verification returned ok=false")
        return payload.get("result") or {}

    identity = call("getMe")
    webhook = call("getWebhookInfo")
    return {
        "ok": True,
        "bot_id": identity.get("id"),
        "username": identity.get("username"),
        "webhook_configured": bool(webhook.get("url")),
        "pending_update_count": int(webhook.get("pending_update_count") or 0),
        "last_webhook_error": webhook.get("last_error_message") or None,
    }


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify-current",
        action="store_true",
        help="verify bot identity/webhook state without touching the long-poll stream",
    )
    args = parser.parse_args()
    if args.verify_current:
        result = verify_current()
        print(json.dumps(result, sort_keys=True))
        return
    token = getpass.getpass("Fresh BotFather token (hidden): ")
    install(token)
    print("Installed the Telegram token with 0600 permissions; token not printed.")
    print("Restart Condor once to activate it, then verify one successful poll owner.")


if __name__ == "__main__":
    main()
