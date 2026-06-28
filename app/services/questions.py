from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.models.api import (
    BackgroundProfile,
    BackgroundSurvey,
    GeneratedQuestion,
    OPIcLevel,
    QuestionType,
    SurveyQuestionType,
)


LEVEL_ORDER = list(OPIcLevel)
LEGACY_TOPIC_MAP = {
    "domestic": "domestic_travel",
    "overseas": "overseas_travel",
    "games": "gaming",
    "running": "jogging",
    "walking": "jogging",
    "work": "office_worker",
    "study": "student",
    "apartment": "family",
}
TOPIC_LABELS = {
    "student": "school life",
    "office_worker": "work life",
    "job_seeker": "job search",
    "status_none": "daily life",
    "alone": "living alone",
    "family": "living with family",
    "roommates": "living with roommates",
    "dormitory": "dormitory life",
    "residence_other": "home life",
    "movies": "movies",
    "music": "music",
    "cafes": "cafes",
    "shopping": "shopping",
    "reading": "reading",
    "cooking": "cooking",
    "gaming": "games",
    "photography": "photography",
    "instruments": "musical instruments",
    "fashion": "fashion",
    "pets": "pets",
    "it": "technology",
    "sns": "social media",
    "jogging": "jogging",
    "gym": "fitness",
    "swimming": "swimming",
    "cycling": "cycling",
    "soccer": "soccer",
    "yoga": "yoga",
    "hiking": "hiking",
    "domestic_travel": "domestic travel",
    "overseas_travel": "overseas travel",
    "beach_travel": "beach trips",
    "mountain_travel": "mountain trips",
    "camping": "camping",
    "staycation": "hotel staycations",
    "food_travel": "food trips",
}
SURVEY_CATEGORY_PRIORITY = {"leisure", "hobbies", "sports", "travel"}


