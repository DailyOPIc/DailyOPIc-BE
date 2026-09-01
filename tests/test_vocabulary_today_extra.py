"""오늘의 단어 만들기(P14.6.1) — 전용 endpoint 20개 · 예전 30개 계약과 완전 분리.

P14.6에서는 두 계약이 `/v1/vocabulary/generate` 하나를 `purpose`로 나눠 썼다.
그 분기를 걷어내고 경로를 둘로 갈랐다:

  - `POST /v1/vocabulary/generate`       → P14.2 그대로 30개(10/10/10). 손대지 않는다.
  - `POST /v1/vocabulary/today/generate` → 오늘의 단어 20개(7/7/6). 이번에 추가.

요청 모델도 · 서비스 함수도 · 구성 상수도 · 토큰 조작 이름공간도 서로 다르다.
어느 쪽이든 값은 데일리 토큰 1개다 — 개수가 다르다고 과금이 달라지지 않는다.

`conftest.py` 가 `MOCK_AI=true` 를 세팅하므로 OpenAI 는 호출되지 않는다.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.main import app
from app.models.api import VocabularyItemType
from app.services import vocabulary
from app.services.ai import AIService, AIServiceUnavailable, VocabularyGenerationResult
from tests.test_api import _headers
from tests.test_vocabulary_generation import _composition, _tokens


PATH = "/v1/vocabulary/today/generate"
OLD_PATH = "/v1/vocabulary/generate"


class CountingTodayAIService:
    """실제 생성기를 감싸 오늘 경로의 호출 횟수만 센다."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.calls = 0
        self.old_calls = 0
        self.model = getattr(inner, "model", "test-model")

    async def generate_today_vocabulary(self, **kwargs: object) -> object:
        self.calls += 1
        return await self._inner.generate_today_vocabulary(**kwargs)

    async def generate_vocabulary(self, **kwargs: object) -> object:
        self.old_calls += 1
        return await self._inner.generate_vocabulary(**kwargs)


class FailingTodayAIService:
    """오늘 경로만 실패하는 제공자. 예전 30개 경로는 멀쩡히 돈다."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.model = getattr(inner, "model", "test-model")

    async def generate_today_vocabulary(self, *args: object, **kwargs: object) -> object:
        raise AIServiceUnavailable("forced today vocabulary failure")

    async def generate_vocabulary(self, **kwargs: object) -> object:
        return await self._inner.generate_vocabulary(**kwargs)


class ShortTodayAIService:
    """정원보다 하나 모자란 결과를 내는 제공자. 부분 결과를 저장하면 안 된다."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.model = getattr(inner, "model", "test-model")

    async def generate_today_vocabulary(
        self, **kwargs: object
    ) -> VocabularyGenerationResult:
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
):
    return client.post(
        PATH,
        headers=_headers(request_id, uid=uid),
        json={"topic": topic, "targetLevel": "IH"},
    )


def _generate_old(client: TestClient, *, uid: str, request_id: str):
    return client.post(
        OLD_PATH,
        headers=_headers(request_id, uid=uid),
        json={"topic": "cafes", "targetLevel": "IH"},
    )


# --- 구성 -------------------------------------------------------------------


def test_today_returns_exactly_twenty_usable_entries(client: TestClient) -> None:
    """정확히 20개. 30개를 만들어 앱에서 10개를 버리는 방식이 아니다."""
    payload = _generate(client, uid=str(uuid.uuid4()), request_id=str(uuid.uuid4())).json()

    entries = payload["entries"]
    assert len(entries) == 20
    assert vocabulary.TODAY_SET_SIZE == 20
    # 쓸 만한 20개다 — 빈 항목도, 같은 표현의 중복도 없다.
    assert all(entry["term"].strip() and entry["meaningKo"].strip() for entry in entries)
    assert len({entry["term"].strip().lower() for entry in entries}) == 20


def test_today_composition_is_seven_seven_six(client: TestClient) -> None:
    """단어 · 표현 · 패턴이 모두 들어간다. 한 종류로 20개를 채우지 않는다."""
    payload = _generate(client, uid=str(uuid.uuid4()), request_id=str(uuid.uuid4())).json()

    assert _composition(payload) == {"word": 7, "phrase": 7, "pattern": 6}
    assert all(count > 0 for count in _composition(payload).values())


def test_today_keeps_the_selected_topic(client: TestClient) -> None:
    payload = _generate(
        client, uid=str(uuid.uuid4()), request_id=str(uuid.uuid4()), topic="travel"
    ).json()

    assert payload["topic"] == "travel"
    assert all(entry["topics"] == ["travel"] for entry in payload["entries"])


