"""
Enum Alias 및 정규화 로직 테스트

DifficultyAdjustment와 QuestionStyle의 _missing_() 메서드 동작 검증.
- 직접값 매칭
- alias 정규화 및 매칭
- 에러 처리 (ValueError)
- 엣지 케이스 (빈 문자열, null, 잘못된 타입)
"""
import pytest

from app.models.api import DifficultyAdjustment, QuestionStyle


# ============================================================================
# DifficultyAdjustment Tests
# ============================================================================


class TestDifficultyAdjustmentPass:
    """DifficultyAdjustment: 성공 케이스 (ValueError 발생 안 함)"""

    # 직접값 (3개)
    def test_da_direct_value_easier(self) -> None:
        """TC-DA-001: 직접값 'easier'"""
        assert DifficultyAdjustment("easier") == DifficultyAdjustment.EASIER

    def test_da_direct_value_same(self) -> None:
        """TC-DA-002: 직접값 'same'"""
        assert DifficultyAdjustment("same") == DifficultyAdjustment.SAME

    def test_da_direct_value_harder(self) -> None:
        """TC-DA-003: 직접값 'harder'"""
        assert DifficultyAdjustment("harder") == DifficultyAdjustment.HARDER

    # Alias: "similar" -> SAME (5개)
    def test_da_alias_similar_lowercase(self) -> None:
        """TC-DA-004: alias 'similar' (소문자)"""
        assert DifficultyAdjustment("similar") == DifficultyAdjustment.SAME

    def test_da_alias_similar_uppercase(self) -> None:
        """TC-DA-005: alias 'SIMILAR' (대문자)"""
        assert DifficultyAdjustment("SIMILAR") == DifficultyAdjustment.SAME

    def test_da_alias_similar_mixed_case(self) -> None:
        """TC-DA-006: alias 'Similar' (혼합 대소문자)"""
        assert DifficultyAdjustment("Similar") == DifficultyAdjustment.SAME

    def test_da_alias_similar_with_spaces(self) -> None:
        """TC-DA-007: alias '  similar  ' (양쪽 공백)"""
        assert DifficultyAdjustment("  similar  ") == DifficultyAdjustment.SAME

    def test_da_alias_similar_uppercase_with_spaces(self) -> None:
        """TC-DA-008: alias '  SIMILAR  ' (공백 + 대문자)"""
        assert DifficultyAdjustment("  SIMILAR  ") == DifficultyAdjustment.SAME


class TestDifficultyAdjustmentFail:
    """DifficultyAdjustment: 실패 케이스 (ValueError 발생)"""

    # 매칭되지 않는 문자열 (3개)
    def test_da_invalid_unknown(self) -> None:
        """TC-DA-F01: 존재하지 않는 값 'unknown'"""
        with pytest.raises(ValueError):
            DifficultyAdjustment("unknown")

    def test_da_invalid_same_but_not(self) -> None:
        """TC-DA-F02: 존재하지 않는 값 'same_but_not'"""
        with pytest.raises(ValueError):
            DifficultyAdjustment("same_but_not")

    def test_da_invalid_typo_easierr(self) -> None:
        """TC-DA-F03: 오타 'easierr'"""
        with pytest.raises(ValueError):
            DifficultyAdjustment("easierr")

    # 잘못된 타입 (3개)
    def test_da_invalid_type_int(self) -> None:
        """TC-DA-F04: 정수 타입"""
        with pytest.raises((ValueError, TypeError)):
            DifficultyAdjustment(123)  # type: ignore

    def test_da_invalid_type_none(self) -> None:
        """TC-DA-F05: None 타입"""
        with pytest.raises((ValueError, TypeError)):
            DifficultyAdjustment(None)  # type: ignore

    def test_da_invalid_type_float(self) -> None:
        """TC-DA-F06: 실수 타입"""
        with pytest.raises((ValueError, TypeError)):
            DifficultyAdjustment(1.5)  # type: ignore

    # 빈 값 (2개)
    def test_da_empty_string(self) -> None:
        """TC-DA-F07: 빈 문자열 ''"""
        with pytest.raises(ValueError):
            DifficultyAdjustment("")

    def test_da_spaces_only(self) -> None:
        """TC-DA-F08: 공백만 '   '"""
        with pytest.raises(ValueError):
            DifficultyAdjustment("   ")


# ============================================================================
# QuestionStyle Tests
# ============================================================================


