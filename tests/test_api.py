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
        assert usage["bonusRemaining"] == 3


def test_mock_requires_reward_and_returns_fifteen_feedback_items() -> None:
    with TestClient(app) as client:
        question_set = client.post(
            "/v1/mock-exams",
            headers=_headers(),
            json={"targetLevel": "IM2", "background": {"travel": ["domestic"]}},
        ).json()
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
