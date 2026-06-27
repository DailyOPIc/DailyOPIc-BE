# DailyOPIc-BE 협업 컨벤션

이 문서는 DailyOPIc 백엔드 레포지토리의 코드 작성, 브랜치 운영, 작업 방식, 리뷰 기준, 테스트, 배포, 문서화 규칙을 정리한 협업 기준입니다.

목표는 단순합니다.

- 다른 사람이 코드를 봐도 기능 위치와 흐름을 빠르게 파악할 수 있어야 합니다.
- AI, 광고 보상, 사용량 제한처럼 장애 비용이 큰 기능은 오류 검출과 롤백 흐름이 명확해야 합니다.
- iOS 앱과 연결되는 API 계약은 임의로 깨지지 않아야 합니다.
- 운영 비밀값, 사용자 음성/전사 데이터, 광고 보상 데이터는 안전하게 다뤄야 합니다.

---

## 1. 프로젝트 원칙

### 1.1 백엔드의 책임

DailyOPIc-BE는 다음만 담당합니다.

- AI 문제 생성
- AI 답변 평가
- Practice 일일 무료 사용량 제한
- Rewarded Ad SSV 검증
- Mock Exam 종합 평가 권한 검증
- Firebase ID Token/App Check 검증
- 중복 요청 방지

백엔드가 담당하지 않는 것:

- 사용자 학습 기록 저장
- 모의고사 답변 기록 영구 저장
- 목표 등급 저장
- Background Survey 저장
- 녹음 파일 영구 저장

위 데이터는 iOS SwiftData/UserDefaults에 저장합니다.

### 1.2 Firestore 사용 범위

Firestore는 아래 컬렉션만 사용합니다.

```text
dailyUsage
adRewardIntents
aiRequests
```

새 컬렉션을 추가하려면 반드시 이 문서와 README에 이유를 남기고 PR에서 승인받아야 합니다.

### 1.3 AI 사용 원칙

- OpenAI API Key는 iOS에 절대 포함하지 않습니다.
- OpenAI 호출은 백엔드에서만 수행합니다.
- 모든 OpenAI 요청은 `store: false`를 사용합니다.
- 오디오 원본은 OpenAI로 보내지 않습니다.
- 백엔드는 FFmpeg로 발화 시간, 무음 비율, WPM 같은 audio metrics만 추출합니다.
- AI 응답은 Structured Output/Pydantic 모델로 검증합니다.
- AI 모델명과 prompt/schema 버전은 결과 추적이 가능하게 기록합니다.

---

## 2. 기본 개발 환경

### 2.1 요구 버전

```text
Python >= 3.12
FastAPI
Pydantic v2
Firebase Admin SDK
OpenAI Python SDK
FFmpeg
```

### 2.2 로컬 실행

```bash
cd DailyOPIc-BE
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env
uvicorn app.main:app --reload
```

### 2.3 로컬 테스트

```bash
cd DailyOPIc-BE
pytest
```

Firestore Emulator 테스트:

```bash
firebase emulators:exec --only firestore \
  'GCLOUD_PROJECT=dailyopic-test pytest tests/test_firestore_emulator.py'
```

### 2.4 로컬 기본 설정

로컬 개발 기본값은 다음 방향을 사용합니다.

```text
AUTH_DISABLED=true
APP_CHECK_REQUIRED=false
FIRESTORE_ENABLED=false
MOCK_AI=true
DEBUG_REWARD_AUTO_VERIFY=true
```

이 설정은 개발 편의를 위한 값입니다. 운영에서는 절대 사용하면 안 됩니다.

---

## 3. 브랜치 컨벤션

### 3.1 기본 전략

기본 전략은 `main` 보호 + 기능 브랜치 방식입니다.

```text
main
 ├── feat/be-101-practice-quota
 ├── fix/be-102-ssv-replay
 ├── refactor/be-103-ai-service-split
 └── docs/be-104-api-contract
```

`main`에는 직접 push하지 않습니다.

### 3.2 브랜치 이름 규칙

```text
<type>/be-<issue-number>-<short-description>
```

사용 가능한 type:

```text
feat      신규 기능
fix       버그 수정
refactor  동작 변경 없는 구조 개선
test      테스트 추가/수정
docs      문서 추가/수정
chore     설정, 의존성, 빌드 작업
hotfix    운영 긴급 수정
```

