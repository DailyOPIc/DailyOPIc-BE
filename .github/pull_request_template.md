## 작업 내용

- [ ] ...

## 추가 설명

- ...

## 관련 이슈

- close #

## 변경 영향 범위

- [ ] Backend
- [ ] iOS
- [ ] Firestore
- [ ] Auth/App Check
- [ ] AdMob SSV
- [ ] Docker/배포
- [ ] 문서
- [ ] 기타:

## 비고

- ...

---

## 테스트

<details>
<summary>테스트 체크리스트</summary>

- [ ] `.venv/bin/python -m pytest -q`
- [ ] 변경 범위별 테스트를 실행했거나 생략 사유 작성
- [ ] Docker/배포 설정 변경 시 `docker build -f DailyOPIc-BE/Dockerfile -t dailyopic-api .`
- [ ] Firestore transaction/schema 변경 시 emulator 테스트
- [ ] iOS 계약 변경 시 iOS 모델/APIClient/build 영향 확인

</details>

<details>
<summary>Backend Only 테스트 방법</summary>

- 기본 회귀: `.venv/bin/python -m pytest -q`
- API/사용량/리워드: `.venv/bin/python -m pytest tests/test_api.py tests/test_state.py -q`
- AI/문제 구조/난이도: `.venv/bin/python -m pytest tests/test_ai.py tests/test_question_structure.py tests/test_difficulty.py -q`
- Auth/App Check: `.venv/bin/python -m pytest tests/test_auth.py -q`
- AdMob SSV: `.venv/bin/python -m pytest tests/test_admob.py -q`
- 로컬 서버 확인: `uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`, `/health`, `/docs`

</details>

<details>
<summary>iOS 에뮬레이터 없이 확인하는 법</summary>

- 백엔드 단독 PR은 pytest와 `/health`, `/docs` 확인만으로 기본 검증 가능
- 실제 앱 연동은 iOS 시뮬레이터 대신 실기기 Debug 빌드 사용
- 일반 API만 확인: 앱 Debug base URL을 `http://<Mac 핫스팟 IP>:8000`로 설정
- AdMob SSV까지 확인: Cloudflare/ngrok HTTPS 터널을 열고 앱 base URL과 AdMob SSV callback URL을 같은 public host로 설정
- SSV 성공 로그: `[SSV] callback received`, `[SSV] reward verified`, `[SSV] reward completed`

</details>

## 스크린샷 / 로그

- 필요 시 첨부

## PR 체크리스트

- [ ] API 요청/응답 변경 시 iOS와 문서 영향 작성
- [ ] 개인 URL, API key, 로컬 config, 서비스 계정 파일이 커밋되지 않았는지 확인
- [ ] 불필요한 로그, 디버그 코드, 임시 print 제거
- [ ] 머지 후 브랜치 삭제
