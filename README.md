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
- Self Assessment 단계는 Firestore `userProfiles`에 저장하며, 최초 설정은 무료이고 단계 변경은 보상형 광고 검증 후 허용합니다.

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
REVENUECAT_SECRET_API_KEY=

FIREBASE_PROJECT_ID=opicmobile-45cd5
ADMOB_REWARDED_AD_UNIT_ID=ca-app-pub-5460686409666356/7091483531

FREE_PRACTICE_LIMIT=3
REWARD_PRACTICE_CREDITS=1
MAX_DAILY_REWARD_COUNT=3
```

실제 iPhone에서 Mac의 로컬 서버를 테스트할 때 iOS 앱의 Debug API URL은 `http://<Mac의_핫스팟_IP>:8000` 형태여야 합니다. iPhone에서 `127.0.0.1`은 Mac이 아니라 iPhone 자기 자신입니다.

실제 OpenAI 호출을 확인하려면 `.env`에 `MOCK_AI=false`, `OPENAI_API_KEY=<local shell secret>`을 설정합니다. API Key는 로그나 문서, 예시 env 파일에 기록하지 않습니다.
`MOCK_AI=true`에서는 OpenAI를 호출하지 않고 catalog fallback 질문을 반환하므로 응답의 `fallbackUsed`가 `true`입니다.

## 운영 설정

운영도 같은 환경변수 구조를 사용합니다. `OPENAI_API_KEY`와
`REVENUECAT_SECRET_API_KEY`는 반드시 Secret Manager로 주입하고 소스, 이미지,
GitHub Actions 평문 환경변수에 저장하지 않습니다.

```env
MOCK_AI=false
OPENAI_MODEL=gpt-5.4-mini-2026-03-17
FIREBASE_PROJECT_ID=opicmobile-45cd5
ADMOB_REWARDED_AD_UNIT_ID=ca-app-pub-5460686409666356/7091483531
FREE_PRACTICE_LIMIT=3
REWARD_PRACTICE_CREDITS=1
MAX_DAILY_REWARD_COUNT=3
```

RevenueCat Secret API Key를 Cloud Run에 연결할 때는 키를 명령행 인자로
넘기지 않고 Secret Manager의 표준 입력으로 새 버전을 추가합니다.

```bash
gcloud secrets create revenuecat-secret-api-key \
  --replication-policy=automatic \
  --project "$PROJECT_ID"

gcloud secrets versions add revenuecat-secret-api-key \
  --data-file=- \
  --project "$PROJECT_ID"

gcloud secrets add-iam-policy-binding revenuecat-secret-api-key \
  --member "serviceAccount:${RUNTIME_SA}" \
  --role roles/secretmanager.secretAccessor \
  --project "$PROJECT_ID"

gcloud run services update "$SERVICE" \
  --region "$REGION" \
  --project "$PROJECT_ID" \
  --update-secrets=REVENUECAT_SECRET_API_KEY=revenuecat-secret-api-key:latest
```

이미 secret이 있는 경우 `gcloud secrets create` 단계는 생략합니다. 서버에
키가 없거나 RevenueCat API 조회가 실패하면 웹훅은 2xx를 반환하지
않으며, 기존 사용자 플랜은 보존됩니다.

iOS 번들 ID `com.mark.opicmobile`에 대해 Firebase App Check/App Attest를 활성화해야 합니다. Cloud Run 서비스 계정에는 Firestore 접근 권한이 필요합니다. 서비스 계정 JSON 파일은 레포지토리에 저장하지 않습니다.

Firestore TTL은 `questionSets`, `aiRequests`, `adRewardIntents`의 `expiresAt` 필드에 설정합니다. 사용하는 컬렉션은 아래 여섯 개입니다.

- `dailyUsage`
- `userProfiles`
- `questionSets`
- `questionHistories`: 최근 `setHash`, `topicId`, `promptHash` 중복 방지용
- `adRewardIntents`: SSV transaction replay 방지 sentinel 포함
- `aiRequests`

## 컨테이너와 Cloud Run 사용

DailyOPIc-BE 저장소 루트에서 빌드합니다.

```bash
docker build -t dailyopic-api .
```

