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
- **`ResourceRelationship`은 접근 관계이지 판단·보유의 정본이 아니다.** requester·assignee 행은 WorkRequest에서 파생되므로 지워도 `rebuild_relationships()`로 같은 집합이 복원되고 두 번 돌려도 늘지 않는다. cc와 meeting share는 그 자체가 접근 관계라 이 표가 소유한다. 누가 보냈고 누가 들고 있는지는 이 표를 지워도 달라지지 않는다 — 그 답은 WorkRequest와 열린 TaskAssignment에서만 나온다.
- **Task는 자기가 만들어진 사실만 소유한다.** `tasks.created_by_actor_id`가 유일한 actor 칼럼이다. 업무를 보낸 사람은 `source_work_request_id`로 연결된 WorkRequest의 `requester_id`에서 권한 적용해 해소하고, 현재 담당자는 열려 있는 TaskAssignment에서, 담당자를 지정·변경한 actor는 그 assignment의 `assigned_by`에서 해소한다. `assigned_by`는 nullable이다 — self 생성과 요청 수락은 아무도 그 사람을 그 일에 앉히지 않았으므로 비어 있다. 담당자 변경은 `POST /api/tasks/{id}/reassign` 하나뿐인 별도 command로, Task 행을 먼저 잠가 두 사람이 같은 순간에 옮기려 할 때 한 쪽만 통과하게 하고, 열려 있던 assignment를 `superseded`로 닫고 `supersedes_assignment_id`로 이어진 새 행을 같은 transaction에 append한다. 한 Task에 열린 assignment는 언제나 최대 하나다. 일반 Task 수정 form에는 담당자 필드가 없다.
- **Task도 자기 이력을 스스로 갖는다.** 제목·설명·일정·상태·담당자·체크리스트·자료를 움직인 모든 변경은 그 변경을 만든 같은 transaction에서 `task_versions`에 그 시점의 Task를 통째로 동결한다(version당 정확히 한 행). 그래서 지금 화면이 감추는 것 — 지운 체크리스트 단계, 뗀 자료 — 도 그때의 모습에는 남아 있다. snapshot은 자료의 bytes를 복사하지 않고 Attachment identity와 `integrity_ref`만 참조하므로 artifact의 identity는 하나뿐이다. 체크리스트·자료 변경도 Task의 변경이므로 `tasks.version`을 1 올린다 — 열어 둔 화면의 `expected_version`은 그 순간 stale이 되고, UI는 성공 뒤 Task를 다시 읽어 다음 수정이 거절되지 않게 한다. `GET /api/tasks/{id}/history`(version 목록 + 원장 활동, 최신순)와 `GET /api/tasks/{id}/history/diff?from=&to=`, MCP `task_history`는 Task 자체를 읽을 수 있는 사람에게만 답하고 새 문을 열지 않는다. Task 상세의 `활동·이력` section이 그 답을 사람 이름·시각·사유가 있는 문장으로 읽고, 한 줄에서 그 version이 만든 변경을 펼친다. 모든 mutation 응답(체크리스트 추가·수정·삭제, 자료 연결·해제)은 자기가 옮긴 `task_version`을 함께 주므로 열어 둔 화면은 refresh를 기다리지 않고 곧바로 이어서 수정할 수 있다.
- **요청자는 판단받기 전에 스스로 보강한다.** 담당자가 아직 판단하지 않은(`pending`) 요청은 요청자가 `조정 요청` 없이 고칠 수 있다. 이것은 ReviewDecision이 아니라 `WorkRequestApplication.amend` 하나뿐인 command이고 REST `POST /api/work-requests/{id}/amend`·MCP `work_request_amend`·AX 확인 카드(`work_request.amend`)·UI가 모두 그것을 부른다. 같은 WorkRequest·ActionItem·논의 identity 아래 새 SubjectVersion·Submission을 추가하고, 아직 답하지 않은 ReviewAssignment를 `superseded`로 닫은 뒤 같은 담당자에게 최신 회차 pending assignment를 정확히 하나 연다. 그래서 사람은 언제나 마지막으로 말해진 것을 판단하고, 열려 있던 승인 카드와 위임 확인은 effect 없이 stale·obsolete가 된다. server가 요청자·`pending` 상태·`expected_version`·실제 변경 여부를 검증하고, 같은 command 재전송은 저장된 command payload와 대조해 receipt가 된다. 이번 slice는 제목·설명·기한만 바꾼다 — 담당자·참조자는 내용이 아니라 관계이므로 별도 command다. 새 회차는 이전 회차의 채택 Evidence를 상속하고 판단의 기한(`decision_items.due_at`과 새 ReviewAssignment)은 최신 회차를 따른다.
- **이전 회차는 권한 범위 안에서 설명할 수 있다.** MCP `work_request_history`가 `work_request_timeline`을 그대로 읽어 회차별 내용·diff·근거와 각 판단의 사유를 돌려주므로, 위임 turn이 현재안뿐 아니라 어떻게 여기까지 왔는지 설명할 수 있다. read-only이고 참여자(cc 포함)만 읽는다.
- **판단은 자기가 본 근거를 동결한다.** accept·reject·negotiate는 결정 시점 Submission의 evidence manifest를 canonical 형태(정렬된 `attachment_id`·`evidence_role`·`fixed_snapshot_ref`)로 계산해 남긴다. server가 동결한 것은 `ReviewDecision.conditions`의 단일 예약 key `_decision` 아래에 `expected_version`·`evidence_hash`·`evidence_manifest`로 모으고, 사람이 쓴 조건(`note`·`changes`)은 conditions 최상위에 그대로 둔다 — 두 종류가 한 dict에서 섞이지 않는다. timeline과 ActionItem 상세의 `evidence_hash` 필드는 그 예약 값에서 온다. manifest identity는 Evidence row id가 아니라 attachment와 그 무결성이므로 row 생성 순서와 무관하게 같은 집합은 같은 hash이고, 빈 근거도 고유한 hash를 갖는다. 판단이 끝난 회차에는 근거를 더할 수 없으므로 그 회차의 현재 manifest는 동결된 hash와 영원히 같고, 근거는 다음 회차에서만 자란다. WorkRequest timeline과 ActionItem 상세가 회차별 현재 manifest·hash와 각 결정이 동결한 hash를 권한 범위 안에서 함께 보여 준다.
- **근거 채택은 요청을 한 걸음 옮긴다.** 채택은 `work_requests.version`을 1 올리고 `updated_at`을 갱신하며, 응답에 `request_version`을 함께 준다. 이미 화면을 연 담당자의 `expected_version`은 그 순간 stale이 되어, 자기가 본 적 없는 근거 위에서 판단하지 못한다. 채택과 판단은 같은 request 행 잠금을 두고 직렬화되므로 어느 쪽이 이기든 동결된 hash는 그 회차의 실제 근거와 일치한다. UI는 채택 성공 뒤 request를 먼저 갱신하고 timeline을 다시 읽어, 같은 사람이 곧바로 이어서 판단할 수 있게 한다.
- **상속은 채택인 척하지 않는다.** 재상신이 복제한 Evidence는 원 채택자의 `adopted_by`와 `adopted_at`을 그대로 보존한다. 복제 자체는 재상신한 사람의 행위이므로 `work_request.evidence_inherited`로 따로 남기고, ActivityEvent와 WorkRequest audit trail 양쪽에 같은 actor·`inherited_count`·`previous_submission_id`·`new_submission_id`로 기록한다. 두 원장 모두 `work_request.resubmitted` **뒤에** 오므로 결과가 원인보다 앞서 읽히지 않는다. 그래서 어떤 행도 하지 않은 시각에 누군가가 채택했다고 말하지 않는다.

