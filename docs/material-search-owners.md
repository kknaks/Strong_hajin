# 범용 자료 검색의 owner 경계

`WorkflowApplication.search_materials`, REST `GET /api/materials/search`, MCP `material_search`는 Task·WorkRequest·개인/팀 자료함·Meeting 녹음 원본·확정 전사/정제본·Report 제출본을 지원한다. Provider에는 canonical `material_search` 하나만 노출한다. Task 범위 검색은 같은 API의 `resource_type=task`·`resource_id`로 좁힌다. 구 Task 검색 API와 facade는 제거했다. 검색은 generic conversation receipt와 현재 권한 재검사를 지원한다. 지원하지 않는 resource type은 빈 결과로 가장하지 않고 거절한다.

## 원본과 읽을 수 있는 연결

범용 검색의 `material_id`는 Attachment artifact UUID다. Task 자료·Graph에서도 같은 ID를 사용하고 연결 UUID는 `binding_id`로 구별한다. 원본 `integrity_ref`, extraction ID/parser version, 정확한 chunk locator와 bounded excerpt를 반환한다. 동일 artifact가 여러 Task나 업무 요청에 연결되어도 같은 chunk hit를 복제하지 않고 `source_contexts`에 현재 읽을 수 있는 연결만 모은다. 다른 본문 구간의 chunk는 별도 hit가 될 수 있다.

Task owner의 현재 목록 읽기와 WorkRequest의 requester·assignee·live cc 정책을 먼저 실행한다. 그 뒤 허용된 artifact ID만 projection/index에 전달한다. `resource_types`와 optional `resource_type`/`resource_id` 쌍으로 좁힐 수 있고, anchor가 없으면 구현된 owner 모두를 검색한다. 미존재/비인가 anchor는 같은 오류를 반환한다. 비인가 연결의 이름·ID·건수는 hit와 unavailable metadata에서 제외한다.

WorkRequest의 댓글은 해당 요청 thread에 속한 댓글의 활성 binding을 사용한다. 제출 근거는 요청의 acceptance decision에 속한 submission의 활성 binding과 immutable Evidence adoption이 함께 있어야 하며 fixed snapshot hash가 원본과 일치해야 한다. 미채택 제출 binding, 해제된 연결, purged 원본은 제외한다. 요청 수정 때 상속된 근거는 원래 submission binding을 통해 접근하며 같은 파일을 중복 반환하지 않는다. 원문 열기도 같은 owner 검사를 사용한다.

## 개인/팀 자료함

`material_folders`는 personal member 또는 team organization 중 정확히 하나를 명시한 owner 원장이다. 생성할 때 personal은 현재 본인으로 고정하고 team은 현재 직접 소속된 조직 단위를 지정한다. 읽기와 업로드는 활성 개인 본인 또는 현재 직접 팀 소속자로 제한한다. 매번 조직 원장의 현재 profile을 읽고, 폐지/비활성 조직을 제외하므로 오래된 Principal이나 광역 업무 조회 grant가 팀 공유를 넓히지 않는다. 현재 소속자는 팀 파일을 올릴 수 있고, 업로더만 자신의 활성 연결을 해제하며 생성자만 읽을 수 있는 자료함을 archive한다. 두 동작 모두 원본 byte를 보존한다.

자료함 REST는 `/api/material-folders`의 생성/목록, `/{folder_id}/materials`의 업로드/목록, `/{folder_id}/materials/{material_id}/content`와 `/detach`, `/{folder_id}/archive`를 제공한다. material_id는 canonical artifact UUID이고 binding_id를 따로 반환한다. 원본 storage key는 `material_folders/<folder UUID>/<file UUID>`이며 기존 안전한 key 검증을 통과해야 한다. 신규 파일은 같은 transaction의 extraction/job으로 처리한다. Table은 additive 변경이며 운영 DB DDL은 별도다.

## Meeting 확정 전사와 정제본

Meeting detail의 현재 읽기 정책을 통과한 뒤 녹음 파일에서 생성된 `completed` raw/refinement revision을 검색한다. 실시간 전사는 제외한다. 기본 검색은 녹음별 최신 확정 raw와 그 raw의 최신 확정 refinement를 선택한다. 더 최신 revision이 처리 중·대기·실패 상태여도 이전 확정본은 유지된다. artifact ID를 명시하면 현재 읽을 수 있는 과거 확정본도 검색·열기할 수 있고 `is_current_revision`으로 구분한다.

Native artifact ID는 source kind와 immutable revision UUID로 결정된다. Attachment는 identity/projection 연결을 제공하며 원본은 Meeting 원장에 남는다. `format_version: 1` canonical JSON은 revision·segment ID, 발화 순서·시간·본문·provider speaker label, 정제본의 raw segment span을 보존한다. 이후 바뀔 수 있는 사람 확인/이름 정보는 원본 hash에 넣지 않는다. canonical JSON의 SHA-256과 녹음 byte의 SHA-256은 별개이며 결과 context는 recording ID/hash lineage를 함께 가진다. Worker는 등록된 JSON hash를 검증한 뒤 전체 발화를 기존 spool/batch 경로로 색인하고, 원문 열기도 같은 hash를 검증한다.

확정 저장과 native attachment/binding/extraction/job 예약은 같은 transaction이다. 기존 전사는 owner 인가 후 처음 발견할 때 동일 identity로 등록한다. Native revision은 일반 파일 binding으로 공유할 수 없고 잘못된 Task/WR/자료함 binding도 읽기 권한을 만들지 않는다. 일반 파일 purge는 canonical Meeting 본문을 지웠다고 가장하지 않도록 native revision을 거절한다. 모든 owner 후보 계산은 현재 조직·권한을 다시 읽고 caller가 좁힌 권한과 교집합을 사용한다.

원문 경로는 `/api/meetings/{meeting_id}/materials/{material_id}/content`이며 매번 현재 Meeting 권한과 활성 native 연결을 검사한다. 전사 artifact는 canonical JSON을, 녹음 artifact는 원본 audio byte/MIME를 반환한다.

업로드가 확정된 녹음은 `native_recording` artifact와 `meeting_recording` owner binding을 갖는다. 녹음 ID로 결정한 material ID, 원본 SHA-256·크기·MIME를 보존하며 recording pipeline의 version/state 변화는 새 audio revision을 만들지 않는다. 기본 검색과 명시적 material 선택에서 `reason: native_recording`, extraction 없음으로 반환하고 텍스트 projection/job은 만들지 않는다. 전사 context의 `recording_material`은 현재 활성인 audio material ID/hash/origin을 연결한다. 전사 실패 뒤에도 업로드된 오디오는 열 수 있지만, audio 연결이 해제되거나 purged 상태라면 링크를 표시하거나 다시 활성화하지 않는다.

녹음 stop의 metadata와 audio artifact/binding은 finalization job과 같은 transaction으로 commit된다. 과거 녹음은 Meeting 인가 이후에 등록하고, 동시 discovery는 recording row lock과 결정적 ID로 직렬화한다. 현재 Meeting 권한을 검사한 뒤에만 녹음 저장소를 읽고 등록된 byte hash를 검증한다. 일반 파일 binding/purge를 통한 우회는 전사와 같이 거절한다.

## Report 제출본

Report의 기존 읽기 계약은 본인 소유다. 현재 활성 principal과 `daily_report.read`를 확인한 뒤 owner-filtered metadata query로 전체 제출본 후보를 읽는다. 최근3개를 보여주는 UI 목록은 검색 범위를 제한하지 않는다. 기본 검색은 보고서별 최신 제출본만 사용하고, 미제출 draft나 이후 편집 중인 draft는 포함하지 않는다. 수정 중에도 이전 제출본은 유지되며 explicit material ID는 과거 제출본을 검색·열기할 수 있다.

`report_submission` source kind와 submission UUID로 canonical artifact를 결정한다. 원본 ledger는 DailyReportSubmission이며 `format_version: 1` JSON에 report/submission ID, 날짜·version, 제출 당시 body/source_refs를 보존한다. 원본 링크는 `/api/daily-reports/{report_id}/materials/{material_id}/content`다. 현재 본인 읽기와 활성 native binding을 확인하고 registered SHA-256에 맞는 JSON만 반환한다. 첨부·Task의 새 읽기 권한을 부여하지 않으며 source_refs는 제출 당시 원본의 일부다.

