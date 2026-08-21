from pathlib import Path

from scripts import rotate_hummingbot_api_password as rotation


def test_replace_env_value_updates_exact_key_only():
    text = "USERNAME=admin\nPASSWORD=old\nBROKER_PASSWORD=leave-me\n"

    updated = rotation._replace_env_value(text, "PASSWORD", "new")

    assert "PASSWORD=new\n" in updated
    assert "BROKER_PASSWORD=leave-me\n" in updated
    assert "PASSWORD=old" not in updated


def test_atomic_write_is_owner_only(tmp_path):
    path = tmp_path / "secret.env"

    rotation._atomic_owner_only(path, "PASSWORD=hidden\n")

    assert path.read_text() == "PASSWORD=hidden\n"
    assert path.stat().st_mode & 0o777 == 0o600
