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

## 판단 통합 (ActionItem) — 현재 상태

사람 판단이 필요한 경로는 업무 요청, 직접 배정, AX 변경 제안 세 가지이고, 셋 다 하나의 판단 계약을 쓴다.

- **하나의 질문 = 하나의 ActionItem.** 조정 요청과 재상신은 identity를 유지한 채 immutable Submission을 추가한다. 서로 독립적으로 판단·거절될 수 있는 질문만 새 ActionItem이다.
- **Query/Command 경로 하나.** `GET /api/action-items`(현재 principal이 답해야 하는 것만), `GET /api/action-items/{id}`(회차·diff·ReviewDecision), `POST /api/action-items/{id}/commands/{command}`.
- **Envelope는 server가 만든다.** `subject`, `operation_label`, `current_question`, 권한 안전 `preview`, `allowed_commands`(필요하면 `requires_reason`), `waiting_on`, `submission_version`, `resource`, 그리고 직전 조정이 남긴 `suggested_changes`. client는 kind로 command·필드·권한을 추론하지 않는다.
- **조정 요청은 사유가 필수, 변경 제안은 선택이다.** 담당자는 필수 사유에 더해 요청자가 실제로 고칠 수 있는 필드(제목·설명·기한)만 구조화해 제안할 수 있고, 그 제안은 해당 회차 ReviewDecision의 `conditions.changes`에 남는다. 제안은 편집이 아니다: 회차 snapshot은 요청자가 실제로 제출한 내용 그대로이고, 반영 여부는 요청자의 재상신으로만 결정된다.
- **논의는 판단을 움직이지 않는다.** 댓글 thread는 ActionItem 상세(`discussion`)에 회차와 무관하게 유지되며, 댓글을 쓰는 것으로는 status도 `waiting_on`도 바뀌지 않는다.
- **재전송은 영수증이다.** 이미 이 principal이 낸 답을 다시 보내면 두 번째 effect 없이 현재 envelope를 돌려준다(수락/거절/조정은 기록된 ReviewDecision, 재상신은 그가 제출한 최신 Submission, 철회는 withdrawn 상태가 근거다). 결정이 끝난 질문에 **다른** 답을 보내는 것은 여전히 거절된다.
- **Command는 소유 모듈의 application operation에 위임한다.** 수락/거절/조정/재상신/철회는 `WorkRequestApplication`, 배정 수락/거절은 `TaskAssignmentApplication`, AX 승인/거절은 `ActionApplication`이 실행한다. 권한은 envelope 자체다: server가 그 principal에게 제시하지 않은 command는 실행되지 않는다.
- **판단 API는 하나다.** kind별 판단 원장이었던 `GET /api/action-inbox`와 `GET /api/task-assignments/inbox`는 제거했다. 남은 kind별 endpoint(`POST /api/work-requests/{id}/accept|reject|negotiate|resubmit`, `POST /api/task-assignments/{id}/accept|decline`, `POST /api/actions/{id}/decide`)는 같은 application operation을 부르는 얇은 호출구다.
- **모든 command는 자기가 답하는 version을 함께 보낸다.** `expected_version`은 REST `ActionCommandRequest`와 MCP schema 모두에서 필수다. WorkRequest는 request version, AX 제안은 Action version, 배정은 envelope가 내보내는 Task version을 답한다(배정 자체에는 version column이 없다). 수락은 Task version을 그대로 두고 거절은 취소하며 정확히 1 올리므로, replay receipt도 그 관계와 저장된 사유가 정확히 맞을 때만 성립한다. fallback으로 현재 version을 끌어다 쓰는 경로는 없다.
- **MCP도 같은 경로를 쓴다.** persona-bound tool `action_item_list`·`action_item_get`·`action_item_command`가 `WorkflowApplication.pending_action_items`·`action_item_detail`·`run_action_command`를 그대로 부른다. adapter는 transport일 뿐이라 kind로 command를 추론하지 않고, persona·member·org·capability를 인자로 받지 않는다(persona는 process 설정이다). 노출은 capability로 갈린다: 읽을 수 있는 kind가 하나라도 있어야 list/get이, canonical handler가 요구하는 결정 capability가 있어야 command가 discovery에 나타나며, discovery 뒤 권한이 회수되면 실제 호출에서 application authorization이 거절한다. 반환은 structured output이고 annotation은 list/get이 read-only·idempotent·closed-world, command는 non-read-only·closed-world·idempotent(payload-aware receipt) 그리고 withdraw/reject/decline 가능성 때문에 보수적으로 destructive다.
- **위임 turn은 판단을 준비만 하고, 사람이 만든다.** `AX_MCP_CAUSATION_ID`가 있는 chat turn에서 `action_item_command`는 effect를 실행하지 않고 `action_item.command` 타입의 gated AX Action을 제안한다. payload에 target `action_item_id`·`command`·필수 `expected_version`·`reason`·`changes`를 보존하고, 사람이 그 카드를 REST/UI로 승인할 때만 canonical `ActionCenterApplication.execute`가 target에 정확히 한 번 적용한다. 카드의 subject·operation_label·preview는 승인자 기준으로 다시 계산한 target envelope에서 오며 raw payload를 노출하지 않는다. 같은 호출을 재시도하면 같은 wrapper Action이 receipt로 돌아오고, 한 turn에 다른 판단을 또 확인시키려 하면 다음 turn으로 미룬다. AX 제안(`ax.*`)은 wrapper로도 직접으로도 위임 turn이 승인할 수 없다 — gate를 접는 self-approval이기 때문이다. causation id가 없는 직접 호출(운영·테스트)은 기존처럼 canonical operation을 바로 실행한다.
- **판단 surface는 하나뿐이다.** kind별 MCP 판단 tool(`work_request_accept|negotiate|reject|resubmit`, `task_assignment_inbox|accept|decline`)은 discovery에서 제거했다. 같은 application operation을 부르더라도 `allowed_commands` policy·payload-aware receipt·위임 gate를 우회하는 두 번째 판단 표면이었고, 외부 production caller가 없는 단계에서 그것을 보존할 이유가 없다. facade method는 내부 호출·테스트용으로 남아 있다. REST의 kind별 compatibility endpoint는 유지하되, `POST /api/work-requests/{id}/negotiate`도 canonical과 같은 allow-list·날짜 계약(`normalize_proposed_changes`)으로 `conditions.changes`를 검증한다 — 검증 없이 쓰면 canonical reader가 읽지 못하는 행이 생겨 그 사람의 판단 목록 전체가 열리지 않는다.
- **수정안도 자기 필드만 바꾼다.** `revise`의 `changes`는 `title`·`description`·`due_date`·`clear_due_date`만 받고 그 밖의 키는 조용히 버리지 않고 거절한다. `adjust`의 구조화 제안과 같은 allow-list를 쓴다.
- **아직 남은 것.** 저장소는 `decision_items`(요청)와 `action_items`(AX 제안), `task_assignments`(배정)로 나뉘어 있다. 이것은 의도된 이중 모델이 아니라 진행 중인 통합의 중간 상태이고, 판단 표면에서는 이미 하나로 보인다. `action_items`를 canonical ActionItem으로 흡수하고 배정에도 Submission 행을 만드는 것이 남은 작업이다.