Cloud Run은 request-based billing, 최소 인스턴스 `0`, timeout `300초`, 보수적인 최대 인스턴스 설정을 권장합니다. Health check는 `/health`를 사용하며 `status`, `mockAI`를 반환합니다.

## GitHub Actions CI/CD

`.github/workflows/deploy-cloud-run.yml`은 백엔드 저장소 루트(`DailyOPIc-BE`) 기준으로 실행됩니다.

- `main` 대상 PR: `pytest -q`만 실행합니다.
- `main` push 또는 PR merge: 테스트 통과 후 Docker 이미지를 빌드하고 Artifact Registry에 push한 뒤 Cloud Run `dailyopic-api`에 배포합니다.
- 이미지 태그는 GitHub full SHA 기반 `sha-<github-sha>`를 사용합니다.
- 배포 이미지 경로는 `asia-northeast3-docker.pkg.dev/opicmobile-45cd5/dailyopic/dailyopic-api:sha-<github-sha>`입니다.
- 배포 후 workflow가 Cloud Run 서비스 URL의 `/health`를 호출해 새 revision 응답을 확인합니다.

GitHub repository secrets에는 아래 값을 등록합니다.

```text
GCP_SA_KEY=<GitHub Actions deployer service account JSON key>
GCP_PROJECT_ID=opicmobile-45cd5
GCP_REGION=asia-northeast3
CLOUD_RUN_SERVICE=dailyopic-api
ARTIFACT_REGISTRY_REPOSITORY=dailyopic
```

현재 workflow는 적용이 쉬운 서비스 계정 키 JSON 방식(`GCP_SA_KEY`)을 사용합니다. 이 방식은 설정이 단순하지만 장기 key가 GitHub Secret에 저장되므로 key 유출 위험, 주기적인 rotation, 퇴사자/권한 변경 시 폐기 절차를 관리해야 합니다. 보안상 더 좋은 방식은 GitHub OIDC 기반 Workload Identity Federation입니다. WIF로 전환하면 JSON key 없이 GitHub Actions가 짧은 수명의 토큰으로 GCP 서비스 계정을 impersonate합니다. 전환 시 workflow의 `google-github-actions/auth` 설정을 `workload_identity_provider`, `service_account` 방식으로 바꾸고, secrets에는 아래 값을 사용합니다.

```text
GCP_WORKLOAD_IDENTITY_PROVIDER=<projects/.../locations/global/workloadIdentityPools/.../providers/...>
GCP_SERVICE_ACCOUNT=github-actions-deployer@opicmobile-45cd5.iam.gserviceaccount.com
```

배포용 서비스 계정 예시는 아래와 같습니다.

```text
github-actions-deployer@opicmobile-45cd5.iam.gserviceaccount.com
```

필요한 API와 IAM 권한은 아래와 같습니다. Artifact Registry writer는 가능하면 프로젝트 전체가 아니라 `dailyopic` repository 단위로 부여합니다.

```bash
export PROJECT_ID=opicmobile-45cd5
export REGION=asia-northeast3
export REPO=dailyopic
export DEPLOYER_SA="github-actions-deployer@${PROJECT_ID}.iam.gserviceaccount.com"
export RUNTIME_SA="<cloud-run-runtime-service-account>"

gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  --project "$PROJECT_ID"

gcloud artifacts repositories add-iam-policy-binding "$REPO" \
  --location "$REGION" \
  --member "serviceAccount:${DEPLOYER_SA}" \
  --role roles/artifactregistry.writer \
  --project "$PROJECT_ID"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member "serviceAccount:${DEPLOYER_SA}" \
  --role roles/run.admin

gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" \
  --member "serviceAccount:${DEPLOYER_SA}" \
  --role roles/iam.serviceAccountUser \
  --project "$PROJECT_ID"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member "serviceAccount:${RUNTIME_SA}" \
  --role roles/firebaseauth.viewer
```

Cloud Run runtime service account에는 운영 실행 권한이 별도로 필요합니다.

- Firestore 접근: `roles/datastore.user`
- Firebase ID 토큰 폐기 여부 확인: `roles/firebaseauth.viewer` (`firebaseauth.users.get`)
- Secret Manager에서 `OPENAI_API_KEY` 또는 `REVENUECAT_SECRET_API_KEY`를 주입하는
  경우 각 secret에 `roles/secretmanager.secretAccessor`

