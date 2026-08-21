#!/usr/bin/env python3
"""Install a freshly revoked/reissued Telegram bot token without exposing it.

BotFather must perform the actual revoke/reissue because it changes an account
credential. Once the operator has the replacement, this helper reads it from a
hidden terminal prompt, atomically updates Condor's ignored ``.env``, and keeps
the file owner-only. The token never appears in argv or normal output.
"""

from __future__ import annotations

import getpass
import re
from pathlib import Path

from scripts.rotate_hummingbot_api_password import (
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


def main() -> None:
    token = getpass.getpass("Fresh BotFather token (hidden): ")
    install(token)
    print("Installed the Telegram token with 0600 permissions; token not printed.")
    print("Restart Condor once to activate it, then verify one successful poll owner.")


if __name__ == "__main__":
    main()