예시:

```text
feat/be-120-mock-evaluation
fix/be-121-reward-transaction-replay
refactor/be-122-split-ai-service
test/be-123-firestore-concurrency
docs/be-124-readme-api-contract
chore/be-125-add-ruff
```

### 3.3 브랜치 크기 기준

하나의 브랜치는 하나의 목적만 가져야 합니다.

좋은 예:

```text
refactor/be-201-split-state-store
```

나쁜 예:

```text
feat/be-201-ai-refactor-ssv-fix-readme-tests
```

하나의 PR에서 구조 변경, 기능 변경, 테스트 변경, 문서 변경이 모두 필요할 수는 있지만, 목적은 하나여야 합니다.

---

## 4. 커밋 컨벤션

### 4.1 커밋 메시지 형식

Conventional Commits 스타일을 사용합니다.

```text
<type>(<scope>): <summary>
```

예시:

```text
feat(practice): add daily quota reservation
fix(admob): reject replayed ssv transaction
refactor(ai): split prompt builders from openai client
test(mock): cover reward nonce single-use flow
docs(readme): document cloud run env variables
chore(deps): update firebase admin sdk
```

### 4.2 type 목록

```text
feat      사용자 관점 기능 추가
fix       버그 수정
refactor  동작 변경 없는 구조 개선
test      테스트 추가/수정
docs      문서 수정
chore     빌드/설정/의존성
perf      성능 개선
ci        CI 설정
revert    이전 커밋 되돌리기
```

### 4.3 커밋 작성 기준

- 한 커밋은 하나의 논리적 변경만 포함합니다.
- 포맷팅만 바꾸는 커밋과 로직 변경 커밋을 섞지 않습니다.
- secret, `.env`, 인증 키, Firebase service account 파일은 커밋하지 않습니다.
- 테스트가 깨지는 중간 커밋은 PR 최종 상태에서 남기지 않습니다.

---

## 5. ISSUE 작성 컨벤션

### 5.1 ISSUE 제목

```text
[BE <Type>] <작업 요약>
```

예시:

```text
[BE Refactor] AI service를 generation/evaluation/client로 분리
[BE Fix] SSV transaction replay 차단
[BE Test] Firestore 동시성 테스트 보강
[BE Docs] API 오류 코드 문서화
```

### 5.2 ISSUE 본문 템플릿

```md
## 목적

이 작업이 필요한 이유를 적는다.

## 현재 문제

- 현재 코드에서 읽기 어렵거나 오류 가능성이 있는 지점
- 장애가 발생할 수 있는 조건
- iOS/API 계약에 미치는 영향

## 작업 범위

- 수정할 파일/모듈
- 추가할 파일/모듈
- 변경할 API 또는 내부 함수

## 제외 범위

- 이번 작업에서 하지 않을 일
- 별도 ISSUE로 분리할 일

## 완료 기준

- 코드 구조 기준
- 테스트 기준
- 문서 기준

## 검증 방법

```bash
pytest
```

## 위험도

- 낮음/중간/높음
- 배포 시 주의사항
```

### 5.3 좋은 ISSUE 기준

- “무엇을 바꿀지”보다 “왜 바꾸는지”가 먼저 설명되어야 합니다.
- 완료 기준이 체크리스트로 검증 가능해야 합니다.
- iOS API 계약 변경 여부가 명확해야 합니다.
- 운영 영향이 있으면 rollback 방법이 있어야 합니다.

---

## 6. PR 컨벤션

### 6.1 PR 제목

```text
[BE] <type>(<scope>): <summary>
```

예시:

```text
[BE] refactor(ai): split OpenAI client and prompt builders
[BE] fix(admob): prevent replayed rewarded transactions
[BE] docs(readme): add API and deployment contract
```

### 6.2 PR 본문 템플릿

```md
## 작업 요약

- 변경 사항 1
- 변경 사항 2

## 변경 이유

- 기존 문제
- 기대 효과

## 주요 변경 파일

- `app/...`
- `tests/...`

## API 계약 변경

- [ ] 없음
- [ ] 있음

변경 내용:

## DB/Firestore 변경

- [ ] 없음
- [ ] 있음

변경 내용:

## 환경변수 변경

- [ ] 없음
- [ ] 있음

변경 내용:

## 테스트

```bash
pytest
```

결과:

## 리뷰 포인트

- 집중해서 봐야 할 부분
- 의도적으로 선택한 trade-off

## 배포 주의사항

- Secret Manager 변경 여부
- Firebase 설정 변경 여부
- AdMob 설정 변경 여부
```