## 판단·요청 연속성·업무 (Work)

| ERD | 테이블 | 비고 |
|---|---|---|
| REQUEST_THREAD | `request_threads` | WorkRequest와 1:1, 댓글·회차·판단·파생 Task의 연속성 |
| WORK_REQUEST | `work_requests` | `request_thread_id`, `subject_id`, `organization_context_id`, `due_date`, `description`. **Delta**: `workflow_run_ref`를 두지 않는다(Decision Log 2026-09-03: 요청·판단은 WorkflowRun 없이 Work 모듈이 직접 처리) |
| SUBJECT / SUBJECT_VERSION | `subjects`, `subject_versions` | 요청 payload의 고정 버전(`content_hash`, `snapshot`) |
| ACTION_ITEM(사람 판단 항목) | `decision_items` | 사람 판단이 필요한 모든 경로의 canonical 모델이다. 하나의 독립 판단 질문 = ActionItem 하나이고, 조정·수정·재상신은 새 ActionItem이 아니라 새 Submission이다. `kind`는 `work_request.acceptance`, `task.assignment.acceptance` 등이며 status는 open→awaiting_revision→resolved. **Delta**: 테이블 이름은 역사적 이유로 `decision_items`이고 AX 제안은 아직 `action_items`에 남아 있다. 이 분리는 의도된 설계가 아니라 통합이 끝나지 않은 상태이며, 두 원장은 `GET /api/action-items` 하나의 query·command 경로 뒤에 감춰져 있다 |
| SUBMISSION | `submissions` | 회차(`submission_version`), `revises_id`, `payload_hash`, `diff`, decision policy snapshot |
| REVIEW_ASSIGNMENT | `review_assignments` | Submission당 history, active 최대 1, `supersedes_assignment_id` |
| REVIEW_DECISION | `review_decisions` | accept/negotiate/reject를 assignment·submission·actor에 바인딩한 immutable 사실 |
| TASK | `tasks` | `description`, `start_date`, `due_date`, `organization_unit_id`(귀속), `origin_kind`(direct/request_effect/assignment), `visibility`, lineage(`request_thread_id`, `source_work_request_id`, `source_decision_item_id`, `source_submission_id`, `source_review_decision_id`, `source_action_item_id`, `source_task_id`) |
| TASK_ASSIGNMENT | `task_assignments` | 모든 Task에 1행 이상: `assignment_kind` self(직접 생성)·request_effect(요청 수락)·direct(관리자 배정), `status` pending/active/declined/superseded, `assigned_by`, source refs. 내 업무·업무 조작은 **active assignment**를 통해서만 가능하다. direct 배정은 `task.assign` capability(팀장·관리자)가 자기 조직(하위 포함) 구성원에게 하고, `task.assignment.acceptance` DecisionItem(+Submission·ReviewAssignment)이 열려 assignee가 수락/거절한다. 거절은 사유가 필수이고 Task를 cancelled로 닫는다. API: `POST /api/tasks/assign`, `GET /api/task-assignment-candidates`, `GET /api/task-assignments/sent`, `POST /api/task-assignments/{id}/accept|decline`; 판단은 canonical `GET /api/action-items`와 `POST /api/action-items/{id}/commands/accept|decline`로 한다; MCP `task_assign`, `task_assignment_inbox|accept|decline` |
| TRIGGER / ACTION / AGENT_RUN / TOOL_DEFINITION / ACTION_TOOL_BINDING | `conversations`, `conversation_turns`, `tool_invocations`, `provider_calls`, `action_items`(AX 제안) | **Delta**: Conversation/Turn 모델로 대체하고 실행 전달은 확장 없는 `durable_jobs` 테이블(FIFO head·lease·fencing token)이 맡는다. Tool은 persona-bound MCP discovery. AX 제안은 `action_items`에 저장하지만 사람이 판단하는 표면에서는 canonical ActionItem 하나로만 보인다: 판단 목록·상세·command는 `GET /api/action-items`, `GET /api/action-items/{id}`, `POST /api/action-items/{id}/commands/{command}`를 쓴다. `action_items`를 canonical 판단 모델로 흡수하는 것이 남은 작업이다 |
| WORKFLOW_DEFINITION / VERSION / RUN | `workflow_definitions`, `workflow_definition_versions`, `workflow_runs`, `workflow_node_executions` | 개인 일일보고 생성만 사용 |
| WORKFLOW_SCOPE | — | future direction(ERD §3) |

