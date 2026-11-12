import time

import jwt

from .helpers import PASSWORD, bearer

ACCOUNT = {"name": "Dana Reviewer", "email": "dana@example.com", "password": PASSWORD}
SECRET = "test-secret-test-secret-test-secret-test-secret"


async def register(client, **overrides):
    return await client.post("/api/auth/register", json={**ACCOUNT, **overrides})


class TestRegistration:
    async def test_creates_an_interviewer_and_never_returns_the_hash(self, client):
        response = await register(client)
        body = response.json()
        assert response.status_code == 201
        assert body["user"]["role"] == "INTERVIEWER"
        assert body["token"]
        assert "passwordHash" not in response.text and "password_hash" not in response.text

    async def test_sets_an_httponly_session_cookie(self, client):
        cookie = (await register(client)).headers["set-cookie"]
        assert "im_session=" in cookie
        assert "httponly" in cookie.lower()
        assert "samesite=lax" in cookie.lower()

    async def test_rejects_duplicates_ignoring_email_case(self, client):
        await register(client)
        assert (await register(client, email="DANA@example.com")).status_code == 409

    async def test_rejects_weak_passwords_and_bad_emails(self, client):
        assert (await register(client, password="short")).status_code == 400
        assert (await register(client, email="nope")).status_code == 400
        assert (await register(client, password="x" * 73)).status_code == 400

    async def test_validation_error_shape(self, client):
        body = (await register(client, password="short")).json()
        assert body["error"]["code"] == "validation_error"
        assert body["error"]["details"][0]["message"] == "Password must be at least 10 characters"

    async def test_cannot_self_assign_a_role_and_needs_the_right_code_for_admin(self, client):
        injected = await register(client, role="ADMIN")
        assert injected.json()["user"]["role"] == "INTERVIEWER"
        wrong = await register(client, email="b@example.com", adminCode="guess")
        assert wrong.status_code == 403
        right = await register(client, email="c@example.com", adminCode="test-admin-code")
        assert right.json()["user"]["role"] == "ADMIN"


