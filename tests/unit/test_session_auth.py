"""Unit tests: JWT cookies + response headers (auth helpers)."""

from starlette.responses import Response

from tg_studio.api.auth import (
    ACCESS_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
    REFRESH_TOKEN_HEADER,
    attach_auth_to_response,
    attach_auth_token_headers,
    clear_auth_cookies,
)


def test_attach_auth_token_headers_sets_bearer_and_refresh():
    r = Response()
    attach_auth_token_headers(r, "access_jwt", "refresh_jwt")
    assert r.headers["Authorization"] == "Bearer access_jwt"
    assert r.headers[REFRESH_TOKEN_HEADER] == "refresh_jwt"


def test_attach_auth_to_response_sets_cookies_and_headers():
    r = Response()
    attach_auth_to_response(r, "atok", "rtok")
    assert r.headers["Authorization"] == "Bearer atok"
    assert r.headers[REFRESH_TOKEN_HEADER] == "rtok"
    set_cookies = r.headers.getlist("set-cookie")
    assert set_cookies
    joined = " ".join(set_cookies)
    assert f"{ACCESS_COOKIE_NAME}=atok" in joined
    assert f"{REFRESH_COOKIE_NAME}=rtok" in joined
    assert "HttpOnly" in joined


def test_clear_auth_cookies_emits_delete():
    r = Response()
    clear_auth_cookies(r)
    set_cookies = r.headers.getlist("set-cookie")
    assert len(set_cookies) == 2
    joined = " ".join(set_cookies).lower()
    assert ACCESS_COOKIE_NAME in joined
    assert REFRESH_COOKIE_NAME in joined
    assert "max-age=0" in joined
