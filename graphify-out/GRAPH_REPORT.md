# Graph Report - .  (2026-08-05)

## Corpus Check
- 60 files · ~56,518 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1023 nodes · 3095 edges · 45 communities (33 shown, 12 thin omitted)
- Extraction: 84% EXTRACTED · 16% INFERRED · 0% AMBIGUOUS · INFERRED: 500 edges (avg confidence: 0.54)
- Token cost: 186,104 input · 0 output

## Community Hubs (Navigation)
- Pydantic API Models
- FastAPI Route Handlers
- Subscription Plan Tiers
- App Config and Audio Metrics
- RevenueCat Client
- AdMob SSV Verification
- Model Validation Test Rationale
- In-Memory State Store
- State Store Interface
- Firestore State Store
- Difficulty Adjustment Tests
- Usage Quota Reservation
- Question Style Tests
- Fallback Question Generator
- Question History Normalization
- Extraction Spec Rules
- Backend Architecture Conventions
- Two-Track Extraction Pipeline
- Query and Setup Steps
- Deploy Verification and Secrets
- CI/CD Test and Deploy
- Repo Clone and Merge Flow
- Clustering and Graph Guards
- Firestore Data and Quota Policy
- Branch and PR Conventions
- Auth and AI Fallback Policy
- Incremental Update and Manifest
- Prohibitions and SSV Logging
- Honesty and Cost Audit
- Query Subcommands
- Onboarding and Collaboration
- Enum Input Normalization
- Operation Idempotency Tests
- Keyed Lock Pool
- Graph DB Exports
- Issue Template Convention
- App Package Init
- Commit Message Convention
- Code Review Criteria
- GraphML Export
- SVG Export
- HTML and Obsidian Export
- Server Package Root

## God Nodes (most connected - your core abstractions)
1. `QuestionStyle` - 84 edges
2. `AIService` - 75 edges
3. `DifficultyAdjustment` - 74 edges
4. `GeneratedQuestion` - 66 edges
5. `FallbackQuestionGenerator` - 65 edges
6. `QuestionPatternRepository` - 62 edges
7. `InMemoryStateStore` - 60 edges
8. `FirestoreStateStore` - 55 edges
9. `BackgroundProfile` - 54 edges
10. `OPIcLevel` - 51 edges

## Surprising Connections (you probably didn't know these)
- `Test Rules (pytest + Firestore emulator)` --semantically_similar_to--> `PR Test Checklist`  [INFERRED] [semantically similar]
  CONTRIBUTING.md → .github/pull_request_template.md
- `Manual Cloud Run Deploy Fallback` --semantically_similar_to--> `CI Job: Build, Push, and Deploy`  [INFERRED] [semantically similar]
  README.md → .github/workflows/deploy-cloud-run.yml
- `graphify Knowledge Graph Workflow` --semantically_similar_to--> `New Developer Onboarding Checklist`  [INFERRED] [semantically similar]
  CLAUDE.md → README_COLLABORATION.md
- `Branch Naming Convention (type/be-issue-description)` --semantically_similar_to--> `Branch Convention (main protection + feature branches)`  [INFERRED] [semantically similar]
  CONTRIBUTING.md → README_COLLABORATION.md
- `Conventional Commits Convention` --semantically_similar_to--> `Commit Convention (Conventional Commits, detailed types)`  [INFERRED] [semantically similar]
  CONTRIBUTING.md → README_COLLABORATION.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Two-track Extraction Pipeline (AST + Semantic + Merge)** — _claude_skills_graphify_skill_step3_extraction, _claude_skills_graphify_skill_part_a_ast_extraction, _claude_skills_graphify_skill_part_b_semantic_extraction, _claude_skills_graphify_skill_semantic_cache, _claude_skills_graphify_skill_part_c_merge [EXTRACTED 1.00]
