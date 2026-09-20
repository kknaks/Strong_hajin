# 업무 코드·DB 구조 재조사

## 1. 조사 범위와 기준

- 조사일: 2026-09-16. 조사 대상은 이 작업 폴더의 코드, SQLAlchemy 모델, HTTP/MCP 진입점, 관련 계약 테스트다.
- 마지막 커밋: `8973791982228f9b5400c2bcaba7e1a9bdda97d7`.
- **마지막 커밋과 수정 중인 작업본을 분리한다.** 현재 persistence.py, work_tasks.py 등에 커밋되지 않은 변경이 있다. 아래 현재 작업본 관찰은 조사 시점의 상태이며 이후 편집으로 달라질 수 있다.
- Makefile 기본 로컬 PostgreSQL(127.0.0.1:54329/ax_demo)에 읽기 전용 스키마 조회를 시도했으나 연결 오류로 실패했다. **실행 DB의 스키마가 모델과 일치한다고 확인한 것이 아니다.** 실제 데이터 행은 읽지 않았다.
- 테스트는 임시 SQLite DB를 사용하는 기존 테스트를 Makefile 타겟으로 실행했다. 실제 운영 DB를 검증한 결과가 아니다.
- 기존 definition.md/lifecycle.md는 정책 논의와 잘못된 구현 설명이 섞인 초안이다. 이번 조사에서는 두 문서와 애플리케이션 코드를 수정하지 않았다. 이 문서는 사실 확인 기록이며 새 통합 설계가 아니다.

## 2. 마지막 커밋에서 검증된 세 경로

| 항목 | 본인 업무 | 일반 업무 요청 | 담당자 지정 |
|---|---|---|---|
| 주요 진입점 | POST /api/tasks | POST /api/work-requests | POST /api/tasks/assign |
| 모듈 | TaskApplication | WorkRequestApplication | TaskAssignmentApplication |
| 생성 시 | Task + 본인 active 배정 | WorkRequest pending + 요청 판단 회차 | Task open + pending 배정 + 배정 판단 회차 |
| 상대방 지정 | 본인 | assignee_id 필수 | assignee_id 필수 |
| 수락 시 | 별도 수락 없음 | 요청 accepted, Task open 및 active 배정 생성 | 기존 배정을 active로 변경, Task open 유지 |
| 거절 시 | 해당 없음 | 요청 rejected, Task 생성 없음 | 배정 declined, 이미 있는 Task cancelled |
| 완료 | 직접 done 가능 | 완료 보고 후 요청자 확인 | 직접 done 가능 |
| 부모 연결 입력 | parent_task_id 지원 | 요청 입력/수락 생성에서 지원하지 않음 | parent_task_id 지원 |

일반 요청과 담당자 지정은 담당자 유무나 AI 채팅 사용 여부로 구분되지 않는다. 둘 다 상대방을 지정하며 둘 다 MCP에 노출돼 있다. 생성 시점, 협의 절차, 권한, 완료 확인 규칙이 다르다.

### 2.1 권한의 차이

- 일반 요청: WORK_REQUEST_CREATE 권한 및 요청 대상 후보 검증. 보통 후보는 요청자와 조직 범위가 겹치고 work_request.decide가 있는 활성 사람이다. 회의 승격은 별도 정책에 따라 시스템 요청자와 활성 구성원 검사를 사용한다.
- 담당자 지정: TASK_ASSIGN 권한 및 배정 가능한 조직/프로젝트 범위 검증. 단순히 같은 조직이라는 이유만으로 배정 권한이 생기지 않는다.
- 코드가 ‘팀장’이라는 직함 문자열만 확인하는 것은 아니다. 팀장→부서원은 권한 기반 배정의 예시다.

근거: `modules/work/assignments.py`의 assign/_assignable, `modules/work/requests.py`의 create, `platform/organization_access.py`의 두 후보 조회 함수.

### 2.2 코드 계층

두 기능 모두 모듈과 플랫폼에 있다.