### 근거·판단 version에 남은 residual

- **UI가 채택 뒤 timeline을 두 번 읽는다.** `onChanged()`가 version을 바꾸면 timeline effect가 이미 재발화하는데 수동 `loadTimeline()`이 한 번 더 부른다. 같은 문서라 무해하다.
- **PostgreSQL race test의 분기 커버리지가 sleep에 의존한다.** 두 순서 모두 단언하지만 관측 결과로 분기하므로, 느린 환경에서는 실패 없이 한쪽만 검증하게 될 수 있다.
- **예약 key namespace.** `evidence_hash`·`evidence_manifest`가 사용자 conditions와 같은 dict를 쓴다.
- **재전송 판정은 그 답을 남긴 기록에 건다.** accept·reject·adjust는 결정이 실제로 소비한 version(`_decision.expected_version`)과 대조하므로, 그 뒤 근거 채택 같은 다른 이유로 request version이 움직여도 영수증은 그대로다. revise는 자기가 만든 회차와, 그 회차가 답한 조정 결정이 남긴 version(negotiating 상태에서는 재상신·철회 말고 request를 움직일 수 있는 것이 없으므로 `_decision.expected_version + 1`이 정확히 소비한 값이다) 둘 다에 건다. 그래서 원래 보낸 command의 재전송만 영수증이고, 다른 version이나 다른 내용은 거절이다. withdraw는 종결 상태라 단일 step 관계로 계속 고정한다.
- **모든 command는 자기가 답하는 version을 함께 보낸다.** `expected_version`은 REST `ActionCommandRequest`와 MCP schema 모두에서 필수다. WorkRequest는 request version, AX 제안은 Action version, 배정은 envelope가 내보내는 Task version을 답한다(배정 자체에는 version column이 없다). 수락은 Task version을 그대로 두고 거절은 취소하며 정확히 1 올리므로, replay receipt도 그 관계와 저장된 사유가 정확히 맞을 때만 성립한다. fallback으로 현재 version을 끌어다 쓰는 경로는 없다.
- **MCP도 같은 경로를 쓴다.** persona-bound tool `action_item_list`·`action_item_get`·`action_item_command`가 `WorkflowApplication.pending_action_items`·`action_item_detail`·`run_action_command`를 그대로 부른다. adapter는 transport일 뿐이라 kind로 command를 추론하지 않고, persona·member·org·capability를 인자로 받지 않는다(persona는 process 설정이다). 노출은 capability로 갈린다: 읽을 수 있는 kind가 하나라도 있어야 list/get이, canonical handler가 요구하는 결정 capability가 있어야 command가 discovery에 나타나며, discovery 뒤 권한이 회수되면 실제 호출에서 application authorization이 거절한다. 반환은 structured output이고 annotation은 list/get이 read-only·idempotent·closed-world, command는 non-read-only·closed-world·idempotent(payload-aware receipt) 그리고 withdraw/reject/decline 가능성 때문에 보수적으로 destructive다.
- **위임 turn은 판단을 준비만 하고, 사람이 만든다.** `AX_MCP_CAUSATION_ID`가 있는 chat turn에서 `action_item_command`는 effect를 실행하지 않고 `action_item.command` 타입의 gated AX Action을 제안한다. 저장되는 payload는 server가 정규화한 canonical form이다: target `action_item_id`·`command`·필수 `expected_version`, 그리고 그 kind가 실제로 받는 `reason`·`changes`만 남는다. 사람이 그 카드를 REST/UI로 승인할 때만 canonical `ActionCenterApplication.execute`가 target에 정확히 한 번 적용한다.
  - **한 turn 한 판단, 판정 기준은 payload 자체다.** 같은 turn의 재시도는 canonical payload hash가 같을 때만 같은 wrapper를 receipt로 돌려준다. target·command가 같아도 version·사유·제안이 다르면 다른 판단이므로 거절하고 다음 turn으로 미룬다. 표시용 문자열은 판정 근거가 아니다.
  - **카드는 지금 읽을 수 있는 것만 말한다.** wrapper row의 title과 payload_summary는 target을 가리키지 않는 generic 문구다. subject·operation_label·preview는 렌더 시점에 현재 principal이 target을 읽을 수 있을 때만 target envelope에서 계산하고, 권한이 없으면 generic 카드가 된다. audit과 effect 실행에 필요한 target id는 payload 안에만 남는다.
  - **AX 제안(`ax.*`)은 wrapper로도 직접으로도 위임 turn이 판단할 수 없다** — gate를 접는 self-approval이기 때문이다. discovery도 이 사실을 반영한다: 위임 server에서 `action_item_command`는 `action.decide`와 ax가 아닌 판단 capability를 함께 가진 persona에게만 노출된다. causation id가 없는 직접 호출(운영·테스트)은 기존처럼 canonical operation을 바로 실행한다.
  - **승인 문도 정확한 version만 replay한다.** `POST /api/actions/{id}/decide`는 이미 같은 결정으로 resolved인 Action에 대해 그 결정이 실제로 소비한 version(`action.version == expected_version + 1`)일 때만 receipt를 주고, 다른 version은 stale이다. wrapper와 기존 AX 제안에 같이 적용된다.
  - **지나간 확인은 지나갔다고 말한다.** 확인 대기 중 target이 움직이면(누가 근거를 채택하는 것으로도 충분하다) wrapper는 `obsolete`로 투영된다: 카드와 판단 상세가 상태 행으로 그 사실을 말하고 `승인`은 아예 제시하지 않으며 `거절`만 남는다. 그래도 승인을 보내면 raw version 오류가 아니라 무엇이 일어났는지로 거절된다. 승인자가 target을 더 이상 읽을 수 없는 경우도 같은 판정이다 — 볼 수 없는 판단은 확인할 수 없으므로 fail-closed로 `승인`을 감춘다. 치운 뒤 같은 판단은 원래 자리에서 다시 하면 되고, 위임 turn은 다음 turn에 새로 제안할 수 있다. `platform/actions`와 `platform/action_center`는 서로를 참조하며 함수 안 지연 import로 순환을 푼다.