### 6.3 PR 크기 기준

권장 기준:

- 변경 파일 10개 이하
- 순수 로직 변경 400줄 이하
- 리뷰 가능한 단일 목적

큰 리팩토링은 아래 순서로 쪼갭니다.

```text
1. 파일 이동/구조 분리
2. 내부 import 정리
3. 공통 에러/모델 도입
4. 기능별 로직 이동
5. 테스트 보강
6. 문서 업데이트
```

---

## 7. 코드 구조 컨벤션

### 7.1 권장 디렉토리 구조

현재 구조를 점진적으로 아래 방향으로 정리합니다.

```text
app/
├── api/
│   ├── router.py
│   ├── health.py
│   ├── practice.py
│   ├── mock_exam.py
│   ├── usage.py
│   └── ad_rewards.py
├── core/
│   ├── config.py
│   ├── errors.py
│   └── logging.py
├── models/
│   ├── common.py
│   ├── questions.py
│   ├── evaluations.py
│   ├── usage.py
│   └── rewards.py
├── services/
│   ├── ai/
│   ├── auth/
│   ├── audio/
│   ├── state/
│   ├── admob/
│   └── tokens.py
└── usecases/
    ├── create_practice_set.py
    ├── create_mock_exam.py
    ├── evaluate_practice.py
    ├── evaluate_mock.py
    ├── create_reward_intent.py
    ├── verify_admob_reward.py
    └── get_usage.py
```

### 7.2 계층별 책임

#### `api/`

HTTP 입출력만 담당합니다.

허용:

- request body/form/header 수신
- response model 반환
- FastAPI dependency 연결
- usecase 호출

금지:

- AI prompt 작성
- Firestore transaction 직접 작성
- quota 계산
- reward 상태 변경
- 복잡한 비즈니스 로직

#### `usecases/`

하나의 사용자 행동을 처리합니다.

예시:

```text
evaluate_practice
evaluate_mock
create_reward_intent
verify_admob_reward
```

usecase는 다음 흐름이 코드에서 읽혀야 합니다.

```text
1. 입력 검증
2. token/session 검증
3. quota 또는 reward 예약
4. audio metrics 생성
5. AI 호출
6. 결과 finalize
7. 실패 시 rollback
```

#### `services/`

외부 시스템 또는 도메인 기능을 담당합니다.

예시:

- OpenAI 호출
- Firebase Auth 검증
- App Check 검증
- Firestore transaction
- AdMob SSV 검증
- FFmpeg audio 분석

`services/`는 FastAPI의 `HTTPException`을 직접 만들지 않습니다.

#### `models/`

Pydantic 모델, enum, 내부 DTO를 둡니다.

원칙:

- API request/response 모델과 내부 도메인 타입을 구분합니다.
- iOS와 계약되는 field name은 alias를 명확히 관리합니다.
- validation rule은 가능한 한 모델 단계에서 처리합니다.

#### `core/`

전역 설정, logging, 공통 error, middleware를 둡니다.

---

## 8. Python 코드 컨벤션

### 8.1 기본 스타일

- Python 3.12 기준으로 작성합니다.
- 모든 public 함수에는 type hint를 작성합니다.
- `Any` 사용은 외부 JSON, Firestore raw document, OpenAI raw response처럼 불가피한 지점으로 제한합니다.
- 함수는 하나의 책임만 갖도록 작성합니다.
- 함수가 길어지면 validation, transform, persistence, external call 단위로 분리합니다.

### 8.2 네이밍

```text
파일명: snake_case.py
함수명: snake_case
변수명: snake_case
클래스명: PascalCase
상수명: UPPER_SNAKE_CASE
```

좋은 예:

```python
async def reserve_practice_credit(...) -> Reservation:
    ...
```

나쁜 예:

```python
async def do_eval(...):
    ...
```

### 8.3 import 순서

```python
from __future__ import annotations

import ...
from datetime import ...

import third_party

from app... import ...
```

상대 import보다 절대 import를 우선합니다.

### 8.4 예외 처리

금지:

```python
except Exception:
    pass
```

권장:

