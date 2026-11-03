import pytest

from tests.conftest import INTERVIEWER

PORTAL_ROUTES = [
    ("get", "/api/me"),
    ("get", "/api/sessions"),
    ("post", "/api/sessions"),
    ("get", "/api/sessions/abc"),
    ("post", "/api/sessions/abc/end"),
]


@pytest.mark.parametrize(("method", "path"), PORTAL_ROUTES)
def test_portal_routes_require_sign_in(client, method, path):
    kwargs = {"json": {"candidate_name": "x"}} if method == "post" else {}
    assert getattr(client, method)(path, **kwargs).status_code == 401


def test_shared_routes_require_pin_or_owner(client, created_session):
    sid = created_session["id"]
    assert client.get(f"/api/sessions/{sid}/files").status_code == 401
    assert client.get(f"/api/sessions/{sid}/changes").status_code == 401


def test_me_returns_google_profile(interviewer):
    response = interviewer.get("/api/me")
    assert response.status_code == 200
    assert response.json() == {"name": INTERVIEWER["name"], "email": INTERVIEWER["email"], "picture": None}


def test_google_login_redirects_with_pkce(client, monkeypatch, app):
    async def fake_load_metadata():
        return {
            "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_endpoint": "https://oauth2.googleapis.com/token",
            "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
            "issuer": "https://accounts.google.com",
        }

    monkeypatch.setattr(app.state.oauth.google, "load_server_metadata", fake_load_metadata)
    response = client.get("/auth/google", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth")
    assert "code_challenge_method=S256" in location
    assert "code_challenge=" in location
    assert "redirect_uri=http%3A%2F%2Ftestserver%2Fauth%2Fgoogle%2Fcallback" in location


def test_logout_clears_session(interviewer):
    assert interviewer.get("/api/me").status_code == 200
    response = interviewer.get("/auth/logout", follow_redirects=False)
    assert response.status_code == 303
    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith("wmc_session=null")
    assert "expires=thu, 01 jan 1970" in set_cookie.lower()