Projections: `GET /api/work-requests/{id}/timeline`(request_timeline: 회차·판단·배정·댓글·활동), `PATCH /api/tasks/{id}`(`task.update`), `POST /api/work-requests/{id}/resubmit`(재상신).

## 논의·자료·감사

| ERD | 테이블 | 비고 |
|---|---|---|
| COMMENT | `comments` | RequestThread 논의, 상태 전이 없음 |
| ATTACHMENT | `attachments` | `source_kind` file/link, `source_ref`(storage key), `integrity_ref` sha256, `provenance`, `lifecycle` |
| ATTACHMENT_BINDING | `attachment_bindings` | context task/comment/submission × role input/output/discussion/supplemental. Task 자료 API(`/api/tasks/{id}/materials`)는 task 바인딩의 projection, 댓글 첨부(`POST /api/work-requests/{id}/comments/{comment_id}/attachments`, 작성자만)는 comment×discussion, 근거 파일은 submission×supplemental 바인딩 |
| (MATERIAL projection, SPEC-006) | `material_extractions`, `material_chunks` | Attachment 버전(`integrity_ref`)별 추출 lifecycle(queued/running/completed/failed/unsupported, 실패 이유 코드)과 bounded chunk. 원본은 Attachment이고 이 둘은 파생 projection이다. `GET /api/tasks/{id}/materials/search`·MCP `task_material_search`가 active assignment·live binding을 재검증한 뒤 lexical retrieval(port 교체 가능)로 excerpt·파일명·integrity·origin만 반환한다 |
| (MATERIAL parser, SPEC-006 후속) | — (미영속) | `DocumentParser` port의 `ParsedDocument`(DOCX/XLSX/PPTX/PDF block + locator + status)는 persistence entity가 아니다. 저장 단위·retention·삭제 전파는 `docs/material-data-management-design.md`의 사용자 gate 뒤 별도 change unit |
| (SPEC-008 evidence card) | `conversation_material_evidence` | delegated AX turn이 `task_material_search`로 실제 읽은 구간을 turn·execution에 묶어 기록(bounded excerpt, origin). 대화 view `material_evidence[]` → 근거 카드 |
| EVIDENCE | `evidence` | `POST /api/work-requests/{id}/evidence`(요청자·담당자만)로 현재 Submission에 채택. `evidence_role`은 담당자면 decision_basis, 요청자면 supporting. `fixed_snapshot_ref`=attachment sha256, `mutable_source=false`. timeline `evidence[]`로 회차별 노출, 참여자(cc 포함)는 `GET /api/work-requests/{id}/attachments/{attachment_id}/content`로 열람 |
| ACTIVITY_EVENT | `activity_events` | append-only, `safe_summary`·before/after ref·actor·reason. Task 생성/상태/수정/자료/배정/배정 수락·거절, WorkRequest 생성/판단/재상신이 기록 |
| (기존) | `task_activities`, `work_request_audit_events`, `action_item_audit_events`, `report_audit_events` | 첫 slice의 module 감사 로그. `task_activities`는 일일보고 source_refs가 참조하므로 유지 |

