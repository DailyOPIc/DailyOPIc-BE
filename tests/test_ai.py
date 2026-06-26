from pathlib import Path

import pytest

from app.models.api import AudioMetrics, BackgroundProfile, OPIcLevel
from app.services.ai import AIService, AIServiceConfigurationError
from app.services.questions import QuestionPatternRepository


@pytest.mark.asyncio
async def test_target_level_does_not_anchor_fallback_grade() -> None:
    repository = QuestionPatternRepository(Path("../opic_mobile/questions.json"))
    service = AIService(api_key=None, model="test-model", mock=True, repository=repository)
    question = (await service.generate_practice(OPIcLevel.IM2, BackgroundProfile()))[0][0]
    transcript = (
        "I usually read the news in the morning because I want to understand current events. "
        "For example, last week I compared several articles and discussed them with my coworkers. "
        "This habit helps me notice different opinions and make better decisions."
    )
    metrics = AudioMetrics(
        durationSeconds=35,
        speakingSeconds=31,
        silenceRatio=0.11,
        wordsPerMinute=105,
    )
    low_target = await service.evaluate_practice(
        question=question,
        transcript=transcript,
        target=OPIcLevel.NM,
        metrics=metrics,
    )
    high_target = await service.evaluate_practice(
        question=question,
        transcript=transcript,
        target=OPIcLevel.AL,
        metrics=metrics,
    )
    assert low_target.predicted_level == high_target.predicted_level


def test_real_ai_requires_api_key() -> None:
    repository = QuestionPatternRepository(Path("../opic_mobile/questions.json"))
    with pytest.raises(AIServiceConfigurationError):
        AIService(api_key=None, model="test-model", mock=False, repository=repository)
