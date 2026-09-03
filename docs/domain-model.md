# Domain model ↔ SCAX Logical ERD

기준: mediness `products/sc-ax/40-architecture/erd.md`(SCAX-ERD, 2026-08-24). 아래 표는 ERD entity가 이 저장소의 어느 테이블·모듈로 구현됐는지와 의도적으로 다르게 간 항목을 기록한다. 물리 이름·type·index는 이 저장소가 소유한다(ERD §9).

## 조직·사람·권한 (Organization & Access)

| ERD | 테이블 | 비고 |
|---|---|---|
| MEMBER | `members` | `account_ref`는 로그인 계정 참조(developer provider: `developer:<id>`), `record_status` |
| EMPLOYMENT_PERIOD | `employment_periods` | 한 시점 열린 재직 기간 1개; 모든 권한 판정이 유효 재직을 선행 요구 |
| ORGANIZATION_UNIT_TYPE | `organization_unit_types` | 회사·본부·실·팀·파트. 깊이와 종류는 별개 |
| ORGANIZATION_UNIT | `organization_units` | `parent_id` 자기 참조, `unit_type_id`, `lifecycle`, `abolished_at`, `display_order` |
| MEMBERSHIP | `memberships` | `membership_kind` primary/additional(겸직) |
| POSITION_DEFINITION | `position_definitions` | 조직 유형별 허용 보직, `slot_key`로 배타 자리 표현 |
| APPOINTMENT | `appointments` | `position_definition_id` + 함께 붙은 표준 역할 `role_id`, `appointment_kind` primary/acting |
| GRADE / GRADE_ASSIGNMENT | `grades`, `grade_assignments` | 직급 |
| JOB / JOB_ASSIGNMENT | `jobs`, `job_assignments` | 직무, primary/additional |
| ROLE / CAPABILITY / ROLE_CAPABILITY | `roles`, `capabilities`, `role_capabilities` | role `sensitivity`·`lifecycle`, capability `group`·`description` |
| STANDARD_GRANT_RULE | `standard_grant_rules` | 임명 시 붙는 표준 역할과 scope 템플릿 |
| ACCESS_GRANT | `access_grants` | `scope_kind`, `scope_ref`, `include_descendants`, `role_id`+`role_capability_version` snapshot, `origin_rule_id/version`, 독립 `revoked_at`. **Delta**: 현재 판정은 grant의 `capability_id`를 직접 평가한다(role snapshot 평가는 후속) |
| RESOURCE_RELATIONSHIP | `resource_relationships` | 테이블 준비. 참조자(cc)·연결 UI는 후속 |

Projection: `GET /api/organization/tree`, `GET /api/organization/units/{id}/members`(하위 조직 포함, 재직자만), `GET /api/organization/me`.

## 판단·요청 연속성·업무 (Work)

