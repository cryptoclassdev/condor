from scripts import install_telegram_token as installer


def test_install_replaces_token_atomically_and_keeps_other_values(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "OTHER=value\n"
        "TELEGRAM_TOKEN=123456:old_token_value_abcdefghijklmnopqrstuvwxyz\n"
    )

    installer.install(
        "123456789:replacement_token_value_abcdefghijklmnop",
        env_path=path,
    )

    text = path.read_text()
    assert "OTHER=value\n" in text
    assert (
        "TELEGRAM_TOKEN=123456789:replacement_token_value_abcdefghijklmnop\n"
        in text
    )
    assert "old_token_value" not in text
    assert path.stat().st_mode & 0o777 == 0o600


def test_install_rejects_malformed_token_without_changing_file(tmp_path):
    path = tmp_path / ".env"
    original = "OTHER=value\n"
    path.write_text(original)

    try:
        installer.install("not-a-token", env_path=path)
    except ValueError:
        pass
    else:
        raise AssertionError("malformed token should be rejected")

    assert path.read_text() == original
