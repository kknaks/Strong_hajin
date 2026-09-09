# 자료 추출 무결성

현재 parser version은 `3`이다. Version `2`에서 TXT/Markdown·DOCX·XLSX·PPTX·PDF의 기존 문자·청크·행·열·페이지 출력 상한을 제거했고, `3`에서 표 구간과 머리글 context를 보존한다. 검색 응답의 hit·excerpt 상한은 유지한다. 업로드 25MB와 OOXML member 수·압축 해제 크기·압축률·암호화 검사는 그대로 적용한다.

추출 worker는 블록과 청크를 익명 임시 파일에 순서대로 저장한다. XLSX는 read-only row iteration과 실제 XML 범위를 사용하며 workbook을 닫는다. DOCX는 중첩 표와 첫 페이지·짝수 페이지 머리글/바닥글을 포함한다. DOCX/PPTX/PDF 라이브러리 내부 문서 모델까지 스트리밍되는 것은 아니다.

DB에는 128개씩 insert한다. 블록·청크 ID는 extraction ID와 sequence로 결정되며, 모든 batch와 terminal 상태는 하나의 트랜잭션에서 공개된다. 중단된 publish는 rollback하고 lease 만료 뒤 원본부터 재시도한다. 중간 batch에서 이어 읽는 체크포인트 방식은 아니다. worker는 성공·실패·stale 반환과 관계없이 임시 파일을 닫는다.

혼합 스캔 또는 decode 실패 PDF는 읽지 못한 페이지와 이유를 `coverage.missing_units`에 남긴다. 읽은 본문이 있으면 `partial`, 없으면 설명 가능한 실패로 끝낸다. `warnings`, `coverage`, parser version과 integrity를 자료 조회와 검색 응답에서 보존한다. `partial`은 일반 검색과 Task만 지정한 검색 후보에서 제외하고, 파일의 `material_id`를 명시했을 때만 검색한다.

`material_extractions.warnings`와 `coverage`는 nullable JSON 열이다. 기존 DB에는 additive schema 반영이 필요하다. 운영 DB에는 DDL·재추출을 실행하지 않았다. 기존 projection은 아래 정책에 따라 현재 검색에서 검증하고 별도 추출 결과로 교체하며, 과거 행을 완전한 coverage로 소급 인정하지 않는다.

## 검증

2026-09-09 첫 구현 묶음: parser/material unit·contract와 architecture 58개 통과. 합성 데이터만 사용했다.

- TXT/Markdown 246,032자 이후 토큰, XLSX 2,002행·205열, DOCX 5,000블록 이후와 긴 문단, PDF 301쪽, PPTX 긴 본문·표·노트의 마지막 토큰을 보존한다.
- PDF 302쪽 이미지 전용 페이지를 `partial` 누락 범위로 기록한다.
- DB 두 번째 chunk batch에서 실패를 주입하면 블록·청크가 모두 rollback된다. 재시도는 전체를 한 번 게시하며 마지막 토큰이 MCP 검색된다.
- 5개 형식에 임시 저장 오류를 주입하면 문서 손상으로 확정하지 않고 transient retry 경로를 따른다.

독립 review에서 Office sink 오류가 손상 문서로 변환되는 문제를 발견하여 수정하고 delta review를 통과했다. 첫 묶음에서는 대형 부하 profile, 운영 admission ceiling, 큰 표의 구간/header context, partial 명시적 파일 검색과 receipt를 검증하지 않았다. 이 문서는 Q3 전체 완료 증거가 아니다.

## 대형 합성 부하와 admission

`backend/scripts/material_integrity_profile.py`는 원문을 생성하여 격리된 SQLite·임시 storage에 업로드하고 실제 worker와 Task 검색 API를 실행한다. 원본·DB는 종료 시 제거한다. 수치는 [JSON evidence](evidence/material-integrity-scale.json)에 보존한다. 파일별 새 프로세스에서 실행했고 CPU·DB·형태소 분석기 cold start를 포함한 로컬 측정으로, 운영 SLA는 아니다.