Workload Identity Federation으로 전환할 때는 아래 API도 활성화합니다.

```bash
gcloud services enable \
  iamcredentials.googleapis.com \
  sts.googleapis.com \
  --project "$PROJECT_ID"
```

배포 실패 시 먼저 GitHub Actions의 실패 step을 확인합니다.

- `Authenticate to Google Cloud`: `GCP_SA_KEY` 누락, JSON key 오류, 비활성화된 서비스 계정
- `Push Docker image`: Artifact Registry repository 경로 오류 또는 `roles/artifactregistry.writer` 부족
- `Deploy to Cloud Run`: `roles/run.admin` 부족 또는 runtime service account에 대한 `roles/iam.serviceAccountUser` 부족
- `Verify health endpoint`: revision 시작 실패, Cloud Run 인증 설정, 런타임 환경변수/Secret Manager/Firestore 권한 문제

Cloud Run과 Artifact Registry 상태는 아래 명령으로 확인합니다.

```bash
gcloud run services logs read dailyopic-api \
  --region asia-northeast3 \
  --project opicmobile-45cd5 \
  --limit 100

gcloud run revisions list \
  --service dailyopic-api \
  --region asia-northeast3 \
  --project opicmobile-45cd5

gcloud artifacts docker images list \
  asia-northeast3-docker.pkg.dev/opicmobile-45cd5/dailyopic/dailyopic-api \
  --include-tags
```

배포 후 서비스 URL을 알고 있으면 직접 `/health`를 확인할 수 있습니다.

```bash
curl -fsS https://<cloud-run-host>/health
```

### 수동 배포 fallback

GitHub Actions 배포가 실패하거나 긴급히 로컬에서 배포해야 할 때만 아래 명령을 사용합니다.

```bash
cd DailyOPIc-BE
export PROJECT_ID=opicmobile-45cd5
export REGION=asia-northeast3
export SERVICE=dailyopic-api
export REPO=dailyopic
export IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${SERVICE}:$(date +%Y%m%d-%H%M%S)"
gcloud config set project "$PROJECT_ID"
gcloud builds submit . --tag "$IMAGE" && \
gcloud run deploy "$SERVICE" \
  --image "$IMAGE" \
  --region "$REGION" \
  --platform managed
```

AdMob 리워드 광고의 Server-side verification callback은 아래 주소로 설정합니다.

```text
https://<cloud-run-host>/v1/admob/ssv
```

Cloudflare Tunnel로 로컬 uvicorn을 검증할 때는 예를 들어 아래처럼 설정합니다.

```text
https://steve-immigration-amy-does.trycloudflare.com/v1/admob/ssv
```

이 callback은 iOS 앱이 호출하는 API URL과 별개입니다. iPhone에서 `http://172.20.x.x:8000`
로컬 서버를 호출할 수 있어도 Google AdMob 서버는 그 사설 IP로 callback을 보낼 수 없습니다.
로컬 앱에서 리워드 광고까지 end-to-end로 테스트하려면 Cloud Run 개발 배포 URL 또는
ngrok/cloudflared 같은 public HTTPS 터널의 `https://<public-host>/v1/admob/ssv`를
AdMob Console의 SSV callback URL로 등록합니다. callback이 정상 수신되면 서버 로그에
`[SSV] callback received`, `[SSV] reward verified`, `[SSV] reward completed`가 출력됩니다.

iOS 앱은 서버에서 받은 `userIdentifier`와 `customData`를 AdMob rewarded ad options에 전달합니다. 서버는 Google SSV callback의 nonce, transaction id, user id, rewarded ad unit id, signature를 검증한 뒤에만 보상을 지급하거나 목표 등급 변경 권한을 소비합니다. AdMob SSV의 `ad_unit`은 전체 광고 단위 ID 대신 뒤쪽 숫자 ID만 보낼 수 있으므로 서버는 `ADMOB_REWARDED_AD_UNIT_ID` 전체 값과 `/` 뒤 숫자 ID를 모두 허용합니다.

## API