- 일반 요청: `modules/work/requests.py` → `platform/work_tasks.py:SqlAlchemyWorkRequestRepository`
- 담당자 지정: `modules/work/assignments.py` → `platform/work_tasks.py:SqlAlchemyTaskAssignmentRepository`
- HTTP와 MCP는 각각의 Application을 호출한다. MCP의 실행 확인은 보내는 사용자의 AI 실행 승인이고, 수신자의 업무 수락과 다르다.
- 기존 요청의 상태 변경 일부는 모듈에, 배정 거절에 따른 Task 취소는 플랫폼 decide()에 있다. 주석·파일 위치만으로 정책을 판단하면 안 된다.

## 3. 테이블의 역할과 관계

```text
work_requests (요청자 requester_id, 수신자 assignee_id, 요청 state)
  ↑ tasks.source_work_request_id (선택 FK, 값이 있으면 요청별 Task 유일)
 tasks (업무 본체, 수행 state)
  ├─ parent_task_id → tasks.id (선택 자기 참조 FK)
  ├─ task_assignments.task_id ← 담당 배정 및 수락/거절 이력
  ├─ task_checklist_items.task_id ← 체크 항목
  ├─ task_versions.task_id ← 버전 스냅샷
  └─ task_activities.task_id ← 상태 기록

work_requests.request_thread_id → request_threads
 tasks.request_thread_id → request_threads (선택)
 comments.request_thread_id → request_threads
```

### 3.1 담당 배정과 판단 배정은 다르다

- `task_assignments`: 업무를 수행할 담당자의 배정. self / request_effect / direct 등의 assignment_kind와 pending / active / declined / superseded / cancelled 등의 status를 사용한다.
- `review_assignments`: 제출물에 대해 판단할 사람을 지정한다. `submissions`를 참조한다.
- `review_decisions`: 수락·거절·협의나 완료 확인 등 판단 결과. 판단 종류와 문맥은 decision_items/submissions와 함께 읽어야 한다.
- `decision_items`: 사람이 판단해야 하는 항목. 요청 수락, 배정 수락, 완료 보고 확인에 사용한다.
- `action_items`: AI 대화에서 사용자 실행 확인을 위한 별도 테이블이다. decision_items와 같은 테이블이 아니다.

### 3.2 키와 제약

- tasks.source_work_request_id에는 NULL이 아닌 값에 대한 유일 인덱스가 있다. 한 요청에서 파생된 Task는 하나다.
- task_versions는 (task_id, version)이 유일하다.
- task_references는 해제되지 않은 동일 참조 쌍에 유일 인덱스가 있다.
- task_assignments.task_id는 실제 FK다. assignee_id, assigned_by는 문자열 컬럼이며 members FK가 아니다.
- task_assignments.supersedes_assignment_id도 UUID 컬럼이지만 현재 정의에 자기 참조 FK는 없다.
- 상태값은 대체로 String 컬럼이다. 애플리케이션 정책의 허용 값과 DB enum/check 제약을 혼동하지 않는다.
- 활성 담당 1개 보장용 task_assignments 부분 유일 인덱스는 조사 시점 작업본에 추가되어 있다. 마지막 커밋에는 없었다.

## 4. 시작·진행·완료의 실제 규칙

### 4.1 일반 상태 전환

`modules/work/lifecycle.py`의 일반 전환표:

| 현재 | 허용 목적 상태 |
|---|---|
| open | in_progress, cancelled |
| in_progress | blocked, done, cancelled |
| blocked | in_progress, cancelled |
| completion_submitted | cancelled |
| done | in_progress |
| cancelled | 없음 |

이 표는 **전체 명령을 망라하지 않는다.** 완료 보고 및 승인·보완은 TaskApplication의 별도 명령에서 상태를 바꾼다.