```python
try:
    ...
except AudioProcessingError:
    await state_store.rollback(...)
    raise
```

원칙:

- 예외를 삼키지 않습니다.
- rollback이 필요한 경우 반드시 명시합니다.
- 사용자에게 보여줄 오류와 내부 로그용 오류를 분리합니다.
- route 바깥 service 계층에서 `HTTPException`을 만들지 않습니다.

### 8.5 async 컨벤션

- FastAPI endpoint와 외부 I/O는 async를 사용합니다.
- blocking SDK는 `asyncio.to_thread`로 감쌉니다.
- Firestore transaction처럼 동기 client를 사용하는 경우 service 내부에서 격리합니다.
- CPU/IO가 섞인 함수는 이름으로 역할을 명확히 합니다.

---

## 9. API 설계 컨벤션

### 9.1 API versioning

모든 앱용 API는 `/v1` prefix를 사용합니다.

```text
POST /v1/question-sets/practice
POST /v1/mock-exams
GET  /v1/usage
POST /v1/evaluations/practice
POST /v1/evaluations/mock
POST /v1/ad-rewards/intents
GET  /v1/ad-rewards/{nonce}
GET  /v1/admob/ssv
```

### 9.2 API 계약 변경 원칙

아래 변경은 breaking change입니다.

- response field 삭제
- response field 타입 변경
- enum value 변경
- error code 변경
- status code 변경
- request 필수 field 추가

breaking change가 필요하면 iOS 작업과 같은 ISSUE/PR 체인으로 관리합니다.

### 9.3 오류 응답 형식

오류 응답은 최종적으로 아래 형태로 통일합니다.

```json
{
  "code": "practice_quota_exhausted",
  "message": "Daily practice quota exhausted.",
  "requestId": "req_..."
}
```

iOS는 `code` 기준으로 분기합니다. 따라서 error code 문자열은 임의 변경하지 않습니다.

### 9.4 주요 오류 코드

```text
invalid_idempotency_key
invalid_questions
invalid_question_number
invalid_set
practice_quota_exhausted
mock_reward_required
request_processing
invalid_audio
reward_not_found
invalid_reward
auth_required
app_check_required
ai_schema_error
ai_provider_error
internal_error
```

---

## 10. AI 기능 컨벤션

### 10.1 AI 기능 목록

백엔드 AI 기능은 네 가지로 구분합니다.

```text
1. Practice question generation
2. Mock exam 15-question generation
3. Practice answer evaluation
4. Mock exam batch evaluation
```

각 기능은 prompt, input schema, output schema, fallback 정책을 별도로 관리합니다.

### 10.2 Prompt 작성 규칙

- prompt는 route/usecase 파일에 inline으로 작성하지 않습니다.
- prompt builder 파일에서 관리합니다.
- prompt에는 역할, 입력, 출력 규칙, 금지사항을 명확히 작성합니다.
- OPIc 공식 결과가 아니라는 고지를 평가 결과에 포함합니다.
- 목표 등급은 샘플 답변과 개선 방향에만 반영합니다.
- 동일 답변의 예상 등급이 목표 등급 때문에 임의로 바뀌면 안 됩니다.

### 10.3 Structured Output 규칙

AI 응답은 반드시 Pydantic 모델로 검증합니다.

검증해야 할 항목:

- `predictedLevel` enum
- confidence range
- score range
- strengths
- priority improvements
- corrected answer
- target-level sample answer
- disclaimer
- Mock Exam의 경우 `perQuestion` 15개

검증 실패 시:

- 요청을 completed로 저장하지 않습니다.
- 사용량/보상 예약을 rollback합니다.
- 클라이언트에는 안정적인 오류 코드를 반환합니다.

### 10.4 Fallback 규칙

Fallback은 다음 경우에만 사용합니다.

- AI 문제 생성 결과가 15문항 blueprint를 만족하지 못함
- AI 문제 생성 JSON이 schema 검증에 실패함
- 개발 환경에서 `MOCK_AI=true`

평가 결과 fallback은 운영에서 기본 허용하지 않습니다. 운영 평가 실패는 실패로 처리하고 크레딧을 반환합니다.

---

## 11. 사용량/Idempotency 컨벤션

### 11.1 Practice quota

기본 정책:

```text
KST 기준 하루 무료 평가 3회
Rewarded Ad 1회 검증 시 Practice 평가 3회 추가
```

