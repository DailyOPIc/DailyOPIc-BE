# DailyOPIc-BE 협업 가이드

백엔드 작업에 필요한 최소 협업 규칙입니다. 자세한 운영/보안/아키텍처 설명은 `README.md`와 `README_COLLABORATION.md`를 참고합니다.

## 1. 기본 작업 흐름

1. 이슈를 먼저 만든다.
2. 이슈 번호 기준으로 브랜치를 만든다.
3. 작은 단위로 커밋한다.
4. 테스트를 통과시킨다.
5. PR을 만들고 리뷰를 받는다.
6. 승인 후 `main`에 머지한다.

`main` 브랜치에 직접 push하지 않습니다.

## 2. 브랜치 컨벤션

형식:

```text
<type>/be-<issue-number>-<short-description>
```

type:

```text
feat      기능 추가
fix       버그 수정
refactor  구조 개선
test      테스트 추가/수정
docs      문서 수정
chore     설정/의존성/빌드 작업
hotfix    운영 긴급 수정
```

예시:

```text
feat/be-12-practice-quota
fix/be-18-admob-ssv-replay
refactor/be-25-split-ai-service
docs/be-31-api-contract
```

## 3. 커밋 컨벤션

Conventional Commits 형식을 사용합니다.

```text
<type>(<scope>): <summary>
```

예시:

```text
feat(practice): add daily quota reservation
fix(admob): reject replayed ssv transaction
refactor(ai): split prompt builder from openai client
test(mock): cover reward nonce single-use flow
docs(readme): document cloud run env variables
```

규칙:

- 한 커밋은 하나의 논리적 변경만 포함합니다.
- 로직 변경과 단순 포맷팅을 섞지 않습니다.
- `.env`, API Key, Firebase credential, Secret 파일은 커밋하지 않습니다.

## 4. 이슈 작성 규칙

이슈 제목:

```text
[BE <Type>] <작업 요약>
```

예시:

```text
[BE Refactor] AI service를 기능별로 분리
[BE Fix] Rewarded Ad transaction replay 차단
[BE Docs] API 오류 코드 문서화
```

이슈에는 최소한 아래 내용을 작성합니다.

- 목적
- 현재 문제
- 작업 범위
- 완료 기준
- 검증 방법

템플릿 위치:

```text
.github/ISSUE_TEMPLATE/backend-task.md
```

## 5. PR 작성 규칙

PR 제목:

```text
[BE] <type>(<scope>): <summary>
```

예시:

```text
[BE] refactor(ai): split ai service modules
[BE] fix(admob): prevent ssv replay
[BE] docs(readme): add api contract
```

PR에는 최소한 아래 내용을 작성합니다.

- 작업 요약
- 변경 이유
- API 계약 변경 여부
- Firestore/환경변수 변경 여부
- 테스트 결과
- 리뷰 포인트

템플릿 위치:

```text
.github/pull_request_template.md
```

## 6. PR 크기 기준

권장:

- 하나의 PR은 하나의 목적만 가진다.
- 기능 변경과 대형 리팩토링을 섞지 않는다.
- API 계약 변경은 문서 수정과 함께 올린다.
- Firestore, 광고 보상, 사용량 제한 로직 변경은 테스트를 포함한다.

큰 작업은 아래처럼 나눕니다.

```text
1. 파일 이동/구조 분리
2. 내부 import 정리
3. 로직 이동
4. 테스트 보강
5. 문서 업데이트
```

## 7. 테스트 규칙

기본 검증:

```bash
pytest
```

Firestore transaction 변경 시:

```bash
firebase emulators:exec --only firestore \
  'GCLOUD_PROJECT=dailyopic-test pytest tests/test_firestore_emulator.py'
```

PR에는 실행한 테스트 명령어와 결과를 적습니다.

## 8. 리뷰 기준

리뷰에서 우선 확인할 내용:

- API request/response 계약이 깨지지 않았는가
- quota/reward/idempotency 흐름이 안전한가
- 실패 시 credit 또는 reward가 rollback 되는가
- 민감정보가 로그나 코드에 남지 않는가
- 테스트가 실패 케이스를 포함하는가
- README 또는 문서 수정이 필요한 변경인가

## 9. 금지 사항

- `main` 직접 push
- `.env` 또는 secret 파일 커밋
- OpenAI API Key를 iOS 또는 Git에 포함
- SSV 검증 없이 reward 지급
- error code 임의 변경
- iOS와 합의 없이 API 응답 필드 삭제/타입 변경