- 업무 상태 변경은 TASK_SELF_MANAGE와 현재 active 담당 배정을 확인한다.
- open → in_progress 전환 시 start_date가 없으면 서울 기준 오늘 날짜를 넣는다. 기존 값은 유지한다.
- task.state_changed 활동에 행위자·before_ref·after_ref·요약·occurred_at을 남긴다. 최초 시작 시각은 최초 open→in_progress 이벤트로 확인할 수 있다. tasks.started_at 전용 컬럼은 없다.
- blocked로 바꿀 때 사유 필수. 재개 시 현재 block_reason은 지우지만 활동 이력은 남는다.

### 4.2 완료 보고의 별도 전환 — 이전 설명의 오류

`TaskApplication.requires_completion_review()`는 **source_work_request_id의 존재 여부만** 검사한다. parent_task_id, assignee가 생성자와 다른지, assignment_kind만으로 판단하지 않는다.

- source_work_request_id가 없으면 일반 done 전환 가능. 미완료 직속 하위 Task가 있으면 완료를 막는다.
- source_work_request_id가 있으면 직접 done을 막고 완료 보고를 요구한다.
- **submit_completion()은 in_progress뿐 아니라 blocked도 허용한다.** 따라서 ‘막힘에서 반드시 재개해야 완료 절차에 들어간다’는 이전 설명과 문서는 코드와 다르다.
- 보고 시 결과 요약 필수. 결과 자료 목록은 비어 있어도 된다. 보고 자료는 해당 Task에 연결된 자료여야 한다.
- 완료 보고 시 submission 스냅샷과 검토 배정을 만들고 completion_submitted로 전환한다.
- 확인자는 work_requests.requester_id에서 찾는다. 임의로 task_assignments.assigned_by로 대체하지 않는다.
- 승인 시 done. 미완료 하위 Task를 검사한다. 보완 시 사유를 요구하고 in_progress로 돌린다.
- 완료 보고 제출 자체는 미완료 하위 업무 차단을 호출하지 않는다. 승인 시점의 검사와 구분한다.
- 보완·재제출은 같은 완료 판단 항목 아래 제출 회차를 늘리는 구조다. 자동으로 ‘수정 Task’를 생성하는 동작이 아니다.

### 4.3 진행 중 기능과 제한

- 제목/설명/일정 수정, 프로젝트 연결 변경(하위는 상위 프로젝트를 따름).
- 체크리스트 추가/문구 수정/체크/해제/순서 변경/보관.
- 파일/링크/리소스 자료 연결 및 해제, 참고 Task 연결 및 해제.
- 본인 하위 Task 생성 및 권한 범위 내 다른 담당자 배정.
- 담당자 재배정, 이력/스냅샷 조회.
- 이러한 기능이 진행 중에 가능하다는 것과, 진행 중에서만 허용된다는 것은 다르다. API별 권한·상태 검사가 다르다.
- 댓글은 request_threads에 저장하며 일반 요청 경유 API를 제공한다. 모든 Task에 대한 직접 댓글 API가 구현된 것은 아니다.

### 4.4 하위 업무와 체크리스트

- parent_task_id는 DB 차원에서 재귀 관계를 저장할 수 있다.
- 현재 parent_for()는 부모가 이미 하위 업무이면 거부한다. **담당자가 달라도 예외가 없다.** ‘다른 담당자에게 넘기면 다음 단계 허용’은 대화에서 제안한 정책이지 현재 구현이 아니다.
- 하위 업무 조회도 부모/자식 한 단계 전제를 포함한다.
- 체크리스트는 별도 task_checklist_items 행이며 Task가 아니다. 생성 시부터 입력할 수 있고 체크 완료자·시각도 남긴다.
- 체크리스트 전체 체크를 완료의 필수 조건으로 검사하지 않는다. ‘체크리스트=완료 기준’은 사용자가 새로 정한 정책이며 구현된 사실과 구분한다.

## 5. 작업본 관찰 — 조사 도중에도 변경됨

