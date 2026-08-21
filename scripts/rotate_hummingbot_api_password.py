#!/usr/bin/env python3
"""Rotate the local Hummingbot API password without printing it.

The Hummingbot Docker environment and Condor server registry must change as one
operation.  This script generates the credential internally, writes both files
atomically with owner-only permissions, and emits only non-sensitive status.
"""

from __future__ import annotations

import os
import secrets
import tempfile
from pathlib import Path

import yaml


CONDOR_CONFIG = Path(__file__).resolve().parents[1] / "config.yml"
HUMMINGBOT_ENV = Path.home() / "hummingbot-api" / ".env"


def _replace_env_value(text: str, key: str, value: str) -> str:
    lines = text.splitlines()
    replacement = f"{key}={value}"
    found = False
    for index, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[index] = replacement
            found = True
            break
    if not found:
        lines.append(replacement)
    return "\n".join(lines) + "\n"


def _atomic_owner_only(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", text=True
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        path.chmod(0o600)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def rotate() -> None:
    password = secrets.token_urlsafe(36)

    env_text = HUMMINGBOT_ENV.read_text(encoding="utf-8")
    config = yaml.safe_load(CONDOR_CONFIG.read_text(encoding="utf-8")) or {}
    servers = config.get("servers") or {}
    if "local" not in servers:
        raise RuntimeError("Condor config has no local Hummingbot server")

    servers["local"]["password"] = password
    config["servers"] = servers

    _atomic_owner_only(
        HUMMINGBOT_ENV,
        _replace_env_value(env_text, "PASSWORD", password),
    )
    _atomic_owner_only(
        CONDOR_CONFIG,
        yaml.safe_dump(config, sort_keys=False),
    )
    print("Rotated Hummingbot API password in both local configurations.")
    print("Permissions set to 0600; the new credential was not printed.")
    print(
        "Before restarting Hummingbot API, confirm there are zero RUNNING executors: "
        "an API container recreation marks live executors SYSTEM_CLEANUP without "
        "closing their on-chain LP positions."
    )


if __name__ == "__main__":
    rotate()
