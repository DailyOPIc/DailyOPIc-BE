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

로컬에서는 `AUTH_DISABLED=true`, `FIRESTORE_ENABLED=false`, `MOCK_AI=true` 설정으로 클라우드 인증 없이 전체 API를 실행할 수 있습니다. 사용자를 바꿔 테스트할 때는 `X-Debug-User-ID` 헤더를 사용합니다.

## 운영 설정

운영 환경에는 아래 환경변수/시크릿이 필요합니다.

- `ENVIRONMENT=production`
- `AUTH_DISABLED=false`
- `APP_CHECK_REQUIRED=true`
- `FIRESTORE_ENABLED=true`
- `FIREBASE_PROJECT_ID=<project>`
- `OPENAI_API_KEY`: Secret Manager에서 주입
- `MOCK_AI=false`
- `TOKEN_SIGNING_SECRET`: Secret Manager에서 주입
- `ADMOB_SSV_REQUIRED=true`
- `ADMOB_REWARDED_AD_UNIT_ID=<production rewarded unit>`
- `DEBUG_REWARD_AUTO_VERIFY=false`

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

Cloud Run은 request-based billing, 최소 인스턴스 `0`, timeout `300초`, 보수적인 최대 인스턴스 설정을 권장합니다. `OPENAI_API_KEY`, `TOKEN_SIGNING_SECRET`은 일반 환경변수가 아니라 Secret Manager로 연결합니다.

AdMob 리워드 광고의 Server-side verification callback은 아래 주소로 설정합니다.

```text
https://<cloud-run-host>/v1/admob/ssv
```

iOS 앱은 먼저 nonce를 생성한 뒤 AdMob `customData`로 전달합니다. 평가는 Google SSV callback이 해당 nonce를 검증한 뒤에만 열립니다.

## API

OpenAPI 문서는 `/docs`에서 확인할 수 있습니다. 인증이 필요한 endpoint는 아래 헤더를 사용합니다.

- `Authorization: Bearer <Firebase ID token>`
- `X-Firebase-AppCheck: <App Check token>`
- 평가 요청에는 `Idempotency-Key` 필요

오디오는 요청 처리 중 임시 파일로만 분석하며 OpenAI로 보내지 않습니다. 요청 처리가 끝나면 임시 오디오는 삭제됩니다.

## CI 검증

기본 테스트는 deterministic AI fixture와 in-memory transactional store를 사용합니다. 30개 기준 답변 calibration corpus는 `tests/fixtures/calibration_answers.json`에 있습니다.

Firestore 동시성 테스트는 Firebase Emulator로 실행합니다.

```bash
firebase emulators:exec --only firestore \
  'GCLOUD_PROJECT=dailyopic-test pytest tests/test_firestore_emulator.py'
```

클라이언트용 Firestore rules는 모든 직접 읽기/쓰기를 거부합니다. Cloud Run은 Admin SDK를 사용하므로 클라이언트 rules의 적용을 받지 않습니다.