- **Graph Integrity and Honesty Guards** — _claude_skills_graphify_skill_honesty_rules, _claude_skills_graphify_skill_shrink_guard, _claude_skills_graphify_skill_empty_graph_guard, _claude_skills_graphify_skill_step45_health_check, _claude_skills_graphify_skill_audit_trail, _claude_skills_graphify_skill_manifest_stamping [INFERRED 0.85]
- **Self-improving Query Feedback Loop** — _claude_skills_graphify_references_query_query_flow, _claude_skills_graphify_references_query_vocab_expansion, _claude_skills_graphify_references_query_save_result, _claude_skills_graphify_references_query_work_memory, _claude_skills_graphify_references_update_incremental_update [EXTRACTED 1.00]
- **AdMob Rewarded SSV Verification and Reward Grant Flow** — readme_admob_ssv_callback, readme_collaboration_ssv_verification_items, readme_collaboration_ssv_state_transition, readme_usage_policy, _github_pull_request_template_ssv_success_logs, readme_admob_ad_unit_suffix_tolerance [INFERRED 0.85]
- **Test-Gated Cloud Run Deploy with Health-Verified Traffic Promotion** — _github_workflows_deploy_cloud_run_test, _github_workflows_deploy_cloud_run_firestore_emulator, _github_workflows_deploy_cloud_run_deploy, _github_workflows_deploy_cloud_run_no_traffic_candidate_revision, _github_workflows_deploy_cloud_run_verify_health_endpoint, _github_workflows_deploy_cloud_run_promote_verified_revision [EXTRACTED 1.00]
- **Credit Safety Invariants (reserve, idempotency, KST reset, rollback on failure)** — readme_collaboration_practice_quota, readme_collaboration_idempotency_key, readme_collaboration_kst_date_basis, readme_collaboration_exception_handling, readme_collaboration_firestore_transaction_rules, readme_collaboration_usecase_flow [INFERRED 0.85]

## Communities (45 total, 12 thin omitted)

### Community 0 - "Pydantic API Models"
Cohesion: 0.08
Nodes (78): AudioMetrics, BackgroundProfile, BackgroundSurvey, ConfidenceBand, EvaluationScores, ExamSection, GeneratedQuestion, MockEvaluation (+70 more)

### Community 1 - "FastAPI Route Handlers"
Cohesion: 0.07
Nodes (99): alias, adjust_mock_session(), admob_ssv(), apply_question_set_adjustment(), _audio_number(), capabilities(), _create_daily_pool(), create_mock_exam() (+91 more)

### Community 2 - "Subscription Plan Tiers"
Cohesion: 0.05
Nodes (46): RewardPurpose, AnalysisDepth, FeatureTier, is_paid(), Plan, plan_from_entitlement_ids(), PlanLimits, StrEnum (+38 more)

### Community 3 - "App Config and Audio Metrics"
Cohesion: 0.06
Nodes (42): get_settings(), model_validator, Settings, endpoint_rate_limit(), lifespan(), request_telemetry(), AudioMetricsService, AudioValidationError (+34 more)

### Community 4 - "RevenueCat Client"
Cohesion: 0.10
Nodes (57): Any, datetime, RuntimeError, Safe-to-log RevenueCat API failure metadata without response or identity data., RevenueCatAPIError, RevenueCatClient, RevenueCatCustomerInfo, AsyncClient (+49 more)

### Community 5 - "AdMob SSV Verification"
Cohesion: 0.08
Nodes (47): AdMobSSVVerifier, ValueError, SSVVerificationError, VerifiedReward, EllipticCurvePrivateKey, LogCaptureFixture, _private_key(), _public_pem() (+39 more)

### Community 6 - "Model Validation Test Rationale"
Cohesion: 0.04
Nodes (25): QuestionStyle: 성공 케이스 (ValueError 발생 안 함), TC-QS-001: 직접값 'description, TC-QS-002: 직접값 'routine, TC-QS-003: 직접값 'past_experience, TC-QS-004: 직접값 'comparison, TC-QS-005: 직접값 'roleplay, TC-QS-006: 직접값 'problem_solving, TC-QS-007: 직접값 'opinion (+17 more)

### Community 7 - "In-Memory State Store"
Cohesion: 0.08
Nodes (28): InMemoryStateStore, InvalidSessionTransition, fixture, app_test_runtime(), MonkeyPatch, asyncio, test_mock_session_enforces_ordered_server_stages(), _legacy_question_set() (+20 more)

