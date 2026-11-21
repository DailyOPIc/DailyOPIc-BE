from fastapi.testclient import TestClient

from app.main import app


USER_ID = "66666666-6666-4666-8666-666666666666"
OTHER_USER_ID = "77777777-7777-4777-8777-777777777777"


def _headers(uid: str) -> dict[str, str]:
    return {"X-DailyOPIc-User-ID": uid, "X-Firebase-AppCheck": "test-app-check-token"}


def _plan() -> dict[str, object]:
    return {
        "studyWeekdays": [1, 2, 3],
        "intensity": "steady",
        "preferredStudyTime": "21:00",
        "timezoneIdentifier": "Asia/Seoul",
    }


def test_delete_account_removes_only_my_data_and_firebase_user(monkeypatch) -> None:
    deleted: list[str] = []
    monkeypatch.setattr("app.services.auth.auth.delete_user", deleted.append)
    with TestClient(app) as client:
        for uid in (USER_ID, OTHER_USER_ID):
            assert client.put("/v1/users/me/study-plan", json=_plan(), headers=_headers(uid)).status_code == 200

        response = client.delete("/v1/users/me", headers=_headers(USER_ID))
        assert response.status_code == 204
        assert deleted == [USER_ID]

        mine = client.get("/v1/users/me/study-plan", headers=_headers(USER_ID)).json()
        others = client.get("/v1/users/me/study-plan", headers=_headers(OTHER_USER_ID)).json()
        assert mine["configured"] is False
        assert others["configured"] is True

        # 다시 호출해도 안전(이미 지운 계정)
        assert client.delete("/v1/users/me", headers=_headers(USER_ID)).status_code == 204


def test_delete_account_treats_missing_firebase_user_as_done(monkeypatch) -> None:
    from firebase_admin import auth

    def missing(uid: str) -> None:
        raise auth.UserNotFoundError("gone")

    monkeypatch.setattr("app.services.auth.auth.delete_user", missing)
    with TestClient(app) as client:
        assert client.delete("/v1/users/me", headers=_headers(USER_ID)).status_code == 204


def test_delete_account_requires_app_check() -> None:
    with TestClient(app) as client:
        assert client.delete("/v1/users/me", headers={"X-DailyOPIc-User-ID": USER_ID}).status_code == 403


def test_delete_account_removes_web_pass_and_tokens_but_keeps_orders(monkeypatch) -> None:
    import asyncio

    monkeypatch.setattr("app.services.auth.auth.delete_user", lambda uid: None)
    with TestClient(app) as client:
        store = client.app.state.state_store
        order = {"orderId": "web_del_1", "uid": USER_ID, "productId": "pass_pro_30d", "amount": 9900,
                 "grant": {"type": "pass", "plan": "pro", "days": 30}, "status": "pending"}
        asyncio.run(store.create_web_order(order))
        asyncio.run(store.complete_web_order("web_del_1", payment_key="pk"))
        assert client.get("/v1/capabilities", headers=_headers(USER_ID)).json()["plan"] == "pro"

        assert client.delete("/v1/users/me", headers=_headers(USER_ID)).status_code == 204
        assert client.get("/v1/capabilities", headers=_headers(USER_ID)).json()["plan"] == "free"
        assert asyncio.run(store.get_web_order("web_del_1"))["status"] == "paid"
