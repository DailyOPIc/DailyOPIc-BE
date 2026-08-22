"""P13 — 사용자가 시작한 AI 작업은 절대 무료가 아니다.

계약: 새 데일리 세트 1개 = 데일리 토큰 1개, AI 분석 1회 = 데일리 토큰 1개(추가).
이 파일은 그 계약을 서버가 강제한다는 것을 증명한다 — 원자적 차감, 멱등,
동시성, 실패 환불, 그리고 "계량되지 않은 AI 경로가 하나도 없음".
"""

from __future__ import annotations

import asyncio
import ast
import pathlib
import re
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.ai import AIServiceUnavailable
from app.services.plans import Plan
from tests.test_api import _headers, _verify_reward


EVAL_PATH = "/v1/evaluations/practice"
SET_PAYLOAD = {"targetLevel": "IH", "background": {"interests": ["news"]}}
TRANSCRIPT = "I read several news sources every morning because I want balanced information."


class CountingAIService:
    """실제(모의) AI 서비스를 감싸 제공자 호출 횟수만 센다."""

    def __init__(self, inner: object, *, delay: float = 0.0) -> None:
        self._inner = inner
        self._delay = delay
        self.calls: dict[str, int] = {}

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    async def _record(self, name: str, args: tuple, kwargs: dict) -> object:
        self.calls[name] = self.calls.get(name, 0) + 1
        if self._delay:
            await asyncio.sleep(self._delay)
        return await getattr(self._inner, name)(*args, **kwargs)

    async def evaluate_practice(self, *args: object, **kwargs: object) -> object:
        return await self._record("evaluate_practice", args, kwargs)

    async def generate_daily_pool(self, *args: object, **kwargs: object) -> object:
        return await self._record("generate_daily_pool", args, kwargs)


class FailingPracticeEvaluationAIService:
    model = "test-model"

    async def evaluate_practice(self, *args: object, **kwargs: object) -> object:
        raise AIServiceUnavailable("forced evaluation failure")


@pytest.fixture
def plus_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    """토큰 여유가 있는 플랜(하루 10개). 무료 1개로는 세트+분석 2개를 못 센다."""

    async def _plan(request: object, uid: str) -> Plan:
        return Plan.PLUS

    monkeypatch.setattr("app.api.routes._current_plan", _plan)


@pytest.fixture
def pro_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _plan(request: object, uid: str) -> Plan:
        return Plan.PRO

    monkeypatch.setattr("app.api.routes._current_plan", _plan)


def _remaining(client: TestClient) -> int:
    usage = client.get("/v1/usage", headers=_headers()).json()
    return usage["freeRemaining"] + usage["bonusRemaining"]


def _create_set(client: TestClient) -> dict:
    response = client.post(
        "/v1/question-sets/practice", headers=_headers(), json=SET_PAYLOAD
    )
    assert response.status_code == 200, response.text
    return response.json()


def _evaluate(
    client: TestClient,
    question_set: dict,
    *,
    index: int = 0,
    key: str | None = None,
) -> object:
    return client.post(
        EVAL_PATH,
        headers=_headers(key or str(uuid.uuid4())),
        data={
            "setId": question_set["setId"],
            "questionNumber": str(question_set["questions"][index]["number"]),
            "transcript": TRANSCRIPT,
            "targetLevel": "IH",
        },
    )


# --- 기본 과금 계약 -----------------------------------------------------------


def test_generation_and_each_analysis_each_cost_one_token(plus_plan: None) -> None:
    with TestClient(app) as client:
        start = _remaining(client)

        question_set = _create_set(client)
        assert _remaining(client) == start - 1, "새 세트 = 토큰 1개"

        assert _evaluate(client, question_set).status_code == 200
        assert _remaining(client) == start - 2, "분석 1회 = 토큰 1개 추가"

        assert _evaluate(client, question_set, index=1).status_code == 200
        assert _remaining(client) == start - 3, "분석 2회 = 토큰 2개"

        # 세트 생성 + 분석 1회 = 정상 데일리 1사이클 = 토큰 2개.
        assert (start - 1) - (start - 3) == 2


def test_user_reanalysis_costs_another_token(plus_plan: None) -> None:
    with TestClient(app) as client:
        question_set = _create_set(client)
        before = _remaining(client)

        # 같은 문항을 사용자가 의도적으로 "다시 분석" → 새 키 → 새로 1개.
        assert _evaluate(client, question_set).status_code == 200
        assert _evaluate(client, question_set).status_code == 200
        assert _remaining(client) == before - 2