**중요: 조사 중 외부 편집으로 파일이 계속 바뀌었다. 아래 1~8은 첫 검사·테스트 시점의 관찰이다. 최종 확인에서 바뀐 내용은 5.1에 따로 적는다. 실패 결과를 이후 작업본의 현재 상태로 단정하지 않는다.** 변경 작성자의 의도나 완료 상태를 추측하지 않는다.

1. tasks.approver_id(선택 문자열)가 추가되어 있다. 현재 완료 승인 판별 함수는 이 컬럼을 읽지 않고 source_work_request_id를 계속 사용한다.
2. task_creation_attempts 테이블 및 SqlAlchemyTaskCreationLedger가 추가되어 있다. (actor_id, command_kind, request_key) 유일 제약으로 생성 멱등성을 다루려는 코드다.
3. 미추적 modules/work/creation.py에는 self/horizontal/managed 생성 경로와 멱등 키 함수가 있다. 검색 시점에는 해당 분기 함수의 실제 호출 연결이 확인되지 않았다.
4. create_request()가 work_requests.state를 pending 대신 assigned로 저장하며 수락용 decision/submission/review 배정을 만들지 않도록 바뀌었다.
5. 하지만 관찰된 bootstrap → WorkRequestApplication.create 경로는 요청 저장 후 반환하며, 새 create_task_for_request()를 즉시 호출하지 않는다. create_task_for_request()는 기존 accept 경유 래퍼에서만 연결되어 있었다.
6. 기존 accept/reject는 pending 또는 negotiating만 허용한다. 따라서 assigned로 새로 만든 요청과 연결이 맞지 않는다. 테스트에서 수락 후 task_id를 얻지 못하는 실패가 발생했다.
7. 담당자 지정 create_assigned_task()는 pending 대신 active로 생성하고 accepted_at을 채우도록 바뀌었다. 수락 대기 판단 항목 생성도 빠졌다. 기존 모듈 주석과 테스트는 수락 대기를 전제로 남아 있다.
8. 이를 ‘현재 정책이 자동 수락으로 확정됐다’고 해석하지 않는다. 작업 중인 변경과 기존 계약이 서로 맞지 않는 상태라는 관찰만 기록한다.

### 5.1 조사 종료 전 다시 확인한 변경

- WorkRequestApplication.create()에 create_task_for_request() 호출이 추가됐다. 따라서 위 5번의 초기 호출 누락은 최종 읽기 시점에는 더 이상 그대로이지 않다. 새 요청은 요청 행과 Task/활성 배정을 함께 생성하도록 연결되고 있다.
- bootstrap이 새 TaskCreationApplication을 사용하도록 변경됐으며 creation_commands.py가 추가됐다. 이전에 호출되지 않았던 생성 분기·원장이 연결되는 중이다.
- 담당자 지정 모듈 주석도 즉시 활성 배정으로 수정됐다.
- 생성 명령에 idempotency_key 입력이 추가되고 있다. 마지막 커밋의 필수 입력 계약과 구분해야 한다.
- 변경이 계속되는 작업본에 대해 전체 구현 완료 여부를 단정하지 않았다. 테스트는 앞선 실행 시점의 결과이고 이 최종 작업본을 다시 검증한 결과가 아니다.
- 최종 읽기 시점 주요 파일 SHA-256과 상태 목록은 `work-code-db-audit-2026-09-16-manifest.json`에 기록했다. 이후 파일이 다르면 이번 조사 스냅샷과 다른 버전이다.

## 6. 검증 결과

기존 계약 테스트 6개를 `PYTEST_ADDOPTS`의 -k 필터와 `make test-contract`로 실행했다. -n auto --dist worksteal을 유지했다.