| 합성 원문 | 처리 | 추출 / worker 전체 | DB 블록·청크 | 마지막 토큰 |
| --- | --- | --- | --- | --- |
| XLSX 10만 행·100만 non-empty 셀 | completed | 5.748 / 22.981초 | 각각 100,000 | 검색됨 |
| DOCX 작성된 350쪽·표 350개·16,800셀 | completed | 0.615 / 1.828초 | 각각 701 | 검색됨 |
| PPTX 990슬라이드·표·노트 | completed | 0.554 / 1.538초 | 각각 2,970 | 검색됨 |
| PPTX 1,000슬라이드·표·노트 | too_large | 0.012 / 0.017초 | 0 | 색인하지 않음 |

최대 RSS는 fixture 생성과 라이브러리 초기화를 포함해 약 699–737MiB였다. 별도 최소 프로세스에서 형태소 분석기 초기화만 수행하면 최대 RSS가 약 579MiB였다. 차이를 곧바로 문서별 순수 메모리 비용으로 해석하지 않는다. 무한히 커지는 출력 목록 대신 spool과 insert batch를 사용하지만 라이브러리 자체의 메모리까지 일정하다고 주장하지 않는다.

PPTX 1,000슬라이드 fixture는 package member 4,039개로 기존 4,000개 제한을 넘는다. 990슬라이드는 3,999개라 통과했다. 이는 해당 template 구조의 실측 경계이며 일반적인 슬라이드 개수 제한이 아니다. 기존 OOXML admission 상한은 유지한다.

PDF는 페이지와 중첩 Form의 decoded/expanded content를 text parser 전에 확인한다. 페이지 8MiB·문서 합계 64MiB·페이지별 Form 호출 4,000회·중첩 32단계를 admission ceiling으로 적용한다. pypdf의 개별 stream decompression 방어도 유지한다. 경계 초과 시 앞에서 읽은 내용도 공개하지 않고 `too_large`로 끝낸다. [pypdf 공식 문서](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)는 text extraction 전에 decoded content 크기를 검사하도록 안내한다. 이 보수적 ceiling의 초과 영역에 대한 운영 용량 확대는 이번 검증에 포함하지 않는다.

설치된 pypdf는 Form 호출 5,000회를 넘으면 예외 없이 본문을 생략할 수 있었다. 5,001회 호출 뒤의 마지막 고유 Form이 사라지는 합성 PDF로 재현했다. application은 라이브러리보다 먼저 실제 호출 확장을 검사하므로 logging level을 높여도 잘린 완료 상태를 만들지 않는다. 그 외 parser warning은 thread별로 수집하여 불확실한 페이지를 `partial` 범위에 표시하고, raw warning 문자열은 결과에 저장하지 않는다.

Q3 구현·검증은 아래 acceptance 대조를 완료했다. Q4의 범용 owner 원장·후속 Turn 권한 재검증과 Q6의 실제 provider 평가는 별도 잔여 범위이며 Task 자료 변경만으로 전체 Work Brief acceptance를 충족하지 않는다.

Admission 묶음은 관련 parser/material unit·contract 60개 통과(2026-09-09, 9.77초). 독립 review에서 Form 호출 생략과 내부 capacity 예외의 warning 변환을 각각 재현해 수정했으며, logging WARNING/CRITICAL 두 경로를 재검토해 통과했다.

## 표 구간과 근거 위치

Version `3`은 DOCX/PPTX 표를 최대 16행·32열 구간, XLSX를 1행·32열 구간으로 나눈다. 빈 셀을 생략해도 DOCX/PPTX 값에는 `R2C3=값`처럼 셀 좌표가 남고, XLSX에는 원래 셀 주소가 남는다. 각 구간의 `source_locator`는 행·열 범위와 시트/슬라이드/표 번호를 보존한다. DOCX 머리글·바닥글 안의 표는 section·container·variant도 포함한다.

`header_context`는 첫 non-empty 행의 해당 열 구간을 최대 512자로 보여 준다. `basis: first_non_empty_row`는 작성자가 지정한 머리글이라는 단정을 피하며, preview가 짧아지면 `preview_truncated`로 표시한다. 원래 행은 자체 블록에 전부 색인한다. 병합 셀이 여러 열 구간에 걸치면 각 구간에서 같은 context를 사용할 수 있다. XLSX 병합 정보는 read-only worksheet가 제공하지 않으므로 worksheet XML을 별도로 순회하며 읽은 element는 즉시 해제한다.