- **판단 surface는 하나뿐이다.** kind별 MCP 판단 tool(`work_request_accept|negotiate|reject|resubmit`, `task_assignment_inbox|accept|decline`)은 discovery에서 제거했다. 같은 application operation을 부르더라도 `allowed_commands` policy·payload-aware receipt·위임 gate를 우회하는 두 번째 판단 표면이었고, 외부 production caller가 없는 단계에서 그것을 보존할 이유가 없다. facade method는 내부 호출·테스트용으로 남아 있다. REST의 kind별 compatibility endpoint는 유지하되, `POST /api/work-requests/{id}/negotiate`도 canonical과 같은 allow-list·날짜 계약(`normalize_proposed_changes`)으로 `conditions.changes`를 검증한다 — 검증 없이 쓰면 canonical reader가 읽지 못하는 행이 생겨 그 사람의 판단 목록 전체가 열리지 않는다.
- **command는 자기 kind가 받는 필드만 받는다.** `revise`의 `changes`는 `title`·`description`·`due_date`·`clear_due_date`, `adjust`의 제안은 `title`·`description`·`due_date`만 받고 그 밖의 키는 조용히 버리지 않고 거절한다. AX 제안 판단은 사유·변경 항목을 받지 않고, 배정 판단은 변경 항목을 받지 않는다. 각 handler의 `normalize`가 이 계약을 소유하므로 REST·MCP·위임 wrapper가 모두 같은 것을 저장하고 같은 것을 실행한다. MCP JSON schema는 generic command 특성상 `changes`를 object로만 표현하므로 server validation이 계약의 정본이다.
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
| TASK_CHECKLIST_ITEM | `task_checklist_items` | Task 안의 실행 단계. Task가 아니므로 담당·기한·판단이 없다. 자기 identity와 `version`을 갖고 stale `expected_version`을 거절하므로 서로 다른 단계를 동시에 고치는 두 사람은 충돌하지 않는다. `state`는 `active|archived` — 목록에서 빼도 지우지 않고 `archived_by`·`archived_at`을 남기며 과거 snapshot에서 복원된다. 순서는 한 단계를 밀지 않고 전체 목록을 다시 쓴다(`POST /api/tasks/{id}/checklist/order`, 모든 단계 정확히 한 번). 모든 mutation은 Task 행을 잠그고 Task version을 정확히 한 번 올린다. 업무 생성·요청·배정은 처음부터 아는 단계를 순서대로 함께 받을 수 있고(`checklist[]`, 최대 50단계), 요청의 단계는 `work_requests.initial_checklist`에 남아 수락으로 만들어진 Task의 체크리스트가 된다 — 그 단계를 쓴 사람은 요청한 사람이므로 `created_by`도 그 사람이다. 회차가 협의하는 내용이 아니므로 재상신·수정은 이 목록을 바꾸지 않는다. MCP `task_checklist_list|add|update|archive|reorder`가 같은 application command를 부르고, 위임 turn 안에서는 아무것도 쓰지 않고 `task.checklist.*` gated Action으로 제안한다 — 사람이 승인할 때만 정확히 한 번 적용된다 |
| TASK_VERSION | `task_versions` | Task 한 버전의 불변 snapshot: `(task_id, version)` unique, `change_kind`(활동 원장과 같은 말), `actor_id`, `reason`, `snapshot`(제목·설명·상태·일정·created_by·source request·열린 assignment·체크리스트·자료 참조), `captured_at`. version을 움직이지 않은 변경은 새로 동결할 것이 없다. API: `GET /api/tasks/{id}/history`, `GET /api/tasks/{id}/history/diff` |
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
| ATTACHMENT (link) | `attachments` | `source_kind=external_link`: URL 자체가 artifact다. `source_ref`=URL(고유), `content_type=text/uri-list`, `size_bytes=0`, `integrity_ref=observed:<시각>` — content hash가 아니다. SCAX가 내용을 가져오지 않고 revision을 고정하지 않았으므로 view가 `mutable_source=true`를 내보내고 UI는 `변경 가능한 링크`로 표시한다. 같은 URL은 하나의 Attachment이고 Task마다 binding이 늘어난다. URL이 바뀌면 기존 행을 덮지 않고 새 Material이 된다. 다운로드 경로는 file에만 있고, 읽을 내용이 없으므로 추출·검색에서 `unavailable_materials[].reason=external_link`로 보고한다. credential이 담긴 URL(`user:pass@`)과 http(s) 아닌 스킴은 거절한다. API: `POST /api/tasks/{id}/materials/links`. |
| ATTACHMENT (reference) | `attachments` | `source_kind=resource_ref`: SCAX 안의 다른 것을 가리킨다. `source_ref=<type>:<id>`(현재 `task`·`meeting`), `size_bytes=0`, `integrity_ref=observed:<시각>`. **제목은 저장값이 아니라 매 조회마다 그 자원을 소유한 module의 인가된 read로 다시 해소한다** — 읽을 수 없게 되면 저장된 이름을 주지 않고 `볼 수 없는 자료`로 가린다. 연결하려면 그 순간 그 자원을 읽을 수 있어야 하고, 없는 자원과 못 읽는 자원은 같은 응답으로 답한다. 자기 자신 참조는 거절한다. UI는 URL로 나가지 않고 제품 안에서 연다. API: `POST /api/tasks/{id}/materials/references` |
| (MATERIAL projection, SPEC-006) | `material_extractions`, `material_chunks` | Attachment 버전(`integrity_ref`)별 추출 lifecycle(queued/running/completed/failed/unsupported, 실패 이유 코드)과 bounded chunk. 원본은 Attachment이고 이 둘은 파생 projection이다. `GET /api/tasks/{id}/materials/search`·MCP `task_material_search`가 active assignment·live binding을 재검증한 뒤 lexical retrieval(port 교체 가능)로 excerpt·파일명·integrity·origin만 반환한다 |
| (MATERIAL parser, SPEC-006 후속) | — (미영속) | `DocumentParser` port의 `ParsedDocument`(DOCX/XLSX/PPTX/PDF block + locator + status)는 persistence entity가 아니다. 저장 단위·retention·삭제 전파는 `docs/material-data-management-design.md`의 사용자 gate 뒤 별도 change unit |
| (SPEC-008 evidence card) | `conversation_material_evidence` | delegated AX turn이 `task_material_search`로 실제 읽은 구간을 turn·execution에 묶어 기록(bounded excerpt, origin). 대화 view `material_evidence[]` → 근거 카드 |
| EVIDENCE | `evidence` | `POST /api/work-requests/{id}/evidence`(요청자·담당자만)로 현재 Submission에 채택. `evidence_role`은 담당자면 decision_basis, 요청자면 supporting. `fixed_snapshot_ref`=attachment sha256, `mutable_source=false`. 채택은 **아직 판단받는 중인 최신 회차에만** 허용한다: 수락·거절·조정·철회로 그 회차가 끝나면 거절하고 재상신 뒤 새 회차에 추가하게 한다. 재상신은 직전 회차의 Evidence를 append-only로 복제한다 — 파일이 아니라 채택만 복제하므로 같은 `attachment_id`·`fixed_snapshot_ref`·`evidence_role`을 참조하되 새 row identity와 `adopted_at`을 가지며, 이후 두 회차의 근거 집합은 독립적으로 자란다. timeline `evidence[]`로 회차별 노출, 참여자(cc 포함)는 `GET /api/work-requests/{id}/attachments/{attachment_id}/content`로 열람 |
| ACTIVITY_EVENT | `activity_events` | append-only, `safe_summary`·before/after ref·actor·reason. Task 생성/상태/수정/자료/배정/배정 수락·거절, WorkRequest 생성/판단/재상신이 기록 |
| (기존) | `task_activities`, `work_request_audit_events`, `action_item_audit_events`, `report_audit_events` | 첫 slice의 module 감사 로그. `task_activities`는 일일보고 source_refs가 참조하므로 유지 |