class TestLogin:
    async def test_accepts_correct_credentials(self, client):
        await register(client)
        response = await client.post("/api/auth/login", json={"email": ACCOUNT["email"], "password": PASSWORD})
        assert response.status_code == 200
        assert response.json()["user"]["email"] == ACCOUNT["email"]

    async def test_same_error_for_wrong_password_and_unknown_email(self, client):
        await register(client)
        wrong = await client.post("/api/auth/login", json={"email": ACCOUNT["email"], "password": "wrong password"})
        unknown = await client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "wrong password"})
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json() == unknown.json()

    async def test_limits_repeated_attempts(self, client):
        await register(client)
        last = 0
        for _ in range(11):
            last = (
                await client.post("/api/auth/login", json={"email": ACCOUNT["email"], "password": "wrong password"})
            ).status_code
        assert last == 429

    async def test_requires_a_json_content_type(self, client):
        response = await client.post(
            "/api/auth/login",
            content="email=a&password=b",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 415

    async def test_rejects_malformed_json_and_oversized_bodies(self, client):
        bad = await client.post("/api/auth/login", content="{nope", headers={"content-type": "application/json"})
        assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_json"
        big = await client.post(
            "/api/auth/login", content=b'{"email":"a@b.co","password":"' + b"x" * 70_000 + b'"}',
            headers={"content-type": "application/json"},
        )
        assert big.status_code == 413


class TestSessions:
    async def test_authenticates_by_bearer_token_and_by_cookie(self, client):
        token = (await register(client)).json()["token"]
        client.cookies.clear()
        assert (await client.get("/api/auth/me", headers=bearer(token))).status_code == 200
        assert (await client.get("/api/auth/me", cookies={"im_session": token})).status_code == 200

    async def test_rejects_missing_forged_wrong_audience_and_expired_tokens(self, client):
        token = (await register(client)).json()["token"]
        client.cookies.clear()
        assert (await client.get("/api/auth/me")).status_code == 401
        assert (await client.get("/api/auth/me", headers=bearer("garbage"))).status_code == 401
        assert (await client.get("/api/auth/me", headers=bearer(token[:-3] + "abc"))).status_code == 401

        claims = jwt.decode(token, SECRET, algorithms=["HS256"], audience="interviewer")
        forged = jwt.encode(claims, "another-secret-another-secret-another", algorithm="HS256")
        assert (await client.get("/api/auth/me", headers=bearer(forged))).status_code == 401

        candidate_audience = jwt.encode({**claims, "aud": "candidate"}, SECRET, algorithm="HS256")
        assert (await client.get("/api/auth/me", headers=bearer(candidate_audience))).status_code == 401

        expired = jwt.encode({**claims, "exp": int(time.time()) - 60}, SECRET, algorithm="HS256")
        assert (await client.get("/api/auth/me", headers=bearer(expired))).status_code == 401

    async def test_logout_invalidates_existing_tokens_not_only_the_cookie(self, client):
        token = (await register(client)).json()["token"]
        assert (await client.post("/api/auth/logout", headers=bearer(token), json={})).status_code == 200
        client.cookies.clear()
        assert (await client.get("/api/auth/me", headers=bearer(token))).status_code == 401

    async def test_tokens_from_the_previous_backend_format_still_work(self, client):
        """Same claims as jose produced: sub, ver, iss, aud, iat, exp."""
        response = await register(client)
        user_id = response.json()["user"]["id"]
        now = int(time.time())
        legacy = jwt.encode(
            {"ver": 0, "sub": user_id, "iss": "interview-monitoring", "aud": "interviewer", "iat": now, "exp": now + 600},
            SECRET,
            algorithm="HS256",
        )
        client.cookies.clear()
        assert (await client.get("/api/auth/me", headers=bearer(legacy))).status_code == 200


class TestMisc:
    async def test_health_and_security_headers(self, client):
        response = await client.get("/api/health")
        assert response.status_code == 200 and response.json() == {"status": "ok"}
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"

    async def test_unknown_route_uses_the_error_shape(self, client):
        response = await client.get("/api/nope")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    async def test_openapi_documents_every_endpoint(self, client):
        schema = (await client.get("/openapi.json")).json()
        paths = {(method.upper(), path) for path, item in schema["paths"].items() for method in item}
        assert paths == {
            ("POST", "/api/auth/register"),
            ("POST", "/api/auth/login"),
            ("POST", "/api/auth/logout"),
            ("GET", "/api/auth/me"),
            ("POST", "/api/interviews"),
            ("GET", "/api/interviews"),
            ("GET", "/api/interviews/{id}"),
            ("POST", "/api/interviews/{id}/join"),
            ("POST", "/api/interviews/{id}/end"),
            ("GET", "/api/interviews/{id}/events"),
            ("POST", "/api/livekit/token"),
            ("POST", "/api/livekit/webhook"),
            ("POST", "/api/monitoring/events"),
            ("POST", "/api/monitoring/status"),
            ("GET", "/api/analytics/overview"),
            ("GET", "/api/health"),
        }


class TestCors:
    async def test_allows_only_configured_origins_with_credentials(self, monkeypatch):
        import httpx

        from app.config import get_settings
        from app.main import create_app

        monkeypatch.setenv("CORS_ORIGINS", "https://dash.example.com/")
        get_settings.cache_clear()
        try:
            app = create_app()
        finally:
            monkeypatch.delenv("CORS_ORIGINS")
            get_settings.cache_clear()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            ok = await http.options(
                "/api/auth/me", headers={"Origin": "https://dash.example.com", "Access-Control-Request-Method": "GET"}
            )
            assert ok.status_code == 200
            assert ok.headers["access-control-allow-origin"] == "https://dash.example.com"
            assert ok.headers["access-control-allow-credentials"] == "true"
            bad = await http.options(
                "/api/auth/me", headers={"Origin": "https://evil.example.com", "Access-Control-Request-Method": "GET"}
            )
            assert "access-control-allow-origin" not in bad.headers
