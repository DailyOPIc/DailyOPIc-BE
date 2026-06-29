from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class OPIcLevel(StrEnum):
    NL = "NL"
    NM = "NM"
    NH = "NH"
    IL = "IL"
    IM1 = "IM1"
    IM2 = "IM2"
    IM3 = "IM3"
    IH = "IH"
    AL = "AL"


class QuestionType(StrEnum):
    INTRODUCTION = "introduction"
    SURVEY = "survey"
    UNEXPECTED = "unexpected"
    ROLEPLAY = "roleplay"
    COMPARISON = "comparison"
    ADVANCED = "advanced"
    PRACTICE = "practice"


class DifficultyAdjustment(StrEnum):
    EASIER = "easier"
    SAME = "same"
    HARDER = "harder"


class QuestionSetStatus(StrEnum):
    AWAITING_ADJUSTMENT = "awaiting_adjustment"
    COMPLETE = "complete"


class SurveyQuestionType(StrEnum):
    DESCRIPTION = "description"
    ROUTINE = "routine"
    PAST_EXPERIENCE = "past_experience"
    COMPARISON = "comparison"
    ROLEPLAY = "roleplay"
    PROBLEM_SOLVING = "problem_solving"
    OPINION = "opinion"


class ConfidenceBand(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class BackgroundProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    occupation: str | None = None
    student_status: str | None = None
    housing: str | None = None
    interests: list[str] = Field(default_factory=list, max_length=12)
    sports: list[str] = Field(default_factory=list, max_length=8)
    travel: list[str] = Field(default_factory=list, max_length=8)


class SurveyOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: str = Field(alias="topicId", min_length=2, max_length=80)
    label: str = Field(min_length=1, max_length=80)
    category: str = Field(min_length=2, max_length=80)


class BackgroundSurvey(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(min_length=2, max_length=80)
    residence: str = Field(min_length=2, max_length=80)
    leisure: list[str] = Field(default_factory=list, max_length=6)
    hobbies: list[str] = Field(default_factory=list, max_length=6)
    sports: list[str] = Field(default_factory=list, max_length=6)
    travel: list[str] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def validate_selection(self) -> "BackgroundSurvey":
        selected = self.leisure + self.hobbies + self.sports + self.travel
        if len(selected) < 3:
            raise ValueError("at least 3 survey topics are required")
        return self

    def topic_ids(self) -> list[str]:
        values = [
            self.status,
            self.residence,
            *self.leisure,
            *self.hobbies,
            *self.sports,
            *self.travel,
        ]
        result: list[str] = []
        for value in values:
            normalized = value.strip()
            if normalized and normalized not in result:
                result.append(normalized)
        return result


class GeneratedQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int = Field(ge=1, le=15)
    type: QuestionType
    combo_id: str | None = Field(default=None, alias="comboId")
    topic: str = Field(min_length=2, max_length=80)
    prompt: str = Field(min_length=8, max_length=700)
    difficulty: OPIcLevel
    rubric_focus: list[str] = Field(alias="rubricFocus", min_length=1, max_length=6)
    question_type: SurveyQuestionType | None = Field(default=None, alias="questionType")
    follow_up_prompt: str | None = Field(default=None, alias="followUpPrompt", max_length=500)
    topic_id: str | None = Field(default=None, alias="topicId", max_length=80)
    category: str | None = Field(default=None, max_length=80)
    estimated_level: OPIcLevel | None = Field(default=None, alias="estimatedLevel")


class PracticeSetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    initial_level: int | None = Field(default=None, alias="initialLevel", ge=1, le=6)
    target_level: OPIcLevel | None = Field(default=None, alias="targetLevel")
    background: BackgroundProfile = Field(default_factory=BackgroundProfile)
    survey: BackgroundSurvey | None = None
    recent_question_hashes: list[str] = Field(
        default_factory=list, alias="recentQuestionHashes", max_length=50
    )

    @model_validator(mode="after")
    def validate_level(self) -> "PracticeSetRequest":
        if self.initial_level is None and self.target_level is None:
            raise ValueError("initialLevel is required")
        return self


class MockExamRequest(PracticeSetRequest):
    survey: BackgroundSurvey | None = None


class PracticeRefreshRequest(PracticeSetRequest):
    adjustment: DifficultyAdjustment = DifficultyAdjustment.SAME


class QuestionSetResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    set_id: str = Field(alias="setId")
    set_hash: str = Field(alias="setHash")
    questions: list[GeneratedQuestion]
    model_version: str = Field(alias="modelVersion")
    generated_at: datetime = Field(alias="generatedAt")
    fallback_used: bool = Field(default=False, alias="fallbackUsed")
    initial_level: int = Field(alias="initialLevel", ge=1, le=6)
    adjustment: DifficultyAdjustment | None = None
    effective_level: int = Field(alias="effectiveLevel", ge=1, le=6)
    effective_level_code: str = Field(alias="effectiveLevelCode")
    expected_target_level: OPIcLevel = Field(alias="expectedTargetLevel")
    status: QuestionSetStatus
    requires_adjustment_after: int | None = Field(
        default=None, alias="requiresAdjustmentAfter"
    )
    is_complete: bool = Field(alias="isComplete")


class QuestionSetAdjustmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adjustment: DifficultyAdjustment


class TargetLevelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    initial_level: int | None = Field(default=None, alias="initialLevel", ge=1, le=6)
    target_level: OPIcLevel | None = Field(default=None, alias="targetLevel")
    reward_nonce: str | None = Field(default=None, alias="rewardNonce", min_length=16)

    @model_validator(mode="after")
    def validate_level(self) -> "TargetLevelRequest":
        if self.initial_level is None and self.target_level is None:
            raise ValueError("initialLevel is required")
        return self


class TargetLevelResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    target_level: OPIcLevel = Field(alias="targetLevel")
    previous_target_level: OPIcLevel | None = Field(
        default=None, alias="previousTargetLevel"
    )
    initial_level: int = Field(alias="initialLevel", ge=1, le=6)
    previous_initial_level: int | None = Field(default=None, alias="previousInitialLevel")
    latest_adjustment: DifficultyAdjustment = Field(alias="latestAdjustment")
    effective_level: int = Field(alias="effectiveLevel", ge=1, le=6)
    effective_level_code: str = Field(alias="effectiveLevelCode")
    changed: bool
    reward_consumed: bool = Field(alias="rewardConsumed")


class EvaluationScores(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_fulfillment: int = Field(alias="taskFulfillment", ge=0, le=100)
    grammar: int = Field(ge=0, le=100)
    vocabulary: int = Field(ge=0, le=100)
    discourse: int = Field(ge=0, le=100)
    fluency: int = Field(ge=0, le=100)


class AudioMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration_seconds: float = Field(alias="durationSeconds", ge=0)
    speaking_seconds: float = Field(alias="speakingSeconds", ge=0)
    silence_ratio: float = Field(alias="silenceRatio", ge=0, le=1)
    words_per_minute: float = Field(alias="wordsPerMinute", ge=0)


class PracticeEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    predicted_level: OPIcLevel = Field(alias="predictedLevel")
    confidence: ConfidenceBand
    scores: EvaluationScores
    strengths: list[str] = Field(min_length=1, max_length=5)
    improvements: list[str] = Field(min_length=1, max_length=5)
    corrected_answer: str = Field(alias="correctedAnswer", min_length=1)
    target_gap: str = Field(alias="targetGap", min_length=1)
    sample_answer: str = Field(alias="sampleAnswer", min_length=1)
    audio_metrics: AudioMetrics = Field(alias="audioMetrics")
    disclaimer: str
    model_version: str = Field(alias="modelVersion")
    prompt_version: str = Field(alias="promptVersion")


class MockAnswerManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int = Field(ge=1, le=15)
    transcript: str = Field(min_length=1, max_length=12000)


class MockEvaluationManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_level: OPIcLevel | None = Field(default=None, alias="targetLevel")
    set_id: str = Field(alias="setId", min_length=8)
    reward_nonce: str = Field(alias="rewardNonce", min_length=16)
    answers: list[MockAnswerManifest]

    @model_validator(mode="after")
    def validate_complete_exam(self) -> "MockEvaluationManifest":
        if [answer.number for answer in self.answers] != list(range(1, 16)):
            raise ValueError("answers must contain ordered numbers 1 through 15")
        return self


class PerQuestionFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int = Field(ge=1, le=15)
    feedback: str = Field(min_length=1, max_length=180)
    sample_answer: str = Field(alias="sampleAnswer", min_length=1, max_length=350)


class MockEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    predicted_level: OPIcLevel = Field(alias="predictedLevel")
    confidence: ConfidenceBand
    scores: EvaluationScores
    strengths: list[str] = Field(min_length=1, max_length=6)
    improvements: list[str] = Field(min_length=1, max_length=6)
    target_gap: str = Field(alias="targetGap")
    overall_feedback: str = Field(alias="overallFeedback")
    per_question: list[PerQuestionFeedback] = Field(alias="perQuestion")
    disclaimer: str
    model_version: str = Field(alias="modelVersion")
    prompt_version: str = Field(alias="promptVersion")

    @field_validator("per_question")
    @classmethod
    def validate_feedback_count(
        cls, value: list[PerQuestionFeedback]
    ) -> list[PerQuestionFeedback]:
        if [item.number for item in value] != list(range(1, 16)):
            raise ValueError("perQuestion must contain ordered numbers 1 through 15")
        return value


class UsageResponse(BaseModel):
    date: str
    free_remaining: int = Field(alias="freeRemaining", ge=0)
    bonus_remaining: int = Field(alias="bonusRemaining", ge=0)


class RewardPurpose(StrEnum):
    PRACTICE_CREDITS = "practice_credits"
    MOCK_RESULT = "mock_result"
    TARGET_LEVEL_CHANGE = "target_level_change"


class RewardIntentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: RewardPurpose
    session_hash: str | None = Field(default=None, alias="sessionHash")

    @model_validator(mode="after")
    def validate_session_hash(self) -> "RewardIntentRequest":
        if self.purpose is RewardPurpose.MOCK_RESULT and not self.session_hash:
            raise ValueError("sessionHash is required for mock_result")
        return self


class RewardIntentResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    nonce: str
    purpose: RewardPurpose
    status: str
    user_identifier: str = Field(alias="userIdentifier")
    custom_data: str = Field(alias="customData")
    expires_at: datetime = Field(alias="expiresAt")


class APIError(BaseModel):
    code: str
    message: str
