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


class ExamSection(StrEnum):
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

    @classmethod
    def _missing_(cls, value: object) -> "DifficultyAdjustment | None":
        if isinstance(value, str) and value.strip().lower() == "similar":
            return cls.SAME
        return None


class QuestionSetStatus(StrEnum):
    AWAITING_ADJUSTMENT = "awaiting_adjustment"
    COMPLETE = "complete"


class QuestionStyle(StrEnum):
    DESCRIPTION = "description"
    ROUTINE = "routine"
    PAST_EXPERIENCE = "past_experience"
    COMPARISON = "comparison"
    ROLEPLAY = "roleplay"
    PROBLEM_SOLVING = "problem_solving"
    OPINION = "opinion"

    @classmethod
    def _missing_(cls, value: object) -> "QuestionStyle | None":
        if not isinstance(value, str):
            return None
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "descriptive": cls.DESCRIPTION,
            "pastexperience": cls.PAST_EXPERIENCE,
            "experience": cls.PAST_EXPERIENCE,
            "problemsolving": cls.PROBLEM_SOLVING,
        }
        return aliases.get(normalized.replace("_", ""))


class ConfidenceBand(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RubricBand(StrEnum):
    FOUNDATION = "foundation"
    DEVELOPING = "developing"
    FUNCTIONAL = "functional"
    STRONG = "strong"
    ADVANCED = "advanced"


class RubricDimension(StrEnum):
    TASK_FULFILLMENT = "taskFulfillment"
    GRAMMAR = "grammar"
    VOCABULARY = "vocabulary"
    DISCOURSE = "discourse"
    FLUENCY = "fluency"


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
    exam_section: ExamSection = Field(alias="examSection")
    combo_id: str | None = Field(default=None, alias="comboId")
    topic: str = Field(min_length=2, max_length=80)
    prompt: str = Field(min_length=8, max_length=700)
    difficulty: OPIcLevel
    rubric_focus: list[str] = Field(alias="rubricFocus", min_length=1, max_length=6)
    question_style: QuestionStyle | None = Field(default=None, alias="questionStyle")
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
    # 취약점 복습 세트(Pro): 지정 루브릭 차원에 편향된 문제 생성 요청.
    focus_dimension: RubricDimension | None = Field(
        default=None, alias="focusDimension"
    )

    @model_validator(mode="after")
    def validate_level(self) -> "PracticeSetRequest":
        if self.initial_level is None and self.target_level is None:
            raise ValueError("initialLevel is required")
        return self


class MockExamRequest(PracticeSetRequest):
    survey: BackgroundSurvey | None = None


class MockSessionStage(StrEnum):
    AWAITING_START_AD = "awaiting_start_ad"
    GENERATING_FRONT = "generating_front"
    ANSWERING_FRONT = "answering_front"
    AWAITING_ADJUSTMENT_AD = "awaiting_adjustment_ad"
    GENERATING_TAIL = "generating_tail"
    ANSWERING_TAIL = "answering_tail"
    AWAITING_RESULT_AD = "awaiting_result_ad"
    EVALUATING = "evaluating"
    COMPLETED = "completed"


class MockSessionRewardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reward_nonce: str = Field(alias="rewardNonce", min_length=16)


class MockSessionAdjustmentRequest(MockSessionRewardRequest):
    adjustment: DifficultyAdjustment


class PracticeRefreshRequest(PracticeSetRequest):
    adjustment: DifficultyAdjustment = DifficultyAdjustment.SAME
    reward_nonce: str = Field(alias="rewardNonce", min_length=16)


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
    provider: str = "openai"
    fallback_reason: str | None = Field(default=None, alias="fallbackReason")
    fallback_question_numbers: list[int] = Field(
        default_factory=list,
        alias="fallbackQuestionNumbers",
    )
    retry_count: int = Field(default=0, alias="retryCount", ge=0)
    prompt_version: str | None = Field(default=None, alias="promptVersion")
    schema_version: str | None = Field(default=None, alias="schemaVersion")
    server_date_key: str | None = Field(default=None, alias="serverDateKey")


class MockSessionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(alias="sessionId")
    session_hash: str = Field(alias="sessionHash")
    server_date_key: str = Field(alias="serverDateKey")
    stage: MockSessionStage
    resets_at: datetime = Field(alias="resetsAt")
    set_id: str | None = Field(default=None, alias="setId")
    set_hash: str | None = Field(default=None, alias="setHash")
    adjustment: DifficultyAdjustment | None = None
    question_set: QuestionSetResponse | None = Field(default=None, alias="questionSet")


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
    before_adjust: int = Field(alias="beforeAdjust", ge=1, le=6)
    previous_before_adjust: int | None = Field(default=None, alias="previousBeforeAdjust")
    after_adjust: int = Field(alias="afterAdjust", ge=1, le=6)
    changed: bool
    reward_consumed: bool = Field(alias="rewardConsumed")


class EvaluationScores(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_fulfillment: int = Field(alias="taskFulfillment", ge=0, le=100)
    grammar: int = Field(ge=0, le=100)
    vocabulary: int = Field(ge=0, le=100)
    discourse: int = Field(ge=0, le=100)
    fluency: int = Field(ge=0, le=100)


class RubricAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: RubricDimension
    band: RubricBand
    evidence: str = Field(min_length=1, max_length=240)
    next_action: str = Field(alias="nextAction", min_length=1, max_length=240)


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
    corrected_answer: str | None = Field(default=None, alias="correctedAnswer")
    target_gap: str | None = Field(default=None, alias="targetGap")
    sample_answer: str | None = Field(default=None, alias="sampleAnswer")
    audio_metrics: AudioMetrics = Field(alias="audioMetrics")
    disclaimer: str
    model_version: str = Field(alias="modelVersion")
    prompt_version: str = Field(alias="promptVersion")
    result_status: str = Field(default="complete", alias="resultStatus")
    rubrics: list[RubricAssessment] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    score_scale_version: str = Field(
        default="rubric-band-v1", alias="scoreScaleVersion"
    )


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
    result_status: str = Field(default="complete", alias="resultStatus")
    rubrics: list[RubricAssessment] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    score_scale_version: str = Field(
        default="rubric-band-v1", alias="scoreScaleVersion"
    )

    @field_validator("per_question")
    @classmethod
    def validate_feedback_count(
        cls, value: list[PerQuestionFeedback]
    ) -> list[PerQuestionFeedback]:
        if value and [item.number for item in value] != list(range(1, 16)):
            raise ValueError("perQuestion must contain ordered numbers 1 through 15")
        return value


class UsageResponse(BaseModel):
    date: str
    free_remaining: int = Field(alias="freeRemaining", ge=0)
    bonus_remaining: int = Field(alias="bonusRemaining", ge=0)
    server_date_key: str | None = Field(default=None, alias="serverDateKey")
    resets_at: datetime | None = Field(default=None, alias="resetsAt")
    daily_analysis_free_remaining: int | None = Field(
        default=None, alias="dailyAnalysisFreeRemaining", ge=0
    )
    daily_analysis_reward_remaining: int | None = Field(
        default=None, alias="dailyAnalysisRewardRemaining", ge=0
    )
    daily_refresh_remaining: int | None = Field(
        default=None, alias="dailyRefreshRemaining", ge=0
    )
    mock_available: bool | None = Field(default=None, alias="mockAvailable")
    mock_session_stage: str | None = Field(default=None, alias="mockSessionStage")


class RewardPurpose(StrEnum):
    PRACTICE_CREDITS = "practice_credits"
    PRACTICE_REFRESH = "practice_refresh"
    MOCK_START = "mock_start"
    MOCK_ADJUSTMENT = "mock_adjustment"
    MOCK_RESULT = "mock_result"
    TARGET_LEVEL_CHANGE = "target_level_change"


class RewardIntentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: RewardPurpose
    session_hash: str | None = Field(default=None, alias="sessionHash")

    @model_validator(mode="after")
    def validate_session_hash(self) -> "RewardIntentRequest":
        if self.purpose in {
            RewardPurpose.MOCK_START,
            RewardPurpose.MOCK_ADJUSTMENT,
            RewardPurpose.MOCK_RESULT,
        } and not self.session_hash:
            raise ValueError("sessionHash is required for mock rewards")
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


class OperationResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    operation_id: str = Field(alias="operationId")
    operation: str
    status: str
    result: dict[str, object] | None = None
    retryable: bool = False
    updated_at: datetime | None = Field(default=None, alias="updatedAt")


class CapabilityQuotaPolicy(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    daily_analysis_free: int = Field(alias="dailyAnalysisFree", ge=0)
    daily_refresh_rewards: int = Field(alias="dailyRefreshRewards", ge=0)
    mock_sessions_per_day: int = Field(default=1, alias="mockSessionsPerDay", ge=0)
    mock_reward_gates: int = Field(default=3, alias="mockRewardGates", ge=0)
    # 플랜별 기능 게이트(클라이언트 UI 게이팅용). 무료 기본값을 유지해 하위 호환.
    practice_daily: int = Field(default=1, alias="practiceDaily", ge=0)
    practice_ad_bonus: int = Field(default=1, alias="practiceAdBonus", ge=0)
    history_days: int | None = Field(default=7, alias="historyDays", ge=0)
    analysis_depth: str = Field(default="summary", alias="analysisDepth")
    grade_trend: str = Field(default="limited", alias="gradeTrend")
    weakness_analysis: str = Field(default="none", alias="weaknessAnalysis")
    review_set: bool = Field(default=False, alias="reviewSet")
    weekly_report: bool = Field(default=False, alias="weeklyReport")
    mock_comparison: str = Field(default="none", alias="mockComparison")
    ads_enabled: bool = Field(default=True, alias="adsEnabled")


class CapabilitiesResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    minimum_supported_app_version: str = Field(alias="minimumSupportedAppVersion")
    question_generation_v2: bool = Field(alias="questionGenerationV2")
    mock_session_v2: bool = Field(alias="mockSessionV2")
    evaluation_rubric_v2: bool = Field(alias="evaluationRubricV2")
    practice_refresh: bool = Field(alias="practiceRefresh")
    guide_schema_version: int = Field(alias="guideSchemaVersion", ge=1)
    plan: str = "free"
    quota_policy: CapabilityQuotaPolicy = Field(alias="quotaPolicy")


class RevenueCatEvent(BaseModel):
    """RevenueCat 웹훅 이벤트(관심 필드만; 나머지 무시)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    type: str
    id: str | None = None
    app_user_id: str | None = None
    original_app_user_id: str | None = None
    entitlement_ids: list[str] | None = None
    entitlement_id: str | None = None
    product_id: str | None = None
    period_type: str | None = None
    expiration_at_ms: int | None = None
    purchased_at_ms: int | None = None
    store: str | None = None


class RevenueCatWebhook(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event: RevenueCatEvent
    api_version: str | None = None
