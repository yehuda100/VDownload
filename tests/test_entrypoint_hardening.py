"""Webhook secret and API access-log wiring."""
from unittest.mock import patch

import config
from main import run_api, webhook_secret, webhook_settings


def test_webhook_path_is_not_the_bot_token():
    settings = webhook_settings()
    assert settings["url_path"] == "telegram-webhook"
    assert settings["url_path"] != config.BOT_TOKEN
    assert config.BOT_TOKEN not in settings["webhook_url"]
    assert settings["webhook_url"] == config.URL + "telegram-webhook"
    assert settings["secret_token"]
    assert config.BOT_TOKEN not in settings["secret_token"]


def test_webhook_secret_uses_config_when_it_matches_telegram_rules(monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "deploy-secret_1", raising=False)
    assert webhook_secret() == "deploy-secret_1"


def test_webhook_secret_ignores_an_unsafe_configured_value(monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "has spaces", raising=False)
    derived = webhook_secret()
    assert derived != "has spaces"
    assert len(derived) == 32
    assert derived.isalnum()


def test_run_api_disables_the_access_log():
    with patch("main.uvicorn.run") as run:
        run_api()
    assert run.call_args.kwargs["access_log"] is False
    assert run.call_args.kwargs["host"] == "127.0.0.1"
    assert run.call_args.kwargs["port"] == 5000
