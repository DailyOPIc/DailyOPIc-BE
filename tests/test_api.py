import json
import uuid

from fastapi.testclient import TestClient

from app.main import app


def _headers(request_id: str | None = None) -> dict[str, str]:
    value = {"X-Debug-User-ID": "api-test-user"}
    if request_id:
        value["Idempotency-Key"] = request_id
    return value


def test_practice_quota_and_reward_flow() -> None:
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        question_set = client.post(
            "/v1/question-sets/practice",
            headers=_headers(),
            json={"targetLevel": "IH", "background": {"interests": ["news"]}},
        ).json()
        form = {
            "questionSet": json.dumps(question_set["questions"]),
            "questionNumber": "1",
            "transcript": "I read several news sources every morning because I want balanced information.",
            "targetLevel": "IH",
            "setToken": question_set["setToken"],
        }
        for _ in range(3):
            response = client.post(
                "/v1/evaluations/practice",
                headers=_headers(str(uuid.uuid4())),
                data=form,
            )
            assert response.status_code == 200, response.text
        blocked = client.post(
            "/v1/evaluations/practice",
            headers=_headers(str(uuid.uuid4())),
            data=form,
        )
        assert blocked.status_code == 402

        reward = client.post(
            "/v1/ad-rewards/intents",
            headers=_headers(),
            json={"purpose": "practice_credits"},
        )
        assert reward.status_code == 200
        usage = client.get("/v1/usage", headers=_headers()).json()
        assert usage["bonusRemaining"] == 1

        response = client.post(
            "/v1/evaluations/practice",
            headers=_headers(str(uuid.uuid4())),
            data=form,
        )
        assert response.status_code == 200, response.text
        usage = client.get("/v1/usage", headers=_headers()).json()
        assert usage["bonusRemaining"] == 0


def test_mock_requires_reward_and_returns_fifteen_feedback_items() -> None:
    with TestClient(app) as client:
        question_set = client.post(
            "/v1/mock-exams",
            headers=_headers(),
            json={
                "targetLevel": "IM2",
                "background": {"travel": ["domestic"]},
                "survey": {
                    "status": "student",
                    "residence": "family",
                    "leisure": ["movies", "music", "cafes"],
                    "hobbies": [],
                    "sports": [],
                    "travel": ["domestic_travel"],
                },
            },
        ).json()
        assert len(question_set["questions"]) == 15
        assert "tags" not in question_set["questions"][1]
        assert {item["topicId"] for item in question_set["questions"][1:10]} >= {
            "movies",
            "music",
            "cafes",
        }
        answers = [
            {"number": number, "transcript": f"This is my complete answer number {number}. I explain a reason and an example."}
            for number in range(1, 16)
        ]
        reward = client.post(
            "/v1/ad-rewards/intents",
            headers=_headers(),
            json={"purpose": "mock_result", "sessionHash": question_set["setHash"]},
        ).json()
        manifest = {
            "targetLevel": "IM2",
            "setToken": question_set["setToken"],
            "rewardNonce": reward["nonce"],
            "questions": question_set["questions"],
            "answers": answers,
        }
        response = client.post(
            "/v1/evaluations/mock",
            headers=_headers(str(uuid.uuid4())),
            data={"manifest": json.dumps(manifest)},
        )
        assert response.status_code == 200, response.text
        assert len(response.json()["perQuestion"]) == 15


def test_daily_reward_intent_quota_returns_402() -> None:
    with TestClient(app) as client:
        for _ in range(3):
            response = client.post(
                "/v1/ad-rewards/intents",
                headers=_headers(),
                json={"purpose": "practice_credits"},
            )
            assert response.status_code == 200, response.text

        blocked = client.post(
            "/v1/ad-rewards/intents",
            headers=_headers(),
            json={"purpose": "practice_credits"},
        )

    assert blocked.status_code == 402
    assert blocked.json()["detail"]["code"] == "reward_quota_exhausted"


def test_client_reward_completion_when_ssv_is_disabled() -> None:
    with TestClient(app) as client:
        settings = client.app.state.settings
        original_auto_verify = settings.debug_reward_auto_verify
        original_ssv_required = settings.admob_ssv_required
        settings.debug_reward_auto_verify = False
        settings.admob_ssv_required = False
        try:
            reward = client.post(
                "/v1/ad-rewards/intents",
                headers=_headers(),
                json={"purpose": "practice_credits"},
            )
            assert reward.status_code == 200, reward.text
            nonce = reward.json()["nonce"]
            assert reward.json()["status"] == "pending"

            complete = client.post(f"/v1/ad-rewards/{nonce}/client-complete", headers=_headers())
            assert complete.status_code == 200, complete.text
            assert complete.json()["status"] == "verified"
            usage = client.get("/v1/usage", headers=_headers()).json()
            assert usage["bonusRemaining"] == 1
        finally:
            settings.debug_reward_auto_verify = original_auto_verify
            settings.admob_ssv_required = original_ssv_required


def test_client_reward_completion_is_rejected_when_ssv_is_required() -> None:
    with TestClient(app) as client:
        settings = client.app.state.settings
        original_auto_verify = settings.debug_reward_auto_verify
        original_ssv_required = settings.admob_ssv_required
        settings.debug_reward_auto_verify = False
        settings.admob_ssv_required = True
        try:
            reward = client.post(
                "/v1/ad-rewards/intents",
                headers=_headers(),
                json={"purpose": "practice_credits"},
            )
            assert reward.status_code == 200, reward.text

            blocked = client.post(
                f"/v1/ad-rewards/{reward.json()['nonce']}/client-complete",
                headers=_headers(),
            )
            assert blocked.status_code == 409
            assert blocked.json()["detail"]["code"] == "ssv_required"
        finally:
            settings.debug_reward_auto_verify = original_auto_verify
            settings.admob_ssv_required = original_ssv_required
