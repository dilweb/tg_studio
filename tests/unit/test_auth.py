"""
Unit tests for Telegram initData authentication (api/auth.py).

No database or network connections required.
"""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from fastapi import HTTPException

from tg_studio.api.auth import (
    INIT_DATA_MAX_AGE_SECONDS,
    _validate_init_data,
)
from tg_studio.config import settings

BOT_TOKEN: str = settings.bot_token  # set to test value via conftest env vars

VALID_USER = {"id": 12345, "first_name": "Alice", "last_name": "Smith"}


def _make_init_data(
    user: dict,
    bot_token: str | None = None,
    age_seconds: int = 0,
) -> str:
    """Build a signed initData string (valid or intentionally tampered)."""
    token = bot_token or BOT_TOKEN
    auth_date = int(time.time()) - age_seconds
    params = {
        "auth_date": str(auth_date),
        "user": json.dumps(user, separators=(",", ":")),
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    params["hash"] = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(params)


class TestValidateInitData:
    def test_valid_signature_returns_user_dict(self):
        init_data = _make_init_data(VALID_USER)
        user = _validate_init_data(init_data, BOT_TOKEN)
        assert user["id"] == VALID_USER["id"]
        assert user["first_name"] == "Alice"

    def test_missing_hash_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_init_data("auth_date=1234567890&user=%7B%22id%22%3A1%7D", BOT_TOKEN)
        assert exc_info.value.status_code == 401
        assert "hash" in exc_info.value.detail.lower()

    def test_invalid_signature_raises_401(self):
        init_data = _make_init_data(VALID_USER) + "tampered"
        with pytest.raises(HTTPException) as exc_info:
            _validate_init_data(init_data, BOT_TOKEN)
        assert exc_info.value.status_code == 401

    def test_wrong_bot_token_raises_401(self):
        init_data = _make_init_data(VALID_USER, bot_token="9999:completely_wrong_token")
        with pytest.raises(HTTPException) as exc_info:
            _validate_init_data(init_data, BOT_TOKEN)
        assert exc_info.value.status_code == 401

    def test_expired_data_raises_401(self):
        age = INIT_DATA_MAX_AGE_SECONDS + 3600  # 25 hours old
        init_data = _make_init_data(VALID_USER, age_seconds=age)
        with pytest.raises(HTTPException) as exc_info:
            _validate_init_data(init_data, BOT_TOKEN)
        assert exc_info.value.status_code == 401
        assert "expired" in exc_info.value.detail.lower()

    def test_no_user_field_raises_401(self):
        auth_date = str(int(time.time()))
        params = {"auth_date": auth_date}
        data_check_string = f"auth_date={auth_date}"
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        params["hash"] = hmac.new(
            secret_key, data_check_string.encode(), hashlib.sha256
        ).hexdigest()
        with pytest.raises(HTTPException) as exc_info:
            _validate_init_data(urlencode(params), BOT_TOKEN)
        assert exc_info.value.status_code == 401
        assert "user" in exc_info.value.detail.lower()

    def test_fresh_valid_data_does_not_raise(self):
        """Just-created initData (0 seconds old) must pass expiry check."""
        init_data = _make_init_data(VALID_USER, age_seconds=0)
        user = _validate_init_data(init_data, BOT_TOKEN)
        assert user["id"] == VALID_USER["id"]


