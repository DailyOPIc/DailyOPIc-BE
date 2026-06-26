# DailyOPIc Backend

## 문서 바로가기

- [CONTRIBUTING.md](CONTRIBUTING.md): 브랜치, 커밋, 이슈, PR 작성 방식만 정리한 간단 협업 가이드
- [README_COLLABORATION.md](README_COLLABORATION.md): 코드 구조, 보안, 테스트, 배포까지 포함한 상세 협업 컨벤션


## 로컬 개발

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env
uvicorn app.main:app --reload
pytest
```

로컬 기본값은 개발용입니다.

```env
ENVIRONMENT=development
AUTH_DISABLED=true
MOCK_AI=true
APP_CHECK_REQUIRED=false
FIRESTORE_ENABLED=false
ADMOB_SSV_REQUIRED=false
DEBUG_REWARD_AUTO_VERIFY=true
FREE_PRACTICE_LIMIT=3
REWARD_PRACTICE_CREDITS=3
MAX_DAILY_REWARD_COUNT=3
```

이 설정에서는 클라우드 인증 없이 전체 API를 실행할 수 있습니다. 사용자를 바꿔 테스트할 때는 `X-Debug-User-ID` 헤더를 사용합니다.

실제 iPhone에서 Mac의 로컬 서버를 테스트할 때는 서버를 모든 인터페이스에 바인딩합니다.

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

iOS 앱의 Debug API URL은 `http://<Mac의_핫스팟_IP>:8000` 형태여야 합니다. iPhone에서 `127.0.0.1`은 Mac이 아니라 iPhone 자기 자신입니다.

실제 OpenAI 호출을 로컬에서 확인하려면 `.env`에 `MOCK_AI=false`, `OPENAI_API_KEY=<local shell secret>`, `AUTH_DISABLED=true`, `FIRESTORE_ENABLED=false`를 설정하고 실행합니다. API Key는 로그나 README, `.env.example`에 기록하지 않습니다. 로컬 `.env`에 주석 처리된 OpenAI 키 문자열이 있었다면 삭제하고, 실제 키였다면 즉시 폐기 후 재발급하세요.

## 운영 설정

운영 환경은 `.env.production.example`를 기준으로 구성합니다. `ADMOB_SSV_REQUIRED=false`는 초기 출시에서 허용하지만, `ADMOB_REWARDED_AD_UNIT_ID`는 항상 리워드 광고 Unit ID만 넣습니다. 배너 광고 Unit ID는 서버 설정에 넣지 않습니다.

```env
ENVIRONMENT=production
AUTH_DISABLED=false
MOCK_AI=false
OPENAI_MODEL=gpt-5.4-mini-2026-03-17
FIREBASE_PROJECT_ID=opicmobile-45cd5
FIRESTORE_ENABLED=true
APP_CHECK_REQUIRED=true
ADMOB_SSV_REQUIRED=false
ADMOB_REWARDED_AD_UNIT_ID=ca-app-pub-5460686409666356/7091483531
DEBUG_REWARD_AUTO_VERIFY=false
QUESTION_PATTERNS_PATH=/app/data/question_patterns.json
FREE_PRACTICE_LIMIT=3
REWARD_PRACTICE_CREDITS=3
MAX_DAILY_REWARD_COUNT=3
ALLOWED_ORIGINS=
```

Secret Manager로 주입해야 하는 값:

- `OPENAI_API_KEY`
- `TOKEN_SIGNING_SECRET`

일반 환경변수로 넣어도 되는 값:

- `ENVIRONMENT`
- `OPENAI_MODEL`
- `FIREBASE_PROJECT_ID`
- `FIRESTORE_ENABLED`
- `APP_CHECK_REQUIRED`
- `ADMOB_SSV_REQUIRED`
- `ADMOB_REWARDED_AD_UNIT_ID`
- `DEBUG_REWARD_AUTO_VERIFY`
- `QUESTION_PATTERNS_PATH`
- `FREE_PRACTICE_LIMIT`
- `REWARD_PRACTICE_CREDITS`
- `MAX_DAILY_REWARD_COUNT`
- `ALLOWED_ORIGINS`

운영에서는 아래 설정이 있으면 서버 시작 시 실패합니다.

- `AUTH_DISABLED=true`
- `MOCK_AI=true`
- `APP_CHECK_REQUIRED=false`
- `DEBUG_REWARD_AUTO_VERIFY=true`
- 빈 `OPENAI_API_KEY`
- 임시/짧은 `TOKEN_SIGNING_SECRET`

iOS 번들 ID `com.mark.opicmobile`에 대해 Firebase 익명 인증과 App Check/App Attest를 활성화해야 합니다. Cloud Run 서비스 계정에는 Firestore 접근 권한이 필요합니다. 서비스 계정 JSON 파일은 레포지토리에 저장하지 않습니다.

Firestore TTL은 `aiRequests`, `adRewardIntents`의 `expiresAt` 필드에 설정합니다. 사용하는 컬렉션은 아래 세 개뿐입니다.

