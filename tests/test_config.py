import pytest

from clicoding_orchestrator.config import Settings


def test_settings_load_per_user_api_keys_and_trusted_identity_flag(monkeypatch):
    monkeypatch.setenv("CLICODING_USER_API_KEYS", '{"alice":"' + ("a" * 40) + '"}')
    monkeypatch.setenv("CLICODING_TRUST_USER_HEADER", "true")
    settings = Settings.from_env()
    assert settings.user_api_keys == {"alice": "a" * 40}
    assert settings.trust_user_header is True


def test_settings_reject_invalid_user_api_key_configuration(monkeypatch):
    monkeypatch.setenv("CLICODING_USER_API_KEYS", "not-json")
    with pytest.raises(ValueError, match="JSON object"):
        Settings.from_env()

    with pytest.raises(ValueError, match="at least 32"):
        Settings(user_api_keys={"alice": "short"})
