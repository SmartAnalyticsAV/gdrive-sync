import json

import pytest

from gdrive_sync import auth, config


def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("GDRIVE_SYNC_HOME", str(tmp_path))
    (tmp_path).mkdir(parents=True, exist_ok=True)


def test_missing_client_secret(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    with pytest.raises(auth.AuthError, match="client_secret"):
        auth.get_credentials(interactive=True)


def test_service_account_rejected(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    config.client_secret_path().write_text(
        json.dumps({"type": "service_account", "private_key": "x", "client_email": "a@b"})
    )
    with pytest.raises(auth.AuthError, match="Desktop app"):
        auth.get_credentials(interactive=True)


def test_non_interactive_without_token(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    with pytest.raises(auth.AuthError, match="интерактивно"):
        auth.get_credentials(interactive=False)
