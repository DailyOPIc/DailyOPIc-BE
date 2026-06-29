## 작업 요약

- 

## 변경 사항

- 

## 테스트

- [ ] `.venv/bin/python -m pytest -q`
- [ ] 변경 범위별 테스트를 실행했거나 생략 사유 작성
- [ ] Docker/배포 설정 변경 시 `docker build -f DailyOPIc-BE/Dockerfile -t dailyopic-api .`
- [ ] Firestore transaction/schema 변경 시 emulator 테스트
- [ ] iOS 계약 변경 시 iOS 모델/APIClient/build 영향 확인

## Backend Only 테스트 방법

- 기본 회귀: `.venv/bin/python -m pytest -q`
- API/사용량/리워드: `.venv/bin/python -m pytest tests/test_api.py tests/test_state.py -q`
- AI/문제 구조/난이도: `.venv/bin/python -m pytest tests/test_ai.py tests/test_question_structure.py tests/test_difficulty.py -q`
- Auth/App Check: `.venv/bin/python -m pytest tests/test_auth.py -q`
- AdMob SSV: `.venv/bin/python -m pytest tests/test_admob.py -q`
- 로컬 서버 확인: `uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`, `/health`, `/docs`

## iOS 에뮬레이터 없이 확인하는 법

- 백엔드 단독 PR은 pytest와 `/health`, `/docs` 확인만으로 기본 검증 가능
- 실제 앱 연동은 iOS 시뮬레이터 대신 실기기 Debug 빌드 사용
- 일반 API만 확인: 앱 Debug base URL을 `http://<Mac 핫스팟 IP>:8000`로 설정
- AdMob SSV까지 확인: Cloudflare/ngrok HTTPS 터널을 열고 앱 base URL과 AdMob SSV callback URL을 같은 public host로 설정
- SSV 성공 로그: `[SSV] callback received`, `[SSV] reward verified`, `[SSV] reward completed`

## 변경된 계약/설정

- API:
- Firestore:
- 환경변수:
- AdMob/Firebase:
- iOS 영향:

## PR 체크리스트

- [ ] API 요청/응답 변경 시 iOS와 문서 영향 작성
- [ ] Firestore 컬렉션/필드/TTL 변경 시 문서와 테스트 업데이트
- [ ] 환경변수 변경 시 `.env.example`, README, Docker/Cloud Run 영향 업데이트
- [ ] quota/reward 변경 시 무료 quota, bonus token, rollback, idempotency 테스트 확인
- [ ] AdMob SSV 변경 시 URL verification, signature, ad unit, transaction replay 테스트 확인
- [ ] App Check/Auth 변경 시 `X-DailyOPIc-User-ID`, `X-Firebase-AppCheck` 필수 검증 유지
- [ ] AI 변경 시 `MOCK_AI=true/false`, 중복 방지, 난이도 경계값 테스트 확인
- [ ] Self Assessment 변경 시 `1 + easier = 1`, `6 + harder = 6` 확인
- [ ] 개인 URL, API key, 로컬 config, 서비스 계정 파일이 커밋되지 않았는지 확인
