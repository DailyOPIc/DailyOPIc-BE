"""오늘의 단어 추가 20개(P14.6) — 구성 20개 · 예전 30개 계약 유지 · 과금 1개.

같은 엔드포인트 · 같은 토큰 경로(`reserve_practice`)를 쓴다. 달라지는 것은
요청의 쓰임새(`purpose`)가 고르는 **구성**뿐이다:

  - 필드 없음 / `custom_set` → 30개(10/10/10). 예전 클라이언트가 여기로 온다.
  - `today_extra`            → 20개(7/7/6).

어느 쪽이든 값은 데일리 토큰 1개다 — 개수가 다르다고 과금이 달라지지 않는다.

`conftest.py` 가 `MOCK_AI=true` 를 세팅하므로 OpenAI 는 호출되지 않는다.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.api import VocabularyGenerationPurpose, VocabularyItemType
from app.services import vocabulary
from app.services.ai import AIServiceUnavailable, VocabularyGenerationResult
from tests.test_api import _headers
from tests.test_vocabulary_generation import (
    CountingVocabularyAIService,
    FailingVocabularyAIService,
    _composition,
    _tokens,
)


PATH = "/v1/vocabulary/generate"
EXTRA = VocabularyGenerationPurpose.TODAY_EXTRA.value


class ShortVocabularyAIService:
    """정원보다 하나 모자란 결과를 내는 제공자. 부분 결과를 저장하면 안 된다."""

    model = "test-model"

    def __init__(self, inner: object) -> None:
        self._inner = inner

    async def generate_vocabulary(self, **kwargs: object) -> VocabularyGenerationResult:
        result = await self._inner.generate_vocabulary(**kwargs)
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
    purpose: str | None = EXTRA,
    topic: str = "cafes",
):
    body: dict[str, object] = {"topic": topic, "targetLevel": "IH"}
    if purpose is not None:
        body["purpose"] = purpose
    return client.post(PATH, headers=_headers(request_id, uid=uid), json=body)


# --- 구성 -------------------------------------------------------------------


def test_today_extra_returns_exactly_twenty_usable_entries(client: TestClient) -> None:
    """정확히 20개. 30개를 만들어 앱에서 10개를 버리는 방식이 아니다."""
    payload = _generate(client, uid=str(uuid.uuid4()), request_id=str(uuid.uuid4())).json()

    entries = payload["entries"]
    assert len(entries) == 20
    assert vocabulary.set_size(VocabularyGenerationPurpose.TODAY_EXTRA) == 20
    # 쓸 만한 20개다 — 빈 항목도, 같은 표현의 중복도 없다.
    assert all(entry["term"].strip() and entry["meaningKo"].strip() for entry in entries)
    assert len({entry["term"].strip().lower() for entry in entries}) == 20


def test_today_extra_composition_is_seven_seven_six(client: TestClient) -> None:
    """단어 · 표현 · 패턴이 모두 들어간다. 한 종류로 20개를 채우지 않는다."""
    payload = _generate(client, uid=str(uuid.uuid4()), request_id=str(uuid.uuid4())).json()

    assert _composition(payload) == {"word": 7, "phrase": 7, "pattern": 6}
    assert all(count > 0 for count in _composition(payload).values())


def test_today_extra_keeps_the_selected_topic(client: TestClient) -> None:
    payload = _generate(
        client, uid=str(uuid.uuid4()), request_id=str(uuid.uuid4()), topic="travel"
    ).json()

    assert payload["topic"] == "travel"
    assert all(entry["topics"] == ["travel"] for entry in payload["entries"])


def test_today_extra_keeps_the_existing_response_contract(client: TestClient) -> None:
    """응답 모양은 그대로다 — 앱의 저장·학습 경로가 그대로 돌아간다."""
    payload = _generate(client, uid=str(uuid.uuid4()), request_id=str(uuid.uuid4())).json()

    assert set(payload) == {"setId", "topic", "targetLevel", "createdAt", "entries", "source"}
    assert payload["source"] == "ai"
    assert all(entry["id"].startswith("ai-") for entry in payload["entries"])
    assert all(entry["source"] == "ai" for entry in payload["entries"])


# --- 예전 클라이언트 호환 ----------------------------------------------------


def test_request_without_purpose_still_returns_thirty(client: TestClient) -> None:
    """필드를 모르는 예전 앱은 예전 계약(30개 = 10/10/10)을 그대로 받는다."""
    payload = _generate(
        client, uid=str(uuid.uuid4()), request_id=str(uuid.uuid4()), purpose=None
    ).json()

    assert len(payload["entries"]) == vocabulary.SET_SIZE == 30
    assert _composition(payload) == {"word": 10, "phrase": 10, "pattern": 10}


def test_explicit_custom_set_purpose_matches_the_old_default(client: TestClient) -> None:
    payload = _generate(
        client, uid=str(uuid.uuid4()), request_id=str(uuid.uuid4()), purpose="custom_set"
    ).json()

    assert len(payload["entries"]) == 30
    assert _composition(payload) == {"word": 10, "phrase": 10, "pattern": 10}


def test_unknown_purpose_is_rejected_without_debit(client: TestClient) -> None:
    uid = str(uuid.uuid4())
    before = _tokens(client, uid)

    response = _generate(
        client, uid=uid, request_id=str(uuid.uuid4()), purpose="today_extra_v2"
    )

    assert response.status_code == 422, response.text
    assert _tokens(client, uid) == before


# --- 토큰 -------------------------------------------------------------------


def test_today_extra_debits_exactly_one_daily_token(client: TestClient) -> None:
    """20개라고 값이 달라지지 않는다. 조작 1회 = 데일리 토큰 1개."""
    uid = str(uuid.uuid4())
    before = _tokens(client, uid)

    assert _generate(client, uid=uid, request_id=str(uuid.uuid4())).status_code == 200

    assert _tokens(client, uid) == before - 1


def test_replaying_the_same_operation_costs_nothing_more(client: TestClient) -> None:
    """같은 조작 id 재전송 = 저장된 결과 재생. 제공자도 다시 부르지 않는다."""
    uid = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    counting = CountingVocabularyAIService(client.app.state.ai_service)
    client.app.state.ai_service = counting

    first = _generate(client, uid=uid, request_id=request_id).json()
    after_first = _tokens(client, uid)
    calls = counting.calls
    second = _generate(client, uid=uid, request_id=request_id).json()

    assert second == first
    assert len(second["entries"]) == 20
    assert _tokens(client, uid) == after_first
    assert counting.calls == calls


def test_zero_token_blocks_today_extra_before_the_provider(client: TestClient) -> None:
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


def test_provider_failure_refunds_exactly_once(client: TestClient) -> None:
    uid = str(uuid.uuid4())
    before = _tokens(client, uid)
    client.app.state.ai_service = FailingVocabularyAIService()

    response = _generate(client, uid=uid, request_id=str(uuid.uuid4()))

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "ai_unavailable"
    assert _tokens(client, uid) == before


def test_incomplete_twenty_is_a_failure_not_a_short_set(client: TestClient) -> None:
    """19개는 성공이 아니다. 모자란 결과를 저장하지 않고 환불한다."""
    uid = str(uuid.uuid4())
    before = _tokens(client, uid)
    client.app.state.ai_service = ShortVocabularyAIService(client.app.state.ai_service)

    response = _generate(client, uid=uid, request_id=str(uuid.uuid4()))

    assert response.status_code == 503, response.text
    assert _tokens(client, uid) == before


def test_retry_after_failure_succeeds_with_one_total_debit(client: TestClient) -> None:
    """실패로 환불된 뒤 같은 키로 다시 성공해도 총 차감은 1개다."""
    uid = str(uuid.uuid4())
    before = _tokens(client, uid)
    working = client.app.state.ai_service
    client.app.state.ai_service = FailingVocabularyAIService()
    request_id = str(uuid.uuid4())
    assert _generate(client, uid=uid, request_id=request_id).status_code == 503

    client.app.state.ai_service = working
    response = _generate(client, uid=uid, request_id=request_id)

    assert response.status_code == 200, response.text
    assert len(response.json()["entries"]) == 20
    assert _tokens(client, uid) == before - 1


# --- 서버가 개수를 소유한다 --------------------------------------------------


def test_request_cannot_choose_its_own_count(client: TestClient) -> None:
    """`count` 같은 값을 보내도 받아 주지 않는다(extra=forbid)."""
    uid = str(uuid.uuid4())
    before = _tokens(client, uid)

    response = client.post(
        PATH,
        headers=_headers(str(uuid.uuid4()), uid=uid),
        json={"topic": "cafes", "targetLevel": "IH", "purpose": EXTRA, "count": 50},
    )

    assert response.status_code == 422, response.text
    assert _tokens(client, uid) == before


def test_every_purpose_has_a_server_owned_composition() -> None:
    for purpose in VocabularyGenerationPurpose:
        limits = vocabulary.composition(purpose)
        assert set(limits) == set(VocabularyItemType)
        assert all(count > 0 for count in limits.values())
        assert sum(limits.values()) == vocabulary.set_size(purpose)
