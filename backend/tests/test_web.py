"""Website mode: served beyond this computer, so every API call needs a login.
Never contacts GeM or Groq."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gem_assistant.api.app import create_app
from gem_assistant.api.auth import COOKIE, Sessions

PASSWORD = "correct horse battery"
SECRET = b"s" * 32


@pytest.fixture
def web(container):
    # https: the session cookie is Secure, so a browser only sends it over https.
    return TestClient(create_app(container, sessions=Sessions(PASSWORD, SECRET)), base_url="https://gem.example.com")


def _login(web, password=PASSWORD):
    return web.post("/api/login", json={"password": password})


def test_the_api_needs_a_login_but_the_page_itself_loads(web):
    assert web.get("/api/bids").status_code == 401
    assert web.get("/api/chat").status_code == 401
    assert web.get("/api/session").json() == {"login_required": True, "logged_in": False}
    assert web.get("/").status_code == 200  # the page, to show the login form


def test_a_wrong_password_is_refused_and_costs_a_second(web, monkeypatch):
    import gem_assistant.api.routes.session as session_route

    slept = []
    monkeypatch.setattr(session_route.time, "sleep", slept.append)
    assert _login(web, "guess").status_code == 401
    assert slept == [1.0]
    assert web.get("/api/bids").status_code == 401


def test_logging_in_opens_the_api_and_logging_out_closes_it(web):
    response = _login(web)
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert all(flag in cookie for flag in ("httponly", "secure", "samesite=strict"))
    assert web.get("/api/bids").status_code == 200
    assert web.get("/api/session").json() == {"login_required": True, "logged_in": True}
    web.post("/api/logout")
    assert web.get("/api/bids").status_code == 401


def test_a_forged_or_expired_session_is_refused(container):
    sessions = Sessions(PASSWORD, SECRET, now=lambda: 1_000_000)
    client = TestClient(create_app(container, sessions=sessions), base_url="https://gem.example.com")
    expired = Sessions(PASSWORD, SECRET, now=lambda: 1_000_000 - 31 * 86400).issue()
    for token in ("9999999999.forged", expired, ""):
        client.cookies.set(COOKIE, token)
        assert client.get("/api/bids").status_code == 401


def test_changing_the_password_logs_everyone_out(container, web):
    _login(web)
    token = web.cookies.get(COOKIE)
    assert Sessions(PASSWORD, SECRET).valid(token)
    assert not Sessions("a different long password", SECRET).valid(token)


def test_another_website_cannot_post_with_his_session(web):
    _login(web)
    response = web.post("/api/chat", json={"messages": []}, headers={"origin": "https://evil.example"})
    assert response.status_code == 403


def test_https_is_remembered_by_the_browser(web):
    assert "max-age" in web.get("/").headers["strict-transport-security"]


# ---- starting it ----------------------------------------------------------------------------

@pytest.mark.parametrize("password", [None, "short"])
def test_serving_as_a_website_needs_a_real_password(monkeypatch, tmp_path, password):
    import uvicorn

    from gem_assistant import cli

    monkeypatch.setenv("GEM_DATA_DIR", str(tmp_path))
    if password:
        monkeypatch.setenv("GEM_PASSWORD", password)
    else:
        monkeypatch.delenv("GEM_PASSWORD", raising=False)
    served = []
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port: served.append(host))
    assert cli.main(["serve", "--host", "0.0.0.0", "--no-open"]) == 2
    assert served == []


def test_serving_as_a_website_with_a_password(monkeypatch, tmp_path):
    import uvicorn

    from gem_assistant import cli

    monkeypatch.setenv("GEM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GEM_PASSWORD", PASSWORD)
    served = []
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port: served.append((app, host, port)))
    assert cli.main(["serve", "--host", "0.0.0.0", "--port", "8080", "--no-open"]) == 0
    ((app, host, port),) = served
    assert (host, port) == ("0.0.0.0", 8080)
    assert TestClient(app, base_url="https://any.host.example").get("/api/bids").status_code == 401
    secret = tmp_path / "session-secret.txt"  # kept, so logins survive a restart
    assert secret.stat().st_mode & 0o777 == 0o600