| ERD | 테이블 | 비고 |
|---|---|---|
| REQUEST_THREAD | `request_threads` | WorkRequest와 1:1, 댓글·회차·판단·파생 Task의 연속성 |
| WORK_REQUEST | `work_requests` | `request_thread_id`, `subject_id`, `organization_context_id`, `due_date`, `description`. **Delta**: `workflow_run_ref`를 두지 않는다(Decision Log 2026-09-03: 요청·판단은 WorkflowRun 없이 Work 모듈이 직접 처리) |
| SUBJECT / SUBJECT_VERSION | `subjects`, `subject_versions` | 요청 payload의 고정 버전(`content_hash`, `snapshot`) |
| ACTION_ITEM(사람 판단 항목) | `decision_items` | 이름만 다르다: `action_items`는 AX 채팅 제안 승인 카드가 이미 쓰고 있어 `decision_items`로 둔다. `kind=work_request.acceptance`, status open→awaiting_revision→resolved |
| SUBMISSION | `submissions` | 회차(`submission_version`), `revises_id`, `payload_hash`, `diff`, decision policy snapshot |
| REVIEW_ASSIGNMENT | `review_assignments` | Submission당 history, active 최대 1, `supersedes_assignment_id` |
| REVIEW_DECISION | `review_decisions` | accept/negotiate/reject를 assignment·submission·actor에 바인딩한 immutable 사실 |
| TASK | `tasks` | `description`, `start_date`, `due_date`, `organization_unit_id`(귀속), `origin_kind`(direct/request_effect), `visibility`, lineage(`request_thread_id`, `source_work_request_id`, `source_decision_item_id`, `source_submission_id`, `source_review_decision_id`, `source_action_item_id`, `source_task_id`) |
| TASK_ASSIGNMENT | `work_request_task_assignments`(요청 수락), `task_assignments`(workflow spike) | **Delta**: 직접 생성 업무는 owner=assignee로 두고 별도 assignment row를 만들지 않는다. 관리자 직접 배정·배정 수락 ActionItem은 후속 |
| TRIGGER / ACTION / AGENT_RUN / TOOL_DEFINITION / ACTION_TOOL_BINDING | `conversations`, `conversation_turns`, `tool_invocations`, `provider_calls`, `action_items`(AX 제안) | **Delta**: PGMQ Conversation/Turn 모델로 대체. Action은 `action_items`(제안→승인→실행 결과), Tool은 persona-bound MCP discovery |
| WORKFLOW_DEFINITION / VERSION / RUN | `workflow_definitions`, `workflow_definition_versions`, `workflow_runs`, `workflow_node_executions` | 개인 일일보고 생성만 사용 |
| WORKFLOW_SCOPE | — | future direction(ERD §3) |

Projections: `GET /api/work-requests/{id}/timeline`(request_timeline: 회차·판단·배정·댓글·활동), `PATCH /api/tasks/{id}`(`task.update`), `POST /api/work-requests/{id}/resubmit`(재상신).

## 논의·자료·감사

| ERD | 테이블 | 비고 |
|---|---|---|
| COMMENT | `comments` | RequestThread 논의, 상태 전이 없음 |
| ATTACHMENT | `attachments` | `source_kind` file/link, `source_ref`(storage key), `integrity_ref` sha256, `provenance`, `lifecycle` |
| ATTACHMENT_BINDING | `attachment_bindings` | context task/comment/submission × role input/output/discussion/supplemental. Task 자료 API(`/api/tasks/{id}/materials`)는 이 바인딩의 projection |
| EVIDENCE | `evidence` | 테이블 준비. Submission에 근거 채택하는 API·UI는 후속 |
| ACTIVITY_EVENT | `activity_events` | append-only, `safe_summary`·before/after ref·actor·reason. Task 생성/상태/수정/자료, WorkRequest 생성/판단/재상신이 기록 |
| (기존) | `task_activities`, `work_request_audit_events`, `action_item_audit_events`, `report_audit_events` | 첫 slice의 module 감사 로그. `task_activities`는 일일보고 source_refs가 참조하므로 유지 |

## ERD 밖 테이블

- `auth_sessions`: 로그인 세션(HttpOnly cookie).
- `daily_reports`, `report_drafts`, `daily_report_submissions`: 보고 SPEC 영역.
- `work_records`, `contract_approvals`, `meeting_evidence`, `task_assignments`: 기술 spike 잔재(`technical_spike` 모드 전용). 다음 정리 대상.

## 남은 Delta 결정

1. `access_grants` role snapshot 기반 판정으로 전환할지(현재 capability 직접 grant + appointment role 파생).
2. 직접 생성 Task에도 TaskAssignment row를 둘지, 관리자 직접 배정·배정 수락 ActionItem.
3. Evidence 채택·ResourceRelationship(참조자) UI, Comment 첨부 바인딩.
4. ERD 문서(mediness)에 WorkflowRun-less 요청과 Conversation 실행 모델 Delta 반영.
