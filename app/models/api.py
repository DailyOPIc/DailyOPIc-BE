from __future__ import annotations

from datetime import date, datetime, time
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _normalize_enum_input(value: str) -> str:
    """Enum alias 입력값 정규화: strip → lowercase → 특수문자 정리

    예:
      "past-experience" → "past_experience"
      "Problem Solving" → "problem_solving"
      "  descriptive  " → "descriptive"
    """
    return value.strip().lower().replace("-", "_").replace(" ", "_")


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
        if not isinstance(value, str):
            return None
        # alias 매핑: "similar" → SAME
        aliases = {"similar": cls.SAME}
        normalized = _normalize_enum_input(value)
        return aliases.get(normalized)


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
        # 정규화: strip → lowercase → 특수문자 정리
        normalized = _normalize_enum_input(value)
        # alias 매핑: underscore 제거 후 검색
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


class AnswerQuality(StrEnum):
    """등급을 얼마나 믿을 수 있는지에 대한 서버 판정(DESIGN_DECISIONS T4의 A/B/C).

    판정 규칙은 app/services/answer_quality.py 한 곳에만 둔다. 클라이언트는
    이 값을 렌더링만 하고 자체 임계값을 만들지 않는다.
    """

    GRADABLE = "gradable"  # A
    LOW_EVIDENCE = "low_evidence"  # B
    INSUFFICIENT = "insufficient"  # C


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
    # 토큰 모델 전환으로 리프레시는 광고/리워드가 아닌 데일리 토큰을 소모한다.
    # rewardNonce는 하위 호환을 위해 옵셔널로 남겨둔다(미사용).
    reward_nonce: str | None = Field(default=None, alias="rewardNonce")


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
    # ffprobe/ffmpeg 실패 또는 오디오 미첨부 시 전사 길이로 추정한 값이면 True.
    # 측정값과 추정값을 구분하지 않으면 추정치를 근거로 신뢰도를 말하게 된다.
    is_estimated: bool = Field(default=False, alias="isEstimated")


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
    # 근거 충분도. 판정 규칙은 app/services/answer_quality.py 한 곳에만 있다.
    answer_quality: AnswerQuality = Field(
        default=AnswerQuality.GRADABLE, alias="answerQuality"
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
    answer_quality: AnswerQuality = Field(
        default=AnswerQuality.GRADABLE, alias="answerQuality"
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
    mock_remaining: int | None = Field(default=None, alias="mockRemaining", ge=0)
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
    review_set: bool = Field(default=False, alias="reviewSet")
    ads_enabled: bool = Field(default=True, alias="adsEnabled")
    # 캘린더 자동화 깊이. 무료 기본값(캘린더는 열리고 자동화만 꺼짐)이라 하위 호환.
    calendar_enabled: bool = Field(default=True, alias="calendarEnabled")
    calendar_auto_replan: bool = Field(default=False, alias="calendarAutoReplan")
    calendar_evaluation_adaptive: bool = Field(default=False, alias="calendarEvaluationAdaptive")
    calendar_exam_backplan: bool = Field(default=False, alias="calendarExamBackplan")
    # 제거: gradeTrend / weaknessAnalysis / weeklyReport / mockComparison.
    # 앞의 셋은 앱이 로컬 기록으로 계산하는 전 플랜 무료 기능이고, 마지막은
    # 구현이 없는 값이었다. 강제하지 않는 게이트는 내려보내지 않는다.


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


class StudyIntensity(StrEnum):
    LIGHT = "light"
    STEADY = "steady"
    FOCUSED = "focused"

    @classmethod
    def _missing_(cls, value: object) -> "StudyIntensity | None":
        if isinstance(value, str):
            return cls.__members__.get(_normalize_enum_input(value).upper())
        return None


# 학습 캘린더 설정. 목표 등급은 여기 두지 않는다 — userProfiles.targetLevel이
# 이미 진실이고(PUT /v1/users/me/target-level), 복제하면 두 값이 갈라진다.
class StudyPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    exam_date: date | None = Field(default=None, alias="examDate")
    study_weekdays: list[int] = Field(alias="studyWeekdays", min_length=1, max_length=7)
    intensity: StudyIntensity
    preferred_study_time: str = Field(alias="preferredStudyTime")
    timezone_identifier: str = Field(alias="timezoneIdentifier", max_length=64)

    @field_validator("study_weekdays")
    @classmethod
    def validate_weekdays(cls, value: list[int]) -> list[int]:
        """ISO 8601 요일(월=1 … 일=7). 중복은 사용자 의도가 모호하므로 거절한다."""
        if any(day < 1 or day > 7 for day in value):
            raise ValueError("studyWeekdays must be ISO weekdays between 1 and 7")
        if len(set(value)) != len(value):
            raise ValueError("studyWeekdays must not contain duplicates")
        return sorted(value)

    @field_validator("preferred_study_time")
    @classmethod
    def validate_preferred_time(cls, value: str) -> str:
        """로컬 벽시계 시각 "HH:MM". 알림은 G5에서 이 값을 쓴다."""
        try:
            parsed = time.fromisoformat(value)
        except ValueError as error:
            raise ValueError("preferredStudyTime must be HH:MM") from error
        if parsed.second or parsed.microsecond or parsed.tzinfo is not None:
            raise ValueError("preferredStudyTime must be HH:MM")
        return f"{parsed.hour:02d}:{parsed.minute:02d}"

    @field_validator("timezone_identifier")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("timezoneIdentifier must be a valid IANA timezone") from error
        return value

    @model_validator(mode="after")
    def validate_exam_date(self) -> "StudyPlanRequest":
        """시험일은 사용자의 로컬 오늘 기준으로 판단한다. UTC로 재면 타임존에
        따라 방금 고른 오늘 날짜가 과거로 취급된다."""
        if self.exam_date is None:
            return self
        today = datetime.now(ZoneInfo(self.timezone_identifier)).date()
        if self.exam_date < today:
            raise ValueError("examDate must not be in the past")
        return self


class StudyPlanSettings(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_version: int = Field(alias="schemaVersion", ge=1)
    exam_date: date | None = Field(default=None, alias="examDate")
    study_weekdays: list[int] = Field(alias="studyWeekdays")
    intensity: StudyIntensity
    preferred_study_time: str = Field(alias="preferredStudyTime")
    timezone_identifier: str = Field(alias="timezoneIdentifier")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class StudyPlanResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    configured: bool
    study_plan: StudyPlanSettings | None = Field(default=None, alias="studyPlan")


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