OpenAPI 문서는 `/docs`에서 확인할 수 있습니다. 보호되는 endpoint는 아래 헤더를 사용합니다.

- `X-DailyOPIc-User-ID: <Keychain UUID>`
- `X-Firebase-AppCheck: <App Check token>`
- `Authorization: Bearer <Firebase Authentication ID token>`
- 평가 요청에는 `Idempotency-Key` 필요

Self Assessment 단계는 `PUT /v1/users/me/target-level`로 저장합니다. 새 요청은 `initialLevel` 1~6을 보내며, 기존 `targetLevel`만 저장된 사용자는 서버가 자동으로 단계 값으로 매핑합니다. 최초 설정과 같은 단계 재확정은 무료이고, 기존 단계에서 다른 단계로 바꿀 때는 `target_level_change` reward intent를 만들고 SSV 검증이 끝난 뒤 `rewardNonce`를 함께 보내야 합니다.

학습 캘린더 설정은 `GET/PUT /v1/users/me/study-plan`으로 읽고 씁니다. 저장 위치는 `userProfiles/{uid}.studyPlan` 한 곳이며 시험일(선택), 공부 요일(ISO 월=1~일=7, 최소 1개), 학습 강도(`light|steady|focused`), 선호 학습 시각(`HH:MM` 로컬), 타임존 식별자만 담습니다. **목표 등급은 여기에 복제하지 않고 `PUT /v1/users/me/target-level`이 계속 진실입니다.** PUT은 전체 교체(멱등)이고 `schemaVersion`/`createdAt`/`updatedAt`은 서버가 매깁니다. 시험일은 요청에 담긴 타임존 기준으로 과거인지 판단합니다. 설정한 적 없는 기존 사용자에게는 오류가 아니라 `{"configured": false, "studyPlan": null}`을 돌려줍니다.

`GET /v1/capabilities`의 `quotaPolicy`는 캘린더 관련 권한 6개를 함께 내려줍니다. **캘린더 자체는 전 플랜이 사용하므로 `calendarEnabled`는 항상 `true`이고, 플랜이 나누는 것은 자동화 깊이와 개인 일정 알림 사용 여부뿐입니다.**

| 필드 | 무료 | 베이직 | 플러스 | 프로 |
|---|---|---|---|---|
| `calendarEnabled` | true | true | true | true |
| `calendarAutoReplan` | false | true | true | true |
| `calendarEvaluationAdaptive` | false | false | true | true |
| `calendarExamBackplan` | false | false | true | true |
| `calendarStudyReminder` | true | true | true | true |
| `calendarEventReminder` | false | true | true | true |

알림 필드 2개는 모두 앱이 **기기에서 로컬 알림을 예약할 수 있는 권한**입니다. 서버는 알림을 보내지 않습니다(FCM·APNs 없음).

- `calendarStudyReminder` — 매일 학습 리마인더. **P9부터 무료를 포함한 전 플랜이 `true`입니다.** 매일 공부하라고 깨워 주는 것까지 유료로 막으면 습관이 붙기 전에 무료 사용자가 떠납니다. P4의 "베이직부터" 정책은 폐기됐습니다.
- `calendarEventReminder` — 개인 일정 알림. 새 유료 경계입니다. 개인 일정 자체(추가·수정·삭제)는 전 플랜이 쓰고, **알림 옵션만** 무료에서 잠깁니다.

구버전 앱이 새 서버를 만나면 `calendarStudyReminder=true`를 받아 학습 알림이 그대로 열립니다(의도한 동작). 새 앱은 학습 알림을 이 필드로 잠그지 않으므로, 구버전 서버가 무료에 `false`를 내려줘도 잠기지 않습니다.

기존 필드는 그대로이며 추가만 했으므로 구버전 클라이언트는 영향을 받지 않습니다. 새 필드를 모르는 앱은 무시하고, 새 앱이 이 필드가 없는 구버전 서버를 만나면 플랜 기준 폴백을 씁니다. 취약점 기반 일정(`calendarWeaknessPlanner`)은 구현이 없으므로 **capability로 노출하지 않습니다.**

Daily 문제와 Mock 문제 생성은 서로 다르게 동작합니다.