### 11.2 차감 원칙

- 평가 요청 시작 시 credit을 예약합니다.
- 평가 성공 시 예약을 확정합니다.
- 평가 실패 시 예약을 반환합니다.
- AI 실패, audio 실패, schema 실패, network 실패 모두 credit을 소비하지 않아야 합니다.

### 11.3 Idempotency-Key

모든 평가 요청은 `Idempotency-Key`를 요구합니다.

원칙:

- 같은 사용자 + 같은 key + completed 결과는 캐시 반환
- 같은 사용자 + 같은 key + processing 상태는 `request_processing`
- 다른 사용자가 같은 key를 쓰면 거부
- key는 8~128자 제한

### 11.4 날짜 기준

사용량은 KST 기준입니다.

```text
Asia/Seoul
YYYYMMDD
```

UTC 날짜 기준으로 구현하면 안 됩니다.

---

## 12. AdMob Rewarded SSV 컨벤션

### 12.1 상태 전이

```text
pending -> verified -> consumed
pending -> expired
```

Practice credit reward:

```text
pending -> verified
verified 시 bonusRemaining += REWARD_PRACTICE_CREDITS (기본값 1)
```

Mock result reward:

```text
pending -> verified
Mock evaluation 시작 시 consumed=true
평가 실패 시 consumed=false로 rollback
```

### 12.2 검증 필수 항목

SSV callback에서 반드시 확인합니다.

- Google signature
- key_id
- transaction_id
- custom_data nonce
- user_id
- ad_unit
- nonce 존재 여부
- nonce 만료 여부
- reward purpose
- transaction replay 여부

### 12.3 금지 사항

- iOS에서 광고 시청 완료만 보고 credit 지급 금지
- SSV 없이 운영 보상 지급 금지
- 동일 transaction_id 재사용 허용 금지
- Mock reward nonce 재사용 허용 금지

---

## 13. Firestore 컨벤션

### 13.1 collection 역할

#### `dailyUsage`

KST 날짜별 무료/보너스 사용량.

#### `adRewardIntents`

광고 reward nonce, SSV transaction, reward 상태.

#### `aiRequests`

idempotency, processing/completed/failed 상태, 24시간 임시 결과 캐시.

### 13.2 transaction 원칙

- read는 write보다 먼저 수행합니다.
- quota 차감과 request 생성은 같은 transaction에서 처리합니다.
- reward consume과 request 생성은 같은 transaction에서 처리합니다.
- transaction 내부 로직은 가능한 짧게 유지합니다.
- 외부 API 호출은 Firestore transaction 안에서 하지 않습니다.

### 13.3 TTL

운영 Firestore에는 TTL을 설정합니다.

```text
aiRequests.expiresAt
adRewardIntents.expiresAt
```

transaction replay sentinel에도 TTL을 둡니다.

---

## 14. 보안/개인정보 컨벤션

### 14.1 secret 관리

커밋 금지:

```text
.env
service-account.json
GoogleService-Info.plist
OPENAI_API_KEY
TOKEN_SIGNING_SECRET
Firebase private key
AdMob secret
```

운영 secret은 Secret Manager에서 관리합니다.

### 14.2 로그 금지 데이터

로그에 남기면 안 되는 값:

- OpenAI API Key
- Firebase ID Token
- App Check Token
- 녹음 파일 원본
- 전사 원문 전체
- 사용자의 민감한 자유 답변
- AdMob signature 원문 전체

허용 가능한 값:

- request id
- uid hash
- idempotency key hash
- endpoint
- error code
- model version
- latency

### 14.3 사용자 데이터 저장 원칙

- 학습 기록은 서버에 저장하지 않습니다.
- 녹음 파일은 서버 요청 처리 중 임시로만 사용합니다.
- AI 평가 결과는 idempotency 캐시 목적으로만 24시간 저장합니다.
- 장기 분석용 사용자 답변 저장은 별도 정책/동의/ISSUE 없이 추가하지 않습니다.

---

## 15. 테스트 컨벤션

### 15.1 테스트 종류

```text
unit test       순수 함수/서비스 단위
api test        FastAPI endpoint 계약
state test      quota/reward/idempotency 상태 전이
emulator test   Firestore transaction 동시성
fixture test    calibration data 품질
contract test   iOS와 맞물리는 response/error 계약
```

