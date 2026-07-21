from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.models.api import (
    BackgroundProfile,
    BackgroundSurvey,
    DifficultyAdjustment,
    ExamSection,
    GeneratedQuestion,
    OPIcLevel,
    QuestionStyle,
)
from app.services.difficulty import adjusted_level, expected_target_level


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
        question_types: list[QuestionStyle],
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
        question_types: list[QuestionStyle] | None,
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
        question_types: list[QuestionStyle] | None,
        used_ids: set[str],
        predicate: Any,
    ) -> dict[str, Any] | None:
        accepted_types = {item.value for item in question_types or []}
        candidates = [
            item
            for item in self._patterns
            if predicate(item)
            and (not accepted_types or item.get("questionStyle") in accepted_types)
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
        item: dict[str, Any], question_types: list[QuestionStyle] | None
    ) -> int:
        if not question_types:
            return 0
        try:
            return [value.value for value in question_types].index(str(item.get("questionStyle")))
        except ValueError:
            return len(question_types)


def validate_mock_blueprint(questions: list[GeneratedQuestion]) -> None:
    if [item.number for item in questions] != list(range(1, 16)):
        raise ValueError("mock exam must contain ordered numbers 1 through 15")
    if questions[0].exam_section is not ExamSection.INTRODUCTION:
        raise ValueError("question 1 must be introduction")
    if any(item.exam_section is not ExamSection.SURVEY for item in questions[1:10]):
        raise ValueError("questions 2 through 10 must be survey-based")
    for start, end in [(2, 4), (5, 7), (8, 10)]:
        group = questions[start - 1 : end]
        combo_ids = {item.combo_id for item in group}
        topic_ids = {item.topic_id for item in group}
        if len(combo_ids) != 1 or None in combo_ids:
            raise ValueError(f"questions {start}-{end} must share one comboId")
        if len(topic_ids) != 1 or None in topic_ids:
            raise ValueError(f"questions {start}-{end} must share one topicId")
    if any(item.exam_section is not ExamSection.ROLEPLAY for item in questions[10:12]):
        raise ValueError("questions 11 and 12 must be roleplay")
    tail_types = [item.exam_section for item in questions[12:]]
    if tail_types != [ExamSection.UNEXPECTED, ExamSection.COMPARISON, ExamSection.ADVANCED]:
        raise ValueError("questions 13-15 must be unexpected, comparison, advanced")


def validate_practice_blueprint(questions: list[GeneratedQuestion]) -> None:
    numbers = [item.number for item in questions]
    if numbers not in [list(range(1, 8)), list(range(8, 11)), list(range(1, 11))]:
        raise ValueError("practice question numbering is invalid")
    if questions and questions[0].number == 1:
        if questions[0].exam_section is not ExamSection.INTRODUCTION:
            raise ValueError("practice question 1 must be introduction")
    complete = {item.number: item for item in questions}
    for start, end in [(2, 4), (5, 7)]:
        group = [complete[number] for number in range(start, end + 1) if number in complete]
        if not group:
            continue
        if len(group) != 3:
            raise ValueError(f"practice questions {start}-{end} must be a full combo")
        if any(item.exam_section is not ExamSection.SURVEY for item in group):
            raise ValueError(f"practice questions {start}-{end} must be survey-based")
        if len({item.combo_id for item in group}) != 1 or group[0].combo_id is None:
            raise ValueError(f"practice questions {start}-{end} must share one comboId")
        if len({item.topic_id for item in group}) != 1 or group[0].topic_id is None:
            raise ValueError(f"practice questions {start}-{end} must share one topicId")
    tail = [complete[number] for number in range(8, 11) if number in complete]
    if tail and len(tail) != 3:
        raise ValueError("practice tail must contain questions 8 through 10")


