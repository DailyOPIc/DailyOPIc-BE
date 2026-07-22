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
        # 무료 플랜: 하루 데일리 학습 1회.
        for _ in range(1):
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


def test_daily_pool_is_archived_and_refresh_uses_verified_ad_not_analysis_quota() -> None:
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
        assert usage["freeRemaining"] == 1

        archived = client.post(
            "/v1/question-sets/practice",
            headers=_headers(),
            json=payload,
        )
        assert archived.status_code == 200, archived.text
        assert archived.json()["setId"] == first_set["setId"]

        refresh_reward = client.post(
            "/v1/ad-rewards/intents",
            headers=_headers(),
            json={"purpose": "practice_refresh"},
        ).json()
        _verify_reward(client, refresh_reward["nonce"])
        refreshed = client.post(
            "/v1/question-sets/practice/refresh",
            headers={**_headers(), "Idempotency-Key": "daily-refresh-test-1"},
            json={
                **payload,
                "adjustment": "harder",
                "rewardNonce": refresh_reward["nonce"],
            },
        )
        assert refreshed.status_code == 200, refreshed.text
        refreshed_set = refreshed.json()
        assert refreshed_set["setId"] != first_set["setId"]
        assert refreshed_set["effectiveLevelCode"] == "5-6"
        assert [item["number"] for item in refreshed_set["questions"]] == list(range(2, 16))

        usage = client.get("/v1/usage", headers=_headers()).json()
        assert usage["freeRemaining"] == 1
        assert usage["dailyRefreshRemaining"] == 0

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
            headers=_headers("mock-adjustment-e2e-1"),
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
        assert response.json()["perQuestion"] == []


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
            headers=_headers("mock-adjustment-retry-1"),
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
        assert retry.json()["perQuestion"] == []


def test_mock_session_v2_requires_three_verified_gates_and_resumes() -> None:
    payload = {
        "initialLevel": 4,
        "background": {"travel": ["domestic"]},
        "survey": {
            "status": "student",
            "residence": "family",
            "leisure": ["movies", "music", "cafes"],
            "hobbies": [],
            "sports": [],
            "travel": ["domestic_travel"],
        },
    }
    with TestClient(app) as client:
        created = client.post(
            "/v1/mock-exams/sessions",
            headers=_headers("mock-session-create-1"),
            json=payload,
        )
        assert created.status_code == 200, created.text
        session = created.json()
        assert session["stage"] == "awaiting_start_ad"
        assert session["questionSet"] is None

        without_reward = client.post(
            f"/v1/mock-exams/{session['sessionId']}/start",
            headers=_headers("mock-session-start-invalid-1"),
            json={"rewardNonce": "x" * 16},
        )
        assert without_reward.status_code == 402
        current = client.get("/v1/mock-exams/current", headers=_headers()).json()
        assert current["stage"] == "awaiting_start_ad"

        start_reward = client.post(
            "/v1/ad-rewards/intents",
            headers=_headers(),
            json={"purpose": "mock_start", "sessionHash": session["sessionHash"]},
        ).json()
        _verify_reward(client, start_reward["nonce"])
        started = client.post(
            f"/v1/mock-exams/{session['sessionId']}/start",
            headers=_headers("mock-session-start-1"),
            json={"rewardNonce": start_reward["nonce"]},
        )
        assert started.status_code == 200, started.text
        session = started.json()
        assert session["stage"] == "answering_front"
        assert len(session["questionSet"]["questions"]) == 7

        adjustment_reward = client.post(
            "/v1/ad-rewards/intents",
            headers=_headers(),
            json={
                "purpose": "mock_adjustment",
                "sessionHash": session["sessionHash"],
            },
        ).json()
        _verify_reward(client, adjustment_reward["nonce"])
        adjusted = client.post(
            f"/v1/mock-exams/{session['sessionId']}/adjustment",
            headers=_headers("mock-session-adjustment-1"),
            json={"adjustment": "same", "rewardNonce": adjustment_reward["nonce"]},
        )
        assert adjusted.status_code == 200, adjusted.text
        session = adjusted.json()
        assert session["stage"] == "answering_tail"
        assert len(session["questionSet"]["questions"]) == 15

        result_reward = client.post(
            "/v1/ad-rewards/intents",
            headers=_headers(),
            json={"purpose": "mock_result", "sessionHash": session["sessionHash"]},
        ).json()
        _verify_reward(client, result_reward["nonce"])
        manifest = {
            "setId": session["setId"],
            "rewardNonce": result_reward["nonce"],
            "answers": [
                {
                    "number": number,
                    "transcript": f"Complete answer {number} with a reason and example.",
                }
                for number in range(1, 16)
            ],
        }
        evaluated = client.post(
            f"/v1/mock-exams/{session['sessionId']}/evaluate",
            headers=_headers("mock-session-evaluate-1"),
            data={"manifest": json.dumps(manifest)},
            files=_mock_audio_files(),
        )
        assert evaluated.status_code == 200, evaluated.text
        assert evaluated.json()["perQuestion"] == []
        completed = client.get("/v1/mock-exams/current", headers=_headers()).json()
        assert completed["stage"] == "completed"
        usage = client.get("/v1/usage", headers=_headers()).json()
        assert usage["mockAvailable"] is False
        assert usage["mockSessionStage"] == "completed"