### 15.2 테스트 파일 이름

```text
tests/test_<domain>.py
```

예시:

```text
tests/test_api.py
tests/test_state.py
tests/test_question_structure.py
tests/test_firestore_emulator.py
tests/test_ai.py
tests/test_config.py
```

### 15.3 테스트 함수 이름

테스트 이름은 기대 동작을 설명해야 합니다.

좋은 예:

```python
async def test_three_free_practice_uses_then_quota_error() -> None:
    ...
```

나쁜 예:

```python
async def test_usage() -> None:
    ...
```

### 15.4 테스트 작성 원칙

- 성공 케이스보다 실패 케이스를 더 중요하게 봅니다.
- quota, reward, idempotency는 동시성 테스트가 필요합니다.
- AI 응답은 fixture/mock으로 고정 가능한 테스트를 먼저 작성합니다.
- 실제 OpenAI 호출 테스트는 기본 CI에 넣지 않습니다.
- Firestore Emulator 테스트는 별도 job으로 분리할 수 있습니다.

### 15.5 PR 필수 검증

최소:

```bash
pytest
```

Firestore 관련 변경 시:

```bash
firebase emulators:exec --only firestore \
  'GCLOUD_PROJECT=dailyopic-test pytest tests/test_firestore_emulator.py'
```

Docker/배포 관련 변경 시:

```bash
docker build -f Dockerfile -t dailyopic-api .
```

단, 현재 Dockerfile은 workspace root에서 빌드하는 구성을 사용할 수 있으므로 README의 최신 명령을 우선합니다.

---

## 16. 코드 품질 도구 컨벤션

현재 필수 검증은 `pytest`입니다.

추후 도입 권장:

```bash
ruff check .
ruff format .
mypy app
```

도입 후에는 PR 필수 검증을 아래로 고정합니다.

```bash
ruff check .
ruff format --check .
pytest
```

도구 도입 전에는 다음을 수동으로 지킵니다.

- unused import 제거
- 죽은 코드 제거
- 임시 print 제거
- broad exception 최소화
- 타입 힌트 작성
- route 함수 비대화 방지

---

## 17. 문서화 컨벤션

### 17.1 README에 반드시 있어야 하는 내용

```text
1. 프로젝트 목적
2. 로컬 실행 방법
3. 테스트 방법
4. 환경변수 설명
5. API 목록
6. Firestore 컬렉션 설명
7. AdMob SSV 설정
8. OpenAI 사용 방식
9. Cloud Run 배포 방법
10. 운영 체크리스트
```

### 17.2 API 문서

API 계약 변경 시 아래 문서를 함께 수정합니다.

```text
README.md
docs/api-contract.md
```

문서에는 endpoint별로 아래를 적습니다.

```text
method/path
목적
인증 필요 여부
request body/form
response
error code
iOS 처리 방식
```

### 17.3 AI 문서

AI prompt/schema/model 변경 시 아래 문서를 함께 수정합니다.

```text
docs/ai-contract.md
```

문서에는 아래를 적습니다.

```text
AI 기능명
입력 데이터
출력 schema
fallback 정책
모델명
prompt/schema version
주의사항
```

---

## 18. 설정/환경변수 컨벤션

### 18.1 `.env.example`

새 환경변수를 추가하면 반드시 `.env.example`도 수정합니다.

각 환경변수는 다음을 설명해야 합니다.

```text
무엇을 위한 값인지
development 기본값
production 필수 여부
Secret Manager 사용 여부
```

### 18.2 운영 필수 설정

운영에서는 아래 조건을 만족해야 합니다.

```text
ENVIRONMENT=production
AUTH_DISABLED=false
APP_CHECK_REQUIRED=true
FIRESTORE_ENABLED=true
MOCK_AI=false
OPENAI_API_KEY=<Secret Manager>
TOKEN_SIGNING_SECRET=<Secret Manager>
ADMOB_SSV_REQUIRED=true
ADMOB_REWARDED_AD_UNIT_ID=<production unit id>
DEBUG_REWARD_AUTO_VERIFY=false
```

서버는 위험한 운영 설정이면 시작 단계에서 실패해야 합니다.

---

## 19. 배포 컨벤션

### 19.1 배포 대상

Cloud Run에 배포합니다.

권장 설정:

```text
request-based billing
minimum instances = 0
timeout = 300s
conservative max instances
Secret Manager for secrets
```