본문 전체를 기존 spool/chunk/batch 경로로 색인한다. locator는 report/submission ID·제출 version과 본문 projection의 문자 구간을 포함한다. 빈 과거 제출본은 `empty_content` 실패로 표시한다. 제출 명령과 artifact/binding/projection/job은 같은 transaction이고, 기존 제출본은 인가 후 발견·등록한다. 일반 native file sharing/purge 차단은 Report에도 적용된다.

## 추출과 기존 첨부

신규 댓글 첨부와 제출 근거는 attachment·binding/adoption과 같은 session transaction에서 extraction/job을 예약한다. 이전 배포의 미추출 파일은 범용 검색에서 권한을 확인한 뒤 호출당 최대20개씩 예약한다. 이 경우 search application이 session을 commit하며, 결과에는 queued 상태를 표시한다. 반복 검색은 존재하는 projection/job을 복제하지 않는다. 과거 parser projection의 전환은 기존 worker idle upgrade 정책이 담당한다.

일반 검색은 검증된 completed projection만 사용한다. canonical artifact ID를 명시한 경우에만 partial을 검색하며, no-hit 응답에서도 `selected_material.extraction`에 coverage/warnings를 보존한다. unavailable 목록은20개까지 반환하고 별도 total과 truncation 여부를 제공한다. 검색 hit 상한은8개이며 원본 전체 추출 범위와 별개다.

## Canonical receipt와 후속 Turn

`conversation_content_evidence`는 canonical artifact·관측한 source contexts·chunk/locator·extraction snapshot·bounded excerpt를 저장하는 additive 원장이다. 본문 검색 receipt의 유일한 저장·읽기 경로다. `conversation_material_evidence` 모델과 읽기/쓰기 분기는 제거했다. MCP 검색에 delegated execution ID가 있으면 conversation owner를 검사하고 같은 transaction에 기록한다. 같은 turn/chunk는 하나의 receipt로 유지하며 같은 turn에서 나중에 실제로 검색한 context만 추가한다.

대화 read는 여러 artifact ID를 owner adapter에서 함께 재인가한다. 당시 관측한 context와 현재 활성 owner/binding의 교집합만 반환하며, artifact hash가 달라지거나 purge된 자료는 제외한다. 현재 title/origin을 사용하므로 회수된 연결 이름·건수가 남지 않고 새 연결을 과거 관측으로 만들지 않는다. 새 receipt에는 artifact ID와 같은 attachment_id가 있으며 Task에 속한다고 가장하는 task_id는 없다.

Receipt 발행과 purge는 같은 artifact row lock을 사용한다. 발행은 artifact를 ID 순서로 잠그고 현재 lifecycle/hash를 다시 읽은 뒤 turn을 잠근다. 검색 직후 purge가 완료됐으면 그 발췌를 새 receipt에 저장하지 않는다. Purge는 기존 receipt와 새 receipt의 excerpt/header를 지우며, 새 receipt의 chunk ID는 제거된 projection의 관측 기록으로 남는다.

자료 검색 receipt 또는 material search Tool 호출이 있는 대화는 후속 provider checkpoint를 재사용하지 않는다. No-hit/unavailable Tool 결과도 이름·건수를 포함할 수 있어 같은 규칙을 따른다. 현재 읽을 수 있는 canonical material seeds를 복구하고, 사용자 발화만 bounded context로 넣는다. 이전 AX 답변은 자료 본문을 담았을 수 있어 재주입하지 않는다. 사용자가 보는 원래 대화 이력을 삭제하는 동작은 아니다. 이 방식은 이후 turn의 context 비용과 연속성에 영향을 줄 수 있으며 Q6 live 평가에서 확인한다.