- 마지막 커밋을 git archive로 별도 임시 디렉터리에 내보내 실행: **6 passed**.
- 수정 중인 작업본의 초기 관찰 시점에서 동일 선택 실행: **2 passed, 4 failed**. 이후 외부 편집이 추가됐으므로 최신 작업본의 결과로 재사용하면 안 된다.
- 선택: 본인/요청 수락 배정, 관리자 배정 수락 전 노출, 배정 거절, 한 단계 하위 제한, 요청 업무 완료 보고, 본인 업무 직접 완료.
- 실패: 요청 수락 후 task_id 없음, 새 배정이 pending이 아닌 active, 배정 거절 API가 수락 대기 상태가 아니라고 거부, 완료 보고 테스트 준비 중 요청 판단 항목 없음.
- 이 결과는 선택한 범위만 검증한다. 전체 테스트나 실제 PostgreSQL 통합 테스트를 통과했다고 의미하지 않는다.
- 로그: `work-code-db-audit-2026-09-16-working-tests.txt`, `work-code-db-audit-2026-09-16-head-tests.txt`.

## 7. 이전 설명·문서에서 바로잡아야 할 항목

| 이전 설명/문서 | 조사 결과 |
|---|---|
| 요청과 배정은 같은 기능이므로 task_assignments로 통일 | 권한·생성 시점·협의·완료 승인 정책이 다르다. 통합은 구현 사실이 아니며 철회한 제안이다. |
| 모든 요청은 Task를 먼저 만든다 | 마지막 커밋의 일반 요청은 수락 시 생성. 작업본은 생성 정책 변경 중이며 경로 불일치가 있다. |
| 모든 거절은 Task cancelled | 마지막 커밋의 일반 요청 거절은 Task가 없다. 배정 거절만 기존 Task를 취소한다. |
| task_assignments가 있으면 요청 업무로 승인받는다 | 본인 Task에도 배정 행이 있다. 현재 완료 승인은 source_work_request_id로 구분한다. |
| blocked에서는 재개 후에만 완료 절차 가능 | 일반 done 전환은 불가하지만 별도 완료 보고는 blocked에서도 가능하다. |
| 시작 날짜·시간이 없어서 새 저장 구조 필요 | activity_events에 상태 전환 시각이 있다. 전용 started_at 부재와 시각 기록 부재는 다르다. |
| 수신자가 하위의 하위를 만들 수 있다 | 현재 코드는 담당자 무관 한 단계 제한. 변경 제안과 구분해야 한다. |
| 모든 체크 항목이 완료돼야 현재 코드도 완료 허용 | 현재 미강제. 새 정책으로 구현이 필요하다. |
| Task마다 댓글 가능 | 요청 스레드 기반 댓글만 확인됐다. |

## 8. 테이블 컬럼 목록 — 조사 시점 ORM 기준

아래 목록은 실행 DB introspection이 아니라 SQLAlchemy 모델을 읽어 추출했다. NULL 허용과 사용자 입력 필수 여부는 다르다. JSON 덤프는 `work-code-db-audit-2026-09-16-schema.json`에 보존했다.

### tasks

| 컬럼 | 모델 타입 | NULL 허용 | 키/참조 |
|---|---|---|---|
| `id` | CHAR(32) | 아니오 | PK |
| `created_by_actor_id` | VARCHAR(100) | 아니오 | — |
| `title` | VARCHAR(300) | 아니오 | — |
| `state` | VARCHAR(40) | 아니오 | — |
| `block_reason` | TEXT | 예 | — |
| `description` | TEXT | 예 | — |
| `start_date` | DATE | 예 | — |
| `due_date` | DATE | 예 | — |
| `organization_unit_id` | VARCHAR(100) | 예 | organization_units.id |
| `origin_kind` | VARCHAR(30) | 아니오 | — |
| `visibility` | VARCHAR(20) | 아니오 | — |
| `request_thread_id` | CHAR(32) | 예 | request_threads.id |
| `source_work_request_id` | CHAR(32) | 예 | work_requests.id |
| `source_decision_item_id` | CHAR(32) | 예 | decision_items.id |
| `source_submission_id` | CHAR(32) | 예 | submissions.id |
| `source_review_decision_id` | CHAR(32) | 예 | review_decisions.id |
| `source_action_item_id` | CHAR(32) | 예 | action_items.id |
| `source_task_id` | CHAR(32) | 예 | tasks.id |
| `source_meeting_id` | CHAR(32) | 예 | meetings.id |
| `source_agenda_id` | CHAR(32) | 예 | meeting_agendas.id |
| `parent_task_id` | CHAR(32) | 예 | tasks.id |
| `project_id` | CHAR(32) | 예 | projects.id |
| `version` | INTEGER | 아니오 | — |
| `approver_id` | VARCHAR(100) | 예 | — |
| `created_at` | DATETIME | 아니오 | — |
| `updated_at` | DATETIME | 아니오 | — |
| `causation_key` | VARCHAR(64) | 예 | — |