def test_today_uses_the_same_response_shape(client: TestClient) -> None:
    """응답 모양은 예전과 같다 — 앱의 저장·학습 경로가 그대로 돌아간다."""
    payload = _generate(client, uid=str(uuid.uuid4()), request_id=str(uuid.uuid4())).json()

    assert set(payload) == {"setId", "topic", "targetLevel", "createdAt", "entries", "source"}
    assert payload["source"] == "ai"
    assert all(entry["id"].startswith("ai-") for entry in payload["entries"])
    assert all(entry["source"] == "ai" for entry in payload["entries"])


def test_today_request_cannot_choose_its_own_count(client: TestClient) -> None:
    """개수도 쓰임새도 요청이 정하지 못한다(extra=forbid)."""
    uid = str(uuid.uuid4())
    before = _tokens(client, uid)

    for body in (
        {"topic": "cafes", "targetLevel": "IH", "count": 50},
        {"topic": "cafes", "targetLevel": "IH", "purpose": "today_extra"},
    ):
        response = client.post(PATH, headers=_headers(str(uuid.uuid4()), uid=uid), json=body)
        assert response.status_code == 422, response.text

    assert _tokens(client, uid) == before


# --- 토큰 -------------------------------------------------------------------


def test_today_debits_exactly_one_daily_token(client: TestClient) -> None:
    """20개라고 값이 달라지지 않는다. 조작 1회 = 데일리 토큰 1개."""
    uid = str(uuid.uuid4())
    before = _tokens(client, uid)

    assert _generate(client, uid=uid, request_id=str(uuid.uuid4())).status_code == 200

    assert _tokens(client, uid) == before - 1


def test_replaying_the_same_operation_costs_nothing_more(client: TestClient) -> None:
    """같은 조작 id 재전송 = 저장된 결과 재생. 제공자도 다시 부르지 않는다."""
    uid = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    counting = CountingTodayAIService(client.app.state.ai_service)
    client.app.state.ai_service = counting

    first = _generate(client, uid=uid, request_id=request_id).json()
    after_first = _tokens(client, uid)
    calls = counting.calls
    second = _generate(client, uid=uid, request_id=request_id).json()

    assert second == first
    assert len(second["entries"]) == 20
    assert _tokens(client, uid) == after_first
    assert counting.calls == calls


def test_zero_token_blocks_today_before_the_provider(client: TestClient) -> None:
    uid = str(uuid.uuid4())
    counting = CountingTodayAIService(client.app.state.ai_service)
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
    client.app.state.ai_service = FailingTodayAIService(client.app.state.ai_service)

    response = _generate(client, uid=uid, request_id=str(uuid.uuid4()))

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "ai_unavailable"
    assert _tokens(client, uid) == before


def test_incomplete_twenty_is_a_failure_not_a_short_set(client: TestClient) -> None:
    """19개는 성공이 아니다. 모자란 결과를 저장하지 않고 환불한다."""
    uid = str(uuid.uuid4())
    before = _tokens(client, uid)
    client.app.state.ai_service = ShortTodayAIService(client.app.state.ai_service)

    response = _generate(client, uid=uid, request_id=str(uuid.uuid4()))

    assert response.status_code == 503, response.text
    assert _tokens(client, uid) == before


def test_retry_after_failure_succeeds_with_one_total_debit(client: TestClient) -> None:
    """실패로 환불된 뒤 같은 키로 다시 성공해도 총 차감은 1개다."""
    uid = str(uuid.uuid4())
    before = _tokens(client, uid)
    working = client.app.state.ai_service
    client.app.state.ai_service = FailingTodayAIService(working)
    request_id = str(uuid.uuid4())
    assert _generate(client, uid=uid, request_id=request_id).status_code == 503

    client.app.state.ai_service = working
    response = _generate(client, uid=uid, request_id=request_id)

    assert response.status_code == 200, response.text
    assert len(response.json()["entries"]) == 20
    assert _tokens(client, uid) == before - 1


# --- 두 계약의 격리 ----------------------------------------------------------


def test_both_endpoints_coexist(client: TestClient) -> None:
    """두 계약이 나란히 산다. 각자 자기 개수를 지킨다.

    데일리 토큰은 하루 1개라 한 사용자가 둘을 다 부를 수 없다. 여기서 보는 것은
    과금이 아니라 두 경로가 동시에 살아 있다는 사실이므로 사용자를 나눈다.
    """
    old = _generate_old(client, uid=str(uuid.uuid4()), request_id=str(uuid.uuid4())).json()
    today = _generate(client, uid=str(uuid.uuid4()), request_id=str(uuid.uuid4())).json()

    assert len(old["entries"]) == 30
    assert _composition(old) == {"word": 10, "phrase": 10, "pattern": 10}
    assert len(today["entries"]) == 20
    assert _composition(today) == {"word": 7, "phrase": 7, "pattern": 6}
    assert old["setId"] != today["setId"]


