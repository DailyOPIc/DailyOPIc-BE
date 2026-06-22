from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any

from app.models.api import (
    BackgroundProfile,
    GeneratedQuestion,
    OPIcLevel,
    QuestionType,
)


LEVEL_ORDER = list(OPIcLevel)


class QuestionPatternRepository:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._patterns = self._load()

    def _load(self) -> list[dict[str, Any]]:
        candidates = [
            self._path,
            Path(__file__).resolve().parents[3] / "opic_mobile" / "questions.json",
            Path(__file__).resolve().parents[2] / "data" / "question_patterns.json",
        ]
        for candidate in candidates:
            if candidate.exists():
                with candidate.open(encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, list) and data:
                    return data
        return []

    def references(
        self,
        *,
        target_level: OPIcLevel,
        background: BackgroundProfile,
        limit: int = 24,
    ) -> list[dict[str, Any]]:
        topics = {
            *background.interests,
            *background.sports,
            *background.travel,
        }
        if background.occupation:
            topics.add("work")
        if background.student_status:
            topics.add("study")

        target_index = LEVEL_ORDER.index(target_level)

        def score(item: dict[str, Any]) -> tuple[int, int]:
            try:
                distance = abs(LEVEL_ORDER.index(OPIcLevel(item["level"])) - target_index)
            except (ValueError, KeyError):
                distance = len(LEVEL_ORDER)
            searchable = {
                str(item.get("category", "")),
                *(str(tag) for tag in item.get("tags", [])),
            }
            topic_score = len(topics.intersection(searchable))
            return (-topic_score, distance)

        return sorted(self._patterns, key=score)[:limit]


def validate_mock_blueprint(questions: list[GeneratedQuestion]) -> None:
    if [item.number for item in questions] != list(range(1, 16)):
        raise ValueError("mock exam must contain ordered numbers 1 through 15")

    expected = {
        1: QuestionType.INTRODUCTION,
        2: QuestionType.SURVEY,
        3: QuestionType.SURVEY,
        4: QuestionType.SURVEY,
        5: QuestionType.SURVEY,
        6: QuestionType.SURVEY,
        7: QuestionType.SURVEY,
        8: QuestionType.UNEXPECTED,
        9: QuestionType.UNEXPECTED,
        10: QuestionType.UNEXPECTED,
        11: QuestionType.ROLEPLAY,
        12: QuestionType.ROLEPLAY,
        13: QuestionType.ROLEPLAY,
        14: QuestionType.COMPARISON,
        15: QuestionType.ADVANCED,
    }
    for item in questions:
        if item.type is not expected[item.number]:
            raise ValueError(f"question {item.number} has invalid type")

    combo_expectations = {
        "survey-a": {2, 3, 4},
        "survey-b": {5, 6, 7},
        "unexpected": {8, 9, 10},
        "roleplay": {11, 12, 13},
    }
    for combo_id, numbers in combo_expectations.items():
        actual = {item.number for item in questions if item.combo_id == combo_id}
        if actual != numbers:
            raise ValueError(f"combo {combo_id} must contain {sorted(numbers)}")


class FallbackQuestionGenerator:
    def __init__(self, repository: QuestionPatternRepository) -> None:
        self._repository = repository

    @staticmethod
    def _prompt(reference: dict[str, Any], fallback: str) -> str:
        value = str(reference.get("questionText", "")).strip()
        return value or fallback

    def practice(
        self, target_level: OPIcLevel, background: BackgroundProfile, count: int = 10
    ) -> list[GeneratedQuestion]:
        references = self._repository.references(
            target_level=target_level, background=background, limit=max(count, 10)
        )
        if not references:
            references = [
                {
                    "questionText": "Describe a memorable experience from your daily life.",
                    "category": "daily_life",
                    "tags": ["experience"],
                }
            ]
        return [
            GeneratedQuestion(
                number=index + 1,
                type=QuestionType.PRACTICE,
                comboId=None,
                topic=str(reference.get("category", "personal")),
                prompt=self._prompt(reference, "Tell me about your daily routine."),
                difficulty=target_level,
                rubricFocus=["task fulfillment", "detail", "coherence"],
            )
            for index, reference in enumerate((references * count)[:count])
        ]

    def mock(
        self, target_level: OPIcLevel, background: BackgroundProfile
    ) -> list[GeneratedQuestion]:
        references = self._repository.references(
            target_level=target_level, background=background, limit=20
        )
        if not references:
            references = [
                {
                    "questionText": "Describe a place you visit often.",
                    "category": "personal",
                    "tags": ["place"],
                }
            ]
        references = (references * 15)[:15]
        types = [
            QuestionType.INTRODUCTION,
            *([QuestionType.SURVEY] * 6),
            *([QuestionType.UNEXPECTED] * 3),
            *([QuestionType.ROLEPLAY] * 3),
            QuestionType.COMPARISON,
            QuestionType.ADVANCED,
        ]
        combos = [
            None,
            "survey-a",
            "survey-a",
            "survey-a",
            "survey-b",
            "survey-b",
            "survey-b",
            "unexpected",
            "unexpected",
            "unexpected",
            "roleplay",
            "roleplay",
            "roleplay",
            None,
            None,
        ]
        prompts = [
            "Please introduce yourself and tell me a little about where you live and what you do.",
            *[self._prompt(item, "Describe a familiar topic in detail.") for item in references[1:]],
        ]
        prompts[10] = (
            "You are calling a business about a problem. Explain the situation and ask two or three questions."
        )
        prompts[11] = (
            "The original plan is no longer possible. Offer two alternatives and explain which one you prefer."
        )
        prompts[12] = (
            "Describe a similar problem you experienced before, explain what you did, and describe the result."
        )
        prompts[13] = (
            "Compare how this topic was different in the past and how it is today. Use specific examples."
        )
        prompts[14] = (
            "Give a detailed opinion about the topic, support it with reasons, and discuss a possible counterargument."
        )
        result = [
            GeneratedQuestion(
                number=index + 1,
                type=types[index],
                comboId=combos[index],
                topic=str(references[index].get("category", "personal")),
                prompt=prompts[index],
                difficulty=target_level,
                rubricFocus=["task fulfillment", "organization", "supporting detail"],
            )
            for index in range(15)
        ]
        validate_mock_blueprint(result)
        return result


def stable_question_id(question: GeneratedQuestion) -> str:
    return hashlib.sha256(question.prompt.encode("utf-8")).hexdigest()[:16]