## ERD 밖 테이블

- `auth_sessions`: 로그인 세션(HttpOnly cookie).
- `daily_reports`, `report_drafts`, `daily_report_submissions`: 보고 SPEC 영역.
- `workflow_definitions`, `workflow_definition_versions`, `workflow_runs`, `workflow_node_executions`, `provider_calls`: 개인 일일보고 생성 runtime.
- `durable_jobs`: Conversation Turn과 material extraction이 공유하는 표준 PostgreSQL job transport(`modules/jobs`). ordering_key별 absolute earliest non-terminal job만 claim 가능, `FOR UPDATE SKIP LOCKED`, lease + fencing token, at-least-once 전달, idempotency key당 활성 job 1개. 2026-09-04에 PGMQ를 대체했다(Azure Flexible Server 미지원 extension).

기술 spike 잔재(`work_records`, `contract_approvals`, `meeting_evidence`, run 기반 `task_assignments`, generic workflow catalog/run/decision API, `technical_spike` 모드)는 2026-09-04에 제거했다.

## 남은 Delta 결정

1. Task 재배정(superseded)·배정 위임 정책, 배정 협의(negotiate) 여부.
2. Evidence를 외부 링크(`source_kind=link`, `mutable_source=true`)로 채택하는 경로.
3. ERD 문서(mediness) §3·§4의 WorkflowRun 전제와 이 구현의 Conversation 실행 모델 차이는 ERD §10 구현 Delta로 기록했다. SPEC-001/002 본문 동기화는 후속.
