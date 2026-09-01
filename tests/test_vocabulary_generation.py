"""단어 만들기 생성(P14.6) — 과금 · 구성 · 재생 · 환불.

핵심 규약은 하나다: **사용자 조작 1회 = 데일리 토큰 1개 = 20개(7/7/6)**.
내부에서 제공자를 몇 번 부르든, 클라이언트가 같은 키로 몇 번 재전송하든
차감은 1개를 넘지 않는다. 쓸 만한 결과가 없으면 정확히 한 번 환불된다.

`conftest.py` 가 `MOCK_AI=true` 를 세팅하므로 OpenAI 는 호출되지 않는다.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.api import OPIcLevel, VocabularyItemType, VocabularyTopic
from app.services import vocabulary
from app.services.ai import AIService, AIServiceUnavailable, VocabularyGenerationResult
from app.services.questions import QuestionPatternRepository
from tests.test_api import _headers


PATH = "/v1/vocabulary/today/generate"


class FailingVocabularyAIService:
    model = "test-model"

    async def generate_today_vocabulary(self, *args: object, **kwargs: object) -> object:
        raise AIServiceUnavailable("forced vocabulary failure")


class CountingVocabularyAIService:
    """실제 생성기를 감싸 호출 횟수만 센다(제공자가 몇 번 불렸는지 확인용)."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.calls = 0
        self.model = getattr(inner, "model", "test-model")

    async def generate_today_vocabulary(self, **kwargs: object) -> object:
        self.calls += 1
        return await self._inner.generate_today_vocabulary(**kwargs)