### Community 8 - "State Store Interface"
Cohesion: 0.08
Nodes (7): _coerce_datetime(), Any, datetime, 오늘 진행 중(미완료) 모의고사 세션(없으면 None)., 완료된 모의고사 세션 수. date_key=None이면 전체 기간(무료 평생 체험 판정용)., 사용자 상태와 이벤트 completed 마커를 함께 저장한다. 신규 완료면 True, 이미 완료된 이벤트면 False., StateStore

### Community 9 - "Firestore State Store"
Cohesion: 0.09
Nodes (13): FirestoreStateStore, asyncio, test_firestore_adjustment_removes_legacy_question_set_fields(), test_firestore_iap_sync_is_atomic_idempotent_and_rejects_stale_state(), test_firestore_legacy_question_set_is_normalized_on_read(), test_firestore_mock_session_stage_transition_is_atomic(), test_firestore_operation_lease_allows_only_one_parallel_owner(), test_firestore_pending_reward_does_not_change_quota_and_ssv_is_idempotent() (+5 more)

### Community 10 - "Difficulty Adjustment Tests"
Cohesion: 0.09
Nodes (16): DifficultyAdjustment, Enum Alias 및 정규화 로직 테스트 DifficultyAdjustment와 QuestionStyle의 _missing_() 메서드 동작…, DifficultyAdjustment: 성공 케이스 (ValueError 발생 안 함), TC-DA-001: 직접값 'easier, TC-DA-003: 직접값 'harder, TC-DA-004: alias 'similar' (소문자), TC-DA-005: alias 'SIMILAR' (대문자), TC-DA-006: alias 'Similar' (혼합 대소문자) (+8 more)

### Community 11 - "Usage Quota Reservation"
Cohesion: 0.11
Nodes (12): IdempotencyConflict, _KeyedLockEntry, RuntimeError, 쿼터 미차감 요청 멱등 예약(토큰 모델에서 평가는 쿼터를 쓰지 않음)., RequestAlreadyProcessing, Reservation, _reward_count_key(), RewardNotVerified (+4 more)

### Community 12 - "Question Style Tests"
Cohesion: 0.11
Nodes (11): QuestionStyle, TC-QS-011: alias ' descriptive ' (공백), TC-QS-021: alias 'problemsolving' (공백 없음), QuestionStyle: 실패 케이스 (ValueError 발생), TC-QS-F01: 존재하지 않는 값 'unknown, TC-QS-F02: 존재하지 않는 값 'description_but_longer, TC-QS-F03: 오타 'descriptiv, TC-QS-F04: 복수형 'past_experiences' (지원 안 함) (+3 more)

### Community 14 - "Question History Normalization"
Cohesion: 0.11
Nodes (16): ABC, AdjustmentAlreadyApplied, _counts_toward_daily_reward_quota(), _is_firestore_contention(), _merge_question_history(), _mode_matches(), _normalize_legacy_question(), _normalize_legacy_question_set() (+8 more)

### Community 15 - "Extraction Spec Rules"
Cohesion: 0.12
Nodes (17): Call Edge Direction and Language Purity Rule, DEEP_MODE Aggressive Inference, file_type Taxonomy, YAML Frontmatter Provenance Propagation, Hyperedge Extraction Rule, Node ID Format Rule, Rationale as Node Attribute, Semantic Similarity Edges (+9 more)

### Community 16 - "Backend Architecture Conventions"
Cohesion: 0.15
Nodes (14): AI Usage Principles (no key on iOS, store:false, no raw audio), API Versioning (/v1 prefix and endpoint list), FFmpeg Audio Metrics Extraction (speech time, silence ratio, WPM), API Breaking Change Policy, Target Directory Structure (api/core/models/services/usecases), Canonical Error Code Set, Unified Error Response Format (code/message/requestId), Exception Handling and Rollback Convention (+6 more)

### Community 17 - "Two-Track Extraction Pipeline"
Cohesion: 0.23
Nodes (12): Watch Debounce Window, needs_update Flag for Semantic Changes, Watch Mode Auto-rebuild, Post-commit Auto-rebuild Hook, Work Memory and LESSONS.md Reflection, Code-only Change Shortcut, Gemini Semantic Extraction Backend, No API Key Required Rule (+4 more)

