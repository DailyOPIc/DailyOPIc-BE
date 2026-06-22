# DailyOPIc Backend

FastAPI backend for DailyOPIc. User learning records stay in SwiftData on the device. The backend only generates/evaluates questions and persists quota, rewarded-ad, and idempotency state.

## Local development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env
uvicorn app.main:app --reload
pytest
```

With `AUTH_DISABLED=true`, `FIRESTORE_ENABLED=false`, and `MOCK_AI=true`, the full API works without cloud credentials. Set `X-Debug-User-ID` to simulate different users.

## Production configuration

Required environment/secrets:

- `ENVIRONMENT=production`
- `AUTH_DISABLED=false`
- `APP_CHECK_REQUIRED=true`
- `FIRESTORE_ENABLED=true`
- `FIREBASE_PROJECT_ID=<project>`
- `OPENAI_API_KEY` from Secret Manager
- `MOCK_AI=false`
- `TOKEN_SIGNING_SECRET` from Secret Manager
- `ADMOB_SSV_REQUIRED=true`
- `ADMOB_REWARDED_AD_UNIT_ID=<production rewarded unit>`
- `DEBUG_REWARD_AUTO_VERIFY=false`

Enable anonymous Firebase Auth and App Check with App Attest for the iOS bundle `com.mark.opicmobile`. The Cloud Run service account needs Firestore access; no service-account JSON is stored in the repository.

Configure Firestore TTL on the `expiresAt` field for `aiRequests` and `adRewardIntents`. Only these collections are used:

- `dailyUsage`
- `adRewardIntents` (including SSV transaction replay sentinels)
- `aiRequests`

## Container and Cloud Run

Build from the workspace root so the existing 100 question patterns are embedded in the image:

```bash
docker build -f DailyOPIc-BE/Dockerfile -t dailyopic-api .
```

Deploy with request-based billing, minimum instances `0`, a 300-second timeout, and conservative maximum instances. Map `OPENAI_API_KEY` and `TOKEN_SIGNING_SECRET` from Secret Manager instead of plain environment variables.

Set the rewarded ad unit's server-side verification callback to:

```text
https://<cloud-run-host>/v1/admob/ssv
```

The app creates a nonce first and supplies it as AdMob `customData`. Evaluation is unlocked only after the signed SSV callback verifies that nonce.

## API

OpenAPI is available at `/docs`. Authenticated endpoints require:

- `Authorization: Bearer <Firebase ID token>`
- `X-Firebase-AppCheck: <App Check token>`
- `Idempotency-Key` for evaluation calls

Audio is processed in the request's temporary filesystem and never sent to OpenAI. It is deleted when the request ends.

## CI verification

The default test suite uses deterministic AI fixtures and the in-memory transactional store. The
30-answer calibration corpus is in `tests/fixtures/calibration_answers.json`.

Run the Firestore concurrency test through the Firebase Emulator:

```bash
firebase emulators:exec --only firestore \
  'GCLOUD_PROJECT=dailyopic-test pytest tests/test_firestore_emulator.py'
```

Client Firestore rules deny every direct read and write. Cloud Run uses the Admin SDK and is not
subject to those client rules.