Context는 청크 본문과 별도로 저장하여 검색과 재색인에 함께 사용한다. 검색 hit·MCP answer resource·Turn receipt는 같은 구조화 위치를 유지하고, hit와 receipt에는 header context와 추출 상태 snapshot을 포함한다. 문자 offset은 원본 파일 byte 위치가 아닌 정규화된 추출 projection 위치이며 `char_offset_basis: extracted_projection`으로 명시한다.

추가 nullable 열은 `material_blocks.source_locator/header_context`, `material_chunks.context_text`, `conversation_content_evidence.source_locator/header_context/extraction_snapshot`이다. 기존 DB의 additive schema 반영이 필요하며 실제 DB DDL은 수행하지 않았다.

2026-09-09 표 묶음 검증은 관련 unit/contract 77개 통과(13.07초). 독립 review에서 빈 셀의 열 의미 유실, XLSX 병합 머리글의 후반 열 누락, receipt의 section 숫자 타입 변환을 찾아 수정했고 delta review를 통과했다. 표 본문과 문서 머리글의 REST·MCP·receipt 위치 일치, context 재색인 이후 결과 동일성을 포함한다.

[Version 3 XLSX 재측정](evidence/material-table-scale.json)은 10만 행·100만 셀을 10만 block/chunk로 완료하고 마지막 셀을 검색했다. 추출 10.143초, worker 40.863초, 마지막 토큰 검색 0.089초, 생성·앱·분석기 포함 최대 RSS 약772MiB였다. 위 version 2 측정에 비해 비용이 늘었으며 XML 추가 순회와 context 보존·색인을 포함한 관측값이다. 개별 원인의 비용 분리는 측정하지 않았다.

## 명시적 파일 선택과 근거 재인가

자료 검색의 optional `material_id`는 자료 목록·Graph와 동일한 artifact ID다. REST `/api/materials/search`와 MCP `material_search`에서 같은 인자를 받는다. Task 범위는 `resource_type=task`·`resource_id`로 좁힌다. 현재 읽을 수 있는 업무의 활성 연결만 먼저 모은 뒤 그 파일로 좁히며, 파일을 명시해도 권한은 넓어지지 않는다. 미존재·비인가·연결 해제·purged 파일은 같은 not-found 응답으로 처리한다.

명시한 파일이 partial이면 읽은 구간을 검색할 수 있다. hit와 receipt에 추출 상태·coverage·warnings가 남고, 검색어가 발견되지 않아도 `selected_material.extraction`에 같은 처리 범위를 반환한다. Provider 지침은 사용자가 명시한 파일만 선택하고 누락 범위를 함께 밝히도록 한다. 일반 검색에서 partial 파일을 임의 선택해 재시도하라는 지침은 제공하지 않는다.

저장된 material evidence는 conversation 조회마다 Task owner 읽기 권한·활성 binding·artifact ID/integrity·purged 상태를 다시 확인한다. 프로젝트 참여 권한 회수, 연결 해제, 원본 무결성 변경 이후에는 이전 발췌를 반환하지 않는다. 자료 검색의 실행 요약에는 재인가할 전체 후보 ID가 없으므로 파일명·검색 건수를 저장하지 않고 호출 결과 수신 사실만 표시한다. 과거 저장 요약도 조회 시 같은 규칙으로 표시하며 실제 자료 근거는 별도 재인가한 카드에 둔다.

2026-09-09 검증: material/MCP/answer resource 47개, conversation/provider/architecture·persistence 포함 29개 통과. 마지막 실행 요약 수정 후 관련 33개 회귀 통과(11.82초). 독립 review가 no-hit metadata 누락과 과거 실행 요약의 제목·건수 노출을 재현했고 두 수정의 delta review를 통과했다. 실제 MCP server 호출·REST parity, no-hit partial 범위, 미존재/비인가 응답 동일성, detach/purge/integrity 및 public project 참여 회수 후 receipt를 검증했다.

## 과거 parser 결과의 전환

현재 검색은 설치된 parser version과 coverage가 맞는 projection만 사용한다. 과거 completed/partial 결과는 DB 행을 보존하지만 현재 조회에서는 `status: failed`, `stored_status`, `failure_reason: parser_upgrade_required`, `reextraction_required: true`로 표시한다. 현재 parser라도 completed/partial과 coverage.complete가 일치하지 않으면 `projection_unverified`로 검색에서 제외한다. 조회가 작업을 생성하거나 과거 행의 상태·블록을 고치지는 않는다.