def test_daily_reward_intent_quota_returns_402() -> None:
    with TestClient(app) as client:
        # 무료 플랜: 데일리 광고 보너스는 하루 1회.
        for _ in range(1):
            response = client.post(
                "/v1/ad-rewards/intents",
                headers=_headers(),
                json={"purpose": "practice_credits"},
            )
            assert response.status_code == 200, response.text
            _verify_reward(client, response.json()["nonce"])

        blocked = client.post(
            "/v1/ad-rewards/intents",
            headers=_headers(),
            json={"purpose": "practice_credits"},
        )

    assert blocked.status_code == 402
    assert blocked.json()["detail"]["code"] == "reward_quota_exhausted"


def test_target_level_change_intent_is_not_blocked_by_practice_reward_quota() -> None:
    with TestClient(app) as client:
        # 무료 플랜의 데일리 광고 보너스 한도(1회)를 소진해도
        # 목표 등급 변경 리워드는 별도 정책이라 막히지 않아야 한다.
        for _ in range(1):
            response = client.post(
                "/v1/ad-rewards/intents",
                headers=_headers(),
                json={"purpose": "practice_credits"},
            )
            assert response.status_code == 200, response.text
            _verify_reward(client, response.json()["nonce"])

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


def _legacy_question(
    number: int, exam_type: str, style: str, combo: str | None, topic_id: str
) -> dict:
    """배포 전 스키마의 개별 문제 (type/questionType 보유)."""
    return {
        "number": number,
        "type": exam_type,
        "comboId": combo,
        "topic": topic_id.replace("_", " ")[:40] or "topic",
        "prompt": f"Legacy question number {number} about {topic_id}.",
        "difficulty": "IM2",
        "rubricFocus": ["task fulfillment"],
        "questionType": style,
        "followUpPrompt": None,
        "topicId": topic_id,
        "category": "survey",
        "estimatedLevel": "IM2",
    }


def _legacy_question_set_doc(*, uid: str, set_id: str, mode: str, questions: list) -> dict:
    """배포 전 스키마의 questionSets 문서 (mode 구값 + 제거된 필드 + dateKey)."""
    from datetime import UTC, datetime, timedelta

    return {
        "uid": uid,
        "setId": set_id,
        "mode": mode,
        "targetLevel": "IM2",
        "expectedTargetLevel": "IM2",
        "initialLevel": 4,
        "adjustment": None,
        "effectiveLevel": 4,
        "effectiveLevelCode": "4-4",
        "status": "complete",
        "frontQuestionCount": 7,
        "poolIndex": 0,
        "background": {},
        "survey": None,
        "questionHash": "legacy-hash",
        "questions": questions,
        "source": "free",
        "dateKey": "20260101",
        "expiresAt": datetime.now(UTC) + timedelta(days=1),
        "createdAt": datetime.now(UTC),
        "updatedAt": datetime.now(UTC),
    }