```text
POST /v1/question-sets/practice
  -> 오늘의 Daily 랜덤 풀, Q2~Q15, status=complete (데일리 토큰 1개 소모)

POST /v1/question-sets/practice/refresh
  body: {"initialLevel":5, "adjustment":"easier|same|harder", "survey":{...}}
  -> 데일리 토큰 1개 소모 후 새 Daily 랜덤 풀 생성

POST /v1/question-sets/review
  body: {..., "focusDimension":"fluency"}
  -> 취약점 복습 세트(프로 전용). mode=daily 새 세트이므로 데일리 토큰 1개 소모

POST /v1/evaluations/practice  (= /v2)
  -> AI 분석 1회. 데일리 토큰 1개 소모

POST /v1/mock-exams
  -> Mock Q1~Q7, status=awaiting_adjustment

POST /v1/question-sets/{setId}/adjustment
  body: {"adjustment":"easier|same|harder"}
  -> Mock Q8~Q15 append

POST /v1/vocabulary/today/generate
  body: {"topic":"cafes", "targetLevel":"IH", "excludeTerms":[...]}
  -> 단어 만들기 1세트 = 20개(단어 7 / 표현 7 / 패턴 6). 데일리 토큰 1개 소모
```

단어장 생성은 개수 · 구성(7/7/6) · 모델 · 내부 보충 횟수를 **서버가 정합니다.**
`excludeTerms`는 이미 가진 표현을 걸러 달라는 힌트일 뿐이고 개수 상한이 있습니다.
부족분을 채우려고 제공자를 두 번 부르더라도 그것은 내부 사정이며, 사용자 차감은
`Idempotency-Key`가 가리키는 조작 1회당 토큰 1개로 고정입니다. 같은 키로 다시
보내면 저장된 세트를 그대로 돌려주고(추가 차감 없음), 쓸 만한 결과를 못 만들면
정확히 한 번 환불합니다.

Daily는 자기소개 Q1을 포함하지 않습니다. 첫 Daily 풀도 데일리 토큰 1개를 쓰며, 같은 날 같은 조건으로 다시 요청하면 이미 만든 풀을 그대로 반환합니다(추가 차감 없음). Mock은 Q1을 항상 `Introduce yourself.`로 고정하고, Q7 이후 adjustment를 적용해 15문항으로 완성합니다. 같은 `setId`에 같은 adjustment를 다시 보내면 완성된 세트를 그대로 반환하고, 다른 adjustment를 다시 보내면 `409 adjustment_already_applied`를 반환합니다.

오디오는 요청 처리 중 임시 파일로만 분석하며 OpenAI로 보내지 않습니다. 요청 처리가 끝나면 임시 오디오는 삭제됩니다.

## 사용량 정책 — 데일리 토큰

**사용자가 시작한 AI 작업은 무료가 아닙니다. 사용자 조작 1회 = 데일리 토큰 1개.**

| 사용자 행동 | 비용 |
|---|---|
| 새 Daily 문제 세트(첫 생성 · refresh · 복습 세트) | 데일리 토큰 1개 |
| AI 분석 1회 | 데일리 토큰 1개 |
| 사용자가 의도적으로 다시 분석 | 데일리 토큰 1개 더 |
| 단어 만들기 1세트(20개 = 단어 7 · 표현 7 · 패턴 6) | 데일리 토큰 1개 |
| 이미 받은 결과 다시 보기 · 이미 받은 문항 다시 열기 | 0 |
| 이미 만든 단어장 보기 · 학습 · 복습, 시드 단어장 전체 | 0 |
| 녹음 · 다시 녹음 · 기기 TTS/STT | 0 |
| 플래너 · 하루 시트 · 학습 기록 · 시험 가이드 | 0 |
| 모의고사 | 데일리 토큰과 별개(플랜별 `mock_daily` 횟수) |

폐기된 계약: *"토큰 1개 = 새 Daily 문제 세트 1개, 세트 내 평가는 무제한."* 세트 내
평가는 더 이상 무료가 아닙니다.

### 서버가 과금 권한을 가진다

- 차감은 서버에서만 일어납니다. 앱의 사전 확인은 UX용이고 신뢰 대상이 아닙니다.
- 순서는 **인증 → 검증 → 토큰 예약(원자적) → AI 호출**입니다. 토큰이 없으면
  OpenAI를 호출하지 않고 `402 practice_quota_exhausted`로 끝냅니다.