### task_assignments

| 컬럼 | 모델 타입 | NULL 허용 | 키/참조 |
|---|---|---|---|
| `id` | CHAR(32) | 아니오 | PK |
| `task_id` | CHAR(32) | 아니오 | tasks.id |
| `assignee_id` | VARCHAR(100) | 아니오 | — |
| `assigned_by` | VARCHAR(100) | 예 | — |
| `assignment_kind` | VARCHAR(30) | 아니오 | — |
| `status` | VARCHAR(30) | 아니오 | — |
| `source_work_request_id` | CHAR(32) | 예 | work_requests.id |
| `source_decision_item_id` | CHAR(32) | 예 | decision_items.id |
| `source_review_decision_id` | CHAR(32) | 예 | review_decisions.id |
| `supersedes_assignment_id` | CHAR(32) | 예 | — |
| `decline_reason` | TEXT | 예 | — |
| `created_at` | DATETIME | 아니오 | — |
| `accepted_at` | DATETIME | 예 | — |
| `declined_at` | DATETIME | 예 | — |
| `superseded_at` | DATETIME | 예 | — |

### work_requests

| 컬럼 | 모델 타입 | NULL 허용 | 키/참조 |
|---|---|---|---|
| `id` | CHAR(32) | 아니오 | PK |
| `request_thread_id` | CHAR(32) | 예 | request_threads.id |
| `subject_id` | CHAR(32) | 예 | subjects.id |
| `organization_context_id` | VARCHAR(100) | 예 | — |
| `requester_id` | VARCHAR(100) | 아니오 | — |
| `assignee_id` | VARCHAR(100) | 아니오 | — |
| `title` | VARCHAR(300) | 아니오 | — |
| `description` | TEXT | 예 | — |
| `due_date` | DATE | 예 | — |
| `state` | VARCHAR(40) | 아니오 | — |
| `version` | INTEGER | 아니오 | — |
| `conditions` | JSON | 예 | — |
| `initial_checklist` | JSON | 예 | — |
| `source_meeting_id` | CHAR(32) | 예 | meetings.id |
| `source_agenda_id` | CHAR(32) | 예 | meeting_agendas.id |
| `promoted_by_member_id` | VARCHAR(100) | 예 | — |
| `created_at` | DATETIME | 아니오 | — |
| `updated_at` | DATETIME | 아니오 | — |
| `causation_key` | VARCHAR(64) | 예 | — |

### task_checklist_items

| 컬럼 | 모델 타입 | NULL 허용 | 키/참조 |
|---|---|---|---|
| `id` | CHAR(32) | 아니오 | PK |
| `task_id` | CHAR(32) | 아니오 | tasks.id |
| `text` | VARCHAR(300) | 아니오 | — |
| `position` | INTEGER | 아니오 | — |
| `done` | BOOLEAN | 아니오 | — |
| `state` | VARCHAR(20) | 아니오 | — |
| `version` | INTEGER | 아니오 | — |
| `created_by` | VARCHAR(100) | 아니오 | — |
| `completed_by` | VARCHAR(100) | 예 | — |
| `completed_at` | DATETIME | 예 | — |
| `archived_by` | VARCHAR(100) | 예 | — |
| `archived_at` | DATETIME | 예 | — |
| `created_at` | DATETIME | 아니오 | — |
| `updated_at` | DATETIME | 아니오 | — |