- `dailyUsage`
- `adRewardIntents`: SSV transaction replay 방지 sentinel 포함
- `aiRequests`

## 컨테이너와 Cloud Run 사용

기존 100문항 패턴을 이미지에 포함하기 위해 워크스페이스 루트에서 빌드합니다.

```bash
docker build -f DailyOPIc-BE/Dockerfile -t dailyopic-api .
```

Cloud Run은 request-based billing, 최소 인스턴스 `0`, timeout `300초`, 보수적인 최대 인스턴스 설정을 권장합니다. `OPENAI_API_KEY`, `TOKEN_SIGNING_SECRET`은 일반 환경변수가 아니라 Secret Manager로 연결합니다. Health check는 `/health`를 사용하며 `status`, `environment`, `mockAI`를 반환합니다.

CORS는 iOS 앱에는 필요하지 않습니다. 브라우저 기반 관리 도구가 필요할 때만 `ALLOWED_ORIGINS=https://admin.example.com`처럼 쉼표 또는 세미콜론 구분 목록으로 활성화합니다. 기본값은 빈 목록입니다.

AdMob 리워드 광고의 Server-side verification callback은 아래 주소로 설정합니다.

```text
https://<cloud-run-host>/v1/admob/ssv
```

iOS 앱은 먼저 nonce를 생성한 뒤 AdMob `customData`로 전달합니다. 초기 출시는 `ADMOB_SSV_REQUIRED=false`로 배포할 수 있습니다. 이 모드에서는 앱이 리워드 획득 콜백을 받은 뒤 인증/App Check 헤더를 포함해 `POST /v1/ad-rewards/{nonce}/client-complete`를 호출해야 보상이 지급됩니다.

Cloud Run URL을 AdMob Console callback에 등록한 뒤 `ADMOB_SSV_REQUIRED=true`로 전환하면 `client-complete`는 거부되고, Google SSV callback이 nonce, `transaction_id`, `user_id`, 리워드 Unit ID, signature를 검증한 뒤에만 보상을 지급합니다.

## API

OpenAPI 문서는 `/docs`에서 확인할 수 있습니다. 인증이 필요한 endpoint는 아래 헤더를 사용합니다.

- `Authorization: Bearer <Firebase ID token>`
- `X-Firebase-AppCheck: <App Check token>`
- 평가 요청에는 `Idempotency-Key` 필요

오디오는 요청 처리 중 임시 파일로만 분석하며 OpenAI로 보내지 않습니다. 요청 처리가 끝나면 임시 오디오는 삭제됩니다.

## 사용량 정책

v1에서는 문제 생성 횟수는 제한하지 않고, AI 피드백/평가 호출만 quota를 사용합니다. 날짜 기준은 KST `YYYYMMDD`입니다.

- 기본 무료 피드백: `FREE_PRACTICE_LIMIT=3`
- 리워드 광고 1회 시 추가 피드백: `REWARD_PRACTICE_CREDITS=3`
- 하루 최대 리워드 intent 수: `MAX_DAILY_REWARD_COUNT=3`

## CI 검증

기본 테스트는 deterministic AI fixture와 in-memory transactional store를 사용합니다. 30개 기준 답변 calibration corpus는 `tests/fixtures/calibration_answers.json`에 있습니다.

Firestore 동시성 테스트는 Firebase Emulator로 실행합니다.

```bash
firebase emulators:exec --only firestore \
  'GCLOUD_PROJECT=dailyopic-test pytest tests/test_firestore_emulator.py'
```

클라이언트용 Firestore rules는 모든 직접 읽기/쓰기를 거부합니다. Cloud Run은 Admin SDK를 사용하므로 클라이언트 rules의 적용을 받지 않습니다.

## 출시 전 체크리스트

- Firebase Anonymous Auth가 활성화되어 있는지 확인
- Firebase App Check App Attest가 iOS 앱 `com.mark.opicmobile`에 연결되어 있는지 확인
- Cloud Run 서비스 계정에 Firestore 접근 권한이 있는지 확인
- `OPENAI_API_KEY`, `TOKEN_SIGNING_SECRET`을 Secret Manager로 연결
- `TOKEN_SIGNING_SECRET`이 임시 문자열이나 짧은 값이 아닌지 확인
- 서버에는 `ADMOB_REWARDED_AD_UNIT_ID=ca-app-pub-5460686409666356/7091483531`만 설정
- AdMob SSV callback URL은 Cloud Run 배포 후 `https://<cloud-run-host>/v1/admob/ssv`로 설정
- 초기 출시에서는 `ADMOB_SSV_REQUIRED=false`, `DEBUG_REWARD_AUTO_VERIFY=false`, 앱의 `client-complete` 호출로 배포 가능
- SSV callback 검증 후 `ADMOB_SSV_REQUIRED=true`로 전환
- `pytest`와 `docker build -f DailyOPIc-BE/Dockerfile -t dailyopic-api .` 통과 확인