- 예약은 `dailyUsage/{uid}:{dateKey}` 문서에 대한 Firestore 트랜잭션
  (`FirestoreStateStore.reserve_practice`)이라, 마지막 1개를 두고 두 요청이
  경합하면 정확히 하나만 성공하고 잔액은 절대 음수가 되지 않습니다.
- 과금 단위는 HTTP 요청 수가 아니라 **사용자 조작 1회**이고, 그 정체성은
  `Idempotency-Key`입니다. 같은 키 재전송은 캐시된 결과를 돌려주고 추가로
  차감하지 않습니다. 사용자가 "다시 분석"을 누르면 앱이 새 키를 보냅니다.
- 쓸 만한 결과를 내지 못한 실패(제공자 오류·예외)는 예약한 토큰을 정확히 한 번
  되돌립니다(`fail_request`). 이중 환불도, 이중 차감도 없습니다.

### 플랜별 하루 데일리 토큰 (`app/services/plans.py`가 진실)

| | 무료 | 베이직 | 플러스 | 프로 |
|---|---|---|---|---|
| `practice_daily` | 1 | 3 | 10 | 20 |
| `practice_ad_bonus` (리워드 광고) | 1 | 0 | 0 | 0 |
| 하루 최대 | **2** | 3 | 10 | 20 |

**정상 Daily 1사이클 = 세트 1개 + 분석 1회 = 토큰 2개.** 같은 세트에서 문항을 하나
더 분석하면 +1개입니다(세트는 14문항이므로 전 문항 분석은 15개).

**무료 플랜의 결과: 무료 사용자는 광고 보너스 1개를 받아야만 하루 1사이클을 끝낼 수
있습니다.** 광고를 보지 않으면 세트를 받고 분석을 하지 못합니다. 이 문서는 그 사실을
숨기지 않으며, 무료 한도를 1에서 2로 올리는 것은 별도 결정 사항입니다.

- 하루 최대 리워드 intent 수: `MAX_DAILY_REWARD_COUNT=3`
- 광고 보너스 토큰은 새 세트와 AI 분석 **양쪽 모두**에 쓸 수 있습니다. 분석 전용
  토큰 같은 별도 화폐는 없습니다.
- Self Assessment 단계 변경용 리워드는 데일리 토큰을 지급하지 않고 변경 권한만 1회
  소비하며, practice reward quota로 차단하지 않습니다.
- `FREE_PRACTICE_LIMIT` / `REWARD_PRACTICE_CREDITS` 환경변수는 테스트 픽스처용
  잔재이며 플랜 한도는 `plans.py`만 결정합니다.

날짜 기준은 KST `YYYYMMDD`입니다.

### 데일리 토큰 ≠ OpenAI 토큰

제품 화폐인 **데일리 토큰**은 "사용자가 시작한 AI 작업 1회"를 세는 단위입니다.
OpenAI가 청구하는 토큰(`inputTokens`/`outputTokens`/`reasoningTokens`)은 모델
입출력 단위이며 둘은 같은 것이 아닙니다. 사용자에게는 OpenAI 토큰 개념을 노출하지
않습니다.

운영 손익은 AdMob 리워드 광고 1회 실수익이 Practice 분석 1회 평균 AI 비용보다
충분히 높아야 합니다. 서버는 OpenAI 응답 usage를 로그로 남기므로, 출시 후
`inputTokens`, `outputTokens`, `reasoningTokens`를 집계해 실제 비용을 주 단위로
확인합니다.

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
- `REVENUECAT_SECRET_API_KEY`를 Secret Manager로 연결
- 서버에는 `ADMOB_REWARDED_AD_UNIT_ID=ca-app-pub-5460686409666356/7091483531`만 설정
- AdMob SSV callback URL은 public HTTPS `https://<cloud-run-host>/v1/admob/ssv` 또는 개발용 HTTPS 터널로 설정
- Firestore TTL을 `questionSets`, `aiRequests`, `adRewardIntents`의 `expiresAt`에 설정
- `pytest`와 `docker build -t dailyopic-api .` 통과 확인