class TestQuestionStylePass:
    """QuestionStyle: 성공 케이스 (ValueError 발생 안 함)"""

    # 직접값 - underscore 형식 (7개)
    def test_qs_direct_value_description(self) -> None:
        """TC-QS-001: 직접값 'description'"""
        assert QuestionStyle("description") == QuestionStyle.DESCRIPTION

    def test_qs_direct_value_routine(self) -> None:
        """TC-QS-002: 직접값 'routine'"""
        assert QuestionStyle("routine") == QuestionStyle.ROUTINE

    def test_qs_direct_value_past_experience(self) -> None:
        """TC-QS-003: 직접값 'past_experience'"""
        assert QuestionStyle("past_experience") == QuestionStyle.PAST_EXPERIENCE

    def test_qs_direct_value_comparison(self) -> None:
        """TC-QS-004: 직접값 'comparison'"""
        assert QuestionStyle("comparison") == QuestionStyle.COMPARISON

    def test_qs_direct_value_roleplay(self) -> None:
        """TC-QS-005: 직접값 'roleplay'"""
        assert QuestionStyle("roleplay") == QuestionStyle.ROLEPLAY

    def test_qs_direct_value_problem_solving(self) -> None:
        """TC-QS-006: 직접값 'problem_solving'"""
        assert QuestionStyle("problem_solving") == QuestionStyle.PROBLEM_SOLVING

    def test_qs_direct_value_opinion(self) -> None:
        """TC-QS-007: 직접값 'opinion'"""
        assert QuestionStyle("opinion") == QuestionStyle.OPINION

    # Alias: "descriptive" -> DESCRIPTION (4개)
    def test_qs_alias_descriptive_lowercase(self) -> None:
        """TC-QS-008: alias 'descriptive' (소문자)"""
        assert QuestionStyle("descriptive") == QuestionStyle.DESCRIPTION

    def test_qs_alias_descriptive_uppercase(self) -> None:
        """TC-QS-009: alias 'DESCRIPTIVE' (대문자)"""
        assert QuestionStyle("DESCRIPTIVE") == QuestionStyle.DESCRIPTION

    def test_qs_alias_descriptive_mixed_case(self) -> None:
        """TC-QS-010: alias 'Descriptive' (혼합)"""
        assert QuestionStyle("Descriptive") == QuestionStyle.DESCRIPTION

    def test_qs_alias_descriptive_with_spaces(self) -> None:
        """TC-QS-011: alias '  descriptive  ' (공백)"""
        assert QuestionStyle("  descriptive  ") == QuestionStyle.DESCRIPTION

    # Alias: "pastexperience" 형식 -> PAST_EXPERIENCE (6개)
    def test_qs_alias_pastexperience_nospace(self) -> None:
        """TC-QS-012: alias 'pastexperience' (공백 없음)"""
        assert QuestionStyle("pastexperience") == QuestionStyle.PAST_EXPERIENCE

    def test_qs_alias_pastexperience_hyphen(self) -> None:
        """TC-QS-013: alias 'past-experience' (하이픈)"""
        assert QuestionStyle("past-experience") == QuestionStyle.PAST_EXPERIENCE

    def test_qs_alias_pastexperience_space(self) -> None:
        """TC-QS-014: alias 'past experience' (공백)"""
        assert QuestionStyle("past experience") == QuestionStyle.PAST_EXPERIENCE

    def test_qs_alias_pastexperience_uppercase_hyphen(self) -> None:
        """TC-QS-015: alias 'PAST-EXPERIENCE' (대문자 + 하이픈)"""
        assert QuestionStyle("PAST-EXPERIENCE") == QuestionStyle.PAST_EXPERIENCE

    def test_qs_alias_pastexperience_space_wrapped(self) -> None:
        """TC-QS-016: alias '  past experience  ' (공백으로 감싼 것)"""
        assert QuestionStyle("  past experience  ") == QuestionStyle.PAST_EXPERIENCE

    def test_qs_alias_pastexperience_mixed_case_hyphen(self) -> None:
        """TC-QS-017: alias 'Past-Experience' (혼합 대소문자)"""
        assert QuestionStyle("Past-Experience") == QuestionStyle.PAST_EXPERIENCE

    # Alias: "experience" -> PAST_EXPERIENCE (3개)
    def test_qs_alias_experience_lowercase(self) -> None:
        """TC-QS-018: alias 'experience' (소문자)"""
        assert QuestionStyle("experience") == QuestionStyle.PAST_EXPERIENCE

    def test_qs_alias_experience_uppercase(self) -> None:
        """TC-QS-019: alias 'EXPERIENCE' (대문자)"""
        assert QuestionStyle("EXPERIENCE") == QuestionStyle.PAST_EXPERIENCE

    def test_qs_alias_experience_with_spaces(self) -> None:
        """TC-QS-020: alias '  experience  ' (공백)"""
        assert QuestionStyle("  experience  ") == QuestionStyle.PAST_EXPERIENCE

    # Alias: "problemsolving" 형식 -> PROBLEM_SOLVING (5개)
    def test_qs_alias_problemsolving_nospace(self) -> None:
        """TC-QS-021: alias 'problemsolving' (공백 없음)"""
        assert QuestionStyle("problemsolving") == QuestionStyle.PROBLEM_SOLVING

    def test_qs_alias_problemsolving_hyphen(self) -> None:
        """TC-QS-022: alias 'problem-solving' (하이픈)"""
        assert QuestionStyle("problem-solving") == QuestionStyle.PROBLEM_SOLVING

    def test_qs_alias_problemsolving_space(self) -> None:
        """TC-QS-023: alias 'problem solving' (공백)"""
        assert QuestionStyle("problem solving") == QuestionStyle.PROBLEM_SOLVING

    def test_qs_alias_problemsolving_uppercase_hyphen(self) -> None:
        """TC-QS-024: alias 'PROBLEM-SOLVING' (대문자 + 하이픈)"""
        assert QuestionStyle("PROBLEM-SOLVING") == QuestionStyle.PROBLEM_SOLVING

    def test_qs_alias_problemsolving_space_wrapped(self) -> None:
        """TC-QS-025: alias '  problem solving  ' (공백으로 감싼 것)"""
        assert QuestionStyle("  problem solving  ") == QuestionStyle.PROBLEM_SOLVING


