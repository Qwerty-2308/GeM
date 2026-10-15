"""Website mode: served beyond this computer, so every API call needs a login (Google, a
password, or both). Never contacts GeM, Groq or Google."""

from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from gem_assistant.api.app import create_app
from gem_assistant.api.auth import COOKIE, GoogleLogin, Login, Sessions

PASSWORD = "correct horse battery"
SECRET = b"s" * 32
CLIENT_ID = "123.apps.googleusercontent.com"
SITE = "https://gem.example.com"


def _client(container, **login) -> TestClient:
    # https: the session cookie is Secure, so a browser only sends it over https.
    return TestClient(create_app(container, login=Login.build(SECRET, **login)), base_url=SITE)


@pytest.fixture
def web(container):
    return _client(container, password=PASSWORD)


def _login(web, password=PASSWORD):
    return web.post("/api/login", json={"password": password})


# ---- password ------------------------------------------------------------------------------

def test_the_api_needs_a_login_but_the_page_itself_loads(web):
    assert web.get("/api/bids").status_code == 401
    assert web.get("/api/chat").status_code == 401
    assert web.get("/api/session").json() == {"login_required": True, "logged_in": False, "methods": ["password"]}
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
    assert web.get("/api/session").json()["logged_in"] is True
    web.post("/api/logout")
    assert web.get("/api/bids").status_code == 401


def test_a_forged_or_expired_session_is_refused(container):
    login = Login(Sessions(SECRET, "b", now=lambda: 1_000_000), password=PASSWORD)
    client = TestClient(create_app(container, login=login), base_url=SITE)
    expired = Sessions(SECRET, "b", now=lambda: 1_000_000 - 31 * 86400).issue()
    for token in ("9999999999.forged", expired, ""):
        client.cookies.set(COOKIE, token)
        assert client.get("/api/bids").status_code == 401


def test_changing_the_password_or_the_allowed_emails_logs_everyone_out(web):
    _login(web)
    token = web.cookies.get(COOKIE)
    assert Login.build(SECRET, password=PASSWORD).sessions.valid(token)
    assert not Login.build(SECRET, password="a different long password").sessions.valid(token)
    google = GoogleLogin(CLIENT_ID, "secret", frozenset({"father@gmail.com"}))
    assert not Login.build(SECRET, password=PASSWORD, google=google).sessions.valid(token)


def test_another_website_cannot_post_with_his_session(web):
    _login(web)
    response = web.post("/api/chat", json={"messages": []}, headers={"origin": "https://evil.example"})
    assert response.status_code == 403


def test_https_is_remembered_by_the_browser(web):
    assert "max-age" in web.get("/").headers["strict-transport-security"]


# ---- Google ---------------------------------------------------------------------------------

def _id_token(**claims):
    payload = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "exp": 2_000_000_000,
               "email": "Father@Gmail.com", "email_verified": True, **claims}

    def part(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'RS256'})}.{part(payload)}.signature"


class FakeGoogle:
    """Google's token endpoint: answers with an ID token carrying the nonce it was sent."""

    def __init__(self, **claims):
        self.claims = claims
        self.nonce = None
        self.requests: list[httpx.Request] = []

    def __call__(self, request):
        self.requests.append(request)
        return httpx.Response(200, json={"id_token": _id_token(**{"nonce": self.nonce, **self.claims})})


def _google_site(container, fake, password=None):
    google = GoogleLogin(CLIENT_ID, "client-secret", frozenset({"father@gmail.com", "son@gmail.com"}),
                         transport=httpx.MockTransport(fake))
    return _client(container, password=password, google=google)


def _sign_in(web, fake, code="the-code"):
    start = web.get("/api/auth/google", follow_redirects=False)
    query = parse_qs(urlparse(start.headers["location"]).query)
    fake.nonce = query["nonce"][0]
    return start, query, web.get(f"/api/auth/google/callback?code={code}&state={query['state'][0]}",
                                 follow_redirects=False)


