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
    # 로컬 알림 권한 2종(서버 푸시 아님). 학습 알림은 전 플랜이 쓰므로 기본값이 True다(P9).
    calendar_study_reminder: bool = Field(default=True, alias="calendarStudyReminder")
    # 개인 일정 알림은 유료 기능이라 무료 기본값(False)으로 두어 구버전과 안전하게 만난다.
    calendar_event_reminder: bool = Field(default=False, alias="calendarEventReminder")
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


# --- 단어장 AI 생성(P14.2) ----------------------------------------------------
# 원시값은 iOS `Models/Vocabulary.swift`의 enum과 **글자 그대로** 같다. 두 곳이
# 어긋나면 앱이 서버 응답을 디코딩하지 못하므로 값을 바꿀 때는 함께 바꾼다.


class VocabularyItemType(StrEnum):
    WORD = "word"
    PHRASE = "phrase"
    PATTERN = "pattern"


class VocabularyTopic(StrEnum):
    HOME_NEIGHBORHOOD = "home_neighborhood"
    SCHOOL = "school"
    WORK = "work"
    CAFES = "cafes"
    FOOD = "food"
    MOVIES = "movies"
    MUSIC = "music"
    EXERCISE = "exercise"
    PARK = "park"
    SHOPPING = "shopping"
    TRAVEL = "travel"
    TRANSPORTATION = "transportation"
    WEATHER = "weather"
    VACATION = "vacation"
    FAMILY_FRIENDS = "family_friends"
    DAILY_LIFE = "daily_life"

    @property
    def label(self) -> str:
        return _VOCABULARY_TOPIC_LABELS[self]


_VOCABULARY_TOPIC_LABELS: dict[str, str] = {
    "home_neighborhood": "집과 동네",
    "school": "학교",
    "work": "직장",
    "cafes": "카페",
    "food": "음식점과 음식",
    "movies": "영화",
    "music": "음악",
    "exercise": "운동",
    "park": "공원",
    "shopping": "쇼핑",
    "travel": "여행",
    "transportation": "교통",
    "weather": "날씨",
    "vacation": "휴가",
    "family_friends": "친구와 가족",
    "daily_life": "일상",
}


class VocabularyUsageRole(StrEnum):
    DESCRIPTION = "description"
    ROUTINE = "routine"
    FREQUENCY = "frequency"
    PREFERENCE = "preference"
    REASON = "reason"
    EMOTION = "emotion"
    PAST_EXPERIENCE = "pastExperience"
    COMPARISON = "comparison"
    CHANGE = "change"
    PROBLEM_SOLUTION = "problemSolution"
    TRANSITION = "transition"


