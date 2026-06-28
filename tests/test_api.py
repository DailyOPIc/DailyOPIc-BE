import json
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.services.admob import VerifiedReward


USER_ID = "11111111-1111-4111-8111-111111111111"


class FakeSSVVerifier:
    def __init__(self, *, nonce: str, user_id: str = USER_ID) -> None:
        self._nonce = nonce
        self._user_id = user_id

    async def verify(self, raw_query: str) -> VerifiedReward:
        return VerifiedReward(
            nonce=self._nonce,
            transaction_id=f"tx-{self._nonce}",
            user_id=self._user_id,
            ad_unit="ca-app-pub-5460686409666356/7091483531",
        )


def _headers(request_id: str | None = None) -> dict[str, str]:
    value = {
        "X-DailyOPIc-User-ID": USER_ID,
        "X-Firebase-AppCheck": "test-app-check-token",
    }
    if request_id:
        value["Idempotency-Key"] = request_id
    return value


def _verify_reward(client: TestClient, nonce: str) -> None:
    client.app.state.ssv_verifier = FakeSSVVerifier(nonce=nonce)
    response = client.get("/v1/admob/ssv?fake=1")
    assert response.status_code == 200, response.text


def _mock_audio_files() -> list[tuple[str, tuple[str, bytes, str]]]:
    return [
        ("audioFiles", (f"answer-{number}.m4a", b"not-real-audio", "audio/mp4"))
        for number in range(1, 16)
    ]


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
        assert "setToken" not in question_set
        form = {
            "setId": question_set["setId"],
            "questionNumber": "1",
            "transcript": "I read several news sources every morning because I want balanced information.",
            "targetLevel": "IH",
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
        assert reward.json()["status"] == "pending"
        _verify_reward(client, reward.json()["nonce"])
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


def test_target_level_change_requires_reward_and_controls_question_sets() -> None:
    with TestClient(app) as client:
        initial = client.put(
            "/v1/users/me/target-level",
            headers=_headers(),
            json={"targetLevel": "IH"},
        )
        assert initial.status_code == 200, initial.text
        assert initial.json()["targetLevel"] == "IH"
        assert initial.json()["changed"] is True
        assert initial.json()["rewardConsumed"] is False

        same = client.put(
            "/v1/users/me/target-level",
            headers=_headers(),
            json={"targetLevel": "IH"},
        )
        assert same.status_code == 200, same.text
        assert same.json()["changed"] is False

        blocked = client.put(
            "/v1/users/me/target-level",
            headers=_headers(),
            json={"targetLevel": "AL"},
        )
        assert blocked.status_code == 402
        assert blocked.json()["detail"]["code"] == "target_level_change_reward_required"

        rejected_questions = client.post(
            "/v1/question-sets/practice",
            headers=_headers(),
            json={"targetLevel": "AL", "background": {"interests": ["news"]}},
        )
        assert rejected_questions.status_code == 402
        assert (
            rejected_questions.json()["detail"]["code"]
            == "target_level_change_reward_required"
        )

        reward = client.post(
            "/v1/ad-rewards/intents",
            headers=_headers(),
            json={"purpose": "target_level_change"},
        )
        assert reward.status_code == 200, reward.text
        _verify_reward(client, reward.json()["nonce"])

        changed = client.put(
            "/v1/users/me/target-level",
            headers=_headers(),
            json={"targetLevel": "AL", "rewardNonce": reward.json()["nonce"]},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["targetLevel"] == "AL"
        assert changed.json()["previousTargetLevel"] == "IH"
        assert changed.json()["rewardConsumed"] is True
        usage = client.get("/v1/usage", headers=_headers()).json()
        assert usage["bonusRemaining"] == 0

        reused = client.put(
            "/v1/users/me/target-level",
            headers=_headers(),
            json={"targetLevel": "IM3", "rewardNonce": reward.json()["nonce"]},
        )
        assert reused.status_code == 402
        assert reused.json()["detail"]["code"] == "target_level_change_reward_required"

        accepted_questions = client.post(
            "/v1/question-sets/practice",
            headers=_headers(),
            json={"targetLevel": "AL", "background": {"interests": ["news"]}},
        )
        assert accepted_questions.status_code == 200, accepted_questions.text
        assert accepted_questions.json()["questions"][0]["difficulty"] == "AL"


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
        assert "setToken" not in question_set
        assert "tags" not in question_set["questions"][1]
        assert {item["topicId"] for item in question_set["questions"][1:10]} >= {
            "movies",
            "music",
            "cafes",
        }
        answers = [
            {
                "number": number,
                "transcript": (
                    f"This is my complete answer number {number}. "
                    "I explain a reason and an example."
                ),
            }
            for number in range(1, 16)
        ]
        reward = client.post(
            "/v1/ad-rewards/intents",
            headers=_headers(),
            json={"purpose": "mock_result", "sessionHash": question_set["setHash"]},
        ).json()
        _verify_reward(client, reward["nonce"])
        manifest = {
            "targetLevel": "IM2",
            "setId": question_set["setId"],
            "rewardNonce": reward["nonce"],
            "answers": answers,
        }
        response = client.post(
            "/v1/evaluations/mock",
            headers=_headers(str(uuid.uuid4())),
            data={"manifest": json.dumps(manifest)},
            files=_mock_audio_files(),
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


def test_target_level_change_intent_is_not_blocked_by_practice_reward_quota() -> None:
    with TestClient(app) as client:
        for _ in range(3):
            response = client.post(
                "/v1/ad-rewards/intents",
                headers=_headers(),
                json={"purpose": "practice_credits"},
            )
            assert response.status_code == 200, response.text

        response = client.post(
            "/v1/ad-rewards/intents",
            headers=_headers(),
            json={"purpose": "target_level_change"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["purpose"] == "target_level_change"
    assert response.json()["status"] == "pending"


def test_mock_evaluation_rejects_unverified_reward() -> None:
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
        reward = client.post(
            "/v1/ad-rewards/intents",
            headers=_headers(),
            json={"purpose": "mock_result", "sessionHash": question_set["setHash"]},
        ).json()
        manifest = {
            "targetLevel": "IM2",
            "setId": question_set["setId"],
            "rewardNonce": reward["nonce"],
            "answers": [
                {"number": number, "transcript": f"Answer number {number} has enough detail."}
                for number in range(1, 16)
            ],
        }

        response = client.post(
            "/v1/evaluations/mock",
            headers=_headers(str(uuid.uuid4())),
            data={"manifest": json.dumps(manifest)},
            files=_mock_audio_files(),
        )

    assert response.status_code == 402
    assert response.json()["detail"]["code"] == "mock_reward_required"
