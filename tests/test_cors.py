from fastapi.testclient import TestClient

from app.main import app


def test_web_origin_preflight_is_allowed_without_auth() -> None:
    with TestClient(app) as client:
        response = client.options(
            "/v1/usage",
            headers={
                "Origin": "https://dailyopic.vercel.app",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,x-firebase-appcheck",
            },
        )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://dailyopic.vercel.app"


def test_unknown_origin_gets_no_cors_header() -> None:
    with TestClient(app) as client:
        response = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers
