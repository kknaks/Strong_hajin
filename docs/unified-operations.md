# API·MCP·승인 실행 경로 일원화

구현 계약은 2026-09-11 SCAX API MCP 승인 실행 경로 일원화 Work Brief의 D1~D11/E1/E2다. 이 문서는 R2의 baseline 관찰, R3 이행, 최종 Acceptance를 고정한다. 행별 현재 상태는 inventory와 함께 판정한다.

> **그 뒤의 변경은 이 기록을 덮지 않는다.** 아래 R2·R3·Acceptance 절은 그 시점에 실제로 관찰하고 통과시킨 것을 그대로 남긴다 — SHA·수치·로그 경로를 나중 작업에 맞춰 고쳐 쓰지 않는다. 2026-09-16 업무 생성 slice(W1)가 **신규 생성 경로의 수락 gate를 걷고 생성 명령에 멱등 키를 필수로 세웠다**: `POST /api/tasks`(담당 지정 포함) · `POST /api/tasks/assign` · `POST /api/work-requests` · `POST /api/meetings/{id}/todos/{todoId}/promote` 가 `Idempotency-Key` 헤더를, 대응 MCP 도구 넷이 `idempotency_key` 명시 인자를 받는다. 신규 요청·배정은 수락을 기다리지 않고 활성 담당을 바로 세우며, 요청·배정 수락 판단은 **과거 행에만** 남는다. 완료 결과 확인과 AX 실행 확인은 그대로다. 현재 동작은 `README.md` 「업무 — 요청과 배정」과 `docs/domain-model.md`가 갖는다.

## Baseline과 전수 표면

- 기준: PR #3 merge `42e43358b866bc02d4a5e401b19d826a6e8397e1`. 2026-09-11 원격 main 재조회도 같은 SHA다.
- R2 관찰 baseline은 HTTP method/path **125개**, 서로 다른 facade operation **120개**, 동적 Task transition 5개를 포함한 MCP tool **42개**였다. 이 수치는 이행 전 비교 기준이다.
- [전수 inventory](unified-operations-inventory.json)의 R3f 현재 runtime은 HTTP **137개**(`verified` 131, 합의된 `excluded` 6)와 실제 MCP tool **120개**(`verified` 120)다. HTTP handler, 화면 호출자, application input/result, owning call, 현재 tool, 노출 정책과 Acceptance 근거를 각 행에 둔다. architecture 검사가 실제 route·동적 discovery schema와 이 목록의 완전 일치를 확인한다.
- 화면 source는 `frontend/src/api.ts` 및 나머지 production TS/TSX의 `/api/` 호출이다. literal path 매칭이 없는 handler도 검사 대상이다. 동적 path, facade의 다른 메서드 경유, 실제 등록은 R3 runtime binding 검사로 보강한다. 이 정적 표만으로 전수 커버리지 완료를 선언하지 않는다.
- 새로 연결할 영역에는 조직/권한 조회, 내 정보/선호, 프로젝트와 구성원, 요청의 논의·근거 자료, 업무 완료 보고·재배정·참고 연결, 회의 수정·공유 해제·노트·녹음·요약 채택, 일반 자료함, 기존 알림, 대화 관리가 포함된다.
- PR #8은 별도 소유권이다. 조회 시 OPEN/CONFLICTING, head `749cd1eb45725b113df01e9dacbabc6bee2fccae`. 이 branch는 참여 이력/일반 자료 원본 보존 의미를 변경하지 않는다. main 반영 시 겹치는 project/access/composition 계약만 재대조한다.

## 명시적 메서드와 한 구성 경계

대안 A를 채택한다. 업무를 임의 문자열과 dict로 실행하는 범용 dispatcher는 도입하지 않는다.

1. `bootstrap`의 session 단위 구성 코드만 owning application을 생성한다. Task, Assignment, Meeting, Reports, WorkRequest, Project, Organization, ActionCenter, Conversation, Material의 필수 의존성을 이곳에서 연결한다. platform executor/presenter는 이미 조립된 명시적 서비스 또는 읽기용 typed port를 받는다. 다른 module의 구현을 service locator로 요청하지 않는다.
2. 하나의 HTTP 호출/승인 안에서는 같은 SQLAlchemy Session을 사용한다. 승인 executor는 session을 새로 열거나 commit하지 않는다. ActionCenter의 최종 Submission, ReviewDecision, 업무 effect, receipt를 application의 바깥 transaction에서 함께 저장한다. Task progress batch만 기존 savepoint 부분 성공 계약을 유지한다.
3. 각 도메인의 input은 Pydantic/dataclass의 명시적 타입으로 정의하고 날짜·UUID·누락/명시적 null·version 정규화를 소유한다. typed result는 도메인 view와 명령 receipt를 구별한다. transport envelope는 HTTP/MCP가 표현하되 동일한 typed input/result를 통과한다. 기존 `Any`/`dict` signature는 baseline이지 목표가 아니다.
4. 도메인별 tool 명세가 ID, 한글 표시명, description, typed schema, 명시적 handler, capability와 실행 방식을 함께 소유한다. HTTP binding은 같은 owning method를 참조한다. 명세는 연결·노출을 설명하며 대상 인가/업무 상태 규칙을 복제하지 않는다.
5. registry가 신규 업무 규칙을 실행하거나 임의 method를 `getattr`로 호출하지 않는다. 각 handler의 typed callable 연결과 실제 등록을 검사한다. 중복 ID, handler 누락, 불명확한 정책, adapter의 application 생성, 필수 의존성 누락을 architecture/contract 검사로 실패시킨다.

Action repository와 presenter가 Task/Meeting/ActionCenter를 내부에서 재조립하는 현행 경로도 대상이다. 표시를 위해 읽는 것 역시 동일한 인가를 사용해야 한다. 구성 순환은 지연된 **정해진 읽기 port**로 풀고, optional dependency의 미제공을 조용히 처리해 권한 범위를 바꾸지 않는다. test-only 최소 fixture는 필요한 port를 명시한다.

## 조회 목적·필터·인가

다음 표의 query family와 inventory의 개별 operation/signature를 함께 적용한다. family 안에서도 목적/반환 의미가 다른 operation은 합치지 않는다. 날짜·상태·검색어·페이지를 바꿔도 actor 관계와 인가 범위는 고정이다.

| Query / API·tool 연결 | 목적·actor 관계·반환 의미 | 허용 필터 / 인가 / 분리 판단 |
|---|---|---|
| `my_work` / GET `/api/my-work` / `my_task_list` | 현재 본인 active 배정 업무 | 종료 포함·날짜·검색 조건만. `task.read`; `mine`, 조직 포함 인자 없음. owning `my_work` query 고정 |
| `list_tasks` / GET `/api/tasks` / `task_list` | 현재 열람 가능한 업무 | 종료 포함 등 같은 조건. `task.read`와 조직/프로젝트/요청 관계. owning `readable_tasks`; 내 업무와 별도 query |
| `get_task`, `task_history`, `task_history_diff` / 기존 API·tool 및 `task_history_diff` | 한 업무의 현재 상세, 불변 이력, 지정 두 version 차이 | task ID·version이 범위를 넓히지 않음. 대상 열람 불가/미존재를 동일 처리 |
| `task_subtask_list`, `task_checklist_list`, `task_materials_list` | 특정 읽을 수 있는 업무의 하위 업무·체크리스트·자료 | 같은 Task 권한, 별도 반환 의미. facet마다 명시적 query 사용 |
| `task_assignment_candidates`, `sent_task_assignments` | 배정 가능한 사람 / 본인이 보낸 배정 | `task.assign`의 현재 범위 / sender 관계. 신규 배정은 수신 판단을 만들지 않으므로 `action_item_list`에 서지 않는다 — 과거 `pending` 배정만 거기서 답한다 |
| `list_work_requests`, `get_work_request`, `work_request_timeline` | 참여하는 요청 목록·상세·회차와 논의 | requester/assignee/cc, 요청 capability. 새 승인 이행도 기존 requester와 assignee의 판단 의미 유지 |
| `work_request_assignee_candidates`, `work_request_cc_candidates` | 요청 수행/참조 대상 후보 | 서로 다른 참여 역할. 수행 후보는 재직·로그인 가능·본인 제외·조직 범위 교집합으로 고른다. 받는 사람이 판단하지 않으므로 판단 capability는 묻지 않으며, 같은 owning query를 생성 명령의 대상 검사도 그대로 쓴다 |
| `pending_action_items`, `action_item_detail` | 내가 판단할 항목 / 내가 참여한 한 판단의 회차·허용 command | 현재 capability와 참여관계. AX proposal을 delegated turn이 직접 confirm할 수 없음 |
| `list_meetings` / GET `/api/meetings` / `meeting_list` | 열람 가능한 조직 일정·공유 회의 | 날짜 구간은 필터. `meeting.read`와 detail 열람 범위. 비인가 private meeting의 busy block도 D9에 따라 결과/건수에서 제거 |
| 새 `my_meetings` / GET `/api/my-meetings` / `my_meeting_list` | 내가 소유하거나 참석하는 회의 | 날짜 조건만. 공유로 읽을 수 있다는 이유만으로 내 회의에 포함하지 않음. 별도 public query |
| `get_meeting` / 기존 API·`meeting_get` | 열람 가능한 회의의 노트·전사·요약·작업 상태 | meeting ID; 현재 detail 인가. 현재 작업 조회가 작업을 새로 접수하지 않음 |
| `daily_report_status`, `daily_report_history`, `daily_report_recent` | 본인 날짜별 보고서 상태, 한 보고서의 회차, 최근 보고서 | 날짜·limit은 필터, 소유자 고정. 각각 `daily_report.read`; 상태에 생성 job/result 포함 |
| `member_directory`, `organization_tree`, `organization_unit_members` | 인가된 구성원 명부, 조직 구조, 지정 부서 구성원 | 조직 범위 불변. 단위 ID는 범위 확대 권한이 아님 |
| `organization_member_detail`, `organization_member_history`, `organization_activity` | 인가된 구성원 상세/발령 축 이력, 조직 활동 | axis·unit·limit·cursor는 같은 목적의 필터. 민감 필드는 현행 인사/관리 권한으로 제한 |
| `my_organization_profile` | 내 정보·보유 권한 설명 | actor를 인자로 바꿀 수 없음. auth/me와 organization/me 모두 같은 query, 모델에 인증 비밀정보를 전달하지 않음 |
| `installed_access_roles`, `member_access` | 관리 권한 안에서 역할 목록/구성원 접근 범위 설명 | 현재 관리 범위 검사. E1은 변경만 제외하므로 AX 조회 유지 |
| `list_projects`, `get_project` | 본인 참여 프로젝트 목록/상세 | project.read 및 실제 project assignment. 조직 전체 권한으로 참여를 우회하지 않음 |
| `list_material_folders`, `list_folder_materials` | 권한 내 자료함/지정 자료함의 자료 | folder owner/access 범위. archive·detach의 baseline 보존 의미 유지 |
| `search_materials` / `material_search` | 인가된 원문 본문 검색 | query, resource types/ID, material ID, 등록일·limit은 동일 목적의 조건. source 인가 후 건수·excerpt·근거 구성 |
| `material_metadata`, `open_*_material`, `open_work_request_attachment` | 한 자료의 metadata / 현재 원문 열기 | 상위 업무·회의·보고서·폴더·요청 권한과 binding/integrity 검사. 모델에는 조회된 안전한 자료 정보/화면 URL, 비밀 토큰 없는 링크 |
| `graph_overview` | 본인과 연결된 업무 관계 전체의 한정된 투영 | 현행 member/team/project는 같은 관계 집합의 grouping이며 범위를 넓히지 않아 view 필터 유지. 실제 코드에서 집합이 달라지는 경우 별도 query로 분리 |
| `graph_search`, `graph_neighbors` | 관계 node 검색 / 한 node 주변 관계 | query·node·limit, 현재 각 source 인가. 원문 본문 검색과 분리 |
| `conversations`, `conversation`, `conversation_search` | 내 저장 대화 목록·내용·과거 발화 검색 | 소유자 및 conversation 권한, query/limit. 과거 답변은 보존하고 source ID 재조회에는 현재 resource 인가 적용 |
| `list_notifications` | 기존 알림 목록 | 수신자 고정, 현재 source 인가. D10의 새 작업 완료 알림은 만들지 않음 |

현재 `my_work`는 기능 권한 거절을 빈 배열로 삼키고 `task_list(mine=True)`는 `include_organization=not mine`을 전달한다. 목표는 각각 이름 있는 query이며 기능 거절은 403/tool error다. 이행 후 공개 `mine` 입력은 허용하지 않는다. 클라이언트/tool 목록/자연어 fixture를 함께 갱신하고 과거 tool 이름은 이력 표시 경계에서만 읽는다.

특정 대상은 미존재·열람 불가 모두 `대상을 찾을 수 없습니다`로 일치시킨다(HTTP 404, MCP isError). 기능 거절은 별도 권한 오류, 잘못된 입력은 검증 오류, stale는 충돌, 서버 장애는 실패다. 수정/승인 실패를 200 성공이나 빈 배열로 바꾸지 않는다. 기존 action round·본문은 D6대로 보존하고 새 source resolution/다운로드에는 현재 인가를 적용한다.

## 권한별 discovery와 metadata

- 모든 tool을 명시적으로 정의한 뒤 **매 `tools/list`에서 현재 principal을 조회**해 기능 capability와 실행 모드에 맞는 도구만 반환한다. `tools/call`도 다시 검사하므로 stale client 목록은 실행 허가가 아니다. reconnect/list 요청이 목록 갱신 경로이며 장기 cache로 권한을 고정하지 않는다.
- 설치된 MCP Python SDK는 lockfile의 **2.1.1**이다. 공개 `MCPServer.list_tools`/`call_tool` 경계를 사용해 wire request와 Python seam에 같은 필터를 적용한다. provisional middleware나 private registry 해킹에 의존하지 않는다. 실제 stdio client에서 권한 부여·회수 후 같은 server의 list/call을 검증한다.
- 한글 표시명은 tool 명세에서 provider 화면 mapper로 전달한다. 표시명/description/schema/handler는 함께 바뀐다. 현재 `material_search` 표기 누락, `task_assign`의 요청/배정 혼동, 옛 `task_material_search`는 이력 읽기 전용 호환 이름으로 정리한다.
- description은 목적 → 대상·범위 → 반환/효과·승인 여부 → 혼동 대상 차이를 설명한다. 필수 version/ID 출처·날짜 offset/기본 시간 규칙을 schema와 일치시킨다.
- 자연어 선택 회귀: 내 업무/열람 가능한 업무, 내 회의/조직 일정, 업무 진행/일일보고, 배정/수평 요청, 자료 본문/관계 탐색의 한국어 요청 쌍을 실제 provider에서 관찰한다. 도구 목록/schema 정합성 검사만으로 의미 검증을 대체하지 않는다.

