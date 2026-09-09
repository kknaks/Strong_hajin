# ax-workspace

SCAX 상용 시스템의 modular monolith 저장소다. 조직·업무·요청·판단·회의·자료·보고와 내장 AX 대화가 하나의 PostgreSQL 원장과 application command 위에서 돈다. 로컬 실행은 별도 demo mode가 아니라 같은 production 경로를 `DeveloperAuthAdapter`와 seed로 지나가는 방식이다.

저장소는 제품과 계약을 갖고 데이터는 갖지 않는다. 실제 조직·자료는 Git 밖 폴더에 있고, 그것을 읽는 명령만 여기 있다.

장기 설계와 진행 상태는 Obsidian vault의 `SCAX 상용 시스템 구축` Project Note와 `SCAX 상용 시스템 설계`가 소유한다. 도메인 모델과 SCAX ERD 대조표는 `docs/domain-model.md`, 디자인 시스템 참조본은 `docs/design/`에 있다.

## 띄우기

필요한 것: Python 3.12+, [uv](https://docs.astral.sh/uv/), Docker.

```sh
make install
make frontend-install
make postgres-up
make reset-demo        # 한 번, 그리고 스키마가 바뀔 때마다
make local-stack       # API 8001 · 워커 셋 · 프론트 5176 · Ctrl+C면 모두 멈춘다
```

`make local-stack`이 기본 실행 경로다. PostgreSQL을 기다리고, 스키마가 없으면 시작하지 않고(먼저 `make reset-demo` — 스택은 스스로 reset하지 않는다), 다섯 프로세스를 함께 띄우고 감독한다. 하나라도 죽으면 나머지를 멈추고 실패한다.

프로세스를 하나씩 띄우는 길도 있다: `make api`(8000, autoreload) · `make conversation-worker` · `make material-worker` · `make meeting-worker` · `make frontend`(5173).

- **대화 워커** — 대기열의 AX turn을 실제 Codex로 실행하는 유일한 프로세스다. 없으면 모든 turn이 `pending`에 머문다.
- **자료 워커** — 올린 자료를 추출하고 색인한다. 없으면 업로드가 `queued`에 머문다. 한가할 때 지난 분석 규칙으로 만들어진 색인을 따라잡는다.
- **회의 워커** — 올린 녹음을 raw STT → 정제 → 최종 요약으로 넘기고, 중단되면 빠진 첫 산출물부터 이어서 한다.

워커와 API는 확장 없는 PostgreSQL job 전송(`durable_jobs`)을 함께 쓴다 — `FOR UPDATE SKIP LOCKED` claim, fencing lease token, 멱등 handler 위의 at-least-once. PGMQ 확장이 없는 Azure Database for PostgreSQL Flexible Server에서도 돌게 하려는 선택이고, 아직 Azure runtime에서 확인하지는 않았다. `AX_JOB_QUEUE_BACKEND`가 `postgres`(기본)와 `memory`(in-process test)를 고른다.

`AX_MCP_PERSONA=mina make mcp`는 그 사람으로 묶인 stdio MCP 도구 집합을 연다. 이 묶임은 필수여서, 묶이지 않은 MCP 서버는 client가 persona를 고르게 두지 않는다.

### 스키마

스키마를 다루는 명령은 하나다. 지울지 말지는 플래그가 가른다.

```sh
make reset-demo        # 파괴적 — 깨끗한 seed, 로컬 데모 데이터는 사라진다
make reset-catalog     # 제품 catalog만. 실제 조직을 넣기 전에 쓴다
make sync-demo-schema  # 지우지 않고 맞춘다 (reset_demo --sync)
```

`--sync`는 모델에 있고 데이터베이스에 없는 표와 열만 더한다. 데이터를 잃을 수 있는 것(사라진 열, 행이 있는 표의 NOT NULL 열)은 하지 않고 사람이 정하도록 출력한다. 개발 편의이지 migration 도구가 아니다 — 운영 스키마 변경과 Alembic baseline은 별도 gate다. 일반 API 시작은 스키마를 절대 바꾸지 않는다.

## 데이터 넣기

데이터를 다루는 명령은 셋이다.

```sh
make dataset-inspect SOURCE=~/Downloads/thesc DATASET_ARGS="--hide-names"
make dataset-import  TARGET=~/scax-datasets/actual                        # 없으면 채울 표를 만들어 준다
make dataset-import  TARGET=~/scax-datasets/actual DATASET_ARGS=--dry-run # 넣어 본 뒤 되돌린다
SCAX_DATASET_PASSWORD=... make dataset-import TARGET=~/scax-datasets/actual
```

`inspect`는 **파일을 열지 않는다.** 경로가 말하는 것만으로 `deny`(계정·비밀번호 자료) · `metadata-only`(읽을 수 없는 형식이나 너무 큰 파일) · `manual-review`(기본) · `import`(사람이 `--allow`로 올린 것)를 정하고, 목록을 원본 폴더 옆에 쓴다. 허용 목록이 deny를 이기지 못하며, macOS가 분해해 저장한 한글 파일명(NFD)도 같은 이름으로 본다.

`import`를 `inspect`와 합치지 않는 이유는 입력이 다르기 때문이다 — 아직 아무도 열어보지 않은 전달 폴더와, 이미 심사를 통과한 dataset 폴더는 같은 것이 아니다.

`import`는 한 폴더를 한 명령으로 넣는다: 없으면 채울 표를 만들고, 있으면 검사한 뒤 조직과 그 위의 예제를 넣는다. 검사를 통과하지 못하면 하나도 쓰지 않고 무엇이 잘못됐는지 말한다.

### 폴더 안의 표

조직 11장과 그 위의 예제 8장이 같은 폴더에 산다. 사람이 편집할 수 있는 외부 key로 서로를 가리키고, 저장소는 그 표를 읽는 계약만 갖는다. 저장소 안을 가리키면 명령이 거절한다.

| 표 | 담는 것 |
|---|---|
| `organization_units` · `members` · `memberships` | 조직 나무와 사람, 소속 |
| `grades` · `positions` · `appointments` · `jobs` · `job_assignments` | 직급·보직·발령·직무 |
| `projects` · `project_assignments` | 프로젝트와 붙은 사람 |
| `logins` | 로그인을 만들 사람 (비밀번호는 표에 없다) |
| `scenario_people` | 사람 key에 붙이는 이름표. 아래 표들은 이 이름만 쓴다 |
| `scenario_work` | 업무 한 줄씩. `project`가 있으면 그 프로젝트의 일, `parent`가 있으면 그 업무의 하위 |
| `scenario_requests` · `scenario_request_cc` | 요청과 참조자 |
| `scenario_assignments` | 배정 |
| `scenario_meetings` · `scenario_attendees` | 회의와 참석자 |
| `scenario_checklists` | 업무·요청·배정의 체크리스트 (`owner_kind`로 구분) |

두 층은 들어가는 길이 다르다. 조직은 원장에 한 transaction으로 들어가고 — 적용할 수 없는 dataset이 절반만 남지 않는다 — 예제는 제품의 정식 command를 **그 사람으로서** 지나간다. actor·회차·활동 이력·권한이 진짜여야 하기 때문이고, 그래서 예제는 되돌릴 수 없다. `--dry-run`이 조직까지만 보여 주는 이유다.

같은 dataset을 다시 넣으면 created 0이다. 비밀번호는 표에 두지 않고 `SCAX_DATASET_PASSWORD`로 준다. 주지 않으면 로그인을 절반만 만들지 않고 "하지 않은 것"으로 보고한다.

전체 구성원이 원장에 있어도 로그인은 일부만 갖는다. 답할 수 없는 사람은 업무 요청과 배정의 수행 후보에서 빠지고 그렇게 만들려는 시도는 원장이 거절하므로, 판단이 영영 기다리는 항목이 생기지 않는다. 명부·과거 업무·회의 참석자·graph node로는 그대로 보인다.

## 로그인과 세션

브라우저는 서버 세션으로 인증한다(`POST /api/auth/login` → HttpOnly `scax_session` 쿠키, `GET /api/auth/me`, `POST /api/auth/logout`). 로그인은 **누구인지를 증명할 뿐 아무 권한도 갖고 오지 않는다** — 세션은 구성원 하나로 풀리고 나머지는 전부 Organization & Access 원장이 정한다.

로컬 provider는 평범한 이메일·비밀번호다. `PBKDF2-SHA256`, 사람마다 다른 salt, 그리고 거절은 언제나 같은 말로 답해서 주소를 넣어 보는 것으로는 아무것도 알 수 없다.

`make reset-demo`는 capability 카탈로그, 권장 역할(외부 참여자 · 구성원 · 팀장 · 인사 담당자 · 대표), 예시 회사, 그리고 사람마다 credential 하나(`<member>@scax.example`, 비밀번호 `scax-demo-1234`)를 만든다. 다시 설치해도 더해지는 것이 없고, 조직이 고친 역할(`roles.customized_at`)은 나중 설치가 덮어쓰지 않는다.

개발 기계에서는 `GET /api/auth/providers`가 로그인 화면에 그 계정들을 함께 넘긴다. 하나를 누르면 폼이 채워지고 **같은 login route로 같은 credential이 간다** — 타이핑을 건너뛰는 길이지 로그인을 건너뛰는 길이 아니며, 다른 사람이 되려면 여전히 로그아웃해야 한다. 데모 도메인의 계정만 나열되므로 로컬에 넣은 실제 계정은 드러나지 않는다. production 프로파일은 이 route를 아예 등록하지 않는다.

`X-Demo-Persona` 헤더는 스크립트가 어느 구성원으로 행동하는지 말하는 개발·테스트 이음매다. 그 구성원이 있고 재직 중인지는 여전히 원장이 정하고, 살아 있는 세션을 덮지 않으며, production에는 없다.

## 권한 — 조직이 정한다

capability 카탈로그 → 버전이 붙은 역할 template → `StandardGrantRule` → `AccessGrant`로 흐른다. 그 사람의 역할(`members.role_key`)과 보직이 데려오는 역할(`positions.role_key`)이 각각 grant를 만들고 **서로 덮어쓰지 않는다** — 인사총무팀장은 팀장이면서 인사 담당자다. 보직이 만든 grant는 그 보직과 함께 끝나고 소속이 만든 grant는 소속이 있는 동안 남으며, 어느 쪽인지는 그 grant를 만든 규칙이 말한다.

범위는 세 가지다: 조직 단위 · 조직 전체 · 프로젝트. 조직 → 내 권한에서 자기 grant를 본다.

## 업무 — 요청과 배정

받은 것은 종류를 가리지 않고 **할일**로 모인다: 업무 요청 · 업무 배정 · 업무 결과 확인 · AX가 제안한 변경. 지금 내 판단을 기다리는 것이 거기 있다.

**요청**과 **배정**은 다르다.

- **요청**은 수평이다. 판단할 수 있는 동료라면 조직 어디로든 보낼 수 있다. 받은 사람이 수락·거절·조정 요청을 하고, 조정을 받으면 요청자가 내용을 고쳐 재상신하며 회차마다 불변 snapshot이 남는다. 참조자(cc)는 읽고 논의하되 판단하지 않는다. 요청자와 담당자는 현재 회차의 근거 자료를 sha256으로 고정해 붙인다.
- **배정**은 수직이다. `task.assign` grant가 닿는 범위 안의 사람에게만 간다. 받은 사람이 수락해야 자기 업무가 되고, 거절에는 사유가 남는다. 협상 왕복은 없다 — 배정자는 재배정으로 다른 사람에게 넘긴다.

모든 업무는 `task_assignments` 행을 갖는다(self · request_effect · direct). `내 업무`는 **active** 배정이 있는 것만 나열한다. 결과가 나오면 완료 보고를 올리고 요청자가 완료 인정 또는 보완 요청을 하며, 이것도 회차로 쌓인다.

업무는 `description` · `start_date` · `due_date`를 갖고 소유자가 `PATCH /api/tasks/{id}`로 고친다(`expected_version` 필수, 시작 ≤ 기한). 체크리스트, 1단계 하위 업무, 참고 업무 연결이 그 위에 붙는다. 모든 변경은 `TaskVersion`으로 남아 `활동·이력`에서 회차와 diff로 읽힌다. 완료한 업무는 다시 열 수 있고 취소는 끝이다.

## 프로젝트

부서를 가로질러 묶이는 일과 그 사람들을 나른다. 조직 단위와 **나란한 두 번째 축**이고, 배정은 조직 보직과 같은 규칙 경로로 그 프로젝트 범위의 grant를 만든다.

프로젝트에는 소유 조직이 없다. 부서를 가로지르려고 있는 것이라 어느 한 부서의 것이라고 적는 순간 그 부서가 열쇠가 되고, `붙어야 보인다`는 규칙에 뒷문이 생긴다. **프로젝트가 열리는 길은 배정 하나뿐이며 조직 전체를 읽는 자격에도 예외가 없다.**

만드는 것은 관리하는 일이라 `project.manage`가 있어야 하고, 만든 사람은 담당자로 함께 기록된다 — 그러지 않으면 만든 사람조차 자기 프로젝트를 찾지 못해 아무도 붙일 수 없다. 담당자가 사람을 붙이고 뗀다. 담당 기간은 없을 수 있다.

## 자료와 본문 검색

자료를 올리면 같은 transaction에 추출 job이 기록된다(업로드 응답의 `extraction.status`가 `queued`로 시작). 자료 워커가 UTF-8 텍스트·Markdown·텍스트 PDF와 Office 문서를 경계가 있는 블록과 chunk로 뽑는다. DOCX 문단/표/머리글·바닥글, XLSX 시트+셀 범위, PPTX 슬라이드+발표자 노트, PDF 페이지가 각자의 source locator와 함께 남는다. 추출·색인은 원문의 끝까지 처리하고 전체 projection이 공개된 뒤에만 `completed`가 된다. 혼합 스캔처럼 읽지 못한 구간이 있으면 `partial`과 처리 범위·경고를 보존하며, 해당 파일을 명시한 검색에서만 반환한다. 용량·암호화·손상·미지원은 이유가 있는 실패로 처리한다. 원문 출력 상한과 검색 결과 개수 제한을 구별한다([추출 무결성과 검증](docs/material-integrity.md)).

원본 byte는 `MaterialStorage` port 뒤에 있다. 로컬 adapter는 `AX_MATERIALS_DIR`(기본 `backend/.scax/materials`, git 제외) 아래에 쓰고, Azure Blob adapter가 같은 port를 구현할 자리다. 파일(`kind=input`/`output`) · 링크 · 다른 자원 참조를 붙일 수 있고, 뗀 뒤에도 기록과 byte는 계보를 위해 남는다.

검색은 제목·파일명이 아니라 추출된 본문을 찾는다. 한국어는 조사가 낱말에 붙어 있어 글자 그대로 비교하면 `견적서를`이 `견적서`를 만나지 못하므로, 문서와 질문에 같은 형태소 분석(Kiwi)을 적용한다. 같은 낱말이 자리에 따라 다르게 갈리는 경우(`납기일은` → `납·기일`, `납기일` → `납기·일`)를 위해 어절에서 조사를 뗀 형태도 함께 색인하고, 갈리면 다른 것이 되는 제품 코드·문서 번호는 원문 그대로도 남긴다. 낱말은 통째로만 맞는다 — `일`은 `일정`이 아니다. 조건·순위·개수는 PostgreSQL `tsvector` + GIN 인덱스가 끝낸다.

분석 규칙은 version을 갖는다(`kiwi-<lib>-r<rules>`). 규칙이 바뀌면 그 규칙으로 만든 색인은 질문과 만나지 못하는데, 자료 워커가 한가할 때 뒤처진 것부터 다시 만든다 — 사람이 규칙이 바뀐 것을 기억했다가 명령을 부르지 않는다. 원문은 건드리지 않고 찾기 위한 형태만 바뀌며, 여러 번 돌려도 한 번 돌린 것과 같고 중간에 멈춰도 이어서 한다.

업무에 연결하지 않은 자료도 찾는다. `GET /api/materials/search?q=`와 MCP `material_search`는 Task·WorkRequest·Meeting·Report·개인/팀 자료함의 현재 owner 권한을 먼저 확인하고 허용된 원본만 검색한다. 결과의 `material_id`는 canonical artifact UUID이며, 같은 구간의 여러 연결은 읽을 수 있는 `source_contexts`로 합친다. 원본 revision·해시·locator·발췌·열기 링크를 함께 반환하고, 해제되거나 읽을 수 없는 연결의 이름·존재·건수는 제외한다. Task·Graph·본문 검색·답변의 `material_id`는 같은 artifact ID다. `binding_id`는 연결을 식별하며 Task 연결 해제는 `/api/tasks/{task_id}/material-bindings/{binding_id}/detach`로 수행한다. Task 범위 검색도 canonical API의 `resource_type=task`·`resource_id`를 사용한다([owner와 공개 계약](docs/material-search-owners.md)).

## AX 대화와 근거

provider는 실제 Codex CLI다(`CodexCliProviderAdapter`). 격리된 runtime home, 사용자 config·rules·skills·plugins 비활성, 읽기 전용 sandbox, `gpt-5.6-terra`, Fast tier, low reasoning, 구조화된 출력 schema로 `codex exec`를 부른다. CLI가 없거나 인증이 안 되면 명시적으로 실패한다 — 결정적 provider는 test에서만 주입한다.

그 세션에는 **그 사람으로 묶인 stdio MCP 서버**가 붙는다. 대화는 대기열로 순서가 보장되고 취소·재시도가 된다.

**쓰기는 바로 일어나지 않는다.** 도구가 변경을 제안하면 ActionItem이 되고, 사람이 승인해야 원장에 반영된다.

답변 아래에는 근거가 한 줄로 접혀 있다. 펼치면 답이 가리키는 정본(원문의 몇 쪽인지 포함), 문서 발췌 카드, 그리고 그 회차가 실제로 걸어간 경로가 나온다. 근거를 확인하려고 대화를 떠나지 않는다. 도구가 읽은 발췌는 그 turn의 `material_evidence`로 남고, 본문이 도구 timeline 요약이나 감사 행에 복제되지 않는다. 근거 카드·정본 링크·Action preview는 관측 당시 context와 현재 owner 권한·원본 해시의 교집합을 다시 확인한다. 자료를 조회한 대화의 후속 질문은 provider checkpoint를 재사용하지 않고 현재 허용된 정본 참조와 사용자 발화에서 이어간다.

`내 업무`와 `조직의 업무`는 다른 질문이다. `task_list`는 그 사람이 든 업무를 돌려주고, 팀이나 프로젝트 전체를 묻는 질문에만 `mine=false`로 넓힌다 — 읽을 수 있다는 것이 그 사람의 일이라는 뜻은 아니다.

## 관계 탐색

노드별 검색·확장·overview·owner read·본문 검색 범위는 [탐색 지원 계약](docs/search-support.md)에 정리되어 있다.

사람·팀·프로젝트·업무·요청·회의·자료·보고가 node이고, edge는 전부 각 원장의 사실이다. **그래프 전용 관계 표는 없다.** 첫 화면이 이미 그래프이며, 한 걸음 나갈 때마다 연결마다 권한을 다시 판정한다.

표현 수준 셋이 각자 하나씩 답한다.

- **구성원 보기** — 내 주변. 내가 속한 팀, 내가 배정된 프로젝트, 나란히 선 사람들, 내 업무·자료·회의·요청
- **팀으로 묶기** — 조직. 사람이 팀으로 접히고 팀 위의 계층이 선다
- **프로젝트로 묶기** — 일이 프로젝트로 접힌다

접힌 node는 자기가 몇을 담고 있는지 말한다. 자리가 없어 접은 것과 권한이 없어 안 보이는 것은 다르다 — 뒤쪽은 개수도 나오지 않는다.

## 일일보고

초안 생성만 versioned Workflow로 실행한다. 사람의 편집·확인·제출·이력과 불변 snapshot은 Reports application과 DailyReport 원장이 직접 소유한다.

정의는 데이터다. 어떤 node가 있는지, 이름이 무엇인지, 무엇이 무엇을 먹이는지, 답이 어느 node의 어느 필드인지(`outputs`)를 정의가 정하고 런타임에 node 이름이 박혀 있지 않다. 대신 런타임은 등록된 node type과 operation, 승인된 provider profile만 허용한다 — 정의는 데이터이지 실행 가능한 설정이 아니다.

node마다 입력 snapshot과 결과가 `workflow_runs`·`workflow_node_executions`에 남고, 만들어진 초안은 `definition_version_id`를 들고 다닌다. 제품에 통합된 workflow는 아직 이 하나다.

## 디자인 시스템

화면 부품은 `frontend/src`에 있고, 그 가운데 **디자인 부품 19개**만 `frontend/ds-entry.tsx`가 따로 내보낸다. 페이지·api·viewModels는 부품이 아니라 거기 없다.

```sh
make storybook         # 부품 카탈로그, :6006
make storybook-build   # 정적 빌드 — 설정이 상했는지 보는 가장 빠른 길
```

스토리는 `.design-sync/previews/<Name>.tsx` **한 벌**이다. 같은 파일을 Storybook과 claude.ai/design 프리뷰 카드가 같이 읽는다 — 두 벌을 두면 한쪽에만 스토리를 더하는 일이 반드시 생기고, 그때부터 둘은 다른 시스템을 보여 준다. 스토리를 더하려면 그 파일에 대문자로 시작하는 export를 하나 더 쓴다. 두 곳에 같이 나간다.

부품을 claude.ai/design 프로젝트로 올리는 것은 `/design-sync`다. 화면을 지을 때의 규약(클래스 어휘·토큰·오버레이 규칙)은 `.design-sync/conventions.md`가, 이 저장소만의 함정은 `.design-sync/NOTES.md`가 갖는다. 시각 규칙의 원본은 `docs/design/design-system-v2.dc.html`이다.

규칙 하나만 여기 옮겨 둔다: **네이티브 `<select>`와 `input[type=date|time]`은 쓰지 않는다.** 브라우저가 OS 위젯으로 그려서 토큰이 닿지 않고, 표기가 로캘을 따라 갈라진다 — 같은 값이 사람마다 `2026/09/30`과 `09/30/2026`, `14:30`과 `오후 2:30`으로 읽힌다. 대신 `DateField`·`DatePicker`·`TimeField`·`TimeRangeField`·`Select`·`MultiSelect`를 쓴다.

## 검증

```sh
make verify          # backend 단위·계약 test + frontend 동작 test + Vite production build
make test-postgres   # PostgreSQL 통합 test
make acceptance-e2e  # 브라우저 journey 전부, 자기 데이터베이스에서
```

`acceptance-e2e`는 reset으로 시작하므로 **자기 데이터베이스**(`ACCEPTANCE_DATABASE_URL`, 기본 `ax_test_acceptance`)에서 돈다 — `DATABASE_URL`에 넣어 둔 조직과 자료를 건드리지 않는다. 고정된 PostgreSQL 16 컨테이너를 띄우고, 정확히 한 번 reset하고, 포트 `18111`/`15186`에 격리된 스택을 세워 하나의 seed 위에서 모든 journey를 돌린 뒤 자기가 띄운 것만 멈춘다. 두 포트 중 하나라도 쓰이고 있으면 reset 전에 실패한다.

개별 `e2e-*` 타깃은 데이터를 reset하지 않는다. 문서화된 E2E 포트(`8001`/`5176`)에 API·워커·프론트가 떠 있기를 기대하므로 `make local-stack`과 함께 쓴다.

```sh
make local-stack     # 다른 터미널에서
make e2e-task-lifecycle
make e2e-work-request
make e2e-material-search
make e2e-graph-question
```

`acceptance-e2e` 밖에 있는 journey가 하나 있다: `e2e-meeting-live-transcript`는 실제 Soniox credential과 macOS 가짜 마이크가 필요해서 `make local-stack`에 대고 돌린다. `make live-report-smoke`는 opt-in 실 Codex 증거다 — seed된 업무 활동을 만들고 `daily_report.generate_draft`를 불러 남은 WorkflowRun·NodeRun 넷·완료된 ProviderCall provenance를 확인하며, 생성된 본문이나 prompt는 출력하지 않는다.

`uv run python backend/scripts/probe_documents.py <dir>`는 로컬 폴더를 읽기 전용으로 살펴 이름·크기·상태만 출력한다.
