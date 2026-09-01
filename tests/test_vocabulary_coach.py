"""단어장 AI 말하기 코치(P14.3) — 과금 · 검증 · 재생 · 환불 · 출력 형식.

핵심 규약은 하나다: **새 코칭 분석 1회 = 데일리 토큰 1개.** 녹음 · 전사 · 이미
받은 코칭 다시 보기는 서버를 거치지 않으므로 여기 나오지도 않는다.

내부에서 제공자를 몇 번 부르든(형식 불량 재시도), 클라이언트가 같은 키로 몇 번
재전송하든 차감은 1개를 넘지 않는다. 쓸 만한 결과가 없으면 정확히 한 번 환불된다.

`conftest.py` 가 `MOCK_AI=true` 를 세팅하므로 OpenAI 는 호출되지 않는다.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.api import (
    OPIcLevel,
    VocabularyItemType,
    VocabularyTopic,
    VocabularyUsageAssessment,
    VOCABULARY_TRANSCRIPT_MAX_CHARS,
)
from app.services import vocabulary
from app.services.plans import Plan
from app.services.ai import AIService, AIServiceUnavailable, AIVocabularyCoachError
from app.services.questions import QuestionPatternRepository
from tests.test_api import _headers


PATH = "/v1/vocabulary/coach"
TRANSCRIPT = "My favorite cafe is very crowded because many people go there."


class FailingCoachAIService:
    model = "test-model"

    async def coach_vocabulary(self, *args: object, **kwargs: object) -> object:
        raise AIServiceUnavailable("forced coaching failure")


class CountingCoachAIService:
    """실제 코치를 감싸 호출 횟수만 센다(제공자가 몇 번 불렸는지 확인용)."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.calls = 0
        self.model = getattr(inner, "model", "test-model")

    async def coach_vocabulary(self, **kwargs: object) -> object:
        self.calls += 1
        return await self._inner.coach_vocabulary(**kwargs)


async def _pro_plan() -> Plan:
    return Plan.PRO


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as value:
        yield value


def _coach(
    client: TestClient,
    *,
    uid: str,
    request_id: str | None,
    term: str = "crowded",
    item_type: str = "word",
    transcript: str = TRANSCRIPT,
    entry_id: str = "vocab-cafes-001",
    extra: dict[str, object] | None = None,
):
    body: dict[str, object] = {
        "entryId": entry_id,
        "term": term,
        "type": item_type,
        "transcript": transcript,
    }
    if extra:
        body.update(extra)
    headers = _headers(uid=uid) if request_id is None else _headers(request_id, uid=uid)
    return client.post(PATH, headers=headers, json=body)


def _tokens(client: TestClient, uid: str) -> int:
    response = client.get("/v1/usage", headers=_headers(uid=uid))
    assert response.status_code == 200, response.text
    payload = response.json()
    return payload["freeRemaining"] + payload["bonusRemaining"]


def _assert_usable_coaching(payload: dict) -> None:
    """사용자에게 보여도 되는 코칭인가. 반쯤 빈 결과가 200으로 나가면 안 된다."""
    assert payload["usageAssessment"] in {item.value for item in VocabularyUsageAssessment}
    for key in (
        "usageFeedbackKo",
        "naturalCorrectionEn",
        "naturalCorrectionKo",
        "expandedAnswerEn",
        "expandedAnswerKo",
    ):
        assert payload[key].strip(), key
    related = payload["relatedExpressions"]
    assert vocabulary.COACH_MIN_RELATED <= len(related) <= vocabulary.COACH_MAX_RELATED
    assert all(item.strip() for item in related)
    assert all(len(item) <= 60 for item in related)
    # 코치는 등급·점수를 내지 않는다. 데일리 분석과 두 개의 진실을 만들지 않는다.
    for forbidden in ("grade", "score", "rubrics", "level", "passed"):
        assert forbidden not in payload


# --- 1. 성공 = 토큰 정확히 1개 -------------------------------------------------


def test_coaching_debits_exactly_one_daily_token(client: TestClient) -> None:
    uid = str(uuid.uuid4())
    before = _tokens(client, uid)

    response = _coach(client, uid=uid, request_id=str(uuid.uuid4()))

    assert response.status_code == 200, response.text
    assert _tokens(client, uid) == before - 1
    payload = response.json()
    assert payload["entryId"] == "vocab-cafes-001"
    assert payload["targetTerm"] == "crowded"
    assert payload["transcript"] == TRANSCRIPT
    assert payload["resultId"]
    assert payload["createdAt"]
    _assert_usable_coaching(payload)


# --- 2. 잔액 0이면 제공자를 부르지 않는다 --------------------------------------