## ERD 밖 테이블

- `auth_sessions`: 로그인 세션(HttpOnly cookie).
- `daily_reports`, `report_drafts`, `daily_report_submissions`: 보고 SPEC 영역.
- `workflow_definitions`, `workflow_definition_versions`, `workflow_runs`, `workflow_node_executions`, `provider_calls`: 개인 일일보고 생성 runtime.
- `durable_jobs`: Conversation Turn과 material extraction이 공유하는 표준 PostgreSQL job transport(`modules/jobs`). ordering_key별 absolute earliest non-terminal job만 claim 가능, `FOR UPDATE SKIP LOCKED`, lease + fencing token, at-least-once 전달, idempotency key당 활성 job 1개. 2026-09-04에 PGMQ를 대체했다(Azure Flexible Server 미지원 extension).

기술 spike 잔재(`work_records`, `contract_approvals`, `meeting_evidence`, run 기반 `task_assignments`, generic workflow catalog/run/decision API, `technical_spike` 모드)는 2026-09-04에 제거했다.

## Actor 용어 3층과 금지 별칭

같은 사람을 가리키는 말이 층마다 달라서 생기는 혼선을 막는다. **아래로만 번역하고, 위로 거슬러 올라가 새 사실을 만들지 않는다.**

| 층 | 무엇 | 예 |
|---|---|---|
| Canonical domain | 저장된 사실. 이것만이 정본이다 | `work_requests.requester_id`·`assignee_id`, `task_assignments.assignee_id`·`assigned_by`·`assignment_kind`, `tasks.source_work_request_id`·`source_action_item_id` |
| Internal projection | 정본에서 해소한 읽기 전용 계산값. 저장하지 않는다 | `TaskOrigin{kind, actor_role, actor, source}`, `assignee`, `TaskApplication._origin_projection` |
| UI | 읽는 사람의 자리에서 본 상대 | 담당자, 보낸 사람, 출처 chip, "A가 B에게 요청/배정" |