### request_threads

| 컬럼 | 모델 타입 | NULL 허용 | 키/참조 |
|---|---|---|---|
| `id` | CHAR(32) | 아니오 | PK |
| `initiated_by` | VARCHAR(100) | 아니오 | — |
| `organization_context_id` | VARCHAR(100) | 예 | — |
| `purpose` | VARCHAR(300) | 아니오 | — |
| `created_at` | DATETIME | 아니오 | — |

### comments

| 컬럼 | 모델 타입 | NULL 허용 | 키/참조 |
|---|---|---|---|
| `id` | CHAR(32) | 아니오 | PK |
| `request_thread_id` | CHAR(32) | 아니오 | request_threads.id |
| `author_member_id` | VARCHAR(100) | 아니오 | — |
| `body` | TEXT | 아니오 | — |
| `created_at` | DATETIME | 아니오 | — |
| `edited_at` | DATETIME | 예 | — |

### task_versions

| 컬럼 | 모델 타입 | NULL 허용 | 키/참조 |
|---|---|---|---|
| `id` | CHAR(32) | 아니오 | PK |
| `task_id` | CHAR(32) | 아니오 | tasks.id |
| `version` | INTEGER | 아니오 | — |
| `change_kind` | VARCHAR(60) | 아니오 | — |
| `actor_id` | VARCHAR(100) | 아니오 | — |
| `reason` | TEXT | 예 | — |
| `snapshot` | JSON | 아니오 | — |
| `captured_at` | DATETIME | 아니오 | — |

### task_activities

| 컬럼 | 모델 타입 | NULL 허용 | 키/참조 |
|---|---|---|---|
| `id` | CHAR(32) | 아니오 | PK |
| `task_id` | CHAR(32) | 아니오 | tasks.id |
| `task_version` | INTEGER | 아니오 | — |
| `state` | VARCHAR(40) | 아니오 | — |
| `occurred_at` | DATETIME | 아니오 | — |

### activity_events

| 컬럼 | 모델 타입 | NULL 허용 | 키/참조 |
|---|---|---|---|
| `id` | CHAR(32) | 아니오 | PK |
| `request_thread_id` | CHAR(32) | 예 | — |
| `target_type` | VARCHAR(40) | 아니오 | — |
| `target_id` | VARCHAR(100) | 아니오 | — |
| `event_kind` | VARCHAR(80) | 아니오 | — |
| `actor_kind` | VARCHAR(30) | 아니오 | — |
| `actor_id` | VARCHAR(100) | 아니오 | — |
| `before_ref` | VARCHAR(200) | 예 | — |
| `after_ref` | VARCHAR(200) | 예 | — |
| `reason` | TEXT | 예 | — |
| `causation_ref` | VARCHAR(200) | 예 | — |
| `safe_summary` | VARCHAR(300) | 아니오 | — |
| `occurred_at` | DATETIME | 아니오 | — |

### decision_items

| 컬럼 | 모델 타입 | NULL 허용 | 키/참조 |
|---|---|---|---|
| `id` | CHAR(32) | 아니오 | PK |
| `kind` | VARCHAR(60) | 아니오 | — |
| `subject_id` | CHAR(32) | 아니오 | subjects.id |
| `context_type` | VARCHAR(40) | 예 | — |
| `context_id` | VARCHAR(100) | 예 | — |
| `supersedes_id` | CHAR(32) | 예 | — |
| `effect_identity` | VARCHAR(200) | 예 | — |
| `status` | VARCHAR(30) | 아니오 | — |
| `due_at` | DATETIME | 예 | — |
| `created_at` | DATETIME | 아니오 | — |
| `resolved_at` | DATETIME | 예 | — |

### submissions