def test_zero_token_rejects_before_calling_provider(client: TestClient) -> None:
    uid = str(uuid.uuid4())
    counting = CountingCoachAIService(client.app.state.ai_service)
    client.app.state.ai_service = counting
    while _tokens(client, uid) > 0:
        assert _coach(client, uid=uid, request_id=str(uuid.uuid4())).status_code == 200
    spent_calls = counting.calls

    response = _coach(client, uid=uid, request_id=str(uuid.uuid4()))

    assert response.status_code == 402, response.text
    assert response.json()["detail"]["code"] == "practice_quota_exhausted"
    assert counting.calls == spent_calls


# --- 3~4. 같은 조작 재생 = 추가 차감도, 제공자 재호출도 없음 --------------------


def test_replay_returns_same_result_without_extra_debit_or_provider_call(
    client: TestClient,
) -> None:
    uid = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    before = _tokens(client, uid)

    first = _coach(client, uid=uid, request_id=request_id)
    assert first.status_code == 200, first.text
    after_first = _tokens(client, uid)

    counting = CountingCoachAIService(client.app.state.ai_service)
    client.app.state.ai_service = counting
    second = _coach(client, uid=uid, request_id=request_id)

    assert second.status_code == 200, second.text
    assert second.json() == first.json()
    assert counting.calls == 0
    assert after_first == before - 1
    assert _tokens(client, uid) == after_first