**해소 규칙.** Task는 자기 자신에 대한 사실만 갖는다. 요청자는 `source_work_request_id`로 연결된 WorkRequest에서, 현재 담당자는 active TaskAssignment에서, 지정·변경 actor는 그 TaskAssignment의 `assigned_by`에서 해소한다(셀프 생성은 상대가 없다). `ResourceRelationship`은 접근 관계와 후보 projection일 뿐 WorkRequest·TaskAssignment 정본을 대신하지 않는다.

**금지 별칭.**
- Task에 requester·assigner·creator를 **고정 역할 칼럼처럼 다루지 않는다.** 상대가 없는 업무에 `생성자: 본인`을 채워 넣는 것은 사실이 아니라 합성이다. 상대가 없으면 `origin`은 아예 없다.
- 일반 Task 상세·수정 화면에 요청자/배정자 고정 metadata 행을 두지 않는다. 담당자 하나와, 있을 때만 나타나는 출처 chip으로 말한다.
- 목록은 읽는 사람의 반대편만 이름한다: 받은 업무는 보낸 사람, 보낸 업무는 담당자. 두 역할을 나란히 고정 칼럼으로 두지 않는다.
- requester·assigner·creator는 generic edit field가 아니다. 담당자 변경도 일반 수정이 아니라 별도 command다.
- canonical role과 audit(누가 언제 무엇을 했는지)은 그대로 보존한다. 관리자·감사 화면에서만 역할을 명시적으로 드러낸다.