class ShortVocabularyAIService:
    """정원보다 하나 모자란 결과를 내는 제공자. 부분 결과를 저장하면 안 된다."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.model = getattr(inner, "model", "test-model")

    async def generate_today_vocabulary(self, **kwargs: object) -> object:
        result = await self._inner.generate_today_vocabulary(**kwargs)
        return VocabularyGenerationResult(
            drafts=result.drafts[:-1],
            provider=result.provider,
            attempts=result.attempts,
            usage=result.usage,
        )


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as value:
        yield value


def _generate(
    client: TestClient,
    *,
    uid: str,
    request_id: str,
    topic: str = "cafes",
    target_level: str = "IH",
    exclude_terms: list[str] | None = None,
):
    body: dict[str, object] = {"topic": topic, "targetLevel": target_level}
    if exclude_terms is not None:
        body["excludeTerms"] = exclude_terms
    return client.post(PATH, headers=_headers(request_id, uid=uid), json=body)


def _tokens(client: TestClient, uid: str) -> int:
    response = client.get("/v1/usage", headers=_headers(uid=uid))
    assert response.status_code == 200, response.text
    payload = response.json()
    return payload["freeRemaining"] + payload["bonusRemaining"]


def _composition(payload: dict) -> dict[str, int]:
    return {
        item.value: sum(1 for entry in payload["entries"] if entry["type"] == item.value)
        for item in VocabularyItemType
    }


def test_generation_debits_exactly_one_daily_token(client: TestClient) -> None:
    """성공 1회 = 토큰 정확히 1개. 20개를 만들었다고 20개를 받지 않는다."""
    uid = str(uuid.uuid4())
    before = _tokens(client, uid)

    response = _generate(client, uid=uid, request_id=str(uuid.uuid4()))

    assert response.status_code == 200, response.text
    assert _tokens(client, uid) == before - 1


def test_zero_token_rejects_before_calling_provider(client: TestClient) -> None:
    """잔액 0이면 402. 제공자는 **호출되지 않는다** — 돈 나가는 호출이 먼저다."""
    uid = str(uuid.uuid4())
    counting = CountingVocabularyAIService(client.app.state.ai_service)
    client.app.state.ai_service = counting
    while _tokens(client, uid) > 0:
        assert _generate(client, uid=uid, request_id=str(uuid.uuid4())).status_code == 200
    spent_calls = counting.calls

    response = _generate(client, uid=uid, request_id=str(uuid.uuid4()))

    assert response.status_code == 402, response.text
    assert response.json()["detail"]["code"] == "practice_quota_exhausted"
    assert counting.calls == spent_calls


def test_generated_set_has_twenty_entries(client: TestClient) -> None:
    uid = str(uuid.uuid4())
    payload = _generate(client, uid=uid, request_id=str(uuid.uuid4())).json()

    assert len(payload["entries"]) == vocabulary.TODAY_SET_SIZE
    assert payload["source"] == "ai"
    assert payload["topic"] == "cafes"
    assert payload["targetLevel"] == "IH"


def test_generated_set_composition_is_seven_seven_six(client: TestClient) -> None:
    """7 단어 / 7 표현 / 6 패턴. 고립된 단어 20개짜리 목록이 아니다."""
    uid = str(uuid.uuid4())
    payload = _generate(client, uid=uid, request_id=str(uuid.uuid4())).json()

    assert _composition(payload) == {"word": 7, "phrase": 7, "pattern": 6}
    ids = [entry["id"] for entry in payload["entries"]]
    assert len(set(ids)) == vocabulary.TODAY_SET_SIZE
    # 시드 항목과 절대 겹치지 않는 접두사.
    assert all(entry_id.startswith("ai-") for entry_id in ids)


def test_excluded_terms_are_filtered_out(client: TestClient) -> None:
    """클라이언트가 보낸 제외 목록은 정규화 후 비교한다(대소문자·문장부호 무시)."""
    uid = str(uuid.uuid4())

    payload = _generate(
        client,
        uid=uid,
        request_id=str(uuid.uuid4()),
        exclude_terms=["Crowded!", " cozy ", "get crowded"],
    ).json()

    terms = {entry["term"].lower() for entry in payload["entries"]}
    assert not terms & {"crowded", "cozy", "get crowded"}
    assert len(payload["entries"]) == vocabulary.TODAY_SET_SIZE


def test_duplicate_candidates_are_deduplicated() -> None:
    """같은 표현의 표기 변형은 한 번만 담긴다."""
    selection = vocabulary.VocabularySelection.create([], vocabulary.TODAY_COMPOSITION)
    draft = vocabulary.VocabularyDraft(
        term="cozy",
        type=VocabularyItemType.WORD,
        meaningKo="아늑한",
        exampleEn="The cafe is really cozy at night.",
        exampleKo="그 카페는 밤에 정말 아늑해요.",
        usageRoles=["description"],
    )
    variant = draft.model_copy(update={"term": " Cozy. "})

    assert selection.add([draft, variant]) == 1
    assert (
        selection.needed()[VocabularyItemType.WORD]
        == vocabulary.TODAY_COMPOSITION[VocabularyItemType.WORD] - 1
    )


def test_malformed_provider_entries_are_rejected() -> None:
    """뜻·예문이 빈 항목은 담지 않는다. 쓰레기를 저장하느니 부족분으로 남긴다."""
    selection = vocabulary.VocabularySelection.create([], vocabulary.TODAY_COMPOSITION)
    blank_meaning = vocabulary.VocabularyDraft(
        term="lively",
        type=VocabularyItemType.WORD,
        meaningKo="   ",
        exampleEn="The street gets lively in the evening.",
        exampleKo="그 거리는 저녁에 활기차져요.",
        usageRoles=["description"],
    )
    blank_example = blank_meaning.model_copy(
        update={"term": "peaceful", "meaningKo": "평화로운", "exampleEn": "        "}
    )

    assert selection.add([blank_meaning, blank_example]) == 0
    with pytest.raises(ValueError):
        vocabulary.VocabularyDraft(
            term="",
            type=VocabularyItemType.WORD,
            meaningKo="빈 표제어",
            exampleEn="This should not validate.",
            exampleKo="이건 통과하면 안 돼요.",
            usageRoles=["description"],
        )


async def test_bounded_fill_completes_missing_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """첫 응답이 한 종류를 통째로 빠뜨려도 보충 1회로 채운다(호출 상한 안에서)."""
    service = AIService(
        api_key=None,
        model="test-model",
        mock=True,
        repository=QuestionPatternRepository(Path("app/data/question_patterns.json")),
    )

    calls: list[dict[VocabularyItemType, int]] = []
    original = vocabulary.mock_drafts

    def flaky(*, topic, needed):  # type: ignore[no-untyped-def]
        calls.append(dict(needed))
        drafts = original(topic=topic, needed=needed)
        if len(calls) == 1:
            drafts = [d for d in drafts if d.type is not VocabularyItemType.PATTERN]
        return drafts

    monkeypatch.setattr(vocabulary, "mock_drafts", flaky)
    result = await service.generate_today_vocabulary(
        topic=VocabularyTopic.CAFES,
        target_level=OPIcLevel.IH,
        exclude_terms=[],
    )

    assert len(result.drafts) == vocabulary.TODAY_SET_SIZE
    assert result.attempts == 2
    assert calls[1] == {
        VocabularyItemType.PATTERN: vocabulary.TODAY_COMPOSITION[
            VocabularyItemType.PATTERN
        ]
    }
    assert len(calls) <= vocabulary.MAX_PROVIDER_ATTEMPTS


def test_internal_fill_does_not_cause_a_second_debit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """보충 호출은 내부 사정이다. 사용자 차감은 그대로 1개."""
    uid = str(uuid.uuid4())
    before = _tokens(client, uid)
    calls = 0
    original = vocabulary.mock_drafts

    def flaky(*, topic, needed):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        drafts = original(topic=topic, needed=needed)
        if calls == 1:
            drafts = [d for d in drafts if d.type is not VocabularyItemType.PHRASE]
        return drafts

    monkeypatch.setattr(vocabulary, "mock_drafts", flaky)
    response = _generate(client, uid=uid, request_id=str(uuid.uuid4()))

    assert response.status_code == 200, response.text
    assert calls == 2
    assert _tokens(client, uid) == before - 1


def test_replay_returns_same_result_without_extra_debit_or_provider_call(
    client: TestClient,
) -> None:
    """같은 조작의 재전송(응답 유실·앱 재시도)은 저장된 결과를 그대로 돌려준다."""
    uid = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    before = _tokens(client, uid)

    first = _generate(client, uid=uid, request_id=request_id)
    assert first.status_code == 200, first.text
    after_first = _tokens(client, uid)

    counting = CountingVocabularyAIService(client.app.state.ai_service)
    client.app.state.ai_service = counting
    second = _generate(client, uid=uid, request_id=request_id)

    assert second.status_code == 200, second.text
    assert second.json() == first.json()
    assert counting.calls == 0
    assert after_first == before - 1
    assert _tokens(client, uid) == after_first


def test_provider_failure_refunds_exactly_once(client: TestClient) -> None:
    """쓸 만한 결과가 없으면 503 + 정확히 한 번 환불. 재시도해도 두 번 환불되지 않는다."""
    uid = str(uuid.uuid4())
    before = _tokens(client, uid)
    client.app.state.ai_service = FailingVocabularyAIService()
    request_id = str(uuid.uuid4())

    first = _generate(client, uid=uid, request_id=request_id)
    assert first.status_code == 503, first.text
    assert first.json()["detail"]["code"] == "ai_unavailable"
    assert _tokens(client, uid) == before

    second = _generate(client, uid=uid, request_id=request_id)
    assert second.status_code == 503, second.text
    assert _tokens(client, uid) == before


def test_retry_after_failure_can_succeed_with_one_total_debit(
    client: TestClient,
) -> None:
    """실패로 환불된 뒤 같은 키로 재시도하면 그때 한 번만 차감된다."""
    uid = str(uuid.uuid4())
    before = _tokens(client, uid)
    healthy = client.app.state.ai_service
    client.app.state.ai_service = FailingVocabularyAIService()
    request_id = str(uuid.uuid4())

    assert _generate(client, uid=uid, request_id=request_id).status_code == 503
    client.app.state.ai_service = healthy
    retried = _generate(client, uid=uid, request_id=request_id)

    assert retried.status_code == 200, retried.text
    assert _tokens(client, uid) == before - 1


def test_invalid_target_level_is_rejected_without_debit(client: TestClient) -> None:
    uid = str(uuid.uuid4())
    before = _tokens(client, uid)

    response = _generate(
        client, uid=uid, request_id=str(uuid.uuid4()), target_level="SUPER"
    )

    assert response.status_code == 422, response.text
    assert _tokens(client, uid) == before


def test_invalid_topic_is_rejected_without_debit(client: TestClient) -> None:
    uid = str(uuid.uuid4())
    before = _tokens(client, uid)

    response = _generate(
        client, uid=uid, request_id=str(uuid.uuid4()), topic="not-a-topic"
    )

    assert response.status_code == 422, response.text
    assert _tokens(client, uid) == before


def test_oversized_exclusion_list_is_rejected(client: TestClient) -> None:
    """제외 목록은 상한이 있다 — 시드 카탈로그 전체를 프롬프트에 밀어 넣지 못한다."""
    uid = str(uuid.uuid4())

    response = _generate(
        client,
        uid=uid,
        request_id=str(uuid.uuid4()),
        exclude_terms=[f"term-{index}" for index in range(500)],
    )

    assert response.status_code == 422, response.text


def test_missing_idempotency_key_is_rejected_before_debit(client: TestClient) -> None:
    """조작 신원이 없으면 시작조차 하지 않는다(재생·환불을 걸 자리가 없다)."""
    uid = str(uuid.uuid4())
    before = _tokens(client, uid)

    response = client.post(
        PATH, headers=_headers(uid=uid), json={"topic": "cafes", "targetLevel": "IH"}
    )

    assert response.status_code == 400, response.text
    assert _tokens(client, uid) == before


# --- 계약 회귀 ------------------------------------------------------------------
#
# 개수 · 구성 · 응답 키는 서버가 소유한다. 앱이 보내는 JSON은 세 필드뿐이고,
# 아래 세 개가 그 계약을 못 박는다.


def test_the_shipped_request_body_is_accepted_verbatim(client: TestClient) -> None:
    """앱이 보내는 JSON 그대로. 새 필드를 요구하지 않는다."""
    uid = str(uuid.uuid4())
    before = _tokens(client, uid)

    response = client.post(
        PATH,
        headers=_headers(str(uuid.uuid4()), uid=uid),
        json={"topic": "cafes", "targetLevel": "IH", "excludeTerms": ["cozy"]},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["entries"]) == 20
    assert _composition(payload) == {"word": 7, "phrase": 7, "pattern": 6}
    assert _tokens(client, uid) == before - 1


def test_the_request_cannot_choose_its_own_count_or_purpose(client: TestClient) -> None:
    """개수도 쓰임새도 요청이 정하지 못한다(extra=forbid). 거절이고 차감도 없다."""
    uid = str(uuid.uuid4())
    before = _tokens(client, uid)

    for body in (
        {"topic": "cafes", "targetLevel": "IH", "count": 50},
        {"topic": "cafes", "targetLevel": "IH", "purpose": "today_extra"},
    ):
        response = client.post(
            PATH, headers=_headers(str(uuid.uuid4()), uid=uid), json=body
        )
        assert response.status_code == 422, response.text

    assert _tokens(client, uid) == before


def test_response_fields_are_unchanged(client: TestClient) -> None:
    """세트 · 항목의 키가 P14.2 그대로다. 앱의 해석 코드가 그대로 돈다."""
    uid = str(uuid.uuid4())
    payload = _generate(client, uid=uid, request_id=str(uuid.uuid4())).json()

    assert set(payload) == {"setId", "topic", "targetLevel", "createdAt", "entries", "source"}
    assert set(payload["entries"][0]) == {
        "id",
        "term",
        "type",
        "meaningKo",
        "exampleEn",
        "exampleKo",
        "collocations",
        "topics",
        "usageRoles",
        "recommendedLevels",
        "source",
    }


def test_incomplete_set_is_a_failure_not_a_short_set(client: TestClient) -> None:
    """19개는 성공이 아니다. 모자란 결과를 저장하지 않고 정확히 한 번 환불한다."""
    uid = str(uuid.uuid4())
    before = _tokens(client, uid)
    client.app.state.ai_service = ShortVocabularyAIService(client.app.state.ai_service)

    response = _generate(client, uid=uid, request_id=str(uuid.uuid4()))

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "ai_unavailable"
    assert _tokens(client, uid) == before