Frontend는 기존 evidence card의 name/origin을 사용한다. 자료 목록/검색의 answer resource는 실제 관측한 context와 동일 원본 hash를 저장하고, 그 context가 현재도 읽을 수 있을 때만 artifact ID와 locator를 표시하며 `GET /api/materials/{material_id}`로 현재 owner metadata를 재인가한다. 원문 링크도 매번 해당 owner 권한을 검사한다. Graph·Task 자료 목록·완료보고 선택·answer resource·후속 seed의 `material_id`는 모두 artifact ID다. Task 원문 열기는 artifact ID, 연결 해제는 별도 `binding_id`를 받는다. Action evidence preview도 해당 Turn의 content receipt에 동일한 현재 owner·active binding·hash 검사를 적용한다. 제안 응답·대화·Action 목록/상세와 중첩 승인 preview는 공통 reader를 사용하고, reader가 없으면 근거를 표시하지 않는다. 같은 artifact의 중복 관측은 이름 하나로 표시하며 서로 다른 artifact의 같은 이름은 합치지 않는다.

## 검증과 남은 범위

Task 미연결 WR 첨부, 여러 owner의 artifact dedup, 제한된 capability, cc 회수 후 index 후보와 원문 열기, 해제/미채택/변경된 snapshot/mutable adoption 제외, 기존 파일 backfill과 metadata 상한, 상속 근거, 명시적 partial/no-hit를 합성 데이터로 검증했다. 기존 Task API·receipt 계약은 별도 회귀로 확인한다. 독립 review는 이 첫 묶음의 correctness·권한·의도 범위를 통과했다.

개인/팀 자료함과 기존 owner·storage·schema 관련27개가 통과했다(8.71s). 직접 소속 종료, 광역 권한 비확대, owner spoof, 여러 연결과 원본 보존, archive·폐지 팀 제외를 포함한다. 독립 reviewer도 자료함5개를 직접 실행해 통과했다. Architecture·기존 자료 persistence13개(3.77s), additive schema의 기존 member 보존·owner check constraint·재실행1개(0.52s)를 확인했다.

격리 PostgreSQL16.6에서 자료함·WR 댓글·WR 제출 근거의 durable enqueue 직후 실패를 주입해 attachment/binding/adoption/extraction/job이 함께 rollback됨을 확인했다. 재업로드 후 worker와 검색도 통과했다. 기존 미추출 파일을 두 검색이 동시에 발견한 경우 projection1개/job1개가 생성됐다. 총4개 통과(5.13s), 임시 컨테이너는 제거했다. 파일 저장소와 DB는 분산 transaction이 아니므로 rollback된 업로드의 orphan byte는 남을 수 있지만, owner/attachment가 없으므로 검색·원문 열기 권한을 만들지 않는다.

Meeting native/기존 owner/architecture36개가 통과했다(26.67s). source hash drift, 1,000발화의 마지막 segment, live 제외, 권한 회수와 잘못된 파일 binding, 과거 revision pin을 검증했다. 독립 리뷰에서 찾은 최신 미확정 revision의 이전 확정본 숨김을 raw processing/refinement pending·failed3개 red→green으로 수정했다. reviewer가3개를 직접 재실행해 Major closure와 현재 native bridge의 commit-ready를 확인했다. Native/unit 추출·무결성39개도 통과했다(5.83s). 기존 Meeting core·녹음 계약19개도 통과했다(12.80s).

PostgreSQL16.6에서는 기존 owner4개와 native2개가 통과했다(6.95s). durable enqueue 직후 실패할 때 확정 raw/segments/attachment/binding/projection/job이 함께 rollback되고, 재시도 후 검색·원문 열기가 동작했다. 동시 legacy native discovery는 source row lock과 결정적 UUID로 artifact/binding/projection/job을 각각1개만 생성했다. 임시 컨테이너는 제거했다.

Audio bridge 관련 native/기존 owner/녹음/architecture56개 통과(36.63s), 추가 audio file-binding 우회1개 통과(2.12s). 전사에서 원본 오디오 열기, 별도 identity/hash/MIME, projection 없음, 오래된 Principal의 조직 소속 종료 후 저장소 접근 차단, audio byte 변조, 전사 실패 이후 원본 유지, 해제/purge 링크 제외를 포함한다. PostgreSQL에서는 기존6개와 audio stop enqueue 실패/rollback/retry1개 총7개 통과(7.07s). 실패한 업로드의 orphan byte는 owner metadata가 없어 검색·열기되지 않는다. audio와 전사 identity가 모두 없는 과거 배포 상태의 동시 discovery도 각각1개로 수렴했다(추가1개,2.25s). 임시 PostgreSQL 컨테이너는 제거했다. 독립 리뷰가 재현한 수동 audio 재추출의 잘못된 작업 생성을 요청 단계 guard로 차단했고 projection/job 없음 회귀를 red→green으로 고정했다(1개,1.96s). reviewer도 해당1개를 독립 재실행하여 finding closure와 현재 audio bridge의 commit-ready를 확인했다.