## 완료·검수·알림 — 결정됨, 아직 구현하지 않음

2026-09-05 설계 논의에서 확정한 경계다. 코드에는 아직 없고, 착수 전에 정본 Work Brief의 Scope에 반영해야 한다.

- **알림은 판단이 아니다.** `확인 필요` ActionItem 원장에는 *지금 이 사람이 답해야 하고, 답하면 상태가 바뀌는 것*만 들어간다. "완료됐으니 알기만 하면 되는 일"을 ActionItem으로 만들면 `allowed_commands`가 빈 유령 판단이 생기고 판단 카운트가 오염된다. 알림은 `activity_events`(append-only 사실 정본) 위의 파생 projection이고, 대상자는 `resource_relationships`(requester·assignee·cc, 필요하면 watcher)에서 파생한다. 알림용 사실을 따로 쓰지 않는다.
- **완료는 전이가 아니라 질문을 연다.** request·direct로 생긴 Task는 담당자의 완료가 곧 종결이 아니라 immutable TaskDelivery 제출이고, 확인 주체에게 **별도 결과 ActionItem**이 열린다. 승인 Execution만 Task를 `done`으로 만들고, 보완 요청은 같은 결과 ActionItem을 `awaiting_revision`으로 두고 Task를 `in_progress`로 되돌린다. 보완 제출은 새 ActionItem이 아니라 같은 ActionItem의 새 Submission이다. 최초 수락 ActionItem과 결과 확인 ActionItem은 서로 독립적으로 판단·거절될 수 있는 다른 질문이므로 절대 합치지 않는다.
- **확인 주체는 출처에서 나오고, 정책으로 바꿀 수 있다.** 기본값은 `self → 검수 없음`, `request_effect → 요청자`, `direct → 배정한 관리자`(`task_assignments.assigned_by`)다. 배정자와 담당자가 같으면 자기 승인이 되므로 검수 없음으로 접는다. 정책은 조직 설정으로 덮어쓸 수 있게 하되, `CompletionPolicyPort` 하나가 완료 제출 시점에 이를 해석하고 **역할이 아니라 구체적인 사람으로 확정해 `ReviewAssignment.reviewer_member_id`에 동결**한다. 조직 개편이 나도 이미 열린 확인 건의 담당이 조용히 바뀌지 않는다.
- **정책은 질문이 열릴 때 얼린다.** `submissions.decision_policy_snapshot`이 이미 그 자리다(현재 값은 가능한 결정과 사유 필수 여부뿐). 완료 검수 정책·다중 리뷰어 quorum도 여기에 동결한다. 정책이 바뀌어도 이미 열린 판단은 열릴 때의 규칙으로 끝난다.
- **여러 명이 함께 판단하는 것은 스키마가 이미 감당한다.** `review_assignments`는 Submission당 1..N이고 `review_decisions`는 (assignment, submission, actor)에 묶인다. 막고 있는 것은 "pending assignment는 항상 하나"라는 구현 불변식과 quorum 해석의 부재뿐이다. 다만 **독립적인 결재 *단계*를 한 ActionItem에 배열로 넣지 않는다** — 따로 판단·거절될 수 있는 질문은 따로 ActionItem이다.
- **이것은 설정 가능한 workflow 엔진이 아니다.** 상태 기계·전이 조건·유형별 정책까지는 티켓 workflow와 같은 문제를 푼다. 다르게 두는 것은 (1) 일의 상태와 사람의 판단을 두 축으로 분리하고, (2) 회차·근거를 동결해 "승인할 때 무엇을 보고 있었나"에 답하고, (3) 누가 답해야 하는지를 가변 필드가 아니라 이력 있는 행으로 두고, (4) 정책을 실행 시점에 읽지 않고 질문 시점에 얼리는 것이다. generic workflow catalog/run/decision API는 2026-09-04에 걷어냈고 되돌리지 않는다. 조직이 고를 수 있는 것은 협상 불가능한 불변식(판단을 합치지 않는다·self 업무에 남의 승인을 강제하지 않는다·자기 자신을 승인하지 않는다) 위층뿐이다.

## 남은 Delta 결정

1. **`tasks.owner_id` 제거와 `created_by` 도입.** 현재 Task는 `owner_id`로 현재 담당자를 복제해 들고 있는데, 정본은 active TaskAssignment다. Task가 직접 소유해야 하는 것은 `created_by`뿐이다. projection·UI는 이미 정본에서 해소하도록 정리했으므로(2026-09-05) 남은 것은 schema/domain delta이고, 별도 unit으로 처리한다.
2. Task 재배정(superseded)·배정 위임 정책, 배정 협의(negotiate) 여부.
3. Evidence를 외부 링크(`source_kind=link`, `mutable_source=true`)로 채택하는 경로.
4. ERD 문서(mediness) §3·§4의 WorkflowRun 전제와 이 구현의 Conversation 실행 모델 차이는 ERD §10 구현 Delta로 기록했다. SPEC-001/002 본문 동기화는 후속.