def question_set_hash(questions: list[dict[str, Any]]) -> str:
    canonical = json.dumps(
        questions, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def prompt_hash(prompt: str) -> str:
    normalized = " ".join(prompt.strip().lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class QuestionPatternRepository:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._patterns = self._load()

    @property
    def patterns(self) -> list[dict[str, Any]]:
        return self._patterns

    def _load(self) -> list[dict[str, Any]]:
        backend_root = Path(__file__).resolve().parents[2]
        candidates = [
            self._path,
            backend_root.parent / "opic_mobile" / "questions.json",
            backend_root / "app" / "data" / "question_patterns.json",
        ]
        for candidate in candidates:
            if candidate.exists():
                with candidate.open(encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, dict):
                    data = data.get("questions", [])
                if isinstance(data, list) and data:
                    return [item for item in data if isinstance(item, dict)]
        return []

    def references(
        self,
        *,
        target_level: OPIcLevel,
        background: BackgroundProfile,
        limit: int = 24,
    ) -> list[dict[str, Any]]:
        topics = {
            self.normalize_topic_id(value)
            for value in [*background.interests, *background.sports, *background.travel]
        }
        if background.occupation:
            topics.add(self.normalize_topic_id(background.occupation))
        if background.student_status:
            topics.add(self.normalize_topic_id(background.student_status))

        def score(item: dict[str, Any]) -> tuple[int, int, str]:
            searchable = {
                str(item.get("category", "")),
                str(item.get("topicId", "")),
                *(str(tag) for tag in item.get("tags", [])),
            }
            topic_score = len(topics.intersection(searchable))
            return (-topic_score, self._level_distance(item, target_level), str(item.get("id", "")))

        return sorted(self._patterns, key=score)[:limit]

    def by_topic(
        self,
        *,
        topic_id: str,
        target_level: OPIcLevel,
        question_types: list[SurveyQuestionType],
        used_ids: set[str],
    ) -> dict[str, Any] | None:
        normalized = self.normalize_topic_id(topic_id)
        return self._best_match(
            target_level=target_level,
            question_types=question_types,
            used_ids=used_ids,
            predicate=lambda item: item.get("topicId") == normalized,
        )

    def by_category(
        self,
        *,
        category: str,
        target_level: OPIcLevel,
        question_types: list[SurveyQuestionType] | None,
        used_ids: set[str],
    ) -> dict[str, Any] | None:
        return self._best_match(
            target_level=target_level,
            question_types=question_types,
            used_ids=used_ids,
            predicate=lambda item: item.get("category") == category,
        )

    def available_survey_topics(self) -> list[str]:
        result: list[str] = []
        for item in self._patterns:
            category = str(item.get("category", ""))
            topic_id = str(item.get("topicId", ""))
            if category in SURVEY_CATEGORY_PRIORITY and topic_id and topic_id not in result:
                result.append(topic_id)
        return result

    def _best_match(
        self,
        *,
        target_level: OPIcLevel,
        question_types: list[SurveyQuestionType] | None,
        used_ids: set[str],
        predicate: Any,
    ) -> dict[str, Any] | None:
        accepted_types = {item.value for item in question_types or []}
        candidates = [
            item
            for item in self._patterns
            if predicate(item)
            and (not accepted_types or item.get("questionType") in accepted_types)
            and str(item.get("id", "")) not in used_ids
        ]
        candidates.sort(
            key=lambda item: (
                self._level_distance(item, target_level),
                self._question_type_rank(item, question_types),
                str(item.get("id", "")),
            )
        )
        return candidates[0] if candidates else None

    @staticmethod
    def normalize_topic_id(value: str) -> str:
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        return LEGACY_TOPIC_MAP.get(normalized, normalized)

    @staticmethod
    def _level_distance(item: dict[str, Any], target_level: OPIcLevel) -> int:
        level_value = item.get("difficulty") or item.get("estimatedLevel") or item.get("level")
        try:
            return abs(LEVEL_ORDER.index(OPIcLevel(level_value)) - LEVEL_ORDER.index(target_level))
        except (ValueError, TypeError):
            return len(LEVEL_ORDER)

    @staticmethod
    def _question_type_rank(
        item: dict[str, Any], question_types: list[SurveyQuestionType] | None
    ) -> int:
        if not question_types:
            return 0
        try:
            return [value.value for value in question_types].index(str(item.get("questionType")))
        except ValueError:
            return len(question_types)


def validate_mock_blueprint(questions: list[GeneratedQuestion]) -> None:
    if [item.number for item in questions] != list(range(1, 16)):
        raise ValueError("mock exam must contain ordered numbers 1 through 15")
    if questions[0].type is not QuestionType.INTRODUCTION:
        raise ValueError("question 1 must be introduction")
    if any(item.type is not QuestionType.SURVEY for item in questions[1:10]):
        raise ValueError("questions 2 through 10 must be survey-based")
    for start, end in [(2, 4), (5, 7), (8, 10)]:
        group = questions[start - 1 : end]
        combo_ids = {item.combo_id for item in group}
        topic_ids = {item.topic_id for item in group}
        if len(combo_ids) != 1 or None in combo_ids:
            raise ValueError(f"questions {start}-{end} must share one comboId")
        if len(topic_ids) != 1 or None in topic_ids:
            raise ValueError(f"questions {start}-{end} must share one topicId")
    if any(item.type is not QuestionType.ROLEPLAY for item in questions[10:12]):
        raise ValueError("questions 11 and 12 must be roleplay")
    tail_types = [item.type for item in questions[12:]]
    if tail_types != [QuestionType.UNEXPECTED, QuestionType.COMPARISON, QuestionType.ADVANCED]:
        raise ValueError("questions 13-15 must be unexpected, comparison, advanced")


class FallbackQuestionGenerator:
    def __init__(self, repository: QuestionPatternRepository) -> None:
        self._repository = repository

    @staticmethod
    def _prompt(reference: dict[str, Any], fallback: str) -> str:
        value = str(reference.get("prompt") or reference.get("questionText") or "").strip()
        return value or fallback

    @staticmethod
    def _follow_up(reference: dict[str, Any]) -> str | None:
        value = str(reference.get("followUpPrompt") or "").strip()
        return value or None

    @staticmethod
    def _question_type(
        reference: dict[str, Any], fallback: SurveyQuestionType
    ) -> SurveyQuestionType:
        try:
            return SurveyQuestionType(str(reference.get("questionType")))
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _estimated_level(reference: dict[str, Any], target_level: OPIcLevel) -> OPIcLevel:
        value = reference.get("estimatedLevel") or reference.get("difficulty")
        try:
            return OPIcLevel(value)
        except (TypeError, ValueError):
            return target_level

    @staticmethod
    def _topic_label(topic_id: str) -> str:
        return TOPIC_LABELS.get(topic_id, topic_id.replace("_", " "))

    @staticmethod
    def _survey_from_background(background: BackgroundProfile) -> BackgroundSurvey:
        interests = [QuestionPatternRepository.normalize_topic_id(item) for item in background.interests]
        sports = [QuestionPatternRepository.normalize_topic_id(item) for item in background.sports]
        travel = [QuestionPatternRepository.normalize_topic_id(item) for item in background.travel]
        selected = interests + sports + travel
        for fallback in ["movies", "music", "cafes"]:
            if len(selected) >= 3:
                break
            if fallback not in selected:
                interests.append(fallback)
                selected.append(fallback)
        status = (
            QuestionPatternRepository.normalize_topic_id(background.student_status)
            if background.student_status
            else QuestionPatternRepository.normalize_topic_id(background.occupation or "status_none")
        )
        residence = QuestionPatternRepository.normalize_topic_id(background.housing or "family")
        return BackgroundSurvey(
            status=status,
            residence=residence,
            leisure=interests[:6],
            hobbies=[],
            sports=sports[:6],
            travel=travel[:6],
        )

    @staticmethod
    def _survey_sequence(target_level: OPIcLevel) -> list[SurveyQuestionType]:
        if target_level in {OPIcLevel.IL, OPIcLevel.IM1}:
            return [
                SurveyQuestionType.DESCRIPTION,
                SurveyQuestionType.ROUTINE,
                SurveyQuestionType.PAST_EXPERIENCE,
            ]
        if target_level in {OPIcLevel.IM2, OPIcLevel.IM3}:
            return [
                SurveyQuestionType.DESCRIPTION,
                SurveyQuestionType.PAST_EXPERIENCE,
                SurveyQuestionType.COMPARISON,
            ]
        return [
            SurveyQuestionType.DESCRIPTION,
            SurveyQuestionType.PROBLEM_SOLVING,
            SurveyQuestionType.OPINION,
        ]

    @staticmethod
    def _tail_sequence(target_level: OPIcLevel) -> list[SurveyQuestionType]:
        if target_level in {OPIcLevel.IL, OPIcLevel.IM1}:
            return [
                SurveyQuestionType.DESCRIPTION,
                SurveyQuestionType.COMPARISON,
                SurveyQuestionType.OPINION,
            ]
        return [
            SurveyQuestionType.PAST_EXPERIENCE,
            SurveyQuestionType.COMPARISON,
            SurveyQuestionType.OPINION,
        ]

    @staticmethod
    def _practice_sequence(target_level: OPIcLevel) -> list[SurveyQuestionType]:
        if target_level in {OPIcLevel.IL, OPIcLevel.IM1}:
            return [
                SurveyQuestionType.DESCRIPTION,
                SurveyQuestionType.ROUTINE,
                SurveyQuestionType.DESCRIPTION,
                SurveyQuestionType.PAST_EXPERIENCE,
                SurveyQuestionType.ROUTINE,
                SurveyQuestionType.PAST_EXPERIENCE,
                SurveyQuestionType.DESCRIPTION,
                SurveyQuestionType.COMPARISON,
                SurveyQuestionType.ROLEPLAY,
                SurveyQuestionType.OPINION,
            ]
        if target_level in {OPIcLevel.IM2, OPIcLevel.IM3}:
            return [
                SurveyQuestionType.DESCRIPTION,
                SurveyQuestionType.PAST_EXPERIENCE,
                SurveyQuestionType.COMPARISON,
                SurveyQuestionType.ROUTINE,
                SurveyQuestionType.DESCRIPTION,
                SurveyQuestionType.PROBLEM_SOLVING,
                SurveyQuestionType.PAST_EXPERIENCE,
                SurveyQuestionType.COMPARISON,
                SurveyQuestionType.ROLEPLAY,
                SurveyQuestionType.OPINION,
            ]
        return [
            SurveyQuestionType.DESCRIPTION,
            SurveyQuestionType.COMPARISON,
            SurveyQuestionType.PROBLEM_SOLVING,
            SurveyQuestionType.OPINION,
            SurveyQuestionType.PAST_EXPERIENCE,
            SurveyQuestionType.COMPARISON,
            SurveyQuestionType.PROBLEM_SOLVING,
            SurveyQuestionType.ROLEPLAY,
            SurveyQuestionType.OPINION,
            SurveyQuestionType.DESCRIPTION,
        ]

    def _practice_topic(
        self, question_type: SurveyQuestionType, offset: int
    ) -> tuple[str, str]:
        candidates: list[tuple[str, str]] = []
        for item in self._repository.patterns:
            if item.get("questionType") != question_type.value:
                continue
            category = str(item.get("category") or "")
            topic_id = str(item.get("topicId") or "")
            if category == "introduction" or not topic_id:
                continue
            candidate = (topic_id, category)
            if candidate not in candidates:
                candidates.append(candidate)
        if not candidates:
            return "unexpected_daily", "unexpected"
        return candidates[offset % len(candidates)]

    def _survey_topics(self, survey: BackgroundSurvey) -> list[str]:
        primary = [
            *survey.leisure,
            *survey.hobbies,
            *survey.sports,
            *survey.travel,
        ]
        result: list[str] = []
        for value in [*primary, survey.status, survey.residence, *self._repository.available_survey_topics()]:
            topic_id = QuestionPatternRepository.normalize_topic_id(value)
            if topic_id and topic_id not in result:
                result.append(topic_id)
            if len(result) == 3:
                return result
        return result or ["movies", "music", "domestic_travel"]

    def _catalog_question(
        self,
        *,
        number: int,
        broad_type: QuestionType,
        combo_id: str | None,
        target_level: OPIcLevel,
        topic_id: str,
        category: str,
        question_types: list[SurveyQuestionType],
        fallback_prompt: str,
        used_ids: set[str],
    ) -> GeneratedQuestion:
        reference = self._repository.by_topic(
            topic_id=topic_id,
            target_level=target_level,
            question_types=question_types,
            used_ids=used_ids,
        )
        if reference is None and category != "survey":
            reference = self._repository.by_category(
                category=category,
                target_level=target_level,
                question_types=question_types,
                used_ids=used_ids,
            )
        reference = reference or {}
        if identifier := str(reference.get("id", "")).strip():
            used_ids.add(identifier)
        question_type = self._question_type(reference, question_types[0])
        return GeneratedQuestion(
            number=number,
            type=broad_type,
            comboId=combo_id,
            topic=str(reference.get("topic") or self._topic_label(topic_id)),
            prompt=self._prompt(reference, fallback_prompt),
            difficulty=target_level,
            rubricFocus=["task fulfillment", "organization", "supporting detail"],
            questionType=question_type,
            followUpPrompt=self._follow_up(reference),
            topicId=str(reference.get("topicId") or topic_id),
            category=str(reference.get("category") or category),
            estimatedLevel=self._estimated_level(reference, target_level),
        )

    def practice(
        self, target_level: OPIcLevel, background: BackgroundProfile, count: int = 10
    ) -> list[GeneratedQuestion]:
        sequence = self._practice_sequence(target_level)
        used_ids: set[str] = set()
        questions: list[GeneratedQuestion] = []
        for index in range(count):
            question_type = sequence[index % len(sequence)]
            topic_id, category = self._practice_topic(question_type, index)
            topic_label = self._topic_label(topic_id)
            questions.append(
                self._catalog_question(
                    number=index + 1,
                    broad_type=QuestionType.PRACTICE,
                    combo_id=None,
                    target_level=target_level,
                    topic_id=topic_id,
                    category=category,
                    question_types=[question_type],
                    fallback_prompt=(
                        f"Talk about {topic_label}. Give clear details and one specific example."
                    ),
                    used_ids=used_ids,
                )
            )
        return questions

    def mock(
        self,
        target_level: OPIcLevel,
        background: BackgroundProfile,
        survey: BackgroundSurvey | None = None,
    ) -> list[GeneratedQuestion]:
        survey = survey or self._survey_from_background(background)
        survey_sequence = self._survey_sequence(target_level)
        tail_sequence = self._tail_sequence(target_level)
        used_ids: set[str] = set()
        questions: list[GeneratedQuestion] = []

        introduction = self._repository.by_category(
            category="introduction",
            target_level=target_level,
            question_types=[SurveyQuestionType.DESCRIPTION],
            used_ids=used_ids,
        )
        if introduction and (identifier := str(introduction.get("id", "")).strip()):
            used_ids.add(identifier)
        questions.append(
            GeneratedQuestion(
                number=1,
                type=QuestionType.INTRODUCTION,
                comboId=None,
                topic="self introduction",
                prompt=self._prompt(
                    introduction or {},
                    "Please introduce yourself and describe your everyday life in a natural way.",
                ),
                difficulty=target_level,
                rubricFocus=["warm-up", "organization", "fluency"],
                questionType=SurveyQuestionType.DESCRIPTION,
                followUpPrompt=self._follow_up(introduction or {}),
                topicId="self_introduction",
                category="introduction",
                estimatedLevel=self._estimated_level(introduction or {}, target_level),
            )
        )

        number = 2
        for group_index, topic_id in enumerate(self._survey_topics(survey), start=1):
            for question_type in survey_sequence:
                topic_label = self._topic_label(topic_id)
                questions.append(
                    self._catalog_question(
                        number=number,
                        broad_type=QuestionType.SURVEY,
                        combo_id=f"survey-{group_index}",
                        target_level=target_level,
                        topic_id=topic_id,
                        category="survey",
                        question_types=[question_type],
                        fallback_prompt=(
                            f"Talk about {topic_label}. Include clear details and one specific example."
                        ),
                        used_ids=used_ids,
                    )
                )
                number += 1

        roleplay_topics = ["roleplay_service", "roleplay_problem"]
        for index, topic_id in enumerate(roleplay_topics, start=11):
            role_types = (
                [SurveyQuestionType.ROLEPLAY]
                if index == 11
                else [SurveyQuestionType.PROBLEM_SOLVING, SurveyQuestionType.ROLEPLAY]
            )
            questions.append(
                self._catalog_question(
                    number=index,
                    broad_type=QuestionType.ROLEPLAY,
                    combo_id="roleplay",
                    target_level=target_level,
                    topic_id=topic_id,
                    category="roleplay",
                    question_types=role_types,
                    fallback_prompt=(
                        "You are speaking with another person. Ask questions, explain the situation, "
                        "and suggest what should happen next."
                    ),
                    used_ids=used_ids,
                )
            )

        tail_specs = [
            (13, QuestionType.UNEXPECTED, "unexpected_daily", "unexpected"),
            (14, QuestionType.COMPARISON, "general_comparison", "general"),
            (15, QuestionType.ADVANCED, "general_opinion", "general"),
        ]
        for (number, broad_type, topic_id, category), question_type in zip(tail_specs, tail_sequence):
            questions.append(
                self._catalog_question(
                    number=number,
                    broad_type=broad_type,
                    combo_id=None,
                    target_level=target_level,
                    topic_id=topic_id,
                    category=category,
                    question_types=[question_type],
                    fallback_prompt=(
                        "Discuss this everyday topic in detail. Explain your view with reasons and examples."
                    ),
                    used_ids=used_ids,
                )
            )

        validate_mock_blueprint(questions)
        return questions


def stable_question_id(question: GeneratedQuestion) -> str:
    return hashlib.sha256(question.prompt.encode("utf-8")).hexdigest()[:16]