def test_legacy_documents_do_not_break_live_endpoints() -> None:
    with TestClient(app) as client:
        store = client.app.state.state_store

        # (1) 조정 전 상태의 구 mock 프론트 세트 → adjustment 엔드포인트 (이전엔 500)
        mock_front = [
            _legacy_question(1, "introduction", "description", None, "self_introduction"),
            _legacy_question(2, "survey", "description", "survey-1", "movies"),
            _legacy_question(3, "survey", "routine", "survey-1", "movies"),
            _legacy_question(4, "survey", "past_experience", "survey-1", "movies"),
            _legacy_question(5, "survey", "description", "survey-2", "music"),
            _legacy_question(6, "survey", "routine", "survey-2", "music"),
            _legacy_question(7, "survey", "past_experience", "survey-2", "music"),
        ]
        front_doc = _legacy_question_set_doc(
            uid=USER_ID, set_id="legacy-mock-front", mode="mock", questions=mock_front
        )
        front_doc["status"] = "awaiting_adjustment"
        store._question_sets["legacy-mock-front"] = front_doc

        adjusted = client.post(
            "/v1/question-sets/legacy-mock-front/adjustment",
            headers=_headers("legacy-adjustment-1"),
            json={"adjustment": "same"},
        )
        assert adjusted.status_code == 200, adjusted.text
        assert len(adjusted.json()["questions"]) == 15

        # (2) 구 완성 mock 세트 → evaluate_mock 검증 통과 (이전엔 401 invalid_set)
        mock_full = [
            _legacy_question(n, "survey", "description", None, f"topic_{n}")
            for n in range(1, 16)
        ]
        store._question_sets["legacymockset123"] = _legacy_question_set_doc(
            uid=USER_ID, set_id="legacymockset123", mode="mock", questions=mock_full
        )
        manifest = {
            "setId": "legacymockset123",
            "rewardNonce": "n" * 16,
            "answers": [
                {"number": n, "transcript": f"answer number {n}"} for n in range(1, 16)
            ],
        }
        eval_mock = client.post(
            "/v1/evaluations/mock",
            headers=_headers(str(uuid.uuid4())),
            data={"manifest": json.dumps(manifest)},
        )
        # 검증(구 필드 정규화)은 통과하고, 오디오 누락 단계에서 걸려야 한다 (401 invalid_set 아님)
        assert eval_mock.status_code != 401, eval_mock.text
        assert eval_mock.json()["detail"]["code"] != "invalid_set"
        assert eval_mock.json()["detail"]["code"] == "missing_audio"

        # (3) 구 daily 세트(mode=practice) → evaluate_practice dual-read (이전엔 401 invalid_set)
        daily = [
            _legacy_question(n, "survey", "description", None, f"daily_{n}")
            for n in range(2, 16)
        ]
        store._question_sets["legacy-daily-set"] = _legacy_question_set_doc(
            uid=USER_ID, set_id="legacy-daily-set", mode="practice", questions=daily
        )
        eval_practice = client.post(
            "/v1/evaluations/practice",
            headers=_headers(str(uuid.uuid4())),
            data={
                "setId": "legacy-daily-set",
                "questionNumber": "2",
                "transcript": "I usually enjoy this activity because it helps me relax and learn.",
            },
        )
        assert eval_practice.status_code == 200, eval_practice.text


def test_idempotent_cache_reuse_is_safe() -> None:
    with TestClient(app) as client:
        question_set = client.post(
            "/v1/question-sets/practice",
            headers=_headers(),
            json={"initialLevel": 4, "background": {"interests": ["news"]}},
        ).json()
        form = {
            "setId": question_set["setId"],
            "questionNumber": str(question_set["questions"][0]["number"]),
            "transcript": "I read the news every morning to stay informed about the world.",
        }
        key = str(uuid.uuid4())
        first = client.post("/v1/evaluations/practice", headers=_headers(key), data=form)
        assert first.status_code == 200, first.text
        second = client.post("/v1/evaluations/practice", headers=_headers(key), data=form)
        assert second.status_code == 200, second.text
        # 멱등 캐시 재사용: 동일 결과 + 구 필드 미포함
        assert second.json() == first.json()
        assert "type" not in second.json()
        assert "questionType" not in second.json()
