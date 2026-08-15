from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.main import app


USER_ID = "44444444-4444-4444-8444-444444444444"
OTHER_USER_ID = "55555555-5555-4555-8555-555555555555"


def _headers(uid: str = USER_ID) -> dict[str, str]:
    return {
        "X-DailyOPIc-User-ID": uid,
        "X-Firebase-AppCheck": "test-app-check-token",
    }


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "studyWeekdays": [1, 2, 3, 4, 5],
        "intensity": "steady",
        "preferredStudyTime": "21:00",
        "timezoneIdentifier": "Asia/Seoul",
    }
    payload.update(overrides)
    return payload


def test_existing_user_without_settings_is_unconfigured_not_an_error() -> None:
    with TestClient(app) as client:
        response = client.get("/v1/users/me/study-plan", headers=_headers())

    assert response.status_code == 200
    assert response.json() == {"configured": False, "studyPlan": None}


def test_put_creates_settings_and_get_reads_them_back() -> None:
    with TestClient(app) as client:
        created = client.put(
            "/v1/users/me/study-plan", json=_payload(), headers=_headers()
        )
        fetched = client.get("/v1/users/me/study-plan", headers=_headers())

    assert created.status_code == 200
    assert fetched.status_code == 200
    assert created.json()["studyPlan"] == fetched.json()["studyPlan"]

    plan = fetched.json()["studyPlan"]
    assert fetched.json()["configured"] is True
    assert plan["schemaVersion"] == 1
    assert plan["studyWeekdays"] == [1, 2, 3, 4, 5]
    assert plan["intensity"] == "steady"
    assert plan["preferredStudyTime"] == "21:00"
    assert plan["timezoneIdentifier"] == "Asia/Seoul"
    assert plan["examDate"] is None
    assert plan["createdAt"] and plan["updatedAt"]


def test_put_replaces_settings_and_keeps_created_at() -> None:
    with TestClient(app) as client:
        first = client.put(
            "/v1/users/me/study-plan", json=_payload(), headers=_headers()
        ).json()["studyPlan"]
        second = client.put(
            "/v1/users/me/study-plan",
            json=_payload(studyWeekdays=[6, 7], intensity="light"),
            headers=_headers(),
        ).json()["studyPlan"]

    assert second["studyWeekdays"] == [6, 7]
    assert second["intensity"] == "light"
    # 설정 교체는 갱신이지 재가입이 아니다.
    assert second["createdAt"] == first["createdAt"]


def test_optional_exam_date_round_trips_and_rejects_past_dates() -> None:
    future = (date.today() + timedelta(days=21)).isoformat()
    past = (date.today() - timedelta(days=1)).isoformat()

    with TestClient(app) as client:
        accepted = client.put(
            "/v1/users/me/study-plan",
            json=_payload(examDate=future),
            headers=_headers(),
        )
        rejected = client.put(
            "/v1/users/me/study-plan",
            json=_payload(examDate=past),
            headers=_headers(),
        )
        cleared = client.put(
            "/v1/users/me/study-plan",
            json=_payload(examDate=None),
            headers=_headers(),
        )

    assert accepted.status_code == 200
    assert accepted.json()["studyPlan"]["examDate"] == future
    assert rejected.status_code == 422
    assert cleared.json()["studyPlan"]["examDate"] is None


def test_todays_exam_date_is_accepted_in_the_submitted_timezone() -> None:
    """UTC 기준으로 재면 UTC보다 늦은 지역 사용자가 방금 고른 '오늘'이 과거가 된다."""
    timezone = "Pacific/Midway"  # UTC-11
    local_today = datetime.now(ZoneInfo(timezone)).date()

    with TestClient(app) as client:
        response = client.put(
            "/v1/users/me/study-plan",
            json=_payload(
                examDate=local_today.isoformat(), timezoneIdentifier=timezone
            ),
            headers=_headers(),
        )

    assert response.status_code == 200
    assert response.json()["studyPlan"]["examDate"] == local_today.isoformat()


def test_invalid_settings_are_rejected() -> None:
    invalid = [
        _payload(studyWeekdays=[]),
        _payload(studyWeekdays=[0, 1]),
        _payload(studyWeekdays=[1, 8]),
        _payload(studyWeekdays=[1, 1, 2]),
        _payload(intensity="extreme"),
        _payload(preferredStudyTime="9pm"),
        _payload(preferredStudyTime="25:00"),
        _payload(preferredStudyTime="21:00:30"),
        _payload(timezoneIdentifier="Mars/Olympus"),
        _payload(targetLevel="IH"),
    ]

    with TestClient(app) as client:
        for payload in invalid:
            response = client.put(
                "/v1/users/me/study-plan", json=payload, headers=_headers()
            )
            assert response.status_code == 422, payload


def test_weekdays_are_normalized_to_sorted_order() -> None:
    with TestClient(app) as client:
        response = client.put(
            "/v1/users/me/study-plan",
            json=_payload(studyWeekdays=[7, 3, 1]),
            headers=_headers(),
        )

    assert response.json()["studyPlan"]["studyWeekdays"] == [1, 3, 7]


def test_settings_are_isolated_per_authenticated_user() -> None:
    with TestClient(app) as client:
        client.put("/v1/users/me/study-plan", json=_payload(), headers=_headers())
        other = client.get("/v1/users/me/study-plan", headers=_headers(OTHER_USER_ID))

    assert other.json() == {"configured": False, "studyPlan": None}


def test_study_plan_does_not_carry_a_second_target_level() -> None:
    """목표 등급의 진실은 userProfiles.targetLevel 하나뿐이다."""
    with TestClient(app) as client:
        plan = client.put(
            "/v1/users/me/study-plan", json=_payload(), headers=_headers()
        ).json()["studyPlan"]

    assert "targetLevel" not in plan


def test_saving_a_study_plan_does_not_change_quota_or_capabilities() -> None:
    with TestClient(app) as client:
        before = client.get("/v1/capabilities", headers=_headers()).json()
        client.put("/v1/users/me/study-plan", json=_payload(), headers=_headers())
        after = client.get("/v1/capabilities", headers=_headers()).json()
        usage = client.get("/v1/usage", headers=_headers())

    assert before == after
    assert usage.status_code == 200


def test_changing_target_level_preserves_the_saved_study_plan() -> None:
    with TestClient(app) as client:
        client.put("/v1/users/me/study-plan", json=_payload(), headers=_headers())
        level = client.put(
            "/v1/users/me/target-level",
            json={"initialLevel": 5},
            headers=_headers(),
        )
        plan = client.get("/v1/users/me/study-plan", headers=_headers())

    assert level.status_code == 200
    assert plan.json()["configured"] is True
    assert plan.json()["studyPlan"]["studyWeekdays"] == [1, 2, 3, 4, 5]


def test_study_plan_requires_authentication() -> None:
    """다른 엔드포인트와 같은 App Check 게이트를 그대로 탄다(403)."""
    with TestClient(app) as client:
        read = client.get("/v1/users/me/study-plan")
        write = client.put("/v1/users/me/study-plan", json=_payload())

    assert read.status_code == 403
    assert write.status_code == 403