class TestQuestionStyleFail:
    """QuestionStyle: 실패 케이스 (ValueError 발생)"""

    # 매칭되지 않는 문자열 (5개)
    def test_qs_invalid_unknown(self) -> None:
        """TC-QS-F01: 존재하지 않는 값 'unknown'"""
        with pytest.raises(ValueError):
            QuestionStyle("unknown")

    def test_qs_invalid_description_longer(self) -> None:
        """TC-QS-F02: 존재하지 않는 값 'description_but_longer'"""
        with pytest.raises(ValueError):
            QuestionStyle("description_but_longer")

    def test_qs_invalid_descriptiv_typo(self) -> None:
        """TC-QS-F03: 오타 'descriptiv'"""
        with pytest.raises(ValueError):
            QuestionStyle("descriptiv")

    def test_qs_invalid_past_experiences_plural(self) -> None:
        """TC-QS-F04: 복수형 'past_experiences' (지원 안 함)"""
        with pytest.raises(ValueError):
            QuestionStyle("past_experiences")

    def test_qs_invalid_compare_not_comparison(self) -> None:
        """TC-QS-F05: 'compare'는 COMPARISON이 아님"""
        with pytest.raises(ValueError):
            QuestionStyle("compare")

    # 잘못된 타입 (5개)
    def test_qs_invalid_type_int(self) -> None:
        """TC-QS-F06: 정수 타입"""
        with pytest.raises((ValueError, TypeError)):
            QuestionStyle(123)  # type: ignore

    def test_qs_invalid_type_none(self) -> None:
        """TC-QS-F07: None 타입"""
        with pytest.raises((ValueError, TypeError)):
            QuestionStyle(None)  # type: ignore

    def test_qs_invalid_type_float(self) -> None:
        """TC-QS-F08: 실수 타입"""
        with pytest.raises((ValueError, TypeError)):
            QuestionStyle(3.14)  # type: ignore

    def test_qs_invalid_type_list(self) -> None:
        """TC-QS-F09: 리스트 타입"""
        with pytest.raises((ValueError, TypeError)):
            QuestionStyle([])  # type: ignore

    def test_qs_invalid_type_dict(self) -> None:
        """TC-QS-F10: 딕셔너리 타입"""
        with pytest.raises((ValueError, TypeError)):
            QuestionStyle({})  # type: ignore

    # 빈 값 (3개)
    def test_qs_empty_string(self) -> None:
        """TC-QS-F11: 빈 문자열 ''"""
        with pytest.raises(ValueError):
            QuestionStyle("")

    def test_qs_spaces_only(self) -> None:
        """TC-QS-F12: 공백만 '   '"""
        with pytest.raises(ValueError):
            QuestionStyle("   ")

    def test_qs_whitespace_chars_only(self) -> None:
        """TC-QS-F13: 공백 문자들 '\\t\\n'"""
        with pytest.raises(ValueError):
            QuestionStyle("\t\n")