공식 확인(2026-09-11): [MCP Python SDK server](https://github.com/modelcontextprotocol/python-sdk/blob/main/src/mcp/server/mcpserver/server.py), [middleware 지위](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/advanced/middleware.md). 구현 시 lockfile 코드와 stdio probe를 우선 대조한다.

## 제외·화면 연결·기존 판단 경로

| 분류 | 이행과 사용자 경로 | 근거/제약 |
|---|---|---|
| E1 3개 access 변경 | 관리자 HTTP/API 유지. 모든 AX tool, proposal action type, command allowlist에서 거절 | 다른 사용자의 권한 변경은 관리자 화면 전용. 권한 조회, 회의 공유, 프로젝트 구성원 변경은 포함하지 않음 |
| E2 auth login/logout/provider account, realtime credential | 인증/브라우저·서버 계층만 처리. 인증 필요 시 로그인 화면으로 안내 | 사용자 인증 자체와 recording 업무를 제외하지 않음. 더미 secret이 모델용 payload/result/error에 없는지 검사 |
| health | 서버 상태 점검만 유지 | 내부 운영 endpoint이며 업무 기능 아님 |
| browser-interaction inventory 행 | `file_attachment_request` / `recording_request`가 actor·target·intent에 묶인 사용자 조작 상태와 화면 경로를 반환. 화면이 선택·업로드/마이크 허용을 마친 뒤 같은 target에서 처리 | 작업 요청은 첨부/녹음 성공이 아님. 선택 대기·취소·거절·실패와 실제 완료를 분리. 외부 MCP는 같은 화면 deep link. 브라우저 callback만 bytes·임시 STT credential을 처리 |
| 현재 자료의 링크/참조 연결 | 각 owning command를 typed 승인 payload로 연결 | 새 파일 선택을 강제하지 않음. 현재 resource 권한과 원본/binding 규칙 유지 |
| canonical-judgement inventory 행 | 기존 HTTP URL을 호환 유지하고 기존 ActionItem의 allowed_commands로 수렴. MCP는 `action_item_command` 하나 | 요청/배정 판단과 AX 확인을 섞지 않음. 과거 pending/Submission을 재작성하지 않음. legacy typed payload reader는 이력/pending 소비자가 남은 동안 유지 |
| 과거 `/api/actions` | 기존 호출자·저장 이력 호환을 보존하고 새 discovery는 ActionItem 목록/상세 | 옛 실행 도구를 다시 노출하지 않음. 제거는 실제 미사용/모든 pending 소진이 확인된 별도 변경에서만 |

화면 조작 뒤 새 대상 ID를 클라이언트가 임의로 바꾸지 못하도록 interaction ID와 원 대상/version을 서버가 묶는다. 권한은 요청·브라우저 완료·서버 후속 실행마다 재검사한다. 업로드 중/녹음 중 이탈은 경고하고 중단된 입력을 접수 완료로 표시하지 않는다. 완료 알림함/푸시/자동 채팅 완료 메시지는 추가하지 않는다.

## 비동기 작업별 이행

기존 `durable_jobs`와 domain별 저장소/worker를 확장한다. 새 범용 workflow 엔진은 만들지 않는다. 모든 작업은 입력 fingerprint·actor·resource·원 작업/시도 ID·stage·현재 owner token·시도 결과를 저장한다. 승인 대상은 최종 승인과 enqueue를 같은 transaction으로 저장한다. 상태 조회는 enqueue하지 않는다.

| 작업 | 현행과 목표 | 자동 복구/실패/수동 새 시도 |
|---|---|---|
| 자료 추출·색인 | 현행 attachment/extraction/chunk + durable material worker 유지. attempt fence에 heartbeat와 lease token 확인을 연결하고 현재 source 인가·actor 기록 추가 | 로컬 immutable bytes 파싱은 안전한 transient만 최대 3회, 2/4초 backoff, 한 시도 120초·총 10분 상한. 손상/미지원/권한 오류는 즉시 terminal. 실패 단계의 새 시도만 허용, integrity/parser 기준이 바뀌면 새 입력 |
| 녹음 후 전사 | 현행 recording finalization + meeting worker 유지. 파일 ID와 transcription ID를 **외부 호출 직후** 영속화, `submitting` 상태를 호출 전에 저장. 기존 요청의 GET/poll과 새 POST 구분 | GET/확실한 429 등 안전한 실패는 최대 3회·2/4초 backoff, polling 총 30분. 모호한 POST 응답은 확인 필요. ID가 있으면 기존 결과 조회; 없으면 client_reference_id로 제한적으로 조회하여 정확히 하나의 동일 입력 요청만 복구. 0개는 미실행 증거가 아니므로 새 POST 금지 |
| 전사 정제·요약 | 기존 immutable raw/refinement/summary lineage 유지. 완료 raw를 다시 전사하지 않고 실패한 stage만 실행 | 새 생성 요청은 이전 effect 유무가 확정됐을 때만 자동 재시도(최대 3회, 2/4초, stage 5분·총 15분). 응답 유실/worker 인계 후 확인할 provider receipt가 없으면 확인 필요. 수동 retry도 같은 가드 |
| 일일보고 생성 | 현재 HTTP/MCP 동기 provider 호출을 durable report job 접수로 변경. `daily_report_status`에서 동일 job의 대기/처리/완료/실패 및 report result 복원. 보고서 화면과 tool 결과를 함께 이행 | 동일 승인/입력 키는 기존 job. 완료된 source 수집 단계/생성 결과는 보존. provider effect가 불확실하면 확인 필요. 같은 3회·2/4초, 5분·총 15분 한도. 최종 실패 후 새 요청은 기존 작업에 연결된 새 attempt |
| Conversation Turn | 기존 durable conversation worker의 순서·취소·복구 계약 유지, 공통 tool 호출과 원문/대화 이력 경계를 검증 | 이 작업에서 별도 conversation 엔진을 만들지 않음. 기존 retry는 원 Turn에 연결되고 위임 승인 우회 불가 |

한도가 지나면 현재 단계의 실패 또는 확인 필요로 멈추고 저장된 성공 결과를 지우지 않는다. elapsed time만으로 외부 미실행을 단정하지 않는다. heartbeat가 끊겨 새 owner가 claim해도 모든 domain 결과 저장에 token을 비교한다. 이전 worker의 늦은 응답은 effect/result를 덮어쓸 수 없다. 권한 회수는 이미 발생한 외부 effect를 되돌리지 않으며 재실행과 새 조회를 제한한다.

검색·자료 metadata·근거 재확인은 기존 등록 자료만 읽는다. 누락된 파일 추출은 material worker의 idle maintenance가 제한된 묶음으로 등록한다. 과거 회의록/전사/보고 제출본의 자료 identity는 운영자가 `AX_JOB_QUEUE_BACKEND=postgres uv run --project backend python -m ax_workspace.entrypoints.material_backfill --member-id <member-id> --limit 100`으로 보강한다. 이 명령은 현재 회원의 읽기 범위와 과거 revision을 확인하고 미등록 자료만 등록하며, 같은 명령의 반복은 중복 자료를 만들지 않는다. 설정된 DB에 쓰므로 실행 대상과 권한을 확인한 뒤 사용한다. 일반 조회는 이 명령을 대신 실행하지 않는다.

R3e 조회 분리 검증: 자료·회의·보고 37 passed, 기존 검색/근거/upgrade·architecture 68 passed, PostgreSQL parser upgrade와 두 worker의 누락 보강 각 1 passed. 자료 worker는 접수 actor와 현재 owner 읽기 권한을 원문 접근 전에 확인하고, 각 attempt의 actor·owner token·heartbeat·결과를 보존한다. transport lease와 도메인 heartbeat를 함께 연장하며 이전 token이나 heartbeat 상한을 지난 결과는 저장하지 않는다. 안전한 transient만 2/4초 간격으로 최대 3회 재시도하고, 한 단계 120초·전체 10분을 넘으면 terminal 실패로 남긴다. 권한·손상·미지원 오류는 재시도하지 않는다. 관련 15 passed와 PostgreSQL 두 worker heartbeat 1 passed(`/tmp/scax-material-worker-bounds.log`, `/tmp/scax-material-worker-postgres.log`).

회의 전사는 Soniox의 공식 [비동기 전사](https://soniox.com/docs/stt/async/async-transcription) 및 [전사 목록](https://soniox.com/docs/api-reference/stt/transcriptions/get_transcriptions) 계약에 맞춰 file ID와 transcription ID를 각 외부 응답 직후 저장한다. POST 직전에는 `submitting`을 먼저 저장한다. ID가 있으면 GET만 사용하고, 응답이 끊긴 POST는 cursor를 끝까지 따라가며 같은 `client_reference_id`와 `file_id`가 정확히 하나인 경우만 복구한다. 0개·복수·목록 확인 실패는 미실행으로 간주하지 않고 `needs_verification`으로 끝내며 새 POST를 하지 않는다. 원격 정리는 raw 전사 저장 뒤 실행한다. 현재 recording actor 권한, attempt actor/token/stage/heartbeat/결과, 안전한 GET 3회 2/4초, 전사 polling 30분, 정제·요약 단계 5분/전체 15분 상한을 저장·검사한다. adapter/worker 30 passed와 PostgreSQL crash takeover 1 passed(`/tmp/scax-meeting-r3e-regressions.log`, `/tmp/scax-meeting-r3e-postgres.log`). 실제 provider 성공은 R3f에서 검증한다.

Soniox 공식 계약에서 `client_reference_id`는 유일성을 강제하지 않는 추적 값이다. 따라서 idempotency key로 취급하지 않는다. 목록/GET의 optional 필드는 안전하게 parse하며 임의 결과 목록을 모델에 반환하지 않는다. endpoint 문서는 terminal `error`, 오류 가이드는 `failed`를 설명하므로 두 값을 captured fixture로 검사한다. 진행 중/모호한 요청의 file/transcription을 finally에서 무조건 삭제하던 경로도 변경한다. 새로운 provider/모델 도입은 없다.

공식 확인(2026-09-11): [생성 계약](https://soniox.com/docs/api-reference/stt/transcriptions/create_transcription), [요청 목록·cursor](https://soniox.com/docs/api-reference/stt/transcriptions/get_transcriptions), [현재 요청 조회](https://soniox.com/docs/api-reference/stt/transcriptions/get_transcription), [오류](https://soniox.com/docs/api-reference/errors). 실제 수신자 발송이나 운영 DB로 probe하지 않고 fixture와 검증 전용 환경을 쓴다.

일일보고 생성은 HTTP와 MCP 모두 동일한 `ReportStatusResult`로 durable generation ID와 대기/처리/완료/실패/확인 필요 상태를 반환한다. 접수와 `durable_jobs` 등록은 같은 transaction이다. 보고 워커는 실행 직전 현재 actor와 `daily_report.generate` 권한을 다시 확인하고 attempt의 actor·owner token·heartbeat·결과를 저장한다. 첫 시도에서 workflow definition과 run을 고정하며 재시도는 완료된 source 수집·prompt render를 재사용하고 실패한 provider node의 retry count만 늘린다. 실행 전 provider 가용성 장애만 2/4초 간격으로 최대 3회 재시도한다. 시작된 실행의 결과가 불확실하거나 시간 한도를 넘으면 `needs_verification`으로 고정하고 같은 날짜의 새 요청도 해당 generation을 돌려주므로 provider를 다시 호출하지 않는다. terminal 실패 뒤 사용자가 다시 요청하면 새 generation을 만들고 `retry_of_generation_id`로 실패한 원 작업과 연결한다. 한 단계 5분·전체 15분 한도와 늦은 결과 fencing을 적용한다.

보고 화면과 오늘 화면은 재진입 시 저장된 generation을 복원하고 대기/처리 중에만 polling한다. 회의 상세도 전사와 첨부 추출이 대기/처리 중이면 같은 회의를 다시 읽으며 편집 중인 회의록 본문을 보존한다. 업무 자료 화면의 기존 추출 polling과 함께 대기·완료·실패/확인 필요를 서버 상태로 표시하며 알림은 추가하지 않는다. report worker 7 passed, 보고·회의 화면 17 passed, 관련 backend 94 passed(`/tmp/scax-report-async-regressions.log`), PostgreSQL 완료 결과 복원/동시 접수 2 passed(`/tmp/scax-report-worker-postgres.log`). 실제 Codex 보고 생성과 전체 화면 journey는 R3f에서 검증한다.

## R3 이행 slice와 증거

각 slice는 같은 branch/owner/PR의 일부다. 아래 순서를 완료 기준으로 축소하지 않는다.

| Slice | Delta / success signal | 검증 |
|---|---|---|
| R3a 구성 | bootstrap 밖 owning application 조립 제거, 동일 session 서비스 주입, mandatory dependency 명시 | architecture guard + Task/Meeting/Report/WorkRequest의 direct/approval/presenter 회귀. 기존 pending/rollback/PostgreSQL |
| R3b 조회·노출·metadata | 전수 query 연결, 내 업무·내 회의 분리, D9/D6, 매 list/call 인가, 한 metadata 정의 | 실제 MCP discovery/stdio·HTTP·화면 mapper·권한 회수, 미존재/비인가 동일 오류, 한국어 도구 선택 |
| R3c 변경·승인 | 미연결 command의 typed input/result와 owning handler, E1/E2 차단, 기존 ActionCenter final payload/receipt | 동일 actor/input 경로 동등성, 승인 재인가/stale/실패 rollback/부분 성공/동일 실패 replay·legacy pending |
| R3d 파일·녹음 화면 연결 | target-bound interaction, 취소/권한 거절/실패 구분, 기존 자료 연결, 이탈 보호 | UI + HTTP/MCP target 검증, 더미 secret 비노출, 실제 녹음 시작과 업로드 receipt |
| R3e 작업 저장·복구 | 위 작업별 durable receipt/attempt, report 비동기, 상태 polling/재진입, 소유권/외부 재확인 | deterministic fault injection·PostgreSQL 두 worker·late result·응답 유실·권한 회수·stage 재사용·새 시도·UI reload |
| R3f Acceptance·PR | 모든 inventory 행 `verified`/합의 제외, 최종 head 기본 7/7 + 독립 review + 관련 문서, commit/push/PR | 전수 coverage가 7개 journey로 대체되지 않음. 실제 provider/MCP/UI/PostgreSQL evidence 및 review finding 처분 |

검증 DB 이름은 `ax_test_unified` / `ax_test_unified_acceptance` (전용 `scax-unified-postgres`, host port `54339`), API/웹 포트는 `18231` / `15231`로 격리하고 사용 여부를 시작 전에 검사한다. 자료/녹음은 이 작업 전용 임시 경로다. 공유 기본 DB·port나 다른 checkout을 reset하지 않는다. final-head review는 code-review 스킬의 독립 reviewer로 수행한다. merge/deploy/production migration/사용자 데이터 삭제는 별도 gate다.

기존 pending을 읽거나 승인할 수 없거나 권한/원자성이 약해지면 해당 slice를 멈추고 원인을 해결한다. 새로운 제외나 사람 판단 의미 변경이 필요하면 사용자에게 묻는다. D1~D11/E1/E2 안의 타입·등록·인덱스·테스트 설계 선택에는 재승인을 요구하지 않는다.

### R3a 구성 검증 checkpoint

- baseline의 runtime 전체 capability discovery는 42개이며 정적 MCP inventory와 집합이 일치했다. 개별 actor의 동적 인가/호출은 R3b에서 검증한다.
- `ActionServices`의 명시적 typed factory는 bootstrap의 현재 session에 묶인다. Task-origin과 Action-preview 사이의 순환만 지연하며 문자열 service lookup은 없다. platform의 두 번째 ActionCenter 구성과 fake recording/key adapter를 제거했다.
- 검증 DB의 첫 이름은 reset 보호 규칙(`ax_test_*`)에 맞지 않아 접속 전에 거절됐다. 보호 규칙을 유지하고 전용 컨테이너 안에 `ax_test_unified`를 생성해 검증한다.

- 검증 결과: backend 비통합 705 passed, 동시 승인 보강 후 ActionCenter/MCP 78 passed, PostgreSQL 54 passed. 전수 route/tool 등록 집합은 baseline 125/42와 일치. 실제 provider/기본 7개 UI journey 및 최종 독립 review는 R3f에 남아 있다.
- 동시 승인 회귀에서 두 요청이 같은 offered envelope를 읽은 뒤 하나가 stale로 끝나는 경쟁을 확인했다. HTTP 진입 전 barrier를 owning handler 실행 직전으로 옮겨 결정적으로 재현하고, WorkRequest row lock 뒤 payload-aware receipt를 다시 확인하여 단일 effect와 같은 결과를 반환하게 했다. 서로 다른 payload/version의 stale 거절과 rollback은 유지된다.
- R3c 잔여: 동일 owning application 주입만으로 전체 효과 동등성을 선언하지 않는다. 직접 경로의 native material 등록과 승인 경로의 생성 부수 효과, 최종 payload 사용 및 typed result를 계속 대조한다.


### R3b 조회·등록 checkpoint

- `TaskApplication.my_work`/`readable_tasks`, `MeetingApplication.my_meetings`/`readable_calendar`로 목적을 분리했다. `my_task_list`와 `my_meeting_list`는 본인 관계를 고정하며 기존 `task_list`/`meeting_list`는 열람 범위만 조회한다. 공개 `mine`/`include_visible` 전환을 제거하고, 이전 인자를 보내면 오류로 거절한다. 회의 캘린더의 비인가 busy row를 서버와 화면에서 제거했다.
- 같은 MCP server의 list/call이 현재 Principal을 다시 조회한다. 등록은 권한과 분리하여 권한 부여 시 재연결 없이 도구가 다시 나타난다. 설치 SDK 공개 hook으로 stdio에도 적용하며 권한 회수된 도구의 직접 호출은 Unknown tool이다.
- `tool_catalog.py`가 도구 ID·한국어 title·description·노출 정책·명시적 adapter binding을 소유한다. 실제 callback signature에서 schema가 생성되고, 모든 callback 등록과 catalog 집합이 일치해야 server가 시작된다. 기존 한국어 실행 내역 mapper도 같은 title을 사용한다. `task_material_search`/`task_transition`은 과거 표시용 이름만 남는다.
- 현재 runtime은 HTTP 126개, 전체 capability MCP 71개이며 title 누락과 활성 legacy ID는 없다. 기존 42개에서 목적 분리 2개와 기존 화면의 미연결 조회 27개를 연결했다. 자료 원문 열기 5개는 owning read로 원본 접근을 검증하고 인증된 브라우저 URL/metadata만 반환한다.
- 특정 대상의 미존재/열람 불가를 owning `ResourceNotFound`로 표현하여 HTTP 404/MCP 오류의 같은 메시지로 옮긴다. 기능 권한 거절과 검색 내부 장애는 빈 목록 성공으로 바꾸지 않는다. 대화의 이미 전달한 본문은 보존하고, 현재 source 카드·원문 링크·새 검색은 다시 인가한다. 관계/근거 projection과 판단 카드가 임의 예외를 비인가로 삼키지 않도록 명시적 거절만 제외한다. 회의 생성 카드의 같은 조직 비공개 회의 충돌 건수도 현재 owning calendar 조회 안에서만 계산한다.
- 전체 backend 2차는 733 passed/1 failed였으며 남은 1개는 비인가 판단 상세의 새 404를 기대하도록 고친 뒤 해당 evidence suite 12 passed. 이전 전체 실패 경로 90 passed, 조회/답변/관계 69 passed, 판단 항목/비공개 충돌 79 passed, discovery/architecture 19 passed, 자료 검색 오류 13 passed, 회의 화면 5 passed, frontend build passed. 최종 전체 검사는 R3f에서 다시 실행한다. 실제 한국어 도구 선택 쌍과 최종 7/7은 R3f에서 검증한다. inventory의 `connected`는 개별 Acceptance `verified`나 전체 완료가 아니다.
- R3c/R3e 후속: input/result의 도메인 타입 공유, 모든 승인 경로의 최종 payload·effect 동등성, read 시 material extraction 접수의 제거는 남아 있다. R3b 등록 구조만으로 이를 완료 선언하지 않는다.

### R3c 승인 효과 checkpoint

- 승인 executor는 진입 시 확정 payload를 한 번 선택하고 하위 실행 전체에 전달한다. 업무 수정·체크리스트·상태·보고서 본문이 저장된 제안 원안을 다시 읽던 회귀 4건을 실제 변경 결과로 검증했다. 기존 pending의 payload fallback은 진입 경계에만 유지한다.
- 회의 노트 version과 보고서 제출의 원본 자료 등록·추출 접수를 owning command 안으로 옮겼다. bootstrap은 같은 session에 묶인 필수 publisher를 제공한다. 직접 facade의 별도 등록 코드를 제거해 승인 실행도 같은 효과를 저장한다. 저장 직후 검색 없이 자료/접수를 확인하는 회귀를 추가했다.
- 원본 노트 직렬화는 저장된 revision을 다시 읽어 구성한다. SQLite의 timezone 저장 정규화 때문에 같은 session의 작성 객체와 후속 원문 읽기 사이에 hash가 달라지던 재현을 해결했다.
- 검증: 최종 payload/기존 MCP 승인 30 passed; 회의·보고·판단·architecture 104 passed/2 failed 후 legacy fixture와 실제 hash 문제 수정; 관련 최종 26 passed. PostgreSQL 접수 실패 시 승인/업무/자료/job rollback, 동일 승인 재전송, 동시 승인 단일 효과 2 passed. 로그 `/tmp/scax-unified-native-effects-final.log`, `/tmp/scax-unified-native-effects-postgres.log`.
- R3c는 계속 진행 중이다. 미연결 변경의 명시적 typed 명령과 input/result 공유, 최종 수정 승인 확장 및 실패 outcome 재전송 계약이 남아 있다. R3d~R3f 및 전체 Acceptance는 별도 검증한다.
- 회의 변경 5개(`meeting_update`, `meeting_revoke_share`, `meeting_note_create`, `meeting_note_save`, `meeting_note_finalize`)를 연결했다. 회의 도메인의 Pydantic input/target command를 HTTP request, MCP callback/facade, 승인 executor와 owning method가 공유한다. 직접 호출은 같은 명령을 실행하고 delegated 호출은 대상과 효과를 보여 주는 승인 카드까지만 만든다. 현재 이 5개는 기존 불변 `approve` 경로이며 수정 가능한 `confirm` editor와 typed result는 계속 이행한다.
- 회의 수정은 생략과 명시적 description null을 구분한다. 생성/변경 시각은 UTC로 저장하고, 빠른 SQLite 검증에서도 제목만 수정할 때 저장 시각의 timezone 소실로 실패하지 않도록 읽기 기준을 일치시켰다. 새 명령 직접/승인 효과와 null/생략/offset 회귀, 기존 MCP/회의 검증 75 passed. 현재 HTTP 126·MCP 76이며 inventory의 연결 상태를 갱신했다. 로그 `/tmp/scax-unified-meeting-commands-regressions.log`.
- 프로젝트 생성·참여 배정/해제·담당 미정 계획 4개를 공통 입력과 명시적 승인 실행으로 연결했다. 승인 결과는 모든 업무에 `execution_result`로 제공하며, 프로젝트 참여 해제로 현재 원문 열람을 잃어도 본인 승인 결과를 다시 읽는다. 동적으로 인가된 preview는 이후 열람 범위에 맞춰 바뀔 수 있다. 관련 기존+신규 23 passed, 자기 참여 해제 후 승인 결과 재전송을 포함한 최종 9 passed.
- 작업 중 PR #8이 `2100747320ae60718c1b131e87890318f72570ec`로 main에 병합됐다(2026-09-11 09:12:57 UTC). 다음 단계에서 이 merge를 feature branch에 반영하고 참여 이력 조회 및 종료 회차/사유 계약을 전수 mapping과 AX 명령에 이행한다. 위 프로젝트 검증은 반영 전 기준이며 병합 뒤 대응 회귀를 다시 수행한다.
- PR #8 통합은 `bf0270e`에 완료했다. 입력 타입 alias와 결과 반환을 유지하면서 기존 종료 회차 lock·이력·재참여 계약을 보존했다. 병합 뒤 프로젝트/가져오기 계약 43 passed, PostgreSQL 동시 참여 및 종료/재참여 2 passed. 로그 `/tmp/scax-unified-pr8-merge-contract.log`, `/tmp/scax-unified-pr8-merge-postgres.log`.
- AX 참여 해제는 `assignment_id`를 필수로 받아 원안부터 종료 회차를 고정한다. HTTP의 기존 선택적 회차·사유 입력을 도메인 타입으로 공유하며, `project_participation_history`도 현재 프로젝트 열람 권한으로 연결했다. 오래된 종료 승인 뒤 재참여 회차와 새 grant가 유지되고 과거 종료 사유가 덮이지 않는 실제 도구 회귀를 추가했다. 프로젝트/등록 30 passed, 새 회차 승인 및 인가된 이력 조회 포함 최종 10 passed. 현재 HTTP 127·MCP 81. 로그 `/tmp/scax-unified-project-history-tools.log`, `/tmp/scax-unified-project-round-approval.log`.
- 자료함 생성/보관/자료 분리 3개를 필수 `ActionServices.material_folders` 구성과 공통 입력으로 연결했다. 도메인의 자료함/보관/분리 결과에 명시적 타입을 두었고, 직접/승인 경로에서 원본 bytes 보존과 승인 전 무변경 및 결과 재전송을 검증했다. 관련 25 passed. 현재 HTTP 127·MCP 84. 전체 transport result 공유와 수정 confirm은 여전히 R3c 잔여다.
- `test_operation_inventory.py`는 실제 MCP 등록 schema/title/ID 집합과 inventory, HTTP 선언과 baseline+명시적 추가 operation 집합을 비교한다. 도구/route 추가나 schema 변경 후 inventory 누락은 실패한다. 등록 검증을 업무 Acceptance로 취급하지 않는다. 로그 `/tmp/scax-unified-folder-commands-green.log`, `/tmp/scax-unified-inventory-drift.log`.
- 참고 업무 연결/해제와 완료 보고 3개를 공통 입력과 직접/승인 명령에 연결했다. 완료 보고의 선택한 산출물을 현재 owning material read로 카드에 표시하며 `ActionServices.materials`는 같은 session의 필수 의존성이다. 승인 후 업무는 `completion_submitted`이고 요청자의 별도 결과 판단 하나가 열린다. 참고 해제는 기존 연결 이력을 보존한다. 관련 task delivery/reference/등록 22 passed, 구성+preview 18 passed, 결과 자료를 포함한 최종 도구 효과 6 passed. 현재 HTTP 127·MCP 87. 로그 `/tmp/scax-unified-task-commands-regressions.log`, `/tmp/scax-unified-task-command-output.log`.

- 업무 재배정은 공통 입력과 승인 명령 `task_reassign`에 연결했다(HTTP 127·MCP 88). 배정 권한만으로 읽을 수 없는 업무를 가져오는 경로를 차단하고 최초 배정/재배정/직접 배정이 동일 수락 원장을 생성하도록 통합했다. 담당 미정 업무의 첫 배정도 업무 버전을 소비하며 응답은 갱신된 배정을 반환한다. 계약 회귀 87 passed, PostgreSQL 최초/재배정 경합 및 새 담당자 수락 2 passed. 기존 배포 데이터의 원장 없는 pending 재배정은 R3c legacy compatibility 검증 대상으로 남긴다. 로그 `/tmp/scax-unified-reassign-ledger-regressions.log`, `/tmp/scax-unified-reassign-ledger-postgres.log`.
- 업무 자료 링크/회의 참조/분리 3개를 공통 입력과 명시적 승인 실행에 연결했다(HTTP 127·MCP 91). 승인 전 무변경·같은 승인 결과 재전송·원본 bytes 보존 6 passed. URL은 승인 초안 저장 전에도 http(s)/credential 금지를 검증한다. 기존 링크·참조/업무/프로젝트 회귀 30 passed 뒤 URL validator의 예외를 HTTP 422로 정규화하고 관련 14 passed. 로그 `/tmp/scax-unified-task-material-url.log`.
- 회의 자료 링크/분리·요약 채택·화자 확인과 후속 내 업무/업무 요청 6개 도구를 연결했다(HTTP 127·MCP 97). 후속 생성의 기존 bootstrap orchestration을 필수 session-bound `MeetingFollowupApplication`으로 이동했고, summary 행 잠금 안에서 이미 승격된 문장을 재사용한다. 직접/승인 효과·수락 판단 유지·receipt replay 12 passed, 기존 회의/구성 회귀 57 passed. PostgreSQL 승인 도중 실패는 업무·참조·승격·판단을 모두 rollback하며, 직접 경로와 승인 경합 뒤에도 한 업무/참조/승격만 남음을 검증했다. 로그 `/tmp/scax-unified-meeting-followup-postgres.log`.
- 회의 소유자 변경 경로의 원본 읽기 확인 누락도 보강했다. 읽을 수 없는 회의와 없는 회의의 자료 변경은 동일 404이고, 읽을 수 있으나 변경 자격이 없는 참석자의 기존 403은 유지된다. 최종 회의 변경/원본 권한 15 passed (`/tmp/scax-unified-meeting-owner-green.log`).
- 캐릭터 설정/알림 읽음/업무 요청 댓글 3개를 공통 입력·승인 실행에 연결했다(HTTP 127·MCP 100). 캐릭터 allowlist는 도메인 schema가 소유하고 알림 조회/읽음은 필수 `NotificationApplication`으로 모았다. 원본 접근 상실은 알림을 숨기고 읽음 변경을 거부하지만 예상 밖 owning 오류를 빈 목록으로 삼키지 않는다. 댓글은 요청 상태/버전과 별개이며 같은 승인 replay는 댓글 하나를 남긴다. 관련 구성/기존 계약 36 passed, 최종 알림 경계/도구 9 passed (`/tmp/scax-unified-personal-command-regressions.log`, `/tmp/scax-unified-notification-boundary-green.log`).
- 대화 생성/메시지 접수/응답 취소/재시도 4개를 공통 입력과 필수 session-bound ConversationApplication에 연결했다(HTTP 127·MCP 104). 메시지 결과는 접수/큐 상태이며 생성 답변으로 표시하지 않는다. 승인 카드의 대화 제목은 action 카드를 재귀 렌더링하지 않는 owning projection으로 읽는다. 같은 대화 자체의 취소도 정상이며 다른 대화의 turn과 없는 turn 재시도는 동일 404이다. 직접/승인 8 passed, 추가 경계·기존 lifecycle·구성 31 passed (`/tmp/scax-unified-conversation-command-regressions.log`).
- 판단 항목의 링크 자료 준비/자료 초안 버리기 2개를 공통 입력과 승인 경로에 연결했다(HTTP 127·MCP 106). 자료 준비 승인만으로 업무·회의는 생성되지 않으며, 생성 confirm이 선택한 draft만 연결한다. 원본 생성 Action을 잠근 뒤 상태를 refresh하므로 다른 요청이 먼저 확정한 항목에 늦게 자료를 준비하지 못한다. 직접/승인 4 passed, stale ORM 상태·기존 초안/구성 23 passed (`/tmp/scax-unified-action-material-command-regressions.log`).

### R3c 수정 확정 확장

- 신규 도메인 명령 35종은 typed 최종 draft를 확인하는 공통 `command` 편집 계약을 사용한다. 대상 ID·근거 회차·기준 버전은 고정하고 실제 편집 가능한 업무 값만 제공한다. 이전에 전달된 `approve`는 내부 호환 명령으로만 유지하며 새 카드에는 `confirm`을 표시한다.
- 채팅과 판단 Drawer가 같은 편집기를 사용하고 사용자/Action/Submission별 입력 복구를 적용한다. 실패 시 입력을 보존한다. nullable과 생략 가능성을 구분한 `empty_policy`로 명시적 삭제·변경 안 함·빈 값 금지를 전달한다. 미인가 대상을 선택지에 노출하지 않는다.
- 검증: 직접/legacy 승인/confirm 명령 효과 회귀 107 passed·16 failed(import 누락) 후 해당 suite 26 passed. 별도 최종 수정·고정 대상·empty 정책·승인 권한 회수 8 passed. PostgreSQL 동시 수정 confirm 1 passed(업무 결과 1, 최종 Submission 1, ReviewDecision 1). UI 기존 연결/복구 75 passed와 empty 정책 4 passed, build passed. 로그 `/tmp/scax-unified-all-command-confirms.log`, `/tmp/scax-unified-all-command-confirm-fixes.log`, `/tmp/scax-unified-extended-authority.log`, `/tmp/scax-unified-extended-concurrent.log`.
- 독립 검토의 nullable/생략 혼동 P2를 수정하고 재검토에서 commit-ready를 받았다. 실제 브라우저 journey와 전체 PR 독립 검토는 R3f에 남아 있다. 기존 명령들의 typed input/result 공유·수정 확정 및 실패 outcome/legacy pending 작업은 계속 진행한다.

### 과거 pending 배정 호환

- 수락 원장 없이 남은 과거 재배정은 조회로 쓰기를 발생시키지 않는다. 권한 있는 수락/거절/취소 명령 안에서 현재 확인한 업무 내용을 새 Submission으로 캡처하고 `legacy_assignment_id`와 실제 캡처 시각을 기록한다. 기존 배정 시각·Submission은 변경하지 않는다. 오류 시 보완 원장과 판단/effect를 함께 rollback한다.
- 판단과 재배정은 Task→배정 순서로 잠그고 캐시된 Task를 갱신한 뒤 기준 버전을 확인한다. 원장이 있는 배정 이력은 실제 immutable Submission/ReviewDecision을 읽으므로 수락 후 업무 편집으로 과거 내용이 바뀌지 않는다.
- 검증: 과거 배정 호환/권한/rollback/캐시 버전/이력과 ActionCenter 58 passed, 기존 배정/재배정 17 passed, PostgreSQL 수락↔취소·동시 재배정·최초 배정 3 passed. 로그 `/tmp/scax-unified-legacy-history-regressions.log`, `/tmp/scax-unified-assignment-lock-postgres.log`.

### D8 실패 결과의 경계

D8 Acceptance의 단건 transaction 실패와 일괄 항목 실패를 구분한다. 단건/예기치 않은 전체 실패는 최종 승인·effect 전체를 rollback한다. 부분 성공을 허용한 일괄 작업은 전부 실패한 경우까지 항목 결과를 저장하며 동일 승인 재전송은 성공/실패 항목을 모두 재실행하지 않는다. 실패 항목을 다시 실행하려면 현재 버전을 읽어 새 제안·사람 확인을 거치며, 실행 직전 권한도 다시 검사한다. 범용 실패 승인 원장을 별도로 도입하지 않는다.

회귀 5 passed: 전체/부분 실패 receipt 재전송 시 owning method 호출 금지, 새 제안/확정의 현재 version·권한 회수, 예상 밖 두 번째 항목 오류 시 앞선 savepoint와 최종 승인 전체 rollback. 로그 `/tmp/scax-unified-batch-failure-receipts.log`. 이 검증은 비동기 작업 시도 원장(D11)을 대신하지 않는다.

### 기존 업무 수정 명령 이행

- 화면의 `TaskUpdateInput`, canonical `TaskEditFields`/`TaskUpdateCommand`와 Task mutation 결과 타입을 연결했다. 업무 수정의 생략/명시적 삭제, UUID/date parsing을 owning command에서 공유한다. MCP에 날짜 삭제·프로젝트 연결/해제 인자를 추가하고 현재 등록 schema/설명을 inventory에 갱신했다.
- `task.update`도 수정 confirm을 제공한다(공통 editor 36종). 옛 nested `changes` pending은 typed reader가 읽으며 원본 Submission은 그대로 두고 최종 수정본만 실행한다. 삭제와 프로젝트 이름을 승인 preview에 표시한다. 빈 변경/잘못된 입력은 422이며 업무 버전을 바꾸지 않는다.
- Task의 기본 mutation result와 assignment/lineage 타입을 owning method·bootstrap·HTTP 응답이 공유한다. MCP의 승인 proposal union을 포함한 나머지 전체 typed result 이행은 아직 남아 있다.
- 직접 HTTP/MCP/최종 confirm·기존 pending·입력 오류 7 passed, 기존 업무/자료/체크리스트 16 passed, inventory 포함 최종 9 passed. 로그 `/tmp/scax-unified-task-edit-regressions.log`, `/tmp/scax-unified-task-edit-inventory.log`.

### 기존 체크리스트 명령 이행

체크리스트 추가·수정·정리·순서 변경 4종의 typed 입력을 HTTP/MCP/승인 executor/owning method에서 공유하며 mutation 결과도 owning method·bootstrap·HTTP에서 같은 타입을 사용한다. 단계와 기준 버전은 고정하고 최종 내용/완료 여부/순서를 수정 confirm한다(공통 editor 40종). 순서는 현재 읽을 수 있는 단계 이름과 위/아래 버튼으로 편집한다. 화면과 마찬가지로 MCP에서도 optional `expected_task_version`을 전달할 수 있고, 단계 자체의 `expected_version`과 구분한다. description/schema/inventory를 함께 갱신했다.

최종 confirm/legacy 승인/기존 단계/생성 회귀 23 passed, 최종 내용·순서·MCP stale 가드 8 passed, inventory 2 passed, UI 편집/복구/순서 5 passed와 build passed. 직접 SDK 호출의 ToolError throw 계약에 맞춰 stale 검증 fixture를 수정했다. 로그 `/tmp/scax-unified-checklist-contract-regressions.log`, `/tmp/scax-unified-checklist-contract-final.log`, `/tmp/scax-unified-checklist-order-ui.log`.

### 기존 업무 상태 명령 이행

상태 변경은 같은 typed Task version/target/reason 명령과 mutation result를 HTTP·MCP·승인·owning method에서 공유한다. `task.transition`의 수정 confirm(공통 editor 41종)은 대상 업무·버전·변경 상태를 고정하고 차단 사유만 편집한다. nullable 표기와 별개로 차단 사유는 빈 값을 금지한다. 완료 보고가 필요한 업무의 requester review, 하위 업무 완료 조건, 최초 시작일과 취소 terminal 의미를 유지한다. 다섯 도구의 설명은 선택 목적과 현재 버전/권한·확인 효과를 명시한다.

상태별 최종 confirm/대상 변경 거절·업무 필드·완료 보고 회귀 18 passed, 최종 editor/inventory 6 passed. 로그 `/tmp/scax-unified-task-transition-green.log`, `/tmp/scax-unified-task-transition-final.log`.

### 내 업무 생성 입력 이행

`TaskCreateInput`을 화면 request·MCP facade·초안 정규화·승인 executor·owning create가 공유한다. 제목 저장 한도 300자를 입력과 tool schema에서 검증하고, 업무 mutation result를 owning create·bootstrap·HTTP가 공유한다. 일정/초기 체크리스트 값 규칙과 오류 클래스를 하위 모듈로 옮기되 기존 application import 이름은 유지해 순환 의존 없이 같은 규칙을 사용한다. 생성 근거/첨부 선택은 승인 transaction의 별도 명시적 값으로 보존한다.

업무 생성·초기 단계·승인/첨부 회귀 66 passed. 수정 중 회의 분기에 생긴 회귀는 복구 후 함께 통과했다. HTTP/MCP/위임 제안의 입력 제한·정규화·inventory 최종 6 passed. 로그 `/tmp/scax-unified-task-create-contract-final.log`, `/tmp/scax-unified-task-create-input-final.log`.

### 업무 배정 생성 입력 이행

업무 생성의 공통 값에 담당자를 더한 `TaskAssignmentInput`을 화면·MCP·최종 초안·승인 executor·owning assign에서 공유한다. 상위/참고 업무 입력을 MCP와 HTTP에 연결하고 기존 수락 판단은 그대로 유지한다. 배정 mutation result도 owning application·bootstrap·HTTP에서 공유한다. 과거 정규화가 덧붙였던 `project_id: null`은 pending/hash 읽기 호환으로만 소비하며, 배정 생성 owner가 처리하지 않는 non-null 필드를 조용히 무시하지 않는다.

HTTP/MCP/수정 confirm의 상위/참고 업무·수락 후 효과 및 기존 배정/초기 단계 11 passed, 생성 입력·inventory 포함 최종 9 passed. 로그 `/tmp/scax-unified-assignment-create-green.log`, `/tmp/scax-unified-assignment-create-final.log`.

### 업무 요청 생성 입력 이행

업무 요청 생성의 typed 입력·최종 초안 정규화·executor·owning create와 mutation result를 연결했다. 요청자/수신자 CC 제거, 중복 참조 제거, 일정·초기 단계 정규화가 같은 의미를 사용한다. 실제 도구 schema와 설명에 제목 저장 한도와 수락 전 효과를 반영했다. 요청 생성 확인은 수신자의 수락 판단을 대신하지 않는다.

직접 HTTP/MCP/수정 confirm의 정규화와 별도 수락·기존 초기 단계 7 passed, 기존 ActionCenter와 inventory 포함 58 passed. 로그 `/tmp/scax-unified-request-create-green.log`, `/tmp/scax-unified-request-create-final.log`.

### 요청 수정·재제출 입력 이행

요청자가 보강하는 amendment와 조정 뒤 resubmit은 공통 revision 입력·mutation result를 사용한다. `work_request.amend`는 대상/기준 버전을 고정하고 최종 수정 confirm을 제공한다(공통 command editor 42종). 생략/null은 보존, 빈 설명은 삭제, `clear_due_date`는 기한 삭제라는 기존 의미를 유지한다. 이력의 원안과 담당자의 별도 수락 판단은 바꾸지 않는다. UI는 설명 삭제값을 복구 후에도 빈 문자열로 전송한다.

직접 HTTP/MCP·최종 수정/재전송·원안 보존과 기존 amendment 14 passed, UI 6 passed. ActionCenter/inventory 회귀는 `/tmp/scax-unified-request-revision-final.log`에 기록한다.

### 회의 생성과 결과 형식 이행

회의 생성의 공통 입력은 조직·제목·일정·공개 범위·참석자 정규화를 소유한다. 기존 회의 editor는 이 입력을 확장한 typed draft로 초기 회의록·서버가 고정한 출처·참고 업무를 함께 확정한다. HTTP/MCP/승인에서 공백 설명·제목 저장 한도·UTC 일정이 같다. 공유도 같은 `MeetingShareCommand`를 통과한다.

회의 목록/상세·변경 receipt, 회의록의 불변 버전, 녹음/전사/정제/요약/화자/자료/추출 상태에 실제 필드 타입을 정의하고 owning application·bootstrap·HTTP와 MCP 조회에 연결했다. MCP 변경 도구의 proposal/result union은 후속 작업이다. 생성/첨부/기존 승인 96 passed, 회의 결과/녹음/자료 46 passed, 생성/조회/inventory 32 passed, 실제 MCP 상세와 HTTP의 중첩 회의록 비교 포함 최종 5 passed. 로그 `/tmp/scax-unified-meeting-create-regressions.log`, `/tmp/scax-unified-meeting-results.log`, `/tmp/scax-unified-meeting-create-final.log`, `/tmp/scax-unified-meeting-result-mcp.log`.

### 보고 수정·제출 확인 이행

보고 수정과 제출은 서로 다른 공통 입력/receipt를 사용한다. 수정 confirm은 본문과 포함/제외할 업무 기록을 편집하고 대상 보고·초안·버전을 고정한다. 제출 confirm은 해당 초안의 실제 본문과 근거 수를 보여 주고 제출 사유만 편집한다. 원래 보고 초안은 보존되며 수정 자체는 제출하지 않는다(공통 command editor 44종).

근거는 task/version/발생 시각의 정체성과 추가로 저장된 JSON metadata를 보존한다. UI에서는 업무 이름으로 선택하며 복구 후에도 전체 근거 객체를 전송한다. 별도 report.read 권한이 없으면 이미 제안에 들어 있는 근거만 선택지로 사용한다. 직접/최종 승인/재전송/근거/원안 검증은 3 passed, 기존 executor 포함 최초 30 passed/1 import 오류 수정 후 통과했다. 최종 보고/원본 자료/inventory 33 passed, UI 7 passed/build passed. 로그 `/tmp/scax-unified-report-confirm-final.log`, `/tmp/scax-unified-report-source-ui.log`, `/tmp/scax-unified-report-source-build.log`.

### 배정 판단 재전송·과거 내용 보호

동시에 제공된 배정 수락/거절 두 요청 중 뒤 요청이 422가 되는 문제를 PostgreSQL barrier로 재현했다. Task→배정 잠금 뒤 저장된 같은 판단을 확인한다. 새 판단에는 소비한 Task 버전을 기록하고, 기존 판단은 연결된 불변 Submission 버전에서 읽는다. 이후 Task 수정이나 재배정은 이미 끝난 수락의 receipt를 지우지 않는다.

닫힌 배정의 preview/receipt는 원래 제출된 내용과 버전을 표시한다. 재배정 후 원문 권한이 사라진 이전 담당자에게 현재 담당자의 새 제목/설명이 보이는 회귀를 재현해 막았다. 캡처가 없는 과거 완료 행은 현재 Task 내용을 과거 사실로 대신 표시하지 않고 `legacy_unavailable`로 구분한다. 배정/legacy/ActionCenter 65 passed, 최초 PostgreSQL 동시 replay 2 passed. 최종 PG 로그 `/tmp/scax-unified-assignment-replay-postgres-final.log`.

### 요청·배정 판단 입력/result 이행

요청 수락/거절/조정과 배정 수락/거절은 공통 version·reason·조건 입력 및 owning result를 HTTP·호환 MCP facade·기존 승인 executor에서 공유한다. 조정 조건은 JSON metadata를 유지하고, 기존 구조화된 변경 검증을 그대로 거친다. 새 독립 판단 도구는 등록하지 않으며 현재 ActionCenter 판단 목적을 유지한다. 과거 배정 수락 payload의 무시되던 reason은 호환 parser에서만 제외한다. 판단/요청/배정 69 passed, legacy/replay/inventory 최종 10 passed(`/tmp/scax-unified-decision-contracts.log`, `/tmp/scax-unified-decision-contracts-final.log`).

보낸 배정 목록의 추가 읽기 확인에서는 원래 배정자가 Task의 읽기 관계를 유지하는 기존 계약을 확인했다. 재배정 사실만으로 이 관계를 제거하는 변경은 하지 않았다. 이전 담당자의 접근 상실과 구분한다.

### MCP 제안·실행 결과 계약 이행

업무 생성/배정/수정/상태/체크리스트, 요청 생성/수정, 회의 생성/공유/수정/회의록/자료/요약 채택/화자, 보고 수정/제출의 27개 공개 도구는 owning 결과와 승인 제안을 구분하는 출력 계약을 사용한다. 일괄 진행 도구는 제안 전용 결과를 선언한다. MCP SDK 2.1.1의 일반 union 래핑을 피하도록 RootModel을 사용하여 기존 최상위 task_id/action_id 등의 JSON 위치를 유지한다. 제안의 편집 선택지·미리보기·자료 receipt를 포함하며 보고 근거는 캡처된 추가 JSON 필드와 원래 키 존재 여부를 보존한다.

실제 SDK의 직접 실행/승인 제안과 저장된 응답 동등성을 확인했다. 업무·회의·요청 67 passed, 보고·회의 자료 22 passed, 최종 보고/MCP 결과/inventory 6 passed. 상세 로그는 `/tmp/scax-unified-result-coverage.log`, `/tmp/scax-unified-result-report.log`. 전체 도구의 타입 이행이나 R3c 완료를 뜻하지 않는다.

### 공통 판단 입력·첨부 선택 이행

HTTP의 ActionCommandRequest를 공통 ActionCommandInput으로 옮기고 MCP도 같은 입력을 정규화한다. 기존 MCP에 없던 attachment_draft_ids를 UUID 목록으로 연결했다. 직접 승인에서 선택한 초안만 연결하며 빈 선택도 보존한다. 같은 선택의 재전송은 저장된 receipt를 반환하고 선택이 다른 재전송은 거부한다. 위임 턴이 AX 제안을 직접 승인하지 못하는 기존 정책을 유지한다. MCP 판단/첨부 계약 34 passed(`/tmp/scax-unified-common-action-input.log`), inventory 2 passed. 전체 ActionEnvelope 결과 타입은 다음 이행 대상이다.

### 공통 판단 envelope 결과 이행

ActionEnvelope/상세/명령 결과와 이력의 제출·근거·판단·댓글 첨부 필드를 공유 타입으로 연결했다. 동적 원안/수정 diff/실행 결과는 도메인별 JSON을 유지하고, AX 편집 계약과 자료 receipt도 보존한다. HTTP 3개 경로와 MCP 판단 조회/명령이 같은 결과를 사용하며 MCP 명령은 envelope 또는 승인 제안을 기존 최상위 형태로 반환한다. 실제 SDK 댓글 첨부·조정 사유/제안·완료 이력 동등성 및 기존 판단 29 passed(`/tmp/scax-unified-action-envelope-results.log`), HTTP 적용 후 ActionCenter/배정/완료보고/legacy/SDK 71 passed(`/tmp/scax-unified-action-envelope-http.log`).

### 업무 조회 결과 이행

개인/읽기 가능 업무 목록, 상세, 변경 이력/비교, 하위 업무/체크리스트의 이름 있는 결과 타입을 공유한다. 담당자·요청/배정 출처와 원문 링크, 현재 읽을 수 있는 하위 업무, 참고 업무, 완료 보고 상태를 보존한다. 읽기 전용 상세에 없는 checklist/references는 optional이며 과거 snapshot은 캡처된 JSON을 그대로 운반한다. 출처/하위/참고/이력/담당자/완료 보고 47 passed(`/tmp/scax-unified-task-query-results.log`), 실제 SDK의 7개 조회와 HTTP 동등성·inventory 3 passed.

### 요청 조회·댓글 결과 이행

요청 목록/상세와 timeline의 제출 원안·수정 diff·근거 manifest·검토 배정/판단·활동·댓글을 실제 필드 타입으로 공유한다. 근거 추가로 이동한 최신 요청 버전을 판단하고도 캡처된 근거와 댓글을 구분해 전달한다. 댓글 명령은 댓글 결과 또는 승인 제안을 기존 JSON 형태로 반환한다. 요청 근거/수정/실제 SDK 조회 13 passed(`/tmp/scax-unified-request-query-results.log`), 댓글 등 개인 명령 10 passed(`/tmp/scax-unified-request-comment-results.log`), inventory 2 passed. 현재 공개 도구 106개 중 제안/실행 union 계약 29개이며 나머지 변경 결과 타입은 계속 이행한다.

### 기존 owning 결과의 MCP 연결

자료 초안 링크 준비/버리기, 대화 메시지 접수/재시도, 알림 읽음, 캐릭터 변경, 자료함 생성/보관/분리, 프로젝트 참여 해제 10개 도구에 이미 정의한 owning 결과를 연결했다. 자료함·알림 목록도 이름 있는 결과 타입을 사용한다. 직접/승인/최종 confirm/replay 관련 54 passed(`/tmp/scax-unified-existing-command-results.log`), 조회 동등성/inventory 27 passed(`/tmp/scax-unified-existing-result-queries.log`). 공개 106개 도구 중 제안/실행 union 계약은 39개다.

### 프로젝트 조회·생성/배정/계획 결과 이행

프로젝트 기본/상세, 현재 참여자, 종료 이력과 계획 업무를 별도 결과 타입으로 공유한다. 생성·참여 배정·담당 미정 계획·업무 재배정 MCP도 owning 결과/제안 계약을 사용한다. 프로젝트 33 passed(`/tmp/scax-unified-project-result-contracts.log`), 실제 SDK 현재/과거 회차 조회 동등성과 별도 배정 수락·inventory 6 passed. 공개 도구 106개, 제안/실행 union 계약 43개다.

### 업무 자료·참고·완료 결과 이행

업무 자료 조회와 파일/링크/내부 참조 연결·분리 결과는 추출 상태, 현재 원문 참조, 변경 가능한 출처 여부, 원본 삭제 상태와 소비 후 Task 버전을 공유 타입으로 전달한다. 참고 업무 연결/해제와 완료 보고도 각 owning 결과를 사용하며 완료 보고 이후 별도 판단 상태를 유지한다. 관련 43 passed(`/tmp/scax-unified-task-material-results.log`), 실제 SDK 자료 조회/명령 동등성과 inventory 11 passed(`/tmp/scax-unified-task-material-result-queries.log`). 공개 도구 106개 중 제안/실행 union 계약 49개다.

### 대화 조회·생성/취소 결과 이행

대화 메시지/턴/도구 관찰/후속 질문, 승인 제안, 현재 다시 허용된 답변 원문·그래프·자료 근거를 이름 있는 결과 타입으로 공유한다. provider usage와 캡처된 locator/header/context metadata는 JSON을 유지한다. 대화 생성/취소는 같은 결과와 제안 union을 사용하며 이전 대화 검색도 개별 turn/excerpt 결과를 선언한다. 현재 공개 106개 도구 중 union 계약 51개다.

대화 제어/생명주기/답변 근거에서 30 passed와 기존 날짜 preview 회귀 1개를 발견했다. `54c4be8`에서 날짜 값의 kind를 date로 복원하고 삭제 표시는 text로 유지했다(수정/preview 9 passed). 대화 원문/그래프/자료 근거 72 passed(`/tmp/scax-unified-conversation-source-results.log`), 실제 SDK 대화 조회/검색 1 passed, inventory 2 passed. 편집 스크립트의 UTF-8 AST column 처리 오류는 검증 전에 복구했다.

### 보고 조회·회의 후속 결과 이행

보고 상태/최근/이력과 초안 생성 결과를 이름 있는 타입으로 공유하고 캡처된 근거 metadata를 보존한다. 회의 후속은 이미 생성되었는지와 현재 읽을 수 있는 업무/요청 결과를 구분해 전달한다. 새 요청 생성과 기존 요청 재조회에서 references 필드의 존재 차이도 보존한다. 관련 32 passed(`/tmp/scax-unified-report-followup-results.log`), inventory 2 passed. 공개 도구 106개 중 제안/실행 union 계약 53개이며 남은 dict 출력 선언은 조직/권한·관계·자료·보낸 배정·후보 조회 19개다. 이후 보고 생성은 R3e에서 durable 접수와 `ReportStatusResult` 상태 복원으로 이행했다.

### 조직·자료·관계 조회 결과 이행

조직 명부/상세/이력·권한 조회와 후보 3종은 기존 민감 필드 제한을 유지하는 이름 있는 결과를 공유한다. 후보는 id/이름만 반환한다. 조직·권한 및 실제 SDK/HTTP 동등성 44 passed, inventory 2 passed.

자료함 첨부·검색·메타데이터와 보낸 배정, 그래프 검색/주변/묶음 결과도 공통 타입을 사용한다. 출처 회차·원문 locator와 현재 읽을 수 있는 연결, 묶음 수의 optional 필드를 보존한다. 자료/그래프 67 passed(`/tmp/scax-final-query-results.log`), 실제 SDK 자료 검색/메타데이터/폴더 목록·그래프 동등성 14 passed(`/tmp/scax-query-sdk-results.log`), inventory 2 passed. 현재 등록 106개 도구의 최상위 출력은 이름 있는 계약으로 연결되었다. 이것은 전체 입력 공유·legacy 실행 경로 감사나 D1~D11 Acceptance 완료를 뜻하지 않는다.

### R3c transport·호환 경로 감사

현재 inventory에는 baseline signature와 별개로 실제 WorkflowApplication의 입력·결과 선언을 기록했다. 공개 MCP 106개와 HTTP 127개를 기준으로 command callback → facade → bootstrap → owning command 및 승인 executor를 대조했다. 입력 모델의 별칭을 사용하는 HTTP는 같은 모델을 유지하고, UUID/date 등의 transport 변환 뒤 owning command가 정규화·인가한다. 남은 브라우저 전용 파일/녹음 입력·결과는 R3d, 보고 생성 접수와 작업 상태는 R3e에서 함께 이행한다. E1의 권한 변경과 E2의 인증 비밀정보는 기존 명시적 제외다.

호환 `/api/actions/{id}/decide`는 기존 생성 3종을 ActionCenter confirm으로 보낸다. 다른 과거 approve는 ActionApplication의 원안 실행 뒤 `SqlAlchemyActionRepository._resolve_canonical_projection`에서 같은 session의 제출·판단·receipt를 저장한다. canonical confirm은 선택한 최종 Submission을 executor에 넘기며, executor 안의 새 session/commit이나 application 재조립은 없다. 과거 pending을 읽는 것만으로 원안을 다시 쓰지 않는다. `approve`/`confirm`의 기존 저장된 판단 명칭을 통일하기 위해 이력을 변경하지 않는다.

자료 초안·대화 접수/재시도·자료함·개인 선호·재배정의 기존 이름 있는 결과를 남은 HTTP/bootstrap 선언에 연결했다. 프로젝트 참여 해제 HTTP의 204 응답은 기존 transport 계약으로 유지하고 owning/MCP receipt는 보존한다. 조회·자료 초안 38 passed(`/tmp/scax-remaining-http-results.log`). 추가 승인/legacy·부분 성공·architecture 106 passed(`/tmp/scax-r3c-adapter-audit.log`). R3d/R3e와 최종 전수 Acceptance는 아직 남아 있다.

### R3d 첫 업무 파일 화면 연결

`browser_interactions`는 actor·요청 키·대상/버전·입력 의도와 대기/완료/취소/거절/실패/미지원 상태를 저장한다. 첫 연결은 업무 파일 첨부다. 요청 단계에는 첨부가 없고, 브라우저가 선택한 파일을 고정된 요청 ID로 업로드할 때 기존 TaskMaterialApplication의 인가·첨부·추출 접수를 실행한다. 같은 키의 대상 변경과 완료 후 다른 파일을 거절한다. 동일 파일 재전송은 저장된 결과이며 완료 뒤 조회/재진입은 업로드하지 않는다.

HTTP 131·MCP 108. 실제 SDK/권한 회수/실패 상태/기존 조회·추출 48 passed(`/tmp/scax-browser-file-regressions.log`), PostgreSQL 동시 요청·동시 업로드에서 첨부/추출/job 각 1개 1 passed(`/tmp/scax-browser-file-postgres-final.log`). UI 및 App 29 passed, native 파일 선택 취소 포함 최종 UI 4 passed; TypeScript/Vite build passed. 브라우저 업로드 중에는 닫기/이탈을 막는다.

이후 파일 연결을 회의 첨부/교체, 자료함, 요청 근거/댓글, 승인 초안으로 확장했다. 대상의 현재 조회 권한과 새 업로드 가능 여부를 구분하므로 판단이 끝난 요청이나 승인된 초안도 현재 읽을 수 있으면 기존 업로드 receipt를 복원한다. 승인 초안은 예약과 브라우저 `uploading` 상태를 먼저 커밋한 뒤 같은 예약으로 파일을 저장한다. 프로세스 중단 후 같은 파일로 재개하며, 저장 실패나 두 단계 사이 승인 권한 철회 시 예약을 폐기하고 실패/거절 상태를 보존한다. 준비된 초안은 최종 승인에서 선택해야 실제 업무 자료가 된다.

다중 owner HTTP/실제 MCP 및 기존 첨부 회귀 33 passed(`/tmp/scax-browser-owners-regressions.log`), PostgreSQL 업무/승인 초안 동시 재전송 각 1 passed(`/tmp/scax-browser-owners-postgres.log`, `/tmp/scax-browser-action-postgres.log`; 첫 실행의 승인 테스트 import 오류를 수정한 뒤 해당 case 재검증). UI 5 passed 및 TypeScript/Vite build passed. 남은 R3d는 녹음, 기존 직접 업로드/녹음 화면의 이탈 보호, 전체 화면 실사용 검증이다. 새 테이블은 검증 전용 DB에서만 생성했으며 운영 반영은 하지 않았다.

외부 MCP의 화면 링크는 `AX_WEB_ORIGIN`(기본 `http://localhost:5173`)과 `/?interaction=<id>`를 결합한다. API·worker·외부 MCP에는 사용자가 접속할 동일한 웹 origin을 설정한다. 대화 실행기의 MCP 자식 프로세스에도 이 값을 전달한다. origin에는 인증 정보·경로·query·fragment를 허용하지 않으며 URL에 인증 토큰을 넣지 않는다. 요청 ID는 접근 권한을 대신하지 않고 브라우저 로그인 및 현재 actor/대상 인가를 다시 적용한다.

### R3d 회의 녹음 화면 연결

`recording_request`는 actor·회의·버전·목적을 고정한 대기 요청이다. 연결된 화면이 마이크를 확보한 다음 서버 녹음을 시작하고, 녹음을 시작한 창의 `capture_id`로 종료/업로드한다. 다른 창의 시작/종료는 충돌이며 같은 창의 재전송은 동일한 녹음/receipt다. 시작 전에 취소·마이크 거절·미지원·권한 철회가 일어나면 녹음을 만들지 않는다. 원본 업로드 실패는 `upload_failed`로 남기고 같은 녹음으로 재시도한다. 오디오를 잃은 브라우저는 새 녹음을 자동 시작하지 않으며, 사용자가 중단하면 실패 기록을 남기고 전사 job은 만들지 않는다.

녹음 시작·종료는 기존 Meeting owner를 호출하며 원본 등록, finalization job, 브라우저 완료 receipt는 같은 session에서 커밋한다. 완료 상태는 업로드 완료를 뜻하고 전사/정제/요약 완료를 뜻하지 않는다. 짧은 수명의 실시간 credential과 segment 전송은 기존 브라우저 API에서만 처리한다. 기존 MeetingDrawer도 마이크 확보 전에 서버 녹음을 만들지 않으며, 실시간 전사 장애에도 원본을 보관하고 녹음/업로드 중 Escape·배경·창 이탈을 보호한다.

HTTP 134·MCP 109. 파일/녹음/실시간 전사 회귀 49 passed(`/tmp/scax-browser-recording-regressions.log`), PG 동시 시작/종료와 다른 capture 거절 1 passed(`/tmp/scax-browser-recording-postgres.log`), 화면 32 passed/build passed. 실제 마이크·provider·전체 journey acceptance와 기존 직접 파일 업로드 화면 보호는 아직 남아 있다.

직접 업무 파일·요청 근거/댓글·승인 초안에도 업로드 중 이탈 보호를 연결했다. 상세 닫기·Escape·다른 업무 열기, 앱 이동·로그아웃·AX 대화 전환이 진행 중인 조작을 잃지 않도록 막고 완료/실패 뒤 해제한다. 업무 화면과 AX 대화는 별도 보호 범위를 사용하므로 녹음 중에도 AX 창을 여닫을 수 있다. 관련 화면 81 passed(`/tmp/scax-upload-exit-regressions.log`), 업무 내부 이동 보호 추가 뒤 해당 화면/App 42 passed 및 build passed. 브라우저 실사용 검증을 이어서 진행한다.

실제 Chrome의 `browser-interactions-e2e.mjs`도 전용 PostgreSQL `ax_test_unified_acceptance`와 API 18231 / web 15231에서 통과했다. 파일 선택·업로드·재진입 시 첨부 1개, MediaRecorder의 실제 encoded audio 19,673 bytes 업로드와 녹음 1개, 두 번째 창의 캡처 비시작, 마이크 거절 시 새 녹음 0개를 확인했다(`/tmp/scax-browser-interactions-e2e.log`). 마이크 입력은 Chrome 테스트 장치이며 실시간 credential 경로는 장애를 주입해 원본 보존을 검증했다. Soniox/정제/요약의 실제 provider 성공 및 기본 7/7은 R3e 구현 이후 R3f의 별도 Acceptance다. R3d 연결 slice는 검증 완료다.

### R3f 최종 Acceptance

최종 inventory는 현재 HTTP 137개를 `verified` 131개와 합의된 제외 6개로 모두 판정했다. 각 HTTP 행은 실제 handler와 재귀적으로 해석한 `WorkflowApplication` 호출까지 고정한다. verified 행의 downstream owner는 현재 `WorkflowApplication` AST에서 추출한 호출과 정확 비교하며, HTTP와 AX가 의도적으로 다른 facade method로 같은 업무 경계에 진입하는 경로는 코드의 명시적 target manifest와 정확 비교한다. 실제 권한별 MCP discovery 120개는 한글 표시명·입출력 schema·description·operation binding·노출 정책·annotation과 행별 Acceptance 근거가 모두 `verified`다. 전체 권한, 무권한, 단일 권한, 한 권한을 뺀 discovery matrix도 catalog와 정확히 비교한다. inventory와 MCP module shape 검사는 6 passed, local stack 구성 검사는 3 passed다.

최종 자동 회귀는 exact code HEAD `a68130492e9e23f94ab4e32b96dee27013f8e646`에서 backend **1123 passed, 62 deselected**, PostgreSQL **62 passed, 1123 deselected**, frontend **43 files / 491 tests**, character assets **9 verified**, production build 성공이다. 같은 HEAD의 독립 검토는 **Critical 0 / Major 0 / Minor 0, commit-ready**로 끝났다. material·report worker의 blocking parser/provider 호출은 `spawn` child process group에서 실행한다. 부모가 transport/domain heartbeat와 단계 상한을 관리하고 lease 상실이나 timeout이면 process group을 종료하므로 다음 job과 worker concurrency가 멈추지 않는다. meeting worker는 이 경계를 쓰지 않는다 — 한 번에 배달 하나(`limit=1`)를 `asyncio.to_thread`로 돌리고, 예외로 끝난 배달만 상한(`MAX_DELIVERIES`)까지 되돌린 뒤 회의를 「실패」로 닫는다. 즉 멈춘 합성을 끊는 수단은 그쪽에 없다. 구버전 parser 후속 job 전달, 완료 커밋과 heartbeat의 경합, crash takeover, 실제 30초 정지 작업의 조기 종료를 SQLite와 PostgreSQL 회귀로 검증했다.

회의실을 고른 생성은 provider POST 전에 durable attempt를 먼저 남긴다. 직접 HTTP는 필수 `Idempotency-Key`, MCP는 `room_id`를 보낼 때 필수인 `idempotency_key`, Action confirm은 Action ID와 최종 회의 본문·선택 자료 ID 전체의 fingerprint를 사용한다. 같은 입력 재전송은 저장 결과로 수렴하고 다른 입력은 provider 호출 전에 거절한다. provider 응답 유실과 보상 취소 불확실성은 자동 재호출하지 않고 `needs_verification`으로 보존하며, 보상 상태는 DELETE 전에 기록한다. 중단 뒤 기본 confirm은 원장에 저장한 최종 본문과 자료 선택을 복원한다. 이를 위한 생성 attempt 테이블은 additive schema이며 운영 반영은 별도 승인 gate다.

격리 PostgreSQL의 전체 브라우저 실행은 업무·요청·승인·회의·보고·자료·복구·권한 흐름부터 access roles까지 연속 통과했다. 마지막 프로젝트 참여 이력 시나리오에서 목록 행이 상세보다 먼저 그려지는 테스트 경합을 발견해 새 프로젝트 상세 제목을 기다리도록 고쳤고, 수정 뒤 참여→종료/접근 상실→이력 보존→재참여와 실제 Codex graph 두 turn을 같은 stack에서 단독 통과했다. 재실행 중 실제 Codex가 명시한 `task_list`를 호출하지 않은 한 turn은 제품 오류 없이 E2E의 tool receipt 대기에서 실패했으며, 같은 chat lifecycle은 직전 전체 실행에서 통과했다. 일일보고는 비동기 생성 완료 뒤 최신 초안을 재진입해 편집·제출·복원했고, 자기 관계 질문은 `graph_overview`에서 시작해 읽은 Task 정본을 저장한 뒤 후속 질문을 같은 대화의 ID로 이어 갔다.

실제 Codex/MCP 한국어 혼동 쌍은 권한 있는 persona의 동적 discovery를 사용해 **10/10** 통과했다(`/tmp/scax-r3f-tool-selection-final2.log`, `/tmp/scax-r3f-tool-selection.json`). 내 업무/열람 가능 업무, 내 회의/조직 일정, 진행 상태/일보 상태, 관리자 배정/수평 요청, 자료 본문/관계 탐색을 구분했고 변경 도구는 pending Action만 만들며 승인하거나 실제 업무를 생성하지 않았다. 첫 관찰에서는 일보 상태가 자료 검색으로 빠지고 배정·요청 앞에 불필요한 관계 탐색이 생겼다. 목적 구분을 description과 서버 지침에 고정한 뒤, 일보·업무 요청 권한이 없는 persona를 사용한 평가 오류도 권한 있는 persona로 바로잡아 최종 경로를 확인했다.

실제 provider 검증은 Soniox 비동기 smoke, Chrome fake microphone의 realtime 1 segment → final 39 segments → refined 3 segments → summary evidence 1건, 실제 Codex durable 일일보고 생성과 workflow/provider provenance로 다시 통과했다. 보고서 smoke는 고정 날짜 대신 현재 업무일을 사용하고, 방금 시작한 Task의 ID·version·state가 `source_refs`에 정확히 한 번 포함됨을 검사한다. post-review 실행 로그 `/tmp/scax-r3f-live-report-post-review.log`는 첫 줄의 HEAD에서 source reference 2건과 workflow node `sources/render/generate/validate`를 확인한다. provider secret, 전사 본문, 운영 DB, 실제 수신자 발송은 이 evidence에 포함하지 않았다.

2026-09-13에는 최신 `main`의 도메인 판단·테스트 피라미드 재구성(`9feb5dd`) 위로 이 작업을 다시 이식했다. 승인 입력 정규화·편집 가능 범위·명령 노출·재실행 판정은 `modules/actions`의 순수 정책이 소유하고, `platform`은 현재 권한·저장된 판단·회의실 예약 attempt를 읽어 정책 입력을 조립한 뒤 effect와 영속화를 수행한다. 이 경계를 유지하면서 회의실 예약 보상, 확정 첨부 선택, 과거 담당자 승인 receipt, 브라우저 업로드 도중 권한 회수 의미를 보존했다. 재통합 검증은 backend **1189 passed**, PostgreSQL **61 passed**, scale **13 passed**, release **1 passed**, frontend **491 tests**와 character assets **9개**, production build 성공이다.

이어서 레거시 회의 생성 Action 계약 `meeting.create`를 걷었다. 회의 생성의 공개 Action type은 SCAX-SPEC-004의 `meeting.reservation.create` 하나이며, `MeetingApplication.create()`는 그 예약 흐름의 공통 도메인 effect로 남는다. 옛 타입의 typed 입력(`MeetingDraftInput`·`MeetingCreateInput`)과 초기 회의록 provenance를 고정하던 `frozen_meeting_source` 경로, 승인 executor 분기, 수정 확정·첨부 초안 대상, 편집 contract의 `내용`/`공개 범위` 필드, MCP 위임 권한 매핑을 모두 제거했다. 저장소·fixture에 남은 `meeting.create` 행은 없었고 새 계약으로 무손실 변환할 pending도 없어 migration 대신 retire를 택했다. 남은 행이 있더라도 원장에서 사라지지 않는다 — `modules/actions/policy.py::RETIRED_ACTION_TYPES`가 소유하는 한 줄이 그 이유를 사람이 읽는 문장으로 돌려주고, 제안은 `obsolete`로 표시되어 승인 명령 없이 거절만 남으며, 직접 실행 요청은 executor와 수정 확정 판정 양쪽에서 막힌다. 과거 표시는 generic history read 경계의 한국어 label로만 유지한다. 검증은 backend **1190 passed**, PostgreSQL **61 passed**, scale **13 passed**, release **1 passed**, frontend **43 files / 491 tests**와 character assets **9개**, production build 성공이다.

독립 review가 재이식분에서 찾은 네 가지를 이어서 고쳤다. **한 위임 턴의 확인 slot은 하나**이며, 같은 판단의 재배달만 첫 receipt로 수렴하고 내용이 다른 두 번째 제안은 거절한다 — 그것을 첫 판단의 receipt로 답하면 AX가 기록되지 않은 변경을 준비했다고 말하게 된다. 이전에는 판단 실행 도구 하나만 그 구분을 하고 나머지 chat action 전부가 첫 제안을 그대로 돌려주었으며, 이제 규칙은 `propose_action` 하나가 소유하고 거절 사유는 transport가 가리지 않고 그대로 전달한다. **결과 없이 끝난 worker 시도**는 배달 하나의 실패로 가둬 워커 루프를 죽이지 않는다. 보고 생성은 여기서 한 걸음 더 간다 — 끝나지 않은 외부 호출 노드가 남아 있으면 시간이 지났다는 사실만으로 외부 미실행을 단정하지 않고 `needs_verification`으로 고정하며, 이는 부모가 정리하는 경로와 다른 호스트가 이어받는 경로 양쪽에 같은 판정으로 걸린다. 호출에 닿기 전에 끝난 시도는 평범한 재시도로 남는다. **HTTP 계약 고정**도 MCP와 대칭으로 맞췄다. inventory의 각 HTTP 행이 실제 route signature를 함께 고정하므로, 입력 모델이나 반환 타입이 느슨해지면 drift 검사가 잡는다. 현재 137개 중 30개가 아직 `dict[str, object]`를 돌려주며 그 사실도 같은 곳에 기록돼 있다. 검증은 backend **1196 passed**, PostgreSQL **61 passed**, scale **13 passed**, release **1 passed**, frontend **43 files / 490 tests**와 character assets **9개**, production build 성공이다.