def test_no_token_rejects_analysis_before_calling_the_provider() -> None:
    with TestClient(app) as client:
        question_set = _create_set(client)  # 무료 토큰 1개를 세트가 소진
        assert _remaining(client) == 0

        spy = CountingAIService(client.app.state.ai_service)
        client.app.state.ai_service = spy

        blocked = _evaluate(client, question_set)
        assert blocked.status_code == 402
        assert blocked.json()["detail"]["code"] == "practice_quota_exhausted"
        # 제공자는 호출되지 않았고, 결과도 공짜로 나오지 않는다.
        assert spy.calls.get("evaluate_practice", 0) == 0
        assert "grade" not in blocked.text
        assert _remaining(client) == 0  # 음수 없음


# --- 멱등 --------------------------------------------------------------------


def test_same_operation_replays_cached_result_without_extra_charge(
    plus_plan: None,
) -> None:
    with TestClient(app) as client:
        question_set = _create_set(client)
        spy = CountingAIService(client.app.state.ai_service)
        client.app.state.ai_service = spy
        before = _remaining(client)
        key = str(uuid.uuid4())

        first = _evaluate(client, question_set, key=key)
        assert first.status_code == 200, first.text
        after_first = _remaining(client)
        assert after_first == before - 1

        replay = _evaluate(client, question_set, key=key)
        assert replay.status_code == 200
        assert replay.json() == first.json()
        assert _remaining(client) == after_first, "재전송은 추가 차감 없음"
        assert spy.calls["evaluate_practice"] == 1, "제공자도 한 번만"

        # 다른 조작(새 키)은 새로 과금된다.
        assert _evaluate(client, question_set).status_code == 200
        assert _remaining(client) == after_first - 1
        assert spy.calls["evaluate_practice"] == 2


def test_missing_idempotency_key_is_rejected_without_charge(plus_plan: None) -> None:
    with TestClient(app) as client:
        question_set = _create_set(client)
        before = _remaining(client)

        response = client.post(
            EVAL_PATH,
            headers=_headers(),  # Idempotency-Key 없음
            data={
                "setId": question_set["setId"],
                "questionNumber": str(question_set["questions"][0]["number"]),
                "transcript": TRANSCRIPT,
            },
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "invalid_idempotency_key"
        assert _remaining(client) == before


# --- 동시성 ------------------------------------------------------------------


def test_two_racing_operations_split_the_last_token() -> None:
    with TestClient(app) as client:
        question_set = _create_set(client)  # 무료 토큰 소진
        reward = client.post(
            "/v1/ad-rewards/intents",
            headers=_headers(),
            json={"purpose": "practice_credits"},
        ).json()
        _verify_reward(client, reward["nonce"])
        assert _remaining(client) == 1  # 남은 토큰 정확히 1개

        spy = CountingAIService(client.app.state.ai_service, delay=0.2)
        client.app.state.ai_service = spy

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [
                future.result()
                for future in [
                    pool.submit(_evaluate, client, question_set, index=index)
                    for index in (0, 1)
                ]
            ]

        codes = sorted(response.status_code for response in results)
        assert codes == [200, 402], [r.status_code for r in results]
        assert spy.calls.get("evaluate_practice", 0) == 1, "패자는 제공자를 부르지 않는다"
        assert _remaining(client) == 0  # 절대 음수가 되지 않는다


# --- 실패와 환불 --------------------------------------------------------------


def test_provider_failure_refunds_exactly_once_and_success_charges_once(
    plus_plan: None,
) -> None:
    with TestClient(app) as client:
        question_set = _create_set(client)
        working = client.app.state.ai_service
        before = _remaining(client)
        key = str(uuid.uuid4())

        client.app.state.ai_service = FailingPracticeEvaluationAIService()
        failed = _evaluate(client, question_set, key=key)
        assert failed.status_code == 503
        assert failed.json()["detail"]["code"] == "ai_unavailable"
        assert _remaining(client) == before, "쓸 만한 결과가 없으면 토큰을 돌려준다"

        # 같은 조작을 다시 실패시켜도 환불이 두 번 일어나지 않는다.
        assert _evaluate(client, question_set, key=key).status_code == 503
        assert _remaining(client) == before

        # 결국 성공하면 최종적으로 정확히 1개만 소모된 상태다.
        client.app.state.ai_service = working
        assert _evaluate(client, question_set, key=key).status_code == 200
        assert _remaining(client) == before - 1


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [("setId", "not-a-real-set", 401), ("questionNumber", "999", 422)],
)
def test_validation_failure_does_not_debit(
    plus_plan: None, field: str, value: str, expected: int
) -> None:
    with TestClient(app) as client:
        question_set = _create_set(client)
        spy = CountingAIService(client.app.state.ai_service)
        client.app.state.ai_service = spy
        before = _remaining(client)

        form = {
            "setId": question_set["setId"],
            "questionNumber": str(question_set["questions"][0]["number"]),
            "transcript": TRANSCRIPT,
            field: value,
        }
        response = client.post(EVAL_PATH, headers=_headers(str(uuid.uuid4())), data=form)

        assert response.status_code == expected, response.text
        assert _remaining(client) == before
        assert spy.calls.get("evaluate_practice", 0) == 0


