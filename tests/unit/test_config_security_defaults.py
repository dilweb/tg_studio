"""Дефолты безопасности конфига (P0).

https-публичный URL включает жёсткий режим автоматически: cookie_secure,
выключение /api/docs, явный список CORS-origin и trusted hosts. Без https
(локалка) поведение прежнее. Дефолтный jwt_secret_key не принимается —
генерируется случайный, иначе подписи подделает любой, кто видел код.
"""

from tg_studio.config import Settings


def _settings(**overrides) -> Settings:
    base = {
        "bot_token": "123:test",
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)


def test_local_defaults_stay_soft():
    """Локалка без https: cookies без Secure, без trusted-hosts."""
    # conftest прокидывает MINIAPP_URL/API_PUBLIC_URL=http://testserver —
    # глушим, чтобы тест был про дефолты, а не про тестовое окружение
    s = _settings(public_url="", miniapp_url="", api_public_url="")
    assert s.require_https is False
    assert s.cookie_secure is False
    assert s.trusted_hosts == []
    assert s.cors_allowed_origins == []


def test_https_public_url_enables_hardening():
    s = _settings(
        public_url="https://studio.example.com",
        miniapp_url="https://studio.example.com/app",
        api_public_url="",
    )
    assert s.require_https is True
    assert s.cookie_secure is True
    # CORS: явные origin'ы, без «*» (с credentials браузер «*» отбросит)
    assert s.cors_allowed_origins == ["https://studio.example.com"]
    assert s.trusted_hosts == ["studio.example.com"]


def test_explicit_cookie_secure_overrides_auto():
    s = _settings(public_url="https://studio.example.com", cookie_secure=False)
    assert s.cookie_secure is False


def test_default_jwt_secret_replaced():
    """Дефолтный ключ из репозитория не принимается — генерируется случайный."""
    s = _settings(jwt_secret_key="change-me-in-production")
    assert s.jwt_secret_key != "change-me-in-production"
    assert len(s.jwt_secret_key) >= 32


def test_empty_jwt_secret_generated():
    s = _settings(jwt_secret_key="")
    assert len(s.jwt_secret_key) >= 32


def test_samesite_none_requires_secure():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _settings(cookie_samesite="none", cookie_secure=False)