Worker의 idle pass는 알려진 이전 버전 `1`·`2`의 활성 결과를 concurrency 크기의 batch로 선택하고, 최신 parser용 별도 extraction과 durable job을 같은 트랜잭션에 만든다. 현재 결과는 queued부터 시작하고 원본 전체를 다시 읽는다. 과거 행은 superseded로 남으며 늦게 끝난 이전 실행은 publish할 수 없다. 배포 전 queued/running 작업도 같은 경로로 전환한다.

Attachment 잠금 안에서 설치된 버전과 활성 버전을 확인하고 동일 artifact/parser 요청을 합친다. 미래·알 수 없는 parser가 활성 상태면 수동 요청도 downgrade하지 않는다. 맞지 않는 worker가 미래 작업을 받으면 compatible worker가 가져갈 수 있도록 delivery를 반환한다. SQLite 테스트만으로 잠금을 주장하지 않고, 격리된 PostgreSQL 16.6에서 동시 Worker의 작업 1개·현재 projection 1개와 기존 durable upload를 검증했다(2개 통과). SQLAlchemy의 [FOR UPDATE 문서](https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.GenerativeSelect.with_for_update)에 정의된 PostgreSQL `OF`/`SKIP LOCKED` 동작을 사용한다.

2026-09-09 관련 unit/contract/architecture 51개 통과(14.07초). 과거 결과 제외·stored status, 원문 tail 재추출과 과거 블록 보존, queued/running 전환, 미래 버전 보호, 설치하지 않은 parser 거절, superseded finish fence를 포함한다. 독립 review에서 수동 재추출의 future downgrade를 재현·수정했고, 보호·finish fence 2개 직접 실행을 포함한 delta review를 통과했다.

추가 [Office v3 profile](evidence/material-office-v3-scale.json)은 DOCX 350쪽/16,800셀에서 추출0.693초·worker2.041초·701 block/chunk, PPTX 990슬라이드/표/노트에서 추출0.606초·worker1.650초·2,970 block/chunk로 마지막 토큰을 찾았다. PPTX 1,000슬라이드는 member4,039개로 too_large·0 projection이었다. 각 profile은 별도 프로세스와 임시 SQLite/storage를 사용했다.

## Q3 acceptance 대조

| 요구 | 현재 근거 |
| --- | --- |
| 원문 출력 상한 제거·끝부분 검색 | `test_material_integrity.py`의 6형식 tail fixture, XLSX/Office v3 대형 profile |
| 정확한 표 구간·header context | `test_table_materials.py`, `test_table_material_evidence.py`의 sparse/merged/section·재색인·REST/MCP/receipt parity |
| admission과 설명 가능한 terminal | 기존 25MiB 경계의 실제 upload 허용/거절, OOXML bomb/encryption preflight, PDF decoded/Form admission·partial 누락 범위 회귀 |
| partial은 명시적 파일에서만 | `test_material_integrity_lifecycle.py`의 일반/Task-only 제외, 단일 material_id 선택, no-hit coverage, 실제 MCP server 호출과 receipt |
| 재시도·중복·중단 publish | batch 중단 rollback·재시도 및 PostgreSQL durable upload·동시 parser upgrade 검증 |
| 원본 revision·과거 projection | `test_material_parser_upgrade.py`의 별도 추출/과거 블록·finish fence·미래 버전 보호, `test_material_source_integrity.py`의 parser 전 원본 SHA256 검사 |
| Non-Goals 보존 | 25MiB·OOXML 방어 유지, OCR·embedding·운영 DDL 없음, hit/excerpt 제한 유지 |

원본 바이트가 기록된 SHA256과 다르면 parser를 호출하지 않고 `failed / integrity_mismatch`로 끝내며, retry로 잘못된 원본을 받아들이지 않는다. 자료 purge는 새로 복사한 receipt header context도 excerpt와 함께 제거한다. 마지막 integrity 수정은 관련 21개 테스트 통과(7.11초), 독립 reviewer가 원본 변조·본문/header 표 purge 3개를 직접 실행해 통과했다. 별도 25MiB 경계 회귀를 포함한 source integrity 2개도 통과(2.21초). 임시 PostgreSQL 컨테이너는 검증 후 제거했다.
