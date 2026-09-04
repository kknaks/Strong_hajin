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
| ACCESS_GRANT | `access_grants` | `scope_kind`, `scope_ref`, `include_descendants`, `role_id`+`role_capability_version` snapshot, `origin_rule_id/version`, 독립 `revoked_at`. 판정은 유효 grant만 본다: role grant는 `role_capabilities.mapping_version <= role_capability_version`인 매핑만 적용(snapshot), `capability_id` grant는 그대로 적용. `origin_rule_id`가 있는 grant는 같은 role의 유효 appointment가 있을 때만 효력(보직 종료 → 표준 부여 종료). `GET /api/organization/me`가 `grants[]`를 projection한다 |
| RESOURCE_RELATIONSHIP | `resource_relationships` | WorkRequest 생성 시 requester·assignee·cc 관계를 기록. cc 구성원은 요청 목록·timeline·댓글·첨부 열람이 가능하고 판단은 못 한다(`cc_member_ids`, `GET /api/work-request-cc-candidates`) |

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
| TASK | `tasks` | `description`, `start_date`, `due_date`, `organization_unit_id`(귀속), `origin_kind`(direct/request_effect/assignment), `visibility`, lineage(`request_thread_id`, `source_work_request_id`, `source_decision_item_id`, `source_submission_id`, `source_review_decision_id`, `source_action_item_id`, `source_task_id`) |
| TASK_ASSIGNMENT | `task_assignments` | 모든 Task에 1행 이상: `assignment_kind` self(직접 생성)·request_effect(요청 수락)·direct(관리자 배정), `status` pending/active/declined/superseded, `assigned_by`, source refs. 내 업무·업무 조작은 **active assignment**를 통해서만 가능하다. direct 배정은 `task.assign` capability(팀장·관리자)가 자기 조직(하위 포함) 구성원에게 하고, `task.assignment.acceptance` DecisionItem(+Submission·ReviewAssignment)이 열려 assignee가 수락/거절한다. 거절은 사유가 필수이고 Task를 cancelled로 닫는다. API: `POST /api/tasks/assign`, `GET /api/task-assignment-candidates`, `GET /api/task-assignments/inbox|sent`, `POST /api/task-assignments/{id}/accept|decline`; MCP `task_assign`, `task_assignment_inbox|accept|decline` |
| TRIGGER / ACTION / AGENT_RUN / TOOL_DEFINITION / ACTION_TOOL_BINDING | `conversations`, `conversation_turns`, `tool_invocations`, `provider_calls`, `action_items`(AX 제안) | **Delta**: PGMQ Conversation/Turn 모델로 대체. Action은 `action_items`(제안→승인→실행 결과), Tool은 persona-bound MCP discovery |
| WORKFLOW_DEFINITION / VERSION / RUN | `workflow_definitions`, `workflow_definition_versions`, `workflow_runs`, `workflow_node_executions` | 개인 일일보고 생성만 사용 |
| WORKFLOW_SCOPE | — | future direction(ERD §3) |

Projections: `GET /api/work-requests/{id}/timeline`(request_timeline: 회차·판단·배정·댓글·활동), `PATCH /api/tasks/{id}`(`task.update`), `POST /api/work-requests/{id}/resubmit`(재상신).

## 논의·자료·감사

| ERD | 테이블 | 비고 |
|---|---|---|
| COMMENT | `comments` | RequestThread 논의, 상태 전이 없음 |
| ATTACHMENT | `attachments` | `source_kind` file/link, `source_ref`(storage key), `integrity_ref` sha256, `provenance`, `lifecycle` |
| ATTACHMENT_BINDING | `attachment_bindings` | context task/comment/submission × role input/output/discussion/supplemental. Task 자료 API(`/api/tasks/{id}/materials`)는 task 바인딩의 projection, 댓글 첨부(`POST /api/work-requests/{id}/comments/{comment_id}/attachments`, 작성자만)는 comment×discussion, 근거 파일은 submission×supplemental 바인딩 |
| EVIDENCE | `evidence` | `POST /api/work-requests/{id}/evidence`(요청자·담당자만)로 현재 Submission에 채택. `evidence_role`은 담당자면 decision_basis, 요청자면 supporting. `fixed_snapshot_ref`=attachment sha256, `mutable_source=false`. timeline `evidence[]`로 회차별 노출, 참여자(cc 포함)는 `GET /api/work-requests/{id}/attachments/{attachment_id}/content`로 열람 |
| ACTIVITY_EVENT | `activity_events` | append-only, `safe_summary`·before/after ref·actor·reason. Task 생성/상태/수정/자료/배정/배정 수락·거절, WorkRequest 생성/판단/재상신이 기록 |
| (기존) | `task_activities`, `work_request_audit_events`, `action_item_audit_events`, `report_audit_events` | 첫 slice의 module 감사 로그. `task_activities`는 일일보고 source_refs가 참조하므로 유지 |

## ERD 밖 테이블

- `auth_sessions`: 로그인 세션(HttpOnly cookie).
- `daily_reports`, `report_drafts`, `daily_report_submissions`: 보고 SPEC 영역.
- `workflow_definitions`, `workflow_definition_versions`, `workflow_runs`, `workflow_node_executions`, `provider_calls`: 개인 일일보고 생성 runtime.

기술 spike 잔재(`work_records`, `contract_approvals`, `meeting_evidence`, run 기반 `task_assignments`, generic workflow catalog/run/decision API, `technical_spike` 모드)는 2026-09-04에 제거했다.

## 남은 Delta 결정

1. Task 재배정(superseded)·배정 위임 정책, 배정 협의(negotiate) 여부.
2. Evidence를 외부 링크(`source_kind=link`, `mutable_source=true`)로 채택하는 경로.
3. ERD 문서(mediness) §3·§4의 WorkflowRun 전제와 이 구현의 Conversation 실행 모델 차이는 ERD §10 구현 Delta로 기록했다. SPEC-001/002 본문 동기화는 후속.
