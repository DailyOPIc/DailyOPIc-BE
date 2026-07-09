# RDB화 리팩토링 — 진행 상황 정리 (핸드오프용)

이 채팅에서 진행한 내용 전체 요약. 새 채팅에서 이어갈 때 이 문서 하나로 컨텍스트 복구 가능하도록 작성.

**중요: 지금까지 `app/` 코드는 단 한 줄도 수정 안 했음.** 전부 분석 + `docs/` 문서 작업만 진행함.

## 목표 / 전제

- Firestore 연동 자체는 그대로 유지 (실제 RDB 엔진으로 이전 안 함)
- "RDB처럼 사용"의 실질적 범위는 3단계: **① 관계 식별 → ② 도메인 제약 추가(Pydantic 스키마 계층) → ③ 필드명/구조 정리**
- API 응답 계약(camelCase, 필드명)은 최대한 현재 상태 유지 — iOS 앱 호환성 때문
- 불필요 필드 제거는 "먼저 사용 현황 분석 리포트 → 승인 → 반영" 순서로 진행

## 생성된 문서 (전부 `docs/`에 있음)

| 파일 | 내용 |
|---|---|
| `docs/firestore-field-audit.md` | 6개 컬렉션 전체 필드의 코드상 사용 여부 전수 조사 (1단계 결과물) |
| `docs/schema.md` | 컬렉션별 필드 한글 설명 + `삭제 예정`/`⚠️` 컬럼으로 결정 사항 추적 |
| `docs/relationSchema.md` | 컬렉션 간 참조 관계(FK 유사) 정리, 강제 방식(코드 검증 여부) 명시 |

이 세 문서가 지금까지의 모든 결정 사항의 근거/기록임.

## 확정된 결정 (schema.md에 반영 완료)

- `dailyUsage.dateKey` → `date`로 이름 변경 (읽히지 않는 필드였어서 API 영향 없음)
- `userProfiles.expectedTargetLevel` 제거 (targetLevel과 항상 같은 값, 응답은 항상 재계산해서 만듦)
- `userProfiles.effectiveLevel` 제거 (저장값을 읽는 코드가 없음, 항상 initialLevel+latestAdjustment로 재계산)
- `userProfiles.effectiveLevelCode` 제거 → `beforeAdjust`/`afterAdjust` 2개 필드로 분리
- `questionSets.expectedTargetLevel` 제거 (targetLevel과 통합)
- `questionSets.effectiveLevelCode` 제거 (응답 생성 시 항상 재계산)
- `questionSets.frontQuestionCount` 제거 (write-only)
- `questionSets.poolIndex` 제거 (write-only)
- `mode` 필드 값을 `practice`/`mock` → `daily`/`mock`으로 통일 예정 (ai.py 내부에서 이미 daily/practice/mock 3개를 섞어 쓰고 있어서 이것도 같이 정리 필요)
- `type`/`questionType` 이름이 겹치는 문제 발견 (`roleplay`, `comparison` 값이 두 enum에 중복). 리네이밍 후보: `type`→`examSection`, `questionType`→`questionStyle` (미확정)

## 미확정 / 다음 채팅에서 결정 필요한 것

1. **`userProfiles.latestAdjustment` 삭제 여부 — 아직 최종 답 안 함.**
   - 경계값(레벨 1/6)에서 easier/same, harder/same이 beforeAdjust/afterAdjust 숫자만으론 구분 안 되는 문제 있음
   - 근데 확인해보니 이 필드는 AI 채점(evaluate_practice/evaluate_mock)이나 문제 생성 어디에도 인자로 안 들어감 — `ai.py`에 아예 등장 안 함. `TargetLevelResponse.latestAdjustment` 응답 표시용으로만 쓰임
   - 즉 지워도 리스크는 "점수 오류"가 아니라 "응답 필드 표시 오류(경계값에서만)" 수준 → 지워도 될 것 같다는 데까지 얘기하다가 새 채팅으로 넘어감. **여기서부터 다시 결정하면 됨**

2. **`beforeAdjust`가 `initialLevel`과 값이 완전히 중복될 수 있음** — 이름만 바꾸는 건지 별개로 둘 다 유지할 건지 미확정

3. **`adRewardIntents` 컬렉션 구조 문제 (가장 큰 구조적 이슈)**
   - 같은 컬렉션에 일반 리워드 문서(nonce, uid, purpose...)와 replay 방지 문서(`_tx_{hash}`, kind/transactionId/expiresAt만 있음)가 섞여 있음
   - RDB 관점에서는 스키마가 다른 두 종류의 row가 한 테이블에 있는 셈 → 컬렉션을 물리적으로 나눌지 결정 필요 (`adRewardIntents` / `adRewardTransactions`)

4. **`aiRequests.result`가 polymorphic** — Practice 평가 결과랑 Mock 평가 결과 모양이 다른데 구분 필드(discriminator)가 없음. 나중에 엄격한 스키마 씌우려면 `resultType` 같은 필드 추가 검토 필요

