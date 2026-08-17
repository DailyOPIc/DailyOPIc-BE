"""실제 OpenAI API를 호출해서 _reconcile_with_rubrics 정합성을 확인한다.

사용법:
    .venv/bin/python scripts/live_reconcile_check.py

- conftest.py 의 MOCK_AI=true 를 우회해 실제 AI 응답을 받는다.
- 5가지 답변 수준(매우 낮음 ~ 매우 높음)을 각 타겟 레벨로 평가한다.
- AI 가 준 predictedLevel 과 rubric 밴드가 _reconcile_with_rubrics 를 통과한 뒤
  모순이 없는지(reconciled=False여야 정상) 확인한다.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.models.api import (
    AudioMetrics,
    ExamSection,
    GeneratedQuestion,
    OPIcLevel,
    QuestionStyle,
)
from app.services.ai import AIService, _reconcile_with_rubrics
from app.services.questions import QuestionPatternRepository

# (prompt, transcript, target_level, 설명)
CASES = [
    (
        "Tell me about your daily routine.",
        "I wake up. I eat. I go to work. I sleep.",
        OPIcLevel.IL,
        "매우 짧고 단편적 → 낮은 레벨 예상",
    ),
    (
        "Describe a memorable trip you have taken.",
        (
            "Last year I went to Jeju Island. It was very beautiful. "
            "I saw the sea and ate delicious food. I want to go again someday."
        ),
        OPIcLevel.IM2,
        "기본 서술, 세부 묘사 부족 → 중간 레벨 예상",
    ),
    (
        "Describe your favorite hobby and explain why you enjoy it.",
        (
            "I enjoy reading books, especially novels. "
            "I read maybe two or three times per week. "
            "It helps me relax after work. I learn new words too."
        ),
        OPIcLevel.IM3,
        "기본 답변, 깊이 부족 → IM 레벨 예상",
    ),
    (
        "Talk about a challenge you faced at work and how you resolved it.",
        (
            "About two years ago, I had a major project deadline conflict with a team member "
            "who had different priorities. I scheduled a one-on-one meeting, clearly explained "
            "the timeline constraints, and we redistributed tasks based on individual strengths. "
            "As a result, we submitted the report on time and our collaboration improved significantly."
        ),
        OPIcLevel.IH,
        "구체적 상황·해결 과정 포함 → IH 근방 예상",
    ),
    (
        "Compare living in a city versus living in the countryside.",
        (
            "Urban environments offer unparalleled access to professional opportunities, cultural events, "
            "and diverse social networks, which can accelerate personal and career growth. "
            "However, this comes at the cost of higher living expenses, noise pollution, and reduced "
            "connection to nature. Rural settings, by contrast, provide tranquility and community bonds, "
            "though residents often face limited job markets and less robust infrastructure. "
            "Personally, I believe the ideal lifestyle depends on one's career stage and values: "
            "younger professionals may thrive in cities, while those prioritizing well-being might "
            "find rural life more fulfilling."
        ),
        OPIcLevel.AL,
        "논리적 비교, 풍부한 어휘, 일관된 구성 → AL 근방 예상",
    ),
]

DUMMY_METRICS = AudioMetrics(
    durationSeconds=30.0,
    speakingSeconds=28.0,
    silenceRatio=0.07,
    wordsPerMinute=120.0,
    isEstimated=True,
)


async def run() -> None:
    api_key = os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    if not api_key:
        print("OPENAI_API_KEY 없음. .env 파일을 확인하세요.")
        sys.exit(1)

    repo = QuestionPatternRepository(Path("app/data/question_patterns.json"))
    service = AIService(api_key=api_key, model=model, mock=False, repository=repo)

    print(f"모델: {model}")
    print("=" * 70)

    passed = 0
    ran = 0

    for i, (prompt_text, transcript, target, description) in enumerate(CASES, 1):
        question = GeneratedQuestion(
            number=2,
            examSection=ExamSection.SURVEY,
            topic=prompt_text[:40],
            prompt=prompt_text,
            difficulty=target,
            rubricFocus=["taskFulfillment", "grammar"],
            questionStyle=QuestionStyle.DESCRIPTION,
            topicId="test_topic",
        )
        print(f"\n[{i}/{len(CASES)}] {description}")
        print(f"  타겟: {target.value}")
        print(f"  답변: {transcript[:70]}...")

        try:
            result = await service.evaluate_practice(
                question=question,
                transcript=transcript,
                target=target,
                metrics=DUMMY_METRICS,
                depth="detailed",
            )
            level = result.predicted_level
            rubrics = result.rubrics
            bands = {r.dimension: r.band for r in rubrics}

            band_summary = " | ".join(
                f"{d.value[:5]}={b.value}" for d, b in bands.items()
            )

            # API 내부에서 이미 reconcile 통과했어야 함 → 재검사
            re_level, re_reconciled = _reconcile_with_rubrics(level, rubrics)

            print(f"  AI 예측 레벨: {level.value}")
            print(f"  밴드: {band_summary}")

            ran += 1
            if re_reconciled:
                print(f"  재검사: 모순 - {level.value} -> {re_level.value} 로 낮아져야 함 (버그)")
            else:
                print(f"  재검사: 정합 (레벨과 밴드가 일치)")
                passed += 1

        except Exception as exc:
            print(f"  오류: {type(exc).__name__}: {exc}")

    print(f"\n{'=' * 70}")
    if ran == 0:
        print("실행된 케이스 없음")
        return
    print(f"결과: {passed}/{ran} 정합 ({passed / ran * 100:.0f}%)")
    if passed == ran:
        print("모든 케이스에서 AI 응답 레벨과 rubric 밴드가 일관됩니다.")
    else:
        print(f"{ran - passed}개 케이스에서 모순 발견. 로그를 확인하세요.")


if __name__ == "__main__":
    asyncio.run(run())