def test_signing_in_with_an_allowed_google_account(container):
    fake = FakeGoogle()
    web = _google_site(container, fake)
    assert web.get("/api/session").json()["methods"] == ["google"]

    start, query, callback = _sign_in(web, fake)
    assert start.headers["location"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert (query["client_id"], query["response_type"], query["scope"], query["redirect_uri"]) == (
        [CLIENT_ID], ["code"], ["openid email"], [f"{SITE}/api/auth/google/callback"])
    assert "samesite=lax" in start.headers["set-cookie"].lower()  # must survive the trip back from Google

    assert callback.headers["location"] == "/"
    assert web.get("/api/bids").status_code == 200
    (token_request,) = fake.requests
    assert str(token_request.url) == "https://oauth2.googleapis.com/token"
    sent = parse_qs(token_request.content.decode())
    assert (sent["code"], sent["client_secret"], sent["grant_type"], sent["redirect_uri"]) == (
        ["the-code"], ["client-secret"], ["authorization_code"], [f"{SITE}/api/auth/google/callback"])


def test_a_google_account_not_on_the_list_is_refused(container):
    fake = FakeGoogle(email="stranger@gmail.com")
    web = _google_site(container, fake)
    *_, callback = _sign_in(web, fake)
    assert callback.headers["location"] == "/?login_error=not_allowed"
    assert web.get("/api/bids").status_code == 401


@pytest.mark.parametrize("claims", [
    {"aud": "someone-elses-app"}, {"exp": 1000}, {"nonce": "replayed"},
    {"iss": "https://evil.example"}, {"email_verified": False},
])
def test_a_token_that_does_not_check_out_is_refused(container, claims):
    fake = FakeGoogle(**claims)
    web = _google_site(container, fake)
    *_, callback = _sign_in(web, fake)
    assert callback.headers["location"].startswith("/?login_error=")
    assert web.get("/api/bids").status_code == 401


def test_a_sign_in_not_started_here_is_refused(container):
    """Login CSRF: someone else's code, sent to him, must not log him in to their session."""
    fake = FakeGoogle()
    web = _google_site(container, fake)
    web.get("/api/auth/google", follow_redirects=False)
    callback = web.get("/api/auth/google/callback?code=x&state=not-the-one-we-sent", follow_redirects=False)
    assert callback.headers["location"] == "/?login_error=failed"
    assert fake.requests == []  # never even asked Google
    assert web.get("/api/bids").status_code == 401


def test_with_only_google_no_password_opens_anything(container):
    web = _google_site(container, FakeGoogle())
    assert _login(web, "").status_code == 404
    assert web.get("/api/bids").status_code == 401


def test_google_and_password_can_both_be_offered(container):
    web = _google_site(container, FakeGoogle(), password=PASSWORD)
    assert web.get("/api/session").json()["methods"] == ["google", "password"]
    assert _login(web).status_code == 200


# ---- starting it ----------------------------------------------------------------------------

def _serve(monkeypatch, tmp_path, **env):
    import uvicorn

    from gem_assistant import cli

    monkeypatch.setenv("GEM_DATA_DIR", str(tmp_path))
    for name in ("GEM_PASSWORD", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GEM_ALLOWED_EMAILS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    served = []
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port: served.append((app, host, port)))
    return cli.main(["serve", "--host", "0.0.0.0", "--port", "8080", "--no-open"]), served


@pytest.mark.parametrize("env", [
    {},
    {"GEM_PASSWORD": "short"},
    {"GOOGLE_CLIENT_ID": CLIENT_ID, "GOOGLE_CLIENT_SECRET": "s"},  # no allowed emails: anyone could log in
    {"GOOGLE_CLIENT_ID": CLIENT_ID, "GEM_ALLOWED_EMAILS": "father@gmail.com"},  # no secret
])
def test_serving_as_a_website_needs_a_real_login(monkeypatch, tmp_path, env):
    code, served = _serve(monkeypatch, tmp_path, **env)
    assert (code, served) == (2, [])


@pytest.mark.parametrize("env, methods", [
    ({"GEM_PASSWORD": PASSWORD}, ["password"]),
    ({"GOOGLE_CLIENT_ID": CLIENT_ID, "GOOGLE_CLIENT_SECRET": "s",
      "GEM_ALLOWED_EMAILS": " Father@gmail.com, son@gmail.com "}, ["google"]),
])
def test_serving_as_a_website_with_a_login(monkeypatch, tmp_path, env, methods):
    code, served = _serve(monkeypatch, tmp_path, **env)
    ((app, host, port),) = served
    assert (code, host, port) == (0, "0.0.0.0", 8080)
    client = TestClient(app, base_url="https://any.host.example")
    assert client.get("/api/bids").status_code == 401
    assert client.get("/api/session").json()["methods"] == methods
    secret = tmp_path / "session-secret.txt"  # kept, so logins survive a restart
    assert secret.stat().st_mode & 0o777 == 0o600
