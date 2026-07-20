import json
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.services.admob import VerifiedReward
from app.services.ai import AIQuestionGenerationError, AIServiceUnavailable


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


class FailingQuestionAIService:
    model = "test-model"

    async def generate_practice(self, *args: object, **kwargs: object) -> object:
        raise AIQuestionGenerationError("forced failure")

    async def generate_daily_pool(self, *args: object, **kwargs: object) -> object:
        raise AIQuestionGenerationError("forced failure")


class FailingMockEvaluationAIService:
    model = "test-model"

    async def evaluate_mock(self, *args: object, **kwargs: object) -> object:
        raise AIServiceUnavailable("forced evaluation failure")


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
    response = client.get(f"/v1/admob/ssv?custom_data={nonce}&fake=1")
    assert response.status_code == 200, response.text


def _mock_audio_files() -> list[tuple[str, tuple[str, bytes, str]]]:
    return [
        ("audioFiles", (f"answer-{number}.m4a", b"not-real-audio", "audio/mp4"))
        for number in range(1, 16)
    ]


def test_admob_ssv_url_verification_without_query_returns_200() -> None:
    with TestClient(app) as client:
        response = client.get("/v1/admob/ssv")

    assert response.status_code == 200
    assert response.text == "OK"


def test_admob_ssv_url_verification_without_custom_data_returns_200() -> None:
    with TestClient(app) as client:
        response = client.get(
            "/v1/admob/ssv"
            "?ad_unit=ca-app-pub-5460686409666356/7091483531"
            "&transaction_id=url-check"
            "&key_id=123"
            "&signature=placeholder"
        )

    assert response.status_code == 200
    assert response.text == "OK"


def test_admob_ssv_with_custom_data_still_requires_signed_parameters() -> None:
    with TestClient(app) as client:
        response = client.get("/v1/admob/ssv?custom_data=nonce-only")

    assert response.status_code == 400
    assert "required SSV parameters are missing" in response.text


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
        assert [item["number"] for item in question_set["questions"]] == list(range(2, 16))
        form = {
            "setId": question_set["setId"],
            "questionNumber": str(question_set["questions"][0]["number"]),
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


def test_daily_pool_is_archived_and_refresh_consumes_practice_token() -> None:
    with TestClient(app) as client:
        payload = {
            "initialLevel": 5,
            "background": {"interests": ["cafes"]},
            "survey": {
                "status": "student",
                "residence": "family",
                "leisure": ["movies", "music", "cafes"],
                "hobbies": [],
                "sports": [],
                "travel": [],
            },
        }
        first = client.post(
            "/v1/question-sets/practice",
            headers=_headers(),
            json=payload,
        )
        assert first.status_code == 200, first.text
        first_set = first.json()
        assert [item["number"] for item in first_set["questions"]] == list(range(2, 16))
        assert all(item["examSection"] != "introduction" for item in first_set["questions"])

        usage = client.get("/v1/usage", headers=_headers()).json()
        assert usage["freeRemaining"] == 3

        archived = client.post(
            "/v1/question-sets/practice",
            headers=_headers(),
            json=payload,
        )
        assert archived.status_code == 200, archived.text
        assert archived.json()["setId"] == first_set["setId"]

        refreshed = client.post(
            "/v1/question-sets/practice/refresh",
            headers=_headers(),
            json={**payload, "adjustment": "harder"},
        )
        assert refreshed.status_code == 200, refreshed.text
        refreshed_set = refreshed.json()
        assert refreshed_set["setId"] != first_set["setId"]
        assert refreshed_set["effectiveLevelCode"] == "5-6"
        assert [item["number"] for item in refreshed_set["questions"]] == list(range(2, 16))

        usage = client.get("/v1/usage", headers=_headers()).json()
        assert usage["freeRemaining"] == 2

        response = client.post(
            "/v1/evaluations/practice",
            headers=_headers(str(uuid.uuid4())),
            data={
                "setId": refreshed_set["setId"],
                "questionNumber": "15",
                "transcript": "Technology changes how I plan my day, communicate, and solve small problems at work.",
                "targetLevel": "AL",
            },
        )
        assert response.status_code == 200, response.text


def test_question_generation_failure_returns_503_without_fallback() -> None:
    with TestClient(app) as client:
        client.app.state.ai_service = FailingQuestionAIService()
        response = client.post(
            "/v1/question-sets/practice",
            headers=_headers(),
            json={"targetLevel": "IH", "background": {"interests": ["news"]}},
        )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "ai_question_generation_failed"


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
        assert len(question_set["questions"]) == 7
        assert question_set["isComplete"] is False
        adjusted = client.post(
            f"/v1/question-sets/{question_set['setId']}/adjustment",
            headers=_headers(),
            json={"adjustment": "same"},
        )
        assert adjusted.status_code == 200, adjusted.text
        question_set = adjusted.json()
        assert len(question_set["questions"]) == 15
        assert question_set["isComplete"] is True
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


def test_mock_ai_failure_rolls_back_reward_for_same_request_retry() -> None:
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
        adjusted = client.post(
            f"/v1/question-sets/{question_set['setId']}/adjustment",
            headers=_headers(),
            json={"adjustment": "same"},
        )
        assert adjusted.status_code == 200, adjusted.text
        question_set = adjusted.json()
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
        request_id = str(uuid.uuid4())
        working_ai_service = client.app.state.ai_service
        client.app.state.ai_service = FailingMockEvaluationAIService()
        try:
            failed = client.post(
                "/v1/evaluations/mock",
                headers=_headers(request_id),
                data={"manifest": json.dumps(manifest)},
                files=_mock_audio_files(),
            )
        finally:
            client.app.state.ai_service = working_ai_service
        assert failed.status_code == 503
        assert failed.json()["detail"]["code"] == "ai_unavailable"

        retry = client.post(
            "/v1/evaluations/mock",
            headers=_headers(request_id),
            data={"manifest": json.dumps(manifest)},
            files=_mock_audio_files(),
        )

        assert retry.status_code == 200, retry.text
        assert len(retry.json()["perQuestion"]) == 15


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


def test_response_contract_uses_renamed_fields() -> None:
    with TestClient(app) as client:
        question_set = client.post(
            "/v1/question-sets/practice",
            headers=_headers(),
            json={"initialLevel": 4, "background": {"interests": ["news"]}},
        )
        assert question_set.status_code == 200, question_set.text
        question = question_set.json()["questions"][0]
        assert "examSection" in question
        assert "questionStyle" in question
        assert "type" not in question
        assert "questionType" not in question

        target = client.put(
            "/v1/users/me/target-level",
            headers=_headers(),
            json={"initialLevel": 4},
        )
        assert target.status_code == 200, target.text
        body = target.json()
        assert "beforeAdjust" in body
        assert "previousBeforeAdjust" in body
        assert "afterAdjust" in body
        assert "initialLevel" not in body
        assert "previousInitialLevel" not in body
        assert "effectiveLevel" not in body
        assert "effectiveLevelCode" not in body
        assert "latestAdjustment" not in body
