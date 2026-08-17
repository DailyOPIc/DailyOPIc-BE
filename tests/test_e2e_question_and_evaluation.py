"""엔드투엔드 통합 테스트 — 실제 엔드포인트로 문항 생성 → 답변 평가.

기존 통합 테스트(`test_question_set_integration.py`)는 `AIService` 를 직접 호출한다.
여기서는 FastAPI 앱을 띄우고 HTTP 로 요청해, 라우터·인증·쿼터·상태 저장까지 포함한
경로에서 문항과 평가 응답이 규약을 지키는지 본다.

`conftest.py` 가 `MOCK_AI=true` 를 세팅하므로 OpenAI 호출은 일어나지 않는다.
"""

from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.api import OPIcLevel, RubricAssessment, RubricDimension
from app.services.ai import RUBRIC_SCORE_BY_BAND, _reconcile_with_rubrics

TARGET_LEVELS = ["IL", "IM1", "IM2", "IM3", "IH", "AL"]
GRADABLE_RANGE = [
    OPIcLevel.IL,
    OPIcLevel.IM1,
    OPIcLevel.IM2,
    OPIcLevel.IM3,
    OPIcLevel.IH,
    OPIcLevel.AL,
]

# 내부 식별자가 문장으로 새어 나왔는지 검사한다.
LABEL_ARTIFACTS = re.compile(
    r"\b(?:roleplay|unexpected|general)\b|\bdaily\s+\d+\b|\b\d+\b", re.IGNORECASE
)
QUESTION_OPENERS = ("what ", "why ", "how ", "when ", "where ", "who ", "which ")

TRANSCRIPT = (
    "I usually watch movies at home on weekends with my family. "
    "We pick one film together and talk about it afterwards, "
    "so it has become a small routine that I look forward to."
)


def _headers(uid: str, *, idempotency_key: str | None = None) -> dict[str, str]:
    value = {
        "X-DailyOPIc-User-ID": uid,
        "X-Firebase-AppCheck": "test-app-check-token",
    }
    if idempotency_key:
        # 평가 엔드포인트는 Idempotency-Key 를 필수로 요구한다(중복 채점 방지).
        value["Idempotency-Key"] = idempotency_key
    return value


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as value:
        yield value


@pytest.mark.parametrize("target_level", TARGET_LEVELS)
def test_practice_set_endpoint_returns_a_usable_exam(
    client: TestClient, target_level: str
) -> None:
    """문항 생성 엔드포인트가 목표 등급마다 쓸 수 있는 셋을 돌려준다."""
    uid = str(uuid.uuid4())
    response = client.post(
        "/v1/question-sets/practice",
        headers=_headers(uid),
        json={
            "targetLevel": target_level,
            "background": {"interests": ["movies", "music"]},
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    questions = payload["questions"]
    assert [item["number"] for item in questions] == list(range(2, 16))

    prompts = [item["prompt"] for item in questions]
    duplicated = sorted({item for item in prompts if prompts.count(item) > 1})
    assert not duplicated, f"{target_level} 중복 프롬프트={duplicated}"

    sections = [item["examSection"] for item in questions]
    assert "roleplay" in sections, f"{target_level} 롤플레이 섹션 없음"

    for item in questions:
        assert item["questionStyle"], f"Q{item['number']} questionStyle 누락"
        assert item["topicId"], f"Q{item['number']} topicId 누락"
        assert item["difficulty"] in [level.value for level in OPIcLevel]
        assert not LABEL_ARTIFACTS.findall(item["topic"]), item["topic"]
        assert not LABEL_ARTIFACTS.findall(item["prompt"]), item["prompt"]
        for sentence in re.findall(r"[^.?!]+[.?!]", item["prompt"]):
            text = sentence.strip()
            if text.lower().startswith(QUESTION_OPENERS):
                assert text.endswith("?"), text


@pytest.mark.parametrize("target_level", TARGET_LEVELS)
def test_practice_evaluation_endpoint_returns_a_consistent_grade(
    client: TestClient, target_level: str
) -> None:
    """평가 엔드포인트 응답에서 항목별 밴드와 예상 등급이 서로 모순되지 않는다."""
    uid = str(uuid.uuid4())
    question_set = client.post(
        "/v1/question-sets/practice",
        headers=_headers(uid),
        json={
            "targetLevel": target_level,
            "background": {"interests": ["movies", "music"]},
        },
    ).json()

    response = client.post(
        "/v1/evaluations/practice",
        headers=_headers(uid, idempotency_key=str(uuid.uuid4())),
        data={
            "setId": question_set["setId"],
            "questionNumber": str(question_set["questions"][0]["number"]),
            "transcript": TRANSCRIPT,
            "targetLevel": target_level,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    level = OPIcLevel(payload["predictedLevel"])
    assert level in GRADABLE_RANGE, "예상 등급은 IL~AL 범위만 나온다"

    rubrics = [RubricAssessment.model_validate(item) for item in payload["rubrics"]]
    assert [item.dimension for item in rubrics] == list(RubricDimension)

    # 점수는 밴드를 치환한 값이어야 한다.
    scores = payload["scores"]
    by_dimension = {item.dimension: item.band for item in rubrics}
    assert scores["taskFulfillment"] == RUBRIC_SCORE_BY_BAND[
        by_dimension[RubricDimension.TASK_FULFILLMENT]
    ]
    assert scores["grammar"] == RUBRIC_SCORE_BY_BAND[by_dimension[RubricDimension.GRAMMAR]]

    # 응답에 담긴 등급은 이미 상한을 통과한 값이므로 다시 보정되지 않아야 한다.
    reconciled_level, reconciled = _reconcile_with_rubrics(level, rubrics)
    assert reconciled is False, (
        f"API 가 밴드와 모순되는 등급을 반환했다: {level.value} -> {reconciled_level.value}"
    )

    assert payload["resultStatus"] in {"complete", "partial"}
    assert payload["answerQuality"] in {"gradable", "low_evidence", "insufficient"}
    assert isinstance(payload["warnings"], list)
    assert payload["strengths"] and payload["improvements"]
    assert payload["disclaimer"]


def test_empty_transcript_is_marked_insufficient(client: TestClient) -> None:
    """채점할 영어 단어가 없으면 등급을 믿을 수 없다고 표시해야 한다."""
    uid = str(uuid.uuid4())
    question_set = client.post(
        "/v1/question-sets/practice",
        headers=_headers(uid),
        json={"targetLevel": "IM2", "background": {"interests": ["movies"]}},
    ).json()

    response = client.post(
        "/v1/evaluations/practice",
        headers=_headers(uid, idempotency_key=str(uuid.uuid4())),
        data={
            "setId": question_set["setId"],
            "questionNumber": str(question_set["questions"][0]["number"]),
            "transcript": "음...",
            "targetLevel": "IM2",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["answerQuality"] == "insufficient"