def test_new_analysis_uses_a_new_operation_and_a_new_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"다시 분석"은 새 조작이다. 새 키를 보내면 그때 새로 1개 나간다.

    무료 플랜은 하루 1개뿐이라 두 번째 조작을 관찰할 잔액이 없다. 잔액이 아니라
    "새 키 = 새 차감"을 보려는 테스트이므로 플랜만 유료로 바꿔 준다.
    """
    monkeypatch.setattr(
        "app.api.routes._current_plan",
        lambda request, uid: _pro_plan(),
    )
    uid = str(uuid.uuid4())
    before = _tokens(client, uid)
    assert before >= 2

    assert _coach(client, uid=uid, request_id=str(uuid.uuid4())).status_code == 200
    assert _coach(client, uid=uid, request_id=str(uuid.uuid4())).status_code == 200

    assert _tokens(client, uid) == before - 2


# --- 5~6. 실패 → 정확히 한 번 환불, 같은 키 재시도로 성공 ----------------------


def test_provider_failure_refunds_exactly_once(client: TestClient) -> None:
    uid = str(uuid.uuid4())
    before = _tokens(client, uid)
    client.app.state.ai_service = FailingCoachAIService()
    request_id = str(uuid.uuid4())

    first = _coach(client, uid=uid, request_id=request_id)
    assert first.status_code == 503, first.text
    assert first.json()["detail"]["code"] == "ai_unavailable"
    # 제공자 스택트레이스·모델명이 새어 나가지 않는다.
    assert "openai" not in first.text.lower()
    assert _tokens(client, uid) == before

    second = _coach(client, uid=uid, request_id=request_id)
    assert second.status_code == 503, second.text
    assert _tokens(client, uid) == before


def test_retry_after_failure_can_succeed_with_one_total_debit(client: TestClient) -> None:
    uid = str(uuid.uuid4())
    before = _tokens(client, uid)
    healthy = client.app.state.ai_service
    client.app.state.ai_service = FailingCoachAIService()
    request_id = str(uuid.uuid4())

    assert _coach(client, uid=uid, request_id=request_id).status_code == 503
    client.app.state.ai_service = healthy
    retried = _coach(client, uid=uid, request_id=request_id)

    assert retried.status_code == 200, retried.text
    assert _tokens(client, uid) == before - 1


# --- 7. 내부 재시도는 과금 단위가 아니다 ---------------------------------------


def test_internal_provider_retry_does_not_cause_a_second_debit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    uid = str(uuid.uuid4())
    before = _tokens(client, uid)
    calls = 0
    original = vocabulary.mock_coach_draft

    def flaky(**kwargs):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 1:
            raise AIServiceUnavailable("malformed structured output")
        return original(**kwargs)

    monkeypatch.setattr(vocabulary, "mock_coach_draft", flaky)
    response = _coach(client, uid=uid, request_id=str(uuid.uuid4()))

    assert response.status_code == 200, response.text
    assert calls == 2
    assert _tokens(client, uid) == before - 1
    _assert_usable_coaching(response.json())


async def test_internal_retry_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """무한 재시도 루프를 만들지 않는다. 상한을 넘기면 실패로 처리한다."""
    service = AIService(
        api_key=None,
        model="test-model",
        mock=True,
        repository=QuestionPatternRepository(Path("app/data/question_patterns.json")),
    )
    calls = 0

    def always_bad(**kwargs):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        raise AIServiceUnavailable("malformed structured output")

    monkeypatch.setattr(vocabulary, "mock_coach_draft", always_bad)
    with pytest.raises(AIVocabularyCoachError):
        await service.coach_vocabulary(
            term="crowded",
            item_type=VocabularyItemType.WORD,
            meaning_ko="붐비는",
            topic=VocabularyTopic.CAFES,
            target_level=OPIcLevel.IH,
            transcript=TRANSCRIPT,
        )

    assert calls == vocabulary.COACH_MAX_PROVIDER_ATTEMPTS


def test_bounded_retry_exhaustion_refunds_exactly_once(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    uid = str(uuid.uuid4())
    before = _tokens(client, uid)

    def always_bad(**kwargs):  # type: ignore[no-untyped-def]
        raise AIServiceUnavailable("malformed structured output")

    monkeypatch.setattr(vocabulary, "mock_coach_draft", always_bad)
    response = _coach(client, uid=uid, request_id=str(uuid.uuid4()))

    assert response.status_code == 503, response.text
    assert _tokens(client, uid) == before


# --- 8. 형식이 깨진 제공자 출력은 절대 사용자에게 나가지 않는다 -----------------


@pytest.mark.parametrize(
    "override",
    [
        {"usageFeedbackKo": "   "},
        {"naturalCorrectionEn": "   "},
        {"expandedAnswerEn": "        "},
        {"expandedAnswerKo": " "},
        {"relatedExpressions": []},
        {"relatedExpressions": ["only one"]},
        {"relatedExpressions": ["  ", "   "]},
        # 표기만 다른 중복은 걷어내므로 쓸 만한 것이 1개뿐이면 거절한다.
        {"relatedExpressions": ["cozy atmosphere", "Cozy  atmosphere"]},
        {"relatedExpressions": ["cozy atmosphere", "x" * 61]},
        {"usageAssessment": "excellent"},
        {"relatedExpressions": ["a", "b", "c", "d", "e"]},
    ],
)
def test_malformed_provider_output_is_rejected(override: dict[str, object]) -> None:
    payload = {
        "usageAssessment": "appropriate",
        "usageFeedbackKo": "crowded를 문맥에 맞게 잘 사용했어요.",
        "naturalCorrectionEn": "My favorite cafe gets especially crowded on weekends.",
        "naturalCorrectionKo": "제가 좋아하는 카페는 주말에 특히 붐벼요.",
        "expandedAnswerEn": (
            "My favorite cafe gets especially crowded on weekends, "
            "but I still go there because it has a cozy atmosphere."
        ),
        "expandedAnswerKo": "주말에 특히 붐비지만 분위기가 아늑해서 그래도 자주 가요.",
        "relatedExpressions": ["get crowded", "especially on weekends"],
    }
    payload.update(override)

    with pytest.raises(ValueError):
        vocabulary.VocabularyCoachDraft.model_validate(payload)


def test_related_expressions_are_deduplicated_and_capped() -> None:
    draft = vocabulary.VocabularyCoachDraft.model_validate(
        {
            "usageAssessment": "needsPolish",
            "usageFeedbackKo": "의미는 전달돼요.",
            "naturalCorrectionEn": "My cafe gets crowded on weekends.",
            "naturalCorrectionKo": "제 카페는 주말에 붐벼요.",
            "expandedAnswerEn": "My cafe gets crowded on weekends, but it is cozy.",
            "expandedAnswerKo": "주말에 붐비지만 아늑해요.",
            "relatedExpressions": [" get crowded ", "GET  CROWDED", "cozy atmosphere"],
        }
    )

    assert draft.related_expressions == ["get crowded", "cozy atmosphere"]


# --- 9~13. 입력 검증은 제공자 호출 **전에** ------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"term": ""},
        {"term": "   "},
        {"term": "x" * 81},
        {"transcript": ""},
        {"transcript": "   "},
        {"transcript": "x" * (VOCABULARY_TRANSCRIPT_MAX_CHARS + 1)},
        {"entry_id": ""},
        {"entry_id": "x" * 121},
        {"item_type": "sentence"},
        {"item_type": ""},
    ],
)
def test_invalid_request_is_rejected_before_debit(
    client: TestClient, body: dict[str, object]
) -> None:
    uid = str(uuid.uuid4())
    counting = CountingCoachAIService(client.app.state.ai_service)
    client.app.state.ai_service = counting
    before = _tokens(client, uid)

    response = _coach(client, uid=uid, request_id=str(uuid.uuid4()), **body)  # type: ignore[arg-type]

    assert response.status_code == 422, response.text
    assert counting.calls == 0
    assert _tokens(client, uid) == before


@pytest.mark.parametrize(
    "extra",
    [
        {"targetLevel": "SUPER"},
        {"topic": "not-a-topic"},
        {"meaningKo": "뜻" * 121},
        {"unexpectedField": "nope"},
    ],
)
def test_invalid_optional_fields_are_rejected_before_debit(
    client: TestClient, extra: dict[str, object]
) -> None:
    uid = str(uuid.uuid4())
    counting = CountingCoachAIService(client.app.state.ai_service)
    client.app.state.ai_service = counting
    before = _tokens(client, uid)

    response = _coach(client, uid=uid, request_id=str(uuid.uuid4()), extra=extra)

    assert response.status_code == 422, response.text
    assert counting.calls == 0
    assert _tokens(client, uid) == before


def test_valid_optional_fields_are_accepted(client: TestClient) -> None:
    uid = str(uuid.uuid4())

    response = _coach(
        client,
        uid=uid,
        request_id=str(uuid.uuid4()),
        extra={"meaningKo": "붐비는, 사람이 많은", "topic": "cafes", "targetLevel": "IH"},
    )

    assert response.status_code == 200, response.text
    _assert_usable_coaching(response.json())


def test_missing_idempotency_key_is_rejected_before_debit(client: TestClient) -> None:
    """조작 신원이 없으면 시작조차 하지 않는다(재생·환불을 걸 자리가 없다)."""
    uid = str(uuid.uuid4())
    before = _tokens(client, uid)

    response = _coach(client, uid=uid, request_id=None)

    assert response.status_code == 400, response.text
    assert _tokens(client, uid) == before


def test_transcript_at_the_maximum_is_accepted(client: TestClient) -> None:
    """상한 자체는 통과해야 한다 — 조용히 잘라서 과금하는 대신 경계를 명확히 둔다."""
    uid = str(uuid.uuid4())
    transcript = "I go there a lot. " * 33  # 594자
    assert len(transcript) <= VOCABULARY_TRANSCRIPT_MAX_CHARS

    response = _coach(client, uid=uid, request_id=str(uuid.uuid4()), transcript=transcript)

    assert response.status_code == 200, response.text


# --- 14~16. 세 가지 사용 양상 모두 코칭 결과다 ---------------------------------


@pytest.mark.parametrize("item_type", ["word", "phrase", "pattern"])
def test_every_vocabulary_type_is_coached(client: TestClient, item_type: str) -> None:
    uid = str(uuid.uuid4())
    term = {"word": "crowded", "phrase": "spend time with", "pattern": "one of my favorite places"}[
        item_type
    ]

    response = _coach(
        client,
        uid=uid,
        request_id=str(uuid.uuid4()),
        term=term,
        item_type=item_type,
        transcript=f"I think {term} is how I would describe my weekend.",
    )

    assert response.status_code == 200, response.text
    assert response.json()["targetTerm"] == term
    _assert_usable_coaching(response.json())


def test_correctly_used_term_is_acknowledged(client: TestClient) -> None:
    uid = str(uuid.uuid4())

    payload = _coach(
        client,
        uid=uid,
        request_id=str(uuid.uuid4()),
        transcript="My favorite cafe gets crowded on weekends.",
    ).json()

    assert payload["usageAssessment"] == VocabularyUsageAssessment.APPROPRIATE.value
    _assert_usable_coaching(payload)


def test_unused_term_is_still_a_coaching_result(client: TestClient) -> None:
    """표현을 안 썼다고 인프라 오류가 아니다. 쓰는 법을 보여주는 코칭이 나온다."""
    uid = str(uuid.uuid4())

    response = _coach(
        client,
        uid=uid,
        request_id=str(uuid.uuid4()),
        transcript="I like going to the park with my dog every morning.",
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["usageAssessment"] == VocabularyUsageAssessment.NOT_USED.value
    _assert_usable_coaching(payload)


def test_awkward_unrelated_answer_is_still_a_coaching_result(client: TestClient) -> None:
    uid = str(uuid.uuid4())

    response = _coach(
        client,
        uid=uid,
        request_id=str(uuid.uuid4()),
        transcript="Crowded crowded I go store yesterday very much people.",
    )

    assert response.status_code == 200, response.text
    _assert_usable_coaching(response.json())


# --- 프롬프트 규칙: 문자열 일치만으로 "잘 썼다"고 하지 않는다 -------------------


def test_coach_prompt_forbids_string_matching_and_grades() -> None:
    text = vocabulary.coach_instructions(OPIcLevel.IH)

    assert "string matching" in text
    assert "just because the characters appear" in text
    assert "no OPIc grade" in text
    assert "no scores" in text
    assert "IH" in text


def test_coach_input_text_omits_optional_fields_when_absent() -> None:
    text = vocabulary.coach_input_text(
        term="crowded",
        item_type=VocabularyItemType.WORD,
        meaning_ko=None,
        topic=None,
        transcript=TRANSCRIPT,
    )

    assert "Korean meaning" not in text
    assert "Topic being practised" not in text
    assert TRANSCRIPT in text