### Community 18 - "Query and Setup Steps"
Cohesion: 0.18
Nodes (12): MCP Stdio Server, Token Reduction Benchmark, BFS and DFS Traversal Modes, Inline NetworkX Traversal Fallback, Query Traversal Flow, Token-budget Aware Subgraph Output, Corpus Size Gate and Narrowing Prompt, Python Interpreter Detection (+4 more)

### Community 19 - "Deploy Verification and Secrets"
Cohesion: 0.18
Nodes (11): Verifying Backend Without iOS Simulator (hotspot IP / HTTPS tunnel), PR Test Checklist, No-Traffic Candidate Revision Deploy, Promote Verified Revision Step, Cloud Run Runtime Service Account (dailyopic-cloudrun), Verify Health Endpoint Step, Pre-Deployment Checklist, Secret Management Rules (no commit, Secret Manager in prod) (+3 more)

### Community 20 - "CI/CD Test and Deploy"
Cohesion: 0.22
Nodes (11): CI Job: Build, Push, and Deploy, Deploy Backend to Cloud Run Workflow, CI Job: Firestore Emulator Tests, GCP_SA_KEY Service Account JSON Authentication, CI Job: Test (pytest excluding emulator tests), Test Rules (pytest + Firestore emulator), GitHub Actions CI/CD Pipeline Description, Test Type Taxonomy (unit/api/state/emulator/fixture/contract) (+3 more)

### Community 21 - "Repo Clone and Merge Flow"
Cohesion: 0.20
Nodes (10): graphify Slash Command Trigger, source_file Verbatim Rule, GitHub Repo Clone, Cross-repo Graph Merge, Monorepo Per-subfolder Extract and Merge, Native CLAUDE.md Integration, build_merge Replace-on-Re-extract, prune_sources Deletion Pruning (+2 more)

### Community 22 - "Clustering and Graph Guards"
Cohesion: 0.20
Nodes (10): Wiki Export, Cluster-only Rerun, Community Detection and Cohesion Scoring, Empty Graph Write Guard, root= source_file Relativization, graph.json Shrink Guard, Step 4 Build Graph, Cluster, Analyze, Step 5 Label Communities (+2 more)

### Community 23 - "Firestore Data and Quota Policy"
Cohesion: 0.24
Nodes (10): Async Convention (asyncio.to_thread for blocking SDKs), Firestore Transaction Rules (read-before-write, no external calls inside), Firestore TTL and Replay Sentinel Expiry, KST (Asia/Seoul) YYYYMMDD Usage Date Basis, Practice Quota Reserve-Confirm-Refund Model, AdMob SSV Reward State Transition (pending/verified/consumed/expired), Firestore Collections and TTL, Reward Ad Revenue vs AI Cost Unit Economics (+2 more)

### Community 24 - "Branch and PR Conventions"
Cohesion: 0.25
Nodes (8): Change Impact Scope (Backend/iOS/Firestore/Auth/AdMob/Docker), Backend Pull Request Template, Issue-to-Merge Basic Workflow, Branch Naming Convention (type/be-issue-description), PR Title and Content Convention, One-Purpose PR Size Rule, Branch Convention (main protection + feature branches), Definition of Done for Backend Work

### Community 25 - "Auth and AI Fallback Policy"
Cohesion: 0.25
Nodes (8): Backend Responsibility Boundary, AI Fallback Policy (generation only, never production evaluation), Idempotency-Key Rules, User Data Retention Policy (24h idempotency cache only), Environment Policy (dev/prod differ only by MOCK_AI), Keychain UUID Identity via X-DailyOPIc-User-ID, MOCK_AI Catalog Fallback Mode, Protected Endpoint Headers (User ID, App Check, Idempotency-Key)

### Community 26 - "Incremental Update and Manifest"
Cohesion: 0.48
Nodes (7): URL Ingest into Corpus, Whisper Video and Audio Transcription, Graph Diff Report, Incremental Update Flow, graphify Pipeline, Semantic Manifest Stamping Gate, Step 9 Manifest, Cost Tracker, Cleanup