| 컬럼 | 모델 타입 | NULL 허용 | 키/참조 |
|---|---|---|---|
| `id` | CHAR(32) | 아니오 | PK |
| `decision_item_id` | CHAR(32) | 아니오 | decision_items.id |
| `subject_version_id` | CHAR(32) | 아니오 | subject_versions.id |
| `submission_version` | INTEGER | 아니오 | — |
| `revises_id` | CHAR(32) | 예 | — |
| `submitted_by` | VARCHAR(100) | 아니오 | — |
| `payload_hash` | VARCHAR(64) | 아니오 | — |
| `decision_policy_snapshot` | JSON | 아니오 | — |
| `diff` | JSON | 예 | — |
| `submitted_at` | DATETIME | 아니오 | — |

### review_assignments

| 컬럼 | 모델 타입 | NULL 허용 | 키/참조 |
|---|---|---|---|
| `id` | CHAR(32) | 아니오 | PK |
| `submission_id` | CHAR(32) | 아니오 | submissions.id |
| `reviewer_member_id` | VARCHAR(100) | 아니오 | — |
| `supersedes_assignment_id` | CHAR(32) | 예 | — |
| `resolution_kind` | VARCHAR(20) | 예 | — |
| `resolution_ref` | VARCHAR(100) | 예 | — |
| `due_at` | DATETIME | 예 | — |
| `expires_at` | DATETIME | 예 | — |
| `status` | VARCHAR(20) | 아니오 | — |
| `assigned_at` | DATETIME | 아니오 | — |

### review_decisions

| 컬럼 | 모델 타입 | NULL 허용 | 키/참조 |
|---|---|---|---|
| `id` | CHAR(32) | 아니오 | PK |
| `review_assignment_id` | CHAR(32) | 아니오 | review_assignments.id |
| `submission_id` | CHAR(32) | 아니오 | submissions.id |
| `actor_member_id` | VARCHAR(100) | 아니오 | — |
| `decision` | VARCHAR(30) | 아니오 | — |
| `reason` | TEXT | 예 | — |
| `conditions` | JSON | 예 | — |
| `decided_at` | DATETIME | 아니오 | — |

### task_creation_attempts

| 컬럼 | 모델 타입 | NULL 허용 | 키/참조 |
|---|---|---|---|
| `id` | CHAR(32) | 아니오 | PK |
| `actor_id` | VARCHAR(100) | 아니오 | — |
| `command_kind` | VARCHAR(50) | 아니오 | — |
| `request_key` | VARCHAR(200) | 아니오 | — |
| `payload_fingerprint` | VARCHAR(64) | 아니오 | — |
| `task_id` | CHAR(32) | 예 | tasks.id |
| `work_request_id` | CHAR(32) | 예 | work_requests.id |
| `created_at` | DATETIME | 아니오 | — |

## 9. 주요 근거 파일

- [modules/work/requests.py](../backend/src/ax_workspace/modules/work/requests.py)
- [modules/work/assignments.py](../backend/src/ax_workspace/modules/work/assignments.py)
- [modules/work/application.py](../backend/src/ax_workspace/modules/work/application.py)
- [modules/work/lifecycle.py](../backend/src/ax_workspace/modules/work/lifecycle.py)
- [modules/work/task_creation.py](../backend/src/ax_workspace/modules/work/task_creation.py)
- [platform/persistence.py](../backend/src/ax_workspace/platform/persistence.py)
- [platform/work_tasks.py](../backend/src/ax_workspace/platform/work_tasks.py)
- [platform/action_center.py](../backend/src/ax_workspace/platform/action_center.py)
- [platform/organization_access.py](../backend/src/ax_workspace/platform/organization_access.py)
- [entrypoints/http.py](../backend/src/ax_workspace/entrypoints/http.py)
- [entrypoints/mcp.py](../backend/src/ax_workspace/entrypoints/mcp.py)
- [bootstrap/application.py](../backend/src/ax_workspace/bootstrap/application.py)

행 번호는 작업 중 편집으로 달라질 수 있으므로 본문의 함수·클래스 이름으로 함께 찾는다.