def test_the_same_idempotency_key_never_crosses_contracts(client: TestClient) -> None:
    """조작 이름공간이 다르다 — 같은 키를 써도 30개 결과가 20개 응답으로 재생되지 않는다.

    같은 사용자가 남은 토큰 1개를 예전 계약에 쓴 뒤, **같은 키로** 오늘 경로를 부른다.
    이름공간이 겹쳤다면 저장된 30개 세트가 200으로 그대로 나왔을 것이다. 실제로는
    새 조작으로 취급되어 잔액 부족에서 걸린다.
    """
    uid = str(uuid.uuid4())
    request_id = str(uuid.uuid4())

    old = _generate_old(client, uid=uid, request_id=request_id)
    today = _generate(client, uid=uid, request_id=request_id)

    assert len(old.json()["entries"]) == 30
    assert today.status_code == 402, today.text
    assert today.json()["detail"]["code"] == "practice_quota_exhausted"


def test_a_today_failure_cannot_change_the_old_result(client: TestClient) -> None:
    """오늘 경로가 통째로 죽어도 예전 30개 계약은 그대로 성공한다."""
    uid = str(uuid.uuid4())
    client.app.state.ai_service = FailingTodayAIService(client.app.state.ai_service)

    failed = _generate(client, uid=uid, request_id=str(uuid.uuid4()))
    old = _generate_old(client, uid=uid, request_id=str(uuid.uuid4()))

    assert failed.status_code == 503
    assert old.status_code == 200, old.text
    assert len(old.json()["entries"]) == 30
    assert _composition(old.json()) == {"word": 10, "phrase": 10, "pattern": 10}


def test_the_old_route_never_names_today_configuration() -> None:
    """예전 핸들러 본문에 오늘 전용 상수·함수가 **글자로도** 없다."""
    source = textwrap.dedent(inspect.getsource(routes.generate_vocabulary_set))
    # 설명(docstring)에서는 새 경로를 안내해도 된다. 금지는 실행되는 코드다.
    handler = ast.parse(source).body[0]
    body = "\n".join(source.splitlines()[handler.body[0].end_lineno :])

    for name in ("TODAY_", "today", "purpose", "generate_today_vocabulary"):
        assert name not in body, f"예전 라우트가 {name!r}를 참조한다"


def test_the_old_service_function_has_no_size_knob() -> None:
    """30개 전용 함수다 — 개수를 고르는 인자가 없다."""
    signature = inspect.signature(AIService.generate_vocabulary)

    assert set(signature.parameters) == {"self", "topic", "target_level", "exclude_terms"}
    today = inspect.signature(AIService.generate_today_vocabulary)
    assert set(today.parameters) == {"self", "topic", "target_level", "exclude_terms"}


def test_the_two_compositions_are_separate_constants() -> None:
    """서버가 개수를 소유하고, 두 계약이 상수를 공유하지 않는다."""
    for limits in (vocabulary.DEFAULT_COMPOSITION, vocabulary.TODAY_COMPOSITION):
        assert set(limits) == set(VocabularyItemType)
        assert all(count > 0 for count in limits.values())

    assert sum(vocabulary.DEFAULT_COMPOSITION.values()) == vocabulary.SET_SIZE == 30
    assert sum(vocabulary.TODAY_COMPOSITION.values()) == vocabulary.TODAY_SET_SIZE == 20
    assert vocabulary.DEFAULT_COMPOSITION != vocabulary.TODAY_COMPOSITION


def test_the_purpose_discriminator_is_gone() -> None:
    """예전 요청 모델이 P14.6 이전 모습으로 돌아왔다."""
    import app.models.api as api

    assert not hasattr(api, "VocabularyGenerationPurpose")
    assert set(api.VocabularyGenerationRequest.model_fields) == {
        "topic",
        "target_level",
        "exclude_terms",
    }
    # 오늘 요청 모델은 별개 타입이고, 예전 모델을 상속해 확장한 것이 아니다.
    assert set(api.TodayVocabularyGenerationRequest.model_fields) == {
        "topic",
        "target_level",
        "exclude_terms",
    }
    assert not issubclass(
        api.TodayVocabularyGenerationRequest, api.VocabularyGenerationRequest
    )