class VocabularyEntry(BaseModel):
    """앱의 단어장 항목 계약(iOS `VocabularyEntry`와 동일 필드).

    발음기호(IPA)·AI 신뢰도 점수·가짜 OPIc 점수는 담지 않는다.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    term: str
    type: VocabularyItemType
    meaning_ko: str = Field(alias="meaningKo")
    example_en: str = Field(alias="exampleEn")
    example_ko: str = Field(alias="exampleKo")
    collocations: list[str] = Field(default_factory=list)
    topics: list[VocabularyTopic]
    usage_roles: list[VocabularyUsageRole] = Field(alias="usageRoles")
    recommended_levels: list[OPIcLevel] = Field(alias="recommendedLevels")
    source: str


class VocabularyGenerationRequest(BaseModel):
    """AI 맞춤 단어장 1회 생성 요청(P14.2). **이미 배포된 계약이라 건드리지 않는다.**

    개수(30) · 구성(10/10/10) · 모델 · 토큰 비용(1)은 **서버가 소유한다**.
    클라이언트가 정할 수 있는 것은 주제 · 목표 등급 · 제외 후보뿐이다.

    오늘의 단어 20개는 이 모델을 쓰지 않는다 — `TodayVocabularyGenerationRequest`가
    따로 있다. 여기에 쓰임새·개수를 고르는 필드를 **추가하지 않는다**: 그 순간
    예전 클라이언트가 보내는 요청의 의미가 서버 사정으로 흔들린다.
    """

    model_config = ConfigDict(extra="forbid")

    topic: VocabularyTopic
    target_level: OPIcLevel = Field(alias="targetLevel")
    # 중복 방지용 힌트. 시드 카탈로그 전체를 프롬프트에 넣지 않기 위해 개수와
    # 길이를 모두 서버가 제한한다.
    exclude_terms: list[str] = Field(
        default_factory=list, alias="excludeTerms", max_length=200
    )

    @field_validator("exclude_terms")
    @classmethod
    def clean_exclude_terms(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        return [item for item in cleaned if 0 < len(item) <= 60]


class TodayVocabularyGenerationRequest(BaseModel):
    """오늘의 단어 만들기 1회 생성 요청(P14.6). 예전 요청 모델과 **별개 타입**이다.

    보내는 값은 같은 셋(주제 · 목표 등급 · 제외 후보)이지만 의미가 다르다 —
    이 요청은 언제나 20개(7/7/6)다. 개수도 쓰임새도 요청에 없다: endpoint가
    곧 쓰임새다. 그래서 discriminator 필드가 필요 없고, 예전 모델에 손댈 일도 없다.
    """

    model_config = ConfigDict(extra="forbid")

    topic: VocabularyTopic
    target_level: OPIcLevel = Field(alias="targetLevel")
    exclude_terms: list[str] = Field(
        default_factory=list, alias="excludeTerms", max_length=200
    )

    @field_validator("exclude_terms")
    @classmethod
    def clean_exclude_terms(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        return [item for item in cleaned if 0 < len(item) <= 60]


class VocabularyGeneratedSet(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    set_id: str = Field(alias="setId")
    topic: VocabularyTopic
    target_level: OPIcLevel = Field(alias="targetLevel")
    created_at: datetime = Field(alias="createdAt")
    entries: list[VocabularyEntry]
    source: str = "ai"


# --- 단어장 AI 말하기 코치(P14.3) ----------------------------------------------
# 단어를 배우고 그 표현으로 직접 말한 뒤, **사용자가 명시적으로 요청할 때만**
# 데일리 토큰 1개로 코칭을 받는다. 녹음 · 전사 · 저장된 결과 재열람은 0개다.
#
# 여기 없는 것: 예상 OPIc 등급 · 숫자 점수 · 5개 영역 루브릭 · 합불 · 발음 점수.
# 그건 데일리 분석(`PracticeEvaluation`)의 몫이고 코치가 흉내 내면 두 제품이 서로
# 다른 등급을 말하게 된다.

# 요청 상한. 짧은 표현 연습이므로 수천 단어짜리 전사를 받지 않는다 —
# 넘치는 입력을 조용히 잘라 과금하는 대신 제공자 호출 **전에** 거절한다.
VOCABULARY_TERM_MAX_CHARS = 80
VOCABULARY_TRANSCRIPT_MAX_CHARS = 600
VOCABULARY_ENTRY_ID_MAX_CHARS = 120


class VocabularyUsageAssessment(StrEnum):
    """대상 표현을 실제로 어떻게 썼는가. 점수가 아니라 세 갈래 판정이다.

    문자열이 전사에 들어 있다는 이유만으로 `appropriate`가 되지 않는다 —
    맥락이 어긋나면 `needs_polish`다(프롬프트가 그렇게 지시한다).
    """

    APPROPRIATE = "appropriate"
    NEEDS_POLISH = "needsPolish"
    NOT_USED = "notUsed"


class VocabularySpeakingCoachRequest(BaseModel):
    """코칭 1회 요청.

    모델 · 시스템 프롬프트 · 출력 스키마 · 토큰 비용(1)은 **서버가 소유한다.**
    앱이 보내는 것은 무엇을 연습했는지와 무엇을 말했는지뿐이다.
    """

    model_config = ConfigDict(extra="forbid")

    entry_id: str = Field(
        alias="entryId", min_length=1, max_length=VOCABULARY_ENTRY_ID_MAX_CHARS
    )
    term: str = Field(min_length=1, max_length=VOCABULARY_TERM_MAX_CHARS)
    type: VocabularyItemType
    transcript: str = Field(min_length=1, max_length=VOCABULARY_TRANSCRIPT_MAX_CHARS)
    # 있으면 코칭 품질이 올라가는 값들. 시드 · AI 생성 항목 모두 갖고 있지만
    # 필수로 만들지 않는다 — 구버전 앱이 보내지 않아도 코칭은 성립한다.
    meaning_ko: str | None = Field(default=None, alias="meaningKo", max_length=120)
    topic: VocabularyTopic | None = None
    target_level: OPIcLevel | None = Field(default=None, alias="targetLevel")

    @field_validator("entry_id", "term", "transcript")
    @classmethod
    def require_content(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class VocabularySpeakingCoachResult(BaseModel):
    """코칭 1회 결과(iOS `VocabularySpeakingCoachResult`와 동일 필드).

    앱은 이 결과를 기기에 저장해 두고 다시 열 때 **무료로** 보여준다.
    """

    model_config = ConfigDict(populate_by_name=True)

    result_id: str = Field(alias="resultId")
    entry_id: str = Field(alias="entryId")
    target_term: str = Field(alias="targetTerm")
    transcript: str
    usage_assessment: VocabularyUsageAssessment = Field(alias="usageAssessment")
    usage_feedback_ko: str = Field(alias="usageFeedbackKo")
    natural_correction_en: str = Field(alias="naturalCorrectionEn")
    natural_correction_ko: str = Field(alias="naturalCorrectionKo")
    expanded_answer_en: str = Field(alias="expandedAnswerEn")
    expanded_answer_ko: str = Field(alias="expandedAnswerKo")
    related_expressions: list[str] = Field(alias="relatedExpressions")
    created_at: datetime = Field(alias="createdAt")