### 19.2 배포 전 체크리스트

- [ ] `pytest` 통과
- [ ] Firestore Emulator 동시성 테스트 통과
- [ ] Docker build 성공
- [ ] production env 검증 통과
- [ ] Secret Manager 값 연결 확인
- [ ] Firestore TTL 설정 확인
- [ ] AdMob SSV callback URL 확인
- [ ] App Check required 확인
- [ ] OpenAI `store: false` 확인
- [ ] iOS Debug/Release API base URL 확인

### 19.3 배포 후 smoke test

```bash
curl https://<cloud-run-host>/health
```

확인 항목:

- status ok
- production environment
- mockAI false

---

## 20. 리뷰 기준

### 20.1 리뷰어가 봐야 할 것

- API 계약이 깨지지 않았는가
- quota/reward rollback이 정확한가
- idempotency가 추가 차감을 막는가
- Firestore transaction이 race condition에 안전한가
- AI schema 검증 실패 시 안전하게 실패하는가
- secret/user data가 로그에 남지 않는가
- route가 비대해지지 않았는가
- 테스트가 실패 케이스를 포함하는가
- README/API 문서가 변경과 함께 수정되었는가

### 20.2 리뷰 코멘트 우선순위

```text
P0 운영 장애/보안/데이터 손상 가능성
P1 API 계약 깨짐, quota/reward 오류 가능성
P2 유지보수성/가독성/테스트 부족
P3 네이밍/스타일/문서 개선
```

### 20.3 승인 기준

다음 조건을 만족해야 merge합니다.

- [ ] 기능 목적이 ISSUE와 일치
- [ ] 테스트 통과
- [ ] 관련 문서 업데이트
- [ ] 운영 설정/secret 영향 검토
- [ ] iOS API 영향 검토
- [ ] 리뷰 코멘트 해결

---

## 21. 리팩토링 컨벤션

### 21.1 리팩토링 원칙

- 동작 변경과 구조 변경을 섞지 않습니다.
- 큰 파일을 나눌 때는 먼저 이동만 하고, 그다음 로직을 개선합니다.
- import 경로 변경 후 테스트를 즉시 실행합니다.
- 리팩토링 PR은 before/after 구조를 PR 본문에 적습니다.

### 21.2 리팩토링 우선순위

```text
1. route 파일 분리
2. AI service 분리
3. state store 분리
4. error handling 표준화
5. usecase 계층 도입
6. README/API 문서 보강
7. 코드 품질 도구 도입
```

---

## 22. 금지 사항

아래 작업은 별도 승인 없이 하지 않습니다.

- Firestore에 사용자 학습 기록 저장
- 녹음 파일 장기 저장
- 전사 원문을 로그에 저장
- iOS API response field 삭제
- error code 이름 변경
- production에서 mock AI 사용
- production에서 debug reward auto verify 사용
- SSV 없이 reward 지급
- OpenAI API Key를 클라이언트에 포함
- `main` 직접 push
- `.env` 커밋

---

## 23. 새 개발자 온보딩 체크리스트

처음 참여한 개발자는 아래 순서로 확인합니다.

- [ ] README 읽기
- [ ] 이 협업 컨벤션 문서 읽기
- [ ] `.env.example` 기반 `.env` 생성
- [ ] 로컬 서버 실행
- [ ] `pytest` 실행
- [ ] `/docs` OpenAPI 확인
- [ ] `app/api` endpoint 흐름 확인
- [ ] `app/services/ai` 또는 기존 `app/services/ai.py` 확인
- [ ] `app/services/state` 또는 기존 `app/services/state.py` 확인
- [ ] Firestore 컬렉션 역할 확인
- [ ] AdMob SSV 흐름 확인
- [ ] 첫 ISSUE를 작은 작업으로 시작

---

## 24. 작업 완료 정의

백엔드 작업은 아래 조건을 만족해야 완료입니다.

- 기능 또는 리팩토링 목적이 달성됨
- 테스트가 추가 또는 수정됨
- `pytest` 통과
- API 계약 변경 여부가 명시됨
- Firestore/env/secret 변경 여부가 명시됨
- README 또는 docs가 필요한 경우 업데이트됨
- 로그에 민감정보가 남지 않음
- iOS와 맞물리는 요청/응답이 깨지지 않음