def validate_daily_pool(questions: list[GeneratedQuestion]) -> None:
    if [item.number for item in questions] != list(range(2, 16)):
        raise ValueError("daily pool must contain ordered numbers 2 through 15")
    if any(item.exam_section is ExamSection.INTRODUCTION for item in questions):
        raise ValueError("daily pool must not include introduction questions")
    for item in questions:
        prompt = item.prompt.lower()
        if "introduce yourself" in prompt or "self introduction" in prompt:
            raise ValueError("daily pool must not include self-introduction prompts")
    prompt_hashes = [prompt_hash(item.prompt) for item in questions]
    if len(prompt_hashes) != len(set(prompt_hashes)):
        raise ValueError("daily pool contains duplicate prompts")


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
        reference: dict[str, Any], fallback: QuestionStyle
    ) -> QuestionStyle:
        try:
            return QuestionStyle(str(reference.get("questionStyle")))
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
    def _level_question_type(
        level: int, requested: QuestionStyle
    ) -> QuestionStyle:
        if level <= 1 and requested not in {
            QuestionStyle.DESCRIPTION,
            QuestionStyle.ROUTINE,
        }:
            return QuestionStyle.DESCRIPTION
        if level <= 2 and requested in {
            QuestionStyle.COMPARISON,
            QuestionStyle.PROBLEM_SOLVING,
            QuestionStyle.OPINION,
            QuestionStyle.ROLEPLAY,
        }:
            return QuestionStyle.PAST_EXPERIENCE
        if level <= 3 and requested in {
            QuestionStyle.PROBLEM_SOLVING,
            QuestionStyle.OPINION,
        }:
            return QuestionStyle.PAST_EXPERIENCE
        if level <= 4 and requested is QuestionStyle.OPINION:
            return QuestionStyle.COMPARISON
        return requested

    @staticmethod
    def _broad_type_for_level(
        level: int, requested: ExamSection, question_type: QuestionStyle
    ) -> ExamSection:
        if requested in {ExamSection.SURVEY, ExamSection.UNEXPECTED}:
            return requested
        if requested is ExamSection.ADVANCED and question_type is QuestionStyle.COMPARISON:
            return ExamSection.COMPARISON
        if level <= 2 and requested in {
            ExamSection.ROLEPLAY,
            ExamSection.COMPARISON,
            ExamSection.ADVANCED,
        }:
            return ExamSection.UNEXPECTED
        return requested

    @staticmethod
    def _prompt_for_level(
        *, level: int, question_type: QuestionStyle, topic_label: str
    ) -> str:
        if level <= 1:
            if question_type is QuestionStyle.ROUTINE:
                return f"What do you usually do when you enjoy {topic_label}."
            return f"Describe {topic_label} in your daily life."
        if level == 2:
            if question_type is QuestionStyle.PAST_EXPERIENCE:
                return f"Tell me about a simple experience related to {topic_label}. Why do you remember it."
            if question_type is QuestionStyle.ROUTINE:
                return f"What do you usually do when you spend time with {topic_label}. Give one simple reason."
            return f"Tell me about {topic_label}. Why do you like it."
        if level == 3:
            if question_type is QuestionStyle.ROUTINE:
                return f"Explain your usual routine for {topic_label}. Give one reason why it fits your life."
            return f"Tell me about a memorable experience with {topic_label}. Explain why it was memorable."
        if level == 4:
            if question_type is QuestionStyle.COMPARISON:
                return f"Compare your experience with {topic_label} now and in the past. Explain what has changed."
            return f"Tell me about a specific experience with {topic_label}. Explain the situation and why it mattered to you."
        if level == 5:
            if question_type is QuestionStyle.ROLEPLAY:
                return f"You need information about {topic_label}. Call someone and explain your situation. Ask three detailed questions and confirm the next step."
            if question_type is QuestionStyle.PROBLEM_SOLVING:
                return f"Describe a problem you experienced with {topic_label}. Explain how you handled it. Tell me what you learned from that experience."
            if question_type is QuestionStyle.COMPARISON:
                return f"Compare two different experiences related to {topic_label}. Explain the main differences. Tell me which one was more meaningful and why."
            if question_type is QuestionStyle.DESCRIPTION:
                return f"Describe the key features of {topic_label}. Explain what makes them distinctive. Tell me why they matter to you."
            if question_type is QuestionStyle.ROUTINE:
                return f"Explain your usual routine involving {topic_label}. Describe how you organize it. Tell me why that routine works well for you."
            return f"Describe a detailed experience related to {topic_label}. Explain the background and the result. Tell me how that experience changed your thinking."
        if question_type is QuestionStyle.OPINION:
            return f"Discuss how {topic_label} influences people or society today. Explain both advantages and disadvantages. Predict one important change in the future."
        if question_type is QuestionStyle.PROBLEM_SOLVING:
            return f"Analyze a complex problem connected to {topic_label}. Explain why the problem matters to different people. Propose a realistic solution and discuss its limits."
        if question_type is QuestionStyle.ROLEPLAY:
            return f"You are handling a complicated situation involving {topic_label}. Explain the background clearly. Negotiate a solution and confirm responsibilities."
        if question_type is QuestionStyle.DESCRIPTION:
            return f"Describe the most important features of {topic_label}. Explain how different people experience it. Analyze why those features matter in daily life."
        if question_type is QuestionStyle.ROUTINE:
            return f"Explain how people usually engage with {topic_label}. Describe how that routine has evolved. Analyze what could change it in the future."
        if question_type is QuestionStyle.PAST_EXPERIENCE:
            return f"Discuss a complex experience related to {topic_label}. Explain how the situation developed. Analyze what it shows about people's choices or values."
        if question_type is QuestionStyle.COMPARISON:
            return f"Compare two contrasting experiences involving {topic_label}. Explain the factors behind their differences. Evaluate which experience has a stronger impact and why."
        return f"Explain an important issue related to {topic_label}. Support your view with a detailed example. Discuss why the issue deserves attention."

    @staticmethod
    def _intro_prompt(level: int) -> str:
        del level
        return "Introduce yourself."

    def _generated_question(
        self,
        *,
        number: int,
        broad_type: ExamSection,
        combo_id: str | None,
        level: int,
        topic_id: str,
        category: str,
        requested_type: QuestionStyle,
    ) -> GeneratedQuestion:
        question_type = self._level_question_type(level, requested_type)
        broad_type = self._broad_type_for_level(level, broad_type, question_type)
        topic_label = self._topic_label(topic_id)
        return GeneratedQuestion(
            number=number,
            examSection=broad_type,
            comboId=combo_id,
            topic=topic_label,
            prompt=self._prompt_for_level(
                level=level,
                question_type=question_type,
                topic_label=topic_label,
            ),
            difficulty=expected_target_level(level),
            rubricFocus=["task fulfillment", "organization", "supporting detail"],
            questionStyle=question_type,
            followUpPrompt=None,
            topicId=topic_id,
            category=category,
            estimatedLevel=expected_target_level(level),
        )

    def _introduction(self, *, level: int) -> GeneratedQuestion:
        return GeneratedQuestion(
            number=1,
            examSection=ExamSection.INTRODUCTION,
            comboId=None,
            topic="self introduction",
            prompt=self._intro_prompt(level),
            difficulty=expected_target_level(level),
            rubricFocus=["warm-up", "organization", "fluency"],
            questionStyle=QuestionStyle.DESCRIPTION,
            followUpPrompt=None,
            topicId="self_introduction",
            category="introduction",
            estimatedLevel=expected_target_level(level),
        )

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
    def _survey_sequence(target_level: OPIcLevel) -> list[QuestionStyle]:
        if target_level in {OPIcLevel.IL, OPIcLevel.IM1}:
            return [
                QuestionStyle.DESCRIPTION,
                QuestionStyle.ROUTINE,
                QuestionStyle.PAST_EXPERIENCE,
            ]
        if target_level in {OPIcLevel.IM2, OPIcLevel.IM3}:
            return [
                QuestionStyle.DESCRIPTION,
                QuestionStyle.PAST_EXPERIENCE,
                QuestionStyle.COMPARISON,
            ]
        return [
            QuestionStyle.DESCRIPTION,
            QuestionStyle.PROBLEM_SOLVING,
            QuestionStyle.OPINION,
        ]

    @staticmethod
    def _tail_sequence(target_level: OPIcLevel) -> list[QuestionStyle]:
        if target_level in {OPIcLevel.IL, OPIcLevel.IM1}:
            return [
                QuestionStyle.DESCRIPTION,
                QuestionStyle.COMPARISON,
                QuestionStyle.OPINION,
            ]
        return [
            QuestionStyle.PAST_EXPERIENCE,
            QuestionStyle.COMPARISON,
            QuestionStyle.OPINION,
        ]

    @staticmethod
    def _practice_sequence(target_level: OPIcLevel) -> list[QuestionStyle]:
        if target_level in {OPIcLevel.IL, OPIcLevel.IM1}:
            return [
                QuestionStyle.DESCRIPTION,
                QuestionStyle.ROUTINE,
                QuestionStyle.DESCRIPTION,
                QuestionStyle.PAST_EXPERIENCE,
                QuestionStyle.ROUTINE,
                QuestionStyle.PAST_EXPERIENCE,
                QuestionStyle.DESCRIPTION,
                QuestionStyle.COMPARISON,
                QuestionStyle.ROLEPLAY,
                QuestionStyle.OPINION,
            ]
        if target_level in {OPIcLevel.IM2, OPIcLevel.IM3}:
            return [
                QuestionStyle.DESCRIPTION,
                QuestionStyle.PAST_EXPERIENCE,
                QuestionStyle.COMPARISON,
                QuestionStyle.ROUTINE,
                QuestionStyle.DESCRIPTION,
                QuestionStyle.PROBLEM_SOLVING,
                QuestionStyle.PAST_EXPERIENCE,
                QuestionStyle.COMPARISON,
                QuestionStyle.ROLEPLAY,
                QuestionStyle.OPINION,
            ]
        return [
            QuestionStyle.DESCRIPTION,
            QuestionStyle.COMPARISON,
            QuestionStyle.PROBLEM_SOLVING,
            QuestionStyle.OPINION,
            QuestionStyle.PAST_EXPERIENCE,
            QuestionStyle.COMPARISON,
            QuestionStyle.PROBLEM_SOLVING,
            QuestionStyle.ROLEPLAY,
            QuestionStyle.OPINION,
            QuestionStyle.DESCRIPTION,
        ]

    def _practice_topic(
        self, question_type: QuestionStyle, offset: int
    ) -> tuple[str, str]:
        candidates: list[tuple[str, str]] = []
        for item in self._repository.patterns:
            if item.get("questionStyle") != question_type.value:
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
        broad_type: ExamSection,
        combo_id: str | None,
        target_level: OPIcLevel,
        topic_id: str,
        category: str,
        question_types: list[QuestionStyle],
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
            examSection=broad_type,
            comboId=combo_id,
            topic=str(reference.get("topic") or self._topic_label(topic_id)),
            prompt=self._prompt(reference, fallback_prompt),
            difficulty=target_level,
            rubricFocus=["task fulfillment", "organization", "supporting detail"],
            questionStyle=question_type,
            followUpPrompt=self._follow_up(reference),
            topicId=str(reference.get("topicId") or topic_id),
            category=str(reference.get("category") or category),
            estimatedLevel=self._estimated_level(reference, target_level),
        )

    def practice_front(
        self, initial_level: int, background: BackgroundProfile
    ) -> list[GeneratedQuestion]:
        survey = self._survey_from_background(background)
        topics = self._survey_topics(survey)
        return [
            self._introduction(level=initial_level),
            *self._combo(
                start=2,
                combo_id="daily-a",
                level=initial_level,
                topic_id=topics[0],
                category="survey",
            ),
            *self._combo(
                start=5,
                combo_id="daily-b",
                level=initial_level,
                topic_id=topics[1],
                category="survey",
            ),
        ]

    def practice_tail(
        self, *, effective_level: int, background: BackgroundProfile
    ) -> list[GeneratedQuestion]:
        survey = self._survey_from_background(background)
        topic = self._survey_topics(survey)[2]
        sequence = [
            QuestionStyle.PAST_EXPERIENCE,
            QuestionStyle.COMPARISON,
            QuestionStyle.OPINION,
        ]
        return [
            self._generated_question(
                number=number,
                broad_type=ExamSection.UNEXPECTED,
                combo_id=None,
                level=effective_level,
                topic_id=topic if index == 0 else f"unexpected_{index + 1}_{topic}",
                category="unexpected",
                requested_type=question_type,
            )
            for index, (number, question_type) in enumerate(zip(range(8, 11), sequence))
        ]

    def daily_pool(
        self,
        initial_level: int,
        background: BackgroundProfile,
        survey: BackgroundSurvey | None = None,
        adjustment: DifficultyAdjustment | str | None = None,
    ) -> list[GeneratedQuestion]:
        level = adjusted_level(initial_level, adjustment)
        survey = survey or self._survey_from_background(background)
        topics = self._survey_topics(survey)
        sequence = [
            QuestionStyle.DESCRIPTION,
            QuestionStyle.ROUTINE,
            QuestionStyle.PAST_EXPERIENCE,
            QuestionStyle.COMPARISON,
            QuestionStyle.ROLEPLAY,
            QuestionStyle.PROBLEM_SOLVING,
            QuestionStyle.OPINION,
            QuestionStyle.DESCRIPTION,
            QuestionStyle.PAST_EXPERIENCE,
            QuestionStyle.COMPARISON,
            QuestionStyle.ROLEPLAY,
            QuestionStyle.PROBLEM_SOLVING,
            QuestionStyle.OPINION,
            QuestionStyle.ROUTINE,
        ]
        broad_types = [
            ExamSection.SURVEY,
            ExamSection.SURVEY,
            ExamSection.SURVEY,
            ExamSection.COMPARISON,
            ExamSection.ROLEPLAY,
            ExamSection.ROLEPLAY,
            ExamSection.ADVANCED,
            ExamSection.UNEXPECTED,
            ExamSection.UNEXPECTED,
            ExamSection.COMPARISON,
            ExamSection.ROLEPLAY,
            ExamSection.ROLEPLAY,
            ExamSection.ADVANCED,
            ExamSection.UNEXPECTED,
        ]
        questions: list[GeneratedQuestion] = []
        for index, number in enumerate(range(2, 16)):
            base_topic = topics[index % len(topics)]
            topic_id = base_topic if index < len(topics) else f"{base_topic}_daily_{number}"
            questions.append(
                self._generated_question(
                    number=number,
                    broad_type=broad_types[index],
                    combo_id=None,
                    level=level,
                    topic_id=topic_id,
                    category="daily",
                    requested_type=sequence[index],
                )
            )
        return questions

    def practice(
        self, target_level: OPIcLevel, background: BackgroundProfile, count: int = 10
    ) -> list[GeneratedQuestion]:
        initial_level = {
            OPIcLevel.IL: 1,
            OPIcLevel.IM1: 3,
            OPIcLevel.IM2: 4,
            OPIcLevel.IM3: 4,
            OPIcLevel.IH: 5,
            OPIcLevel.AL: 6,
        }.get(target_level, 4)
        questions = [
            *self.practice_front(initial_level, background),
            *self.practice_tail(effective_level=initial_level, background=background),
        ]
        return questions[:count]

    def _combo(
        self,
        *,
        start: int,
        combo_id: str,
        level: int,
        topic_id: str,
        category: str,
    ) -> list[GeneratedQuestion]:
        sequence = [
            QuestionStyle.DESCRIPTION,
            QuestionStyle.ROUTINE,
            QuestionStyle.PAST_EXPERIENCE,
        ]
        return [
            self._generated_question(
                number=start + offset,
                broad_type=ExamSection.SURVEY,
                combo_id=combo_id,
                level=level,
                topic_id=topic_id,
                category=category,
                requested_type=question_type,
            )
            for offset, question_type in enumerate(sequence)
        ]

    def mock_front(
        self,
        initial_level: int,
        background: BackgroundProfile,
        survey: BackgroundSurvey | None = None,
    ) -> list[GeneratedQuestion]:
        survey = survey or self._survey_from_background(background)
        topics = self._survey_topics(survey)
        return [
            self._introduction(level=initial_level),
            *self._combo(
                start=2,
                combo_id="survey-1",
                level=initial_level,
                topic_id=topics[0],
                category="survey",
            ),
            *self._combo(
                start=5,
                combo_id="survey-2",
                level=initial_level,
                topic_id=topics[1],
                category="survey",
            ),
        ]

    def mock_tail(
        self,
        *,
        effective_level: int,
        background: BackgroundProfile,
        survey: BackgroundSurvey | None = None,
    ) -> list[GeneratedQuestion]:
        survey = survey or self._survey_from_background(background)
        topics = self._survey_topics(survey)
        topic_c = topics[2]
        questions = [
            *self._combo(
                start=8,
                combo_id="survey-3",
                level=effective_level,
                topic_id=topic_c,
                category="survey",
            )
        ]
        roleplay = [
            (11, ExamSection.ROLEPLAY, "roleplay_service", QuestionStyle.ROLEPLAY),
            (12, ExamSection.ROLEPLAY, "roleplay_problem", QuestionStyle.PROBLEM_SOLVING),
            (13, ExamSection.UNEXPECTED, "unexpected_daily", QuestionStyle.PAST_EXPERIENCE),
            (14, ExamSection.COMPARISON, "general_comparison", QuestionStyle.COMPARISON),
            (15, ExamSection.ADVANCED, "general_opinion", QuestionStyle.OPINION),
        ]
        questions.extend(
            self._generated_question(
                number=number,
                broad_type=broad_type,
                combo_id=None,
                level=effective_level,
                topic_id=topic_id,
                category=broad_type.value,
                requested_type=question_type,
            )
            for number, broad_type, topic_id, question_type in roleplay
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
            question_types=[QuestionStyle.DESCRIPTION],
            used_ids=used_ids,
        )
        if introduction and (identifier := str(introduction.get("id", "")).strip()):
            used_ids.add(identifier)
        questions.append(
            GeneratedQuestion(
                number=1,
                examSection=ExamSection.INTRODUCTION,
                comboId=None,
                topic="self introduction",
                prompt=self._prompt(
                    introduction or {},
                    "Please introduce yourself and describe your everyday life in a natural way.",
                ),
                difficulty=target_level,
                rubricFocus=["warm-up", "organization", "fluency"],
                questionStyle=QuestionStyle.DESCRIPTION,
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
                        broad_type=ExamSection.SURVEY,
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
                [QuestionStyle.ROLEPLAY]
                if index == 11
                else [QuestionStyle.PROBLEM_SOLVING, QuestionStyle.ROLEPLAY]
            )
            questions.append(
                self._catalog_question(
                    number=index,
                    broad_type=ExamSection.ROLEPLAY,
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
            (13, ExamSection.UNEXPECTED, "unexpected_daily", "unexpected"),
            (14, ExamSection.COMPARISON, "general_comparison", "general"),
            (15, ExamSection.ADVANCED, "general_opinion", "general"),
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
