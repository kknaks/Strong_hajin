# API·MCP·승인 실행 경로 일원화

구현 계약은 2026-09-11 SCAX API MCP 승인 실행 경로 일원화 Work Brief의 D1~D11/E1/E2다. 이 문서는 R2의 baseline 관찰과 이행 설계를 고정한다. **설계 확정은 제품 Acceptance 통과가 아니다.** 행별 구현 상태와 최종 검증은 inventory 및 Work Brief에서 별도로 판정한다.

## Baseline과 전수 표면

- 기준: PR #3 merge `42e43358b866bc02d4a5e401b19d826a6e8397e1`. 2026-09-11 원격 main 재조회도 같은 SHA다.
- [전수 inventory](unified-operations-inventory.json): HTTP method/path **125개**, 서로 다른 facade operation **120개**, 동적 Task transition 5개를 포함한 MCP tool **42개**. HTTP handler, 정적으로 대응한 화면 호출자, application input/result annotation, owning call, 현재 tool과 목표 tool, 노출 정책을 각 행에 둔다. 아직 tool이 없는 행도 `planned`로 유지한다.
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
| `task_assignment_candidates`, `sent_task_assignments` | 배정 가능한 사람 / 본인이 보낸 배정 | `task.assign`의 현재 범위 / sender 관계. 수신 판단함은 `action_item_list`로 연결 |
| `list_work_requests`, `get_work_request`, `work_request_timeline` | 참여하는 요청 목록·상세·회차와 논의 | requester/assignee/cc, 요청 capability. 새 승인 이행도 기존 requester와 assignee의 판단 의미 유지 |
| `work_request_assignee_candidates`, `work_request_cc_candidates` | 요청 수행/참조 대상 후보 | 서로 다른 참여 역할. active/login 판단 가능 조건은 현행 owning query 유지 |
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

Soniox 공식 계약에서 `client_reference_id`는 유일성을 강제하지 않는 추적 값이다. 따라서 idempotency key로 취급하지 않는다. 목록/GET의 optional 필드는 안전하게 parse하며 임의 결과 목록을 모델에 반환하지 않는다. endpoint 문서는 terminal `error`, 오류 가이드는 `failed`를 설명하므로 두 값을 captured fixture로 검사한다. 진행 중/모호한 요청의 file/transcription을 finally에서 무조건 삭제하던 경로도 변경한다. 새로운 provider/모델 도입은 없다.

공식 확인(2026-09-11): [생성 계약](https://soniox.com/docs/api-reference/stt/transcriptions/create_transcription), [요청 목록·cursor](https://soniox.com/docs/api-reference/stt/transcriptions/get_transcriptions), [현재 요청 조회](https://soniox.com/docs/api-reference/stt/transcriptions/get_transcription), [오류](https://soniox.com/docs/api-reference/errors). 실제 수신자 발송이나 운영 DB로 probe하지 않고 fixture와 검증 전용 환경을 쓴다.

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