def test_unauthenticated_analysis_does_not_debit(plus_plan: None) -> None:
    with TestClient(app) as client:
        question_set = _create_set(client)
        before = _remaining(client)

        response = client.post(
            EVAL_PATH,
            headers={"Idempotency-Key": str(uuid.uuid4())},
            data={
                "setId": question_set["setId"],
                "questionNumber": str(question_set["questions"][0]["number"]),
                "transcript": TRANSCRIPT,
            },
        )
        assert response.status_code in {401, 403}
        assert _remaining(client) == before


# --- 복습 세트(프로 전용)도 새 데일리 세트다 -----------------------------------


def test_review_set_consumes_a_daily_token(pro_plan: None) -> None:
    with TestClient(app) as client:
        before = _remaining(client)
        response = client.post(
            "/v1/question-sets/review",
            headers=_headers(str(uuid.uuid4())),
            json={**SET_PAYLOAD, "focusDimension": "fluency"},
        )
        assert response.status_code == 200, response.text
        assert _remaining(client) == before - 1


# --- AI 경로 인벤토리: 계량되지 않은 사용자 경로가 0인지 --------------------------

PROVIDER_METHODS = {
    "generate_practice",
    "generate_daily_pool",
    "generate_mock",
    "evaluate_practice",
    "evaluate_mock",
}

# routes.py 안에서 AI 제공자를 부르는 함수 → 그 호출을 계량하는 수단.
# 새 AI 호출을 추가하면 이 테이블이 깨진다. 그때 과금 정책을 정하라는 뜻이다.
AI_CALL_SITES = {
    "_create_daily_pool": "caller-metered: reserve_practice (새 데일리 세트 = 토큰 1개)",
    "_create_question_set": "caller-metered: reserve_mock / 하루 1회 결정적 set_id",
    "apply_question_set_adjustment": "부모 세트 과금에 포함(세트당 1회로 제한)",
    "evaluate_practice": "reserve_practice (분석 1회 = 토큰 1개)",
    "evaluate_mock_session": "mock 쿼터(reserve_mock)",
    "evaluate_mock": "mock 쿼터(reserve_mock)",
}

# 위 헬퍼를 호출하는 라우트는 반드시 자기 안에서 데일리/모의 쿼터를 잡아야 한다.
HELPER_CALLERS_REQUIRING_A_METER = {
    "create_practice_set": "reserve_practice",
    "create_review_set": "reserve_practice",
    "refresh_practice_set": "reserve_practice",
    "start_mock_session": "reserve_mock",
}


def _routes_tree() -> ast.Module:
    source = pathlib.Path(__file__).resolve().parents[1] / "app" / "api" / "routes.py"
    return ast.parse(source.read_text(encoding="utf-8"))


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr)
            elif isinstance(func, ast.Name):
                names.add(func.id)
    return names


def _functions() -> dict[str, ast.AST]:
    return {
        node.name: node
        for node in ast.walk(_routes_tree())
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def test_every_ai_call_site_is_declared_with_a_meter() -> None:
    found = {
        name
        for name, node in _functions().items()
        if _called_names(node) & PROVIDER_METHODS
    }
    assert found == set(AI_CALL_SITES), (
        "routes.py의 AI 호출 지점이 바뀌었다. 새 경로의 과금 정책을 정하고 "
        "AI_CALL_SITES에 선언하라(계량되지 않은 사용자 AI 경로는 0이어야 한다)."
    )


def test_every_generation_route_reserves_a_token() -> None:
    functions = _functions()
    for route, meter in HELPER_CALLERS_REQUIRING_A_METER.items():
        assert meter in _called_names(functions[route]), f"{route}가 {meter}를 잡지 않는다"


def test_no_uncharged_reservation_helper_survives() -> None:
    # 과금 없이 멱등만 잡던 reserve_request는 P13에서 제거됐다.
    state = (pathlib.Path(__file__).resolve().parents[1] / "app" / "services" / "state.py").read_text()
    assert not re.search(r"\breserve_request\b", state)
