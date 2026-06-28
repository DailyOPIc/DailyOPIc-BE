# DailyOPIc Backend

## 문서 바로가기

- [CONTRIBUTING.md](CONTRIBUTING.md): 브랜치, 커밋, 이슈, PR 작성 방식만 정리한 간단 협업 가이드
- [README_COLLABORATION.md](README_COLLABORATION.md): 코드 구조, 보안, 테스트, 배포까지 포함한 상세 협업 컨벤션

## 환경 정책

개발 서버와 운영 서버는 같은 구조로 실행합니다. 차이는 AI 호출만 `MOCK_AI`로 전환한다는 점입니다.

- Firestore는 항상 사용합니다.
- Firebase App Check는 항상 검증합니다.
- Firebase Auth는 사용하지 않습니다.
- 사용자 식별은 iOS Keychain에 저장된 UUID를 `X-DailyOPIc-User-ID` 헤더로 전달합니다.
- AdMob 보상은 Server-side verification callback으로만 지급합니다.
- 질문 세트는 Firestore `questionSets`에 저장하고 `setId`로 조회합니다.

## 로컬 개발

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
pytest
```

개발 서버도 Firebase 프로젝트, Firestore, App Check 설정이 필요합니다. iOS 시뮬레이터나 로컬 Debug 빌드는 Firebase App Check debug token을 Firebase Console에 등록한 뒤 사용합니다.

```env
MOCK_AI=true
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.4-mini-2026-03-17

FIREBASE_PROJECT_ID=opicmobile-45cd5
ADMOB_REWARDED_AD_UNIT_ID=ca-app-pub-5460686409666356/7091483531

FREE_PRACTICE_LIMIT=3
REWARD_PRACTICE_CREDITS=1
MAX_DAILY_REWARD_COUNT=3
```

실제 iPhone에서 Mac의 로컬 서버를 테스트할 때 iOS 앱의 Debug API URL은 `http://<Mac의_핫스팟_IP>:8000` 형태여야 합니다. iPhone에서 `127.0.0.1`은 Mac이 아니라 iPhone 자기 자신입니다.

실제 OpenAI 호출을 확인하려면 `.env`에 `MOCK_AI=false`, `OPENAI_API_KEY=<local shell secret>`을 설정합니다. API Key는 로그나 문서, 예시 env 파일에 기록하지 않습니다.

## 운영 설정

운영도 같은 환경변수 구조를 사용합니다. Secret Manager로 주입해야 하는 값은 `OPENAI_API_KEY`뿐입니다.

```env
MOCK_AI=false
OPENAI_MODEL=gpt-5.4-mini-2026-03-17
FIREBASE_PROJECT_ID=opicmobile-45cd5
ADMOB_REWARDED_AD_UNIT_ID=ca-app-pub-5460686409666356/7091483531
FREE_PRACTICE_LIMIT=3
REWARD_PRACTICE_CREDITS=1
MAX_DAILY_REWARD_COUNT=3
```

iOS 번들 ID `com.mark.opicmobile`에 대해 Firebase App Check/App Attest를 활성화해야 합니다. Cloud Run 서비스 계정에는 Firestore 접근 권한이 필요합니다. 서비스 계정 JSON 파일은 레포지토리에 저장하지 않습니다.

Firestore TTL은 `questionSets`, `aiRequests`, `adRewardIntents`의 `expiresAt` 필드에 설정합니다. 사용하는 컬렉션은 아래 네 개입니다.

- `dailyUsage`
- `questionSets`
- `adRewardIntents`: SSV transaction replay 방지 sentinel 포함
- `aiRequests`

## 컨테이너와 Cloud Run 사용

워크스페이스 루트에서 빌드합니다.

```bash
docker build -f DailyOPIc-BE/Dockerfile -t dailyopic-api .
```

Cloud Run은 request-based billing, 최소 인스턴스 `0`, timeout `300초`, 보수적인 최대 인스턴스 설정을 권장합니다. Health check는 `/health`를 사용하며 `status`, `mockAI`를 반환합니다.

AdMob 리워드 광고의 Server-side verification callback은 아래 주소로 설정합니다.

```text
https://<cloud-run-host>/v1/admob/ssv
```

iOS 앱은 서버에서 받은 `userIdentifier`와 `customData`를 AdMob rewarded ad options에 전달합니다. 서버는 Google SSV callback의 nonce, transaction id, user id, rewarded ad unit id, signature를 검증한 뒤에만 보상을 지급합니다.

## API

OpenAPI 문서는 `/docs`에서 확인할 수 있습니다. 보호되는 endpoint는 아래 헤더를 사용합니다.

- `X-DailyOPIc-User-ID: <Keychain UUID>`
- `X-Firebase-AppCheck: <App Check token>`
- 평가 요청에는 `Idempotency-Key` 필요

오디오는 요청 처리 중 임시 파일로만 분석하며 OpenAI로 보내지 않습니다. 요청 처리가 끝나면 임시 오디오는 삭제됩니다.

## 사용량 정책

v1에서는 문제 생성 횟수는 제한하지 않고, AI 피드백/평가 호출만 quota를 사용합니다. 날짜 기준은 KST `YYYYMMDD`입니다.

- 기본 무료 피드백: `FREE_PRACTICE_LIMIT=3`
- 리워드 광고 1회 시 추가 피드백: `REWARD_PRACTICE_CREDITS=1`
- 하루 최대 리워드 intent 수: `MAX_DAILY_REWARD_COUNT=3`

운영 손익은 AdMob 리워드 광고 1회 실수익이 Practice 분석 1회 평균 AI 비용보다 충분히 높아야 합니다. 서버는 OpenAI 응답 usage를 로그로 남기므로, 출시 후 `inputTokens`, `outputTokens`, `reasoningTokens`를 집계해 실제 비용을 주 단위로 확인합니다.

## CI 검증

기본 테스트는 deterministic AI fixture와 in-memory transactional store를 사용합니다.

```bash
pytest
```

Firestore 동시성 테스트는 Firebase Emulator로 실행합니다.

```bash
firebase emulators:exec --only firestore \
  'GCLOUD_PROJECT=dailyopic-test pytest tests/test_firestore_emulator.py'
```

클라이언트용 Firestore rules는 모든 직접 읽기/쓰기를 거부합니다. Cloud Run은 Admin SDK를 사용하므로 클라이언트 rules의 적용을 받지 않습니다.

## 출시 전 체크리스트

- Firebase App Check App Attest가 iOS 앱 `com.mark.opicmobile`에 연결되어 있는지 확인
- 개발/시뮬레이터용 App Check debug token을 Firebase Console에 등록
- Cloud Run 서비스 계정에 Firestore 접근 권한이 있는지 확인
- `OPENAI_API_KEY`를 Secret Manager로 연결
- 서버에는 `ADMOB_REWARDED_AD_UNIT_ID=ca-app-pub-5460686409666356/7091483531`만 설정
- AdMob SSV callback URL은 Cloud Run 배포 후 `https://<cloud-run-host>/v1/admob/ssv`로 설정
- Firestore TTL을 `questionSets`, `aiRequests`, `adRewardIntents`의 `expiresAt`에 설정
- `pytest`와 `docker build -f DailyOPIc-BE/Dockerfile -t dailyopic-api .` 통과 확인