Report/Meeting/기존 owner/architecture48개 통과(30.11s), 기존 Report 관련2개 통과(1.47s). 최신·과거 제출본, draft 제외, 최근3개 밖의 오래된 보고서, 25만 자 이상 본문의 tail/문자 locator, 권한 회수 전후 후보와 원문, legacy 인가 후 등록, 원본 변조 및 빈 본문 실패를 검증했다. 독립 reviewer가 신규6개와 기존 편집·제출·이력1개를 직접 실행하고 추가 finding 없이 commit-ready로 판정했다.

PostgreSQL16.6에서는 기존 owner/Meeting7개와 Report2개 총9개 통과(9.95s). 제출 중 durable enqueue 직후 실패할 때 report 상태·submission·artifact/binding/projection/job이 함께 rollback되고, 재시도 후 worker/검색/원문 열기가 성공했다. 동시에 기존 제출본을 발견한 두 검색도 각각1개 artifact/binding/projection/job을 생성했다. 임시 컨테이너는 제거했다.

Generic receipt의 신규·기존 자료/answer resource/architecture44개 통과(26.57s), reviewer finding 수정 뒤 관련 회귀와 additive schema37개 통과(13.90s), frontend `tsc -b` 통과. 실제 conversation worker에서 권한 회수 후 checkpoint 재사용 금지·현재 seeds/사용자 발화만 전달됨을 확인했다. No-hit 관측, 다른 principal의 execution 사용 거절, 다중 context의 현재 교집합과 이후 관측 합치기, purge 발췌/header 삭제도 포함한다.

독립 리뷰가 찾은 legacy receipt의 follow-up anchor 누락과 search→purge→receipt 발행의 copied text 재생성을 각각 red→green으로 수정했다. reviewer가2개를 직접 재실행하여 Major closure와 현재 receipt 묶음의 commit-ready를 확인했다. 격리 PostgreSQL16.6에서는 동시 검색의 turn/chunk receipt1개, projection purge 뒤 관측 기록 보존/발췌 제거, 검색 후 purge 완료 interleaving에서 새 발췌 저장 차단2개가 통과했다(3.08s). 임시 컨테이너는 제거했다. 새 table의 운영 DDL은 실행하지 않았다. 이후 제출본이 생겨도 과거 receipt의 불변 revision과 현재 허용 seed가 유지되는 추가 회귀1개도 통과했다(1.96s).

Q4 owner·공개 검색·receipt·answer resource·Action preview 구현을 완료했다. 이후의 [로컬 corpus 평가](material-corpus-evaluation.md), [실제 provider 라우팅 평가](search-routing-evaluation.md)와 [전체 acceptance 대조](search-routing-acceptance.md)를 함께 확인한다.

공개 canonical 검색은 실제 MCP server와 REST의 Task·WR·개인/팀 자료함·Meeting·Report parity, Task capability 없는 본인 자료함, 부분 추출 명시 선택/no-hit, legacy binding ID 호환, controlled 오류를 검증했다. 기존 material/API/MCP/answer resource48개(19.87s), provider/access/receipt/architecture33개(21.08s), 리뷰 수정 후 public/receipt/answer resource/summary35개(12.85s)가 통과했다. Frontend typecheck와 metadata peek6개, E2E 스크립트 문법 검사도 통과했다. 실제 provider 실행은 Q6에서 수행한다.


## Q4 acceptance 대조

