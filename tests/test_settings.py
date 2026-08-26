import pytest
from pydantic import ValidationError

from app.settings import Settings


def test_settings_load_from_env_vars(monkeypatch):
    monkeypatch.setenv("TARGET_URL", "https://example.com")
    monkeypatch.setenv("AUTH_USERNAME", "alice")
    monkeypatch.setenv("AUTH_PASSWORD", "s3cret")
    monkeypatch.setenv("CONTEXT_CHARS", "40")
    monkeypatch.setenv("CONCURRENCY", "8")
    monkeypatch.setenv("MAX_PAGES", "250")

    settings = Settings(_env_file=None)

    assert settings.target_url == "https://example.com"
    assert settings.auth_username == "alice"
    assert settings.auth_password == "s3cret"
    assert settings.context_chars == 40
    assert settings.concurrency == 8
    assert settings.max_pages == 250


def test_settings_use_defaults_when_optional_vars_missing(monkeypatch):
    monkeypatch.setenv("TARGET_URL", "https://example.com")
    monkeypatch.setenv("AUTH_USERNAME", "alice")
    monkeypatch.setenv("AUTH_PASSWORD", "s3cret")
    monkeypatch.delenv("CONTEXT_CHARS", raising=False)
    monkeypatch.delenv("CONCURRENCY", raising=False)
    monkeypatch.delenv("MAX_PAGES", raising=False)

    settings = Settings(_env_file=None)

    assert settings.context_chars == 80
    assert settings.concurrency == 4
    assert settings.max_pages == 100


def test_settings_missing_required_var_raises_clear_error(monkeypatch):
    monkeypatch.delenv("TARGET_URL", raising=False)
    monkeypatch.delenv("AUTH_USERNAME", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    missing_fields = {error["loc"][0] for error in exc_info.value.errors()}
    assert missing_fields == {"TARGET_URL", "AUTH_USERNAME", "AUTH_PASSWORD"}
