import json

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


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps(self.payload).encode()


def test_verify_current_is_read_only_and_does_not_call_get_updates(tmp_path):
    path = tmp_path / ".env"
    token = "123456789:replacement_token_value_abcdefghijklmnop"
    path.write_text(f"OTHER=value\nTELEGRAM_TOKEN={token}\n")
    original = path.read_bytes()
    methods = []

    def opener(request, timeout):
        assert timeout == 10
        methods.append(request.full_url.rsplit("/", 1)[-1])
        if methods[-1] == "getMe":
            return _Response(
                {"ok": True, "result": {"id": 42, "username": "condor_bot"}}
            )
        return _Response(
            {
                "ok": True,
                "result": {"url": "", "pending_update_count": 3},
            }
        )

    result = installer.verify_current(env_path=path, opener=opener)

    assert result == {
        "ok": True,
        "bot_id": 42,
        "username": "condor_bot",
        "webhook_configured": False,
        "pending_update_count": 3,
        "last_webhook_error": None,
    }
    assert methods == ["getMe", "getWebhookInfo"]
    assert "getUpdates" not in methods
    assert path.read_bytes() == original


def test_current_token_accepts_quoted_env_value(tmp_path):
    path = tmp_path / ".env"
    token = "123456789:replacement_token_value_abcdefghijklmnop"
    path.write_text(f'TELEGRAM_TOKEN="{token}"\n')
    assert installer._current_token(path) == token