| 요구 | 구현과 판별 근거 |
|---|---|
| Task 미연결 자료와 명시적 owner | `material_sources.py`의 Task/WR/개인·팀 자료함/Meeting/Report owner adapter; `test_material_search_owners.py`, `test_material_folders.py`, `test_meeting_material_search.py`, `test_report_material_search.py` |
| canonical ID·원본 revision/hash·locator | shared artifact 검색과 native revision bridge; `test_material_search_public.py`, `test_table_material_evidence.py`, Meeting/Report contract |
| owner filters·모든 허용 자료·중복/비인가 context 제거 | `MaterialSearchApplication`은 현재 owner 집합 뒤에 index를 조회; owner contract의 복수 binding·cc/팀/참석자/Report capability 회수 검증 |
| application/REST/MCP와 completed/partial/pending/failed | 실제 registered `material_search`와 public REST parity; 명시 partial/no-hit coverage와 미완료 독립 자료 contract; 기존 Task 호환 별도 유지 |
| Turn 관측·현재 권한·후속 질문 | 관측 context와 현재 권한 교집합, artifact hash·purge fence, no-hit 포함 provider checkpoint reset; `test_material_evidence_owners.py`, `test_material_search_public.py` |
| Action 근거의 현재 읽기 | `test_material_action_evidence.py`: Report 권한 회수·Task detach의 제안/대화/Action 목록·상세 parity, 두 receipt 계약의 artifact dedup, 이전 Turn 관측 불포함 |
| durable write·동시성 | 위 기록의 격리 PostgreSQL owner enqueue rollback/native discovery/receipt publish-purge 검증; 운영 DDL은 실행하지 않음 |

Action 연결의 신규/기존 preview4개(3.26s), artifact dedup1개, Turn 격리1개(2.05s), 기존 Action center·대화 lifecycle·MCP 승인·architecture71개(46.54s)가 통과했다. 독립 reviewer가 관련5개를 직접 실행하고 추가 finding 없이 commit-ready를 확인했다.


## Q7 — 하위 호환 제거 (2026-09-09)

Q1–Q6의 위 검증 수치는 해당 시점의 이력이다. Q7은 구 검색 경로와 ID 변환을 제거한다. 신규 schema에는 `conversation_content_evidence` 하나만 만들며, Task 자료 DTO는 `material_id`(artifact)와 `binding_id`(연결)를 구분한다. Task 다운로드는 `/api/tasks/{task_id}/materials/{material_id}/content`, 해제는 `/api/tasks/{task_id}/material-bindings/{binding_id}/detach`다.

Graph의 동일 Task–artifact 관계는 여러 역할의 binding이 있어도 limit 적용 전에 한 edge로 합친다. Edge receipt에는 실제 관측한 binding contexts와 hash를 저장한다. 연결 해제 후 같은 Task에 다시 붙여도 과거 receipt가 살아나지 않고, 새 조회만 새 근거가 된다. 자료를 여러 곳에서 관측한 answer resource는 실제 관측 context를 합치되 현재 살아 있는 context만 표시한다. 완료보고의 산출물 선택은 artifact당 한 번이며 output binding을 우선한다.

기존 DB의 구 receipt나 binding ID 기반 저장값은 변환하지 않는다. 이번 변경에 포함된 새 nullable 열은 `conversation_answer_resources.source_contexts/integrity_ref`, `conversation_graph_receipts.source_contexts/integrity_ref`다. 실제 DB의 DDL·구 테이블 삭제·배포는 수행하지 않았다.


Q7 검증: 전체 비통합 suite는 605 passed/4 failed(240.92s)였으며 실패 4개는 구 ID·route를 기대한 fixture였다. 전환 후 최종 관련 묶음은 101 passed/자료함 호출처 1 failed(56.57s), 해당 자료함의 최종 재실행은 5 passed(3.78s)다. 새 Graph 관측·재연결·hash·seed·중복 회귀 5개는 구현자와 독립 reviewer가 각각 통과했다(4.16s/4.09s). Frontend 전체 194 passed(7.01s), 서로 다른 binding으로 해제하는 추가 회귀를 포함한 관련 41 passed(1.48s), 최종 typecheck 통과. 격리 PostgreSQL16.6의 content receipt 동시성/purge 2개(3.34s)와 metadata 관측 context 합집합 1개(1.48s)가 통과했고 임시 컨테이너를 제거했다. Live harness는 합성 fixture dry-run만 재확인했으며 새 provider 평가 수치를 만들지 않았다.