5. **도메인 제약 추가 (2단계, 아직 시작 안 함)** — 각 필드의 enum/범위/nullable 여부를 Pydantic 모델로 강제하는 작업. `relationSchema.md`로 관계까지 정리했으니 이제 이 단계로 넘어갈 수 있음

6. **필드명 리네이밍 후보들 실행** — type/questionType, mode 값 통일 등, 문서에는 적어뒀지만 실제 코드 변경은 전혀 안 한 상태

## 지우면 안 되는 것 (재확인 완료, 건드리지 말 것)

- `questionSets.initialLevel/effectiveLevel/targetLevel(+expectedTargetLevel 통합본)` — userProfiles와 값이 겹쳐 보여도 "생성 시점 스냅샷"이라 의도적 중복임. 라이브 조인으로 바꾸면 안 됨 (사용자가 프로필을 바꾼 뒤 예전 세트를 평가하면 채점 기준이 틀어지는 버그 생김)
- `adRewardIntents.credited` (중복 지급 방지), `sessionHash`(다른 세션 리워드 도용 방지), `consumed`(재사용 방지) — 금전/어뷰징 방지 핵심 로직
- `adRewardIntents.consumedAt/consumedFor/transactionId` — 코드상 write-only라 기술적으로는 삭제 후보지만, 광고 보상 관련 감사 로그라 고객 문의 대응용으로 남겨두길 권장
- `expiresAt`류 필드 전체 — Firestore TTL 정책이 이 필드명을 기준으로 걸려 있을 가능성 높음 (이 repo엔 TTL 설정 파일이 없어서 GCP 콘솔/gcloud로 별도 설정돼 있을 것). 이름 바꾸기 전에 실제 TTL 정책 확인 필요

## 실제 RDB(SQLite) 도입 — 구현 완료 (이번 세션)

앞선 분석/문서 단계 이후, "Firestore CRUD 구조 유지 + RDB 모델·관계 매핑 + SQLite 사용"을 실제 코드로 구현함. `app/` 코드 수정 시작.

- `app/models/db.py` (신규): `domain-model.dbml`의 6개 테이블을 SQLAlchemy 2.0 declarative 모델로 정의. 컬럼은 snake_case, timestamp는 tz-aware UTC를 보장하는 `UtcDateTime` 커스텀 타입 사용. `adRewardIntents`의 `_tx_` replay 문서는 별도 테이블 `ad_reward_transactions`로 분리(핸드오프에서 지적한 구조적 이슈 해소). FK 컬럼은 스키마 문서화용으로 선언하되 SQLite FK 강제(PRAGMA)는 켜지 않음 — usage/request가 프로필보다 먼저 생기는 기존 흐름과 Firestore 무결성 의미론을 유지.
- `app/services/sql_store.py` (신규): `SqlAlchemyStateStore(StateStore)`. 추상 메서드 전부 구현하며 반환 계약(camelCase dict)을 `FirestoreStateStore`와 동일하게 유지. Firestore 트랜잭션의 원자적 read-modify-write는 프로세스 내 전역 락 + 세션 트랜잭션으로 대체. `state.py`의 순수 헬퍼(`_profile_from_value`, `_merge_question_history`, `_target_change_response` 등)를 재사용해 도메인 로직 중복 없음.
- `app/config.py`: `state_backend`(기본값 `sqlite`), `sqlite_url` 설정 추가. 값은 `sqlite`/`firestore`/`memory`.
- `app/main.py`: `build_state_store(settings)` 팩토리로 백엔드 선택. **기본 백엔드가 SQLite로 전환됨**(Firestore 코드는 그대로 남겨 `STATE_BACKEND=firestore`로 선택 가능).
- `pyproject.toml`: `sqlalchemy>=2.0,<3` 의존성 추가.
- 테스트: `tests/test_sql_store.py`(신규, 11 케이스 — 소유권/만료/히스토리 트림/쿼터/멱등성/리워드 단일사용/replay 방지/병렬 예약 한도). `tests/conftest.py`는 `STATE_BACKEND=memory`로 고정해 기존 API 테스트가 InMemory로 계속 동작. 전체 `pytest` 63 passed / 4 skipped(에뮬레이터 전용).

미착수(그대로 남음): 필드명 리네이밍(type/questionType, mode 값 통일), 도메인 제약(enum/범위) Pydantic 강제, `latestAdjustment` 삭제 여부 최종 결정. 이번 작업은 API 응답 계약을 바꾸지 않았음.

## 시각 자료

- Chen ER 표기법으로 컬렉션 간 관계도(개체/약한개체/관계/약한관계/카디널리티)를 그려서 보여줬음 (채팅 내 위젯, 파일로 저장되진 않음— 필요하면 새 채팅에서 relationSchema.md 보고 다시 그려달라고 하면 됨)
- 다음으로 제안했던 것: 키 속성(타원)까지 얹은 확장판 다이어그램 — 아직 안 만듦
