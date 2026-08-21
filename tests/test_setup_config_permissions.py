from pathlib import Path


def test_setup_locks_config_before_writing_credentials():
    script = (Path(__file__).parents[1] / "setup-environment.sh").read_text()
    secure = 'chmod 600 "$CONFIG_FILE"'
    write = 'cat > "$CONFIG_FILE"'

    assert secure in script
    assert script.index(secure) < script.index(write)