### Community 27 - "Prohibitions and SSV Logging"
Cohesion: 0.29
Nodes (7): SSV Success Log Markers, Prohibited Actions (main push, secret commit, unverified reward), AdMob ad_unit Numeric Suffix Tolerance, AdMob SSV Callback URL Configuration, Log-Prohibited Data vs Allowed Log Fields, Prohibited Actions Without Approval, SSV Callback Mandatory Verification Items

### Community 28 - "Honesty and Cost Audit"
Cohesion: 0.40
Nodes (5): Discrete Confidence Score Rubric, Constrained Query Vocabulary Expansion, EXTRACTED/INFERRED/AMBIGUOUS Audit Trail, Cumulative Token Cost Tracker, Honesty Rules

### Community 29 - "Query Subcommands"
Cohesion: 0.40
Nodes (5): graphify explain Node Explanation, graphify path Shortest Path, save-result Feedback Loop, Self-composed Whisper Domain Hint, God Nodes

### Community 30 - "Onboarding and Collaboration"
Cohesion: 0.50
Nodes (5): graphify Knowledge Graph Workflow, DailyOPIc-BE Collaboration Guide (CONTRIBUTING), DailyOPIc-BE Collaboration Convention (detailed), New Developer Onboarding Checklist, DailyOPIc Backend README

### Community 32 - "Operation Idempotency Tests"
Cohesion: 0.83
Nodes (3): asyncio, test_same_operation_id_with_different_payload_conflicts(), test_twenty_parallel_operation_reservations_allow_one_owner()

## Knowledge Gaps
- **34 isolated node(s):** `dailyopic-server`, `Step 0 GitHub Repos and Multi-path Merge`, `Gemini Semantic Extraction Backend`, `Step 6 HTML and Obsidian Export`, `Surprising Connections` (+29 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **12 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `DifficultyAdjustment` connect `Difficulty Adjustment Tests` to `Pydantic API Models`, `FastAPI Route Handlers`, `Keyed Lock Pool`, `Model Validation Test Rationale`, `In-Memory State Store`, `State Store Interface`, `Firestore State Store`, `Usage Quota Reservation`, `Question Style Tests`, `Fallback Question Generator`, `Question History Normalization`, `Enum Input Normalization`?**
  _High betweenness centrality (0.134) - this node is a cross-community bridge._
- **Why does `QuestionStyle` connect `Question Style Tests` to `Pydantic API Models`, `FastAPI Route Handlers`, `App Config and Audio Metrics`, `Model Validation Test Rationale`, `Difficulty Adjustment Tests`, `Fallback Question Generator`, `Enum Input Normalization`?**
  _High betweenness centrality (0.097) - this node is a cross-community bridge._
- **Why does `RewardPurpose` connect `Subscription Plan Tiers` to `Pydantic API Models`, `FastAPI Route Handlers`, `Keyed Lock Pool`, `In-Memory State Store`, `State Store Interface`, `Firestore State Store`, `Usage Quota Reservation`, `Question History Normalization`?**
  _High betweenness centrality (0.079) - this node is a cross-community bridge._
- **Are the 24 inferred relationships involving `QuestionStyle` (e.g. with `AIMockResult` and `AIPracticeResult`) actually correct?**
  _`QuestionStyle` has 24 INFERRED edges - model-reasoned connections that need verification._
- **Are the 23 inferred relationships involving `AIService` (e.g. with `AudioMetrics` and `BackgroundProfile`) actually correct?**
  _`AIService` has 23 INFERRED edges - model-reasoned connections that need verification._
- **Are the 36 inferred relationships involving `DifficultyAdjustment` (e.g. with `AIMockResult` and `AIPracticeResult`) actually correct?**
  _`DifficultyAdjustment` has 36 INFERRED edges - model-reasoned connections that need verification._
- **Are the 20 inferred relationships involving `GeneratedQuestion` (e.g. with `AIMockResult` and `AIPracticeResult`) actually correct?**
  _`GeneratedQuestion` has 20 INFERRED edges - model-reasoned connections that need verification._