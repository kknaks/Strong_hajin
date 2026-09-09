# 첨부 데이터 관리 설계안 — 원문·파생 결과·검색 projection

2026-09-09 현재 추출 계약은 [자료 추출 무결성](material-integrity.md)을 따른다. 아래 2026-09-06 검토·결정 중 출력 상한, 표 전체 1블록, 이전 결과의 검색 유지, 저장된 evidence의 권한 재검사 관련 내용은 최신 계약으로 대체됐다. 원본/파생 projection 분리와 미정 retention은 유지한다.

기준: Work Brief `2026-09-04 - SCAX Office 문서 파서와 첨부 데이터 관리 설계`, SPEC-006(자료 검색·출처 계보), ERD §6(ATTACHMENT / ATTACHMENT_BINDING / EVIDENCE), 현재 구현 `ax-workspace` (`attachments`, `attachment_bindings`, `material_extractions`, `material_chunks`, `conversation_material_evidence`, `durable_jobs`).

합의된 방향(Brief 핵심 판단): **원본 byte는 private object storage**, **정규화된 추출 텍스트와 source locator는 PostgreSQL**, **lexical index·향후 embedding은 추출 결과에서 재생성 가능한 별도 projection**. 아래는 그 방향 안에서 아직 고정하지 않은 항목을 비교하고 사용자 결정을 분리한 문서다. 이 문서는 코드/DB 계약이 아니다. Office parser(`modules/work/document_parsing.py`, `platform/document_parsers.py`)의 `ParsedDocument`는 persistence entity가 아니며, 아래 결정이 확정되기 전에는 어떤 테이블에도 자동 저장하지 않는다.

## 1. 세 lifecycle과 그 경계

| 계층 | 정본(source of truth) | 현재 구현 | 목표 |
|---|---|---|---|
| A. 원본 byte | object storage의 immutable object(version) | `MaterialStorage` port, `LocalDirectoryMaterialStorage`(`AX_MATERIALS_DIR`), `attachments.source_ref`=storage key, `integrity_ref`=sha256 | Azure Blob container adapter(같은 port), object key = `tasks/<task>/<uuid>` 유지, blob version 또는 immutability policy |
| B. 추출 결과(정규화 텍스트 + locator) | PostgreSQL, attachment 버전(`integrity_ref`)별 파생 projection | `material_extractions`(lifecycle) + `material_chunks`(sequence·page·text). Office 결과는 미저장 | `ParsedDocument.blocks`를 저장할 "extracted block" 테이블 — granularity·locator 컬럼은 §3 결정 |
| C. 검색 projection | B에서 언제든 재생성 가능한 파생물 | `LexicalMaterialRetriever`가 B의 chunk를 직접 스캔(별도 index 없음) | PostgreSQL FTS(`tsvector` GIN) 또는 trigram index를 B 위에 두거나 별도 테이블로 분리; embedding은 같은 자리에 별도 projection으로 추가 |

원칙:
- A는 삭제·보존 정책의 대상이고, B·C는 A의 버전에 종속되어 A가 사라지면 함께 정리된다(전파 방향 A → B → C).
- B는 parser 버전과 budget에 종속된다. parser가 바뀌면 B를 재생성할 수 있어야 하며, 그때 C도 재생성한다.
- 권한 판정은 어느 계층에서 읽어도 `attachment_bindings`(context·role·unbound_at)와 TaskAssignment를 거친다. C에 권한 정보를 복제하지 않는다.

## 2. 원본 byte (A) — 선택지 비교

| 선택지 | 장점 | 단점 | 판단 |
|---|---|---|---|
| A1. DB `bytea` | 트랜잭션 일관성, 단일 백업 | DB 팽창, 25MB 파일이 WAL·replica를 압박, Azure Flexible Server 저장 비용 | **제외** (Brief 합의) |
| A2. Object storage + DB에 key·hash·metadata | 표준 패턴, 버전/immutability/lifecycle 정책을 storage가 제공, `MaterialStorage` port로 이미 분리 | 업로드 트랜잭션과 object write의 원자성은 "먼저 object, 그 다음 row commit; 실패 시 orphan object GC" 규약 필요 | **채택** |
| A3. 외부 원본 참조만(Drive/SharePoint locator) | 복제 없음 | SPEC-006 S-3 legacy metadata에 해당, 내용 검색 불가 | Attachment `source_kind=link`로 병행 지원, 이번 범위 밖 |

A2 세부 결정 필요:
- **Blob versioning vs immutable-by-key**: 현재 key에 uuid를 포함해 사실상 immutable. 같은 파일을 다시 올리면 새 attachment+key가 생기므로 blob versioning은 불필요. → 제안: immutable key, versioning 끔, soft-delete 보존 기간만 설정.
- **Orphan GC**: object write 후 DB commit 실패 시 남는 object. → 제안: `attachments`에 없는 key를 주기 스캔해 N일 후 삭제하는 별도 job kind(`durable_jobs`) 후속.
- **암호화·접근**: Azure Blob은 저장 시 암호화 기본, SAS 미사용(서버가 대리 읽기). 원본 열기(`/content`)는 항상 SCAX API를 통해 권한 재검증 후 스트리밍.

## 3. 추출 결과 (B) — granularity와 schema 후보

현재 `material_chunks`는 "약 1,000자 overlapping window"다. Office parser는 문서 구조 단위 block(문단·표·시트 행·슬라이드·노트·페이지)을 낸다. 두 granularity를 어떻게 저장할지가 핵심 미결 사항이다.

| 선택지 | 저장 단위 | 장점 | 단점 |
|---|---|---|---|
| B1. 현재 chunk 테이블 재사용 | block 텍스트를 이어 붙인 뒤 1,000자 window로 재분할 | schema 변경 없음, 검색 코드 그대로 | 표·시트 행 경계와 locator(시트/셀 범위·슬라이드)가 window 안에서 사라짐; 근거 카드가 "어느 시트 몇 행"을 말할 수 없음 |
| B2. block 테이블 신설 + chunk를 block 위에 파생 | `material_blocks`(order, kind, text, locator JSON/컬럼) + `material_chunks.block_id`로 window가 어느 block(들)에서 왔는지 연결 | locator 보존, 문서 구조 기반 근거 카드, chunk는 검색용 파생물로 재생성 가능 | 테이블 1개 추가, 저장량 ≈ 텍스트 2배(block + chunk). text/PDF 경로도 block(문단/페이지)으로 통일해야 일관 |
| B3. block만 저장, 검색은 block 단위 | `material_blocks`만 | 단순, locator 정확 | 긴 문단(4,000자)이 excerpt 품질을 떨어뜨림; 짧은 시트 행은 수천 개 row → 검색 잡음 |

제안: **B2**. 이유: (1) SPEC-006 "출처 계보"와 근거 카드의 "구간·원본 열기"가 locator를 요구, (2) chunk는 언제든 재생성 가능한 파생물이므로 정본은 block이어야 한다, (3) text/Markdown·PDF도 이미 문단/페이지 단위 block으로 자연스럽게 맞는다.

B2 컬럼 후보(사용자 검토용, 미확정):
- `material_blocks`: `id`, `extraction_id`(FK, attachment 버전에 종속), `order`, `kind`(paragraph·table·header·footer·sheet_row·slide_text·slide_table·notes·page), `text`(≤4,000자), `locator_kind`, `page`, `sheet`, `cell_range`, `slide`, `ordinal`, `warnings`(JSON). unique(extraction_id, order).
- `material_extractions` 확장: `parser`(예: `office@1`, `pypdf@1`), `parser_budget_hash`, `status`에 `needs_ocr` 추가, `warnings`(JSON), `block_count`.
- `material_chunks`: `block_start_order`, `block_end_order`(window가 걸친 block 범위) 추가; 기존 `page`는 block에서 파생.

결정 질문(사용자):
1. **표(table)의 단위**: 표 전체 1 block(현재 parser) vs 행 단위. 계약서 표는 전체가 자연스럽고, 매출 시트는 행 단위가 맞다 → DOCX/PPTX 표는 전체, XLSX는 행 단위로 가는 현재 parser 결정을 유지할지.
2. **XLSX 행 상한**: 시트당 2,000행·행당 200셀(현재 budget). 데일리 매출·광고 보고서 같은 표본은 수백 행 규모지만, 상한을 넘는 시트는 "일부만 색인"으로 표시해야 한다. 상한 값과 표시 방식 확인.
3. **숨김 시트**: 색인하되 warning(현재) vs 제외. 개인정보가 숨김 시트에 있는 사례가 있어 제외가 안전할 수 있다.
4. **발표자 노트**: 색인 포함(현재) vs 제외.
5. **PDF 페이지 상한 300**과 OCR 필요 상태의 UI 문구.

## 4. 검색 projection (C)

| 선택지 | 내용 | 판단 |
|---|---|---|
| C1. 현재: Python lexical scan | 권한 범위 chunk 전부 읽어 토큰 매칭 | Task 1개 범위(수백 chunk)에서는 충분. 전사 검색(SPEC-006 S-1)으로 확장하면 부족 |
| C2. PostgreSQL FTS | `tsvector` 컬럼 + GIN. 한국어는 기본 parser가 형태소를 못 나눠 `simple` + bigram 보조가 필요 | 확장 없이 가능. 전사 검색 도입 시 채택 후보 |
| C3. `pg_trgm` | 부분 문자열·오탈자에 강함 | Azure Flexible Server 지원 extension 목록에 있음(확인 필요). 한국어에 유리 |
| C4. embedding(pgvector) | 의미 검색 | Azure 지원 extension이지만 Brief에서 "eval이 필요성을 보인 뒤" |

제안: 지금은 C1 유지(범위가 Task 하나), block 테이블 도입 시 C2/C3를 같은 마이그레이션에 넣지 않고 별도 change unit으로 분리. `MaterialRetriever` port는 이미 교체 가능.

## 5. Version · 재처리 · 삭제 전파 · retention

- **버전 identity**: attachment 1행 = 파일 1버전(`integrity_ref`). 같은 파일 재업로드 = 새 attachment. extraction/block/chunk는 (attachment, parser 버전)에 종속. parser 업그레이드 시 새 extraction row를 만들고 이전 row는 `superseded`로 남길지(감사) vs 교체할지 → **결정 질문 6**.
- **재처리 trigger**: (a) parser 버전 변경, (b) 실패 재시도(현재 transient만 자동), (c) 사용자의 "다시 읽기". 모두 `durable_jobs` kind `material.extraction`으로 멱등 처리. 재처리 중에는 이전 결과를 계속 검색에 쓰다가 완료 시 원자적으로 교체(현재 `complete()`가 chunk를 delete+insert하는 방식 유지).
- **삭제 전파**: 현재 detach는 binding만 닫고 attachment·chunk를 남긴다(lineage 유지). 검색은 live binding만 보므로 노출되지 않는다. 진짜 삭제(개인정보 삭제 요청)는 A object 삭제 → B/C 삭제 → `attachments.lifecycle=purged`, `conversation_material_evidence`의 excerpt는 **redact**(행은 남기되 텍스트를 지움)해야 감사 연속성이 유지된다 → **결정 질문 7**: evidence excerpt를 지울지 유지할지.
- **권한 변경 전파**: TaskAssignment가 바뀌면(배정 거절·업무 취소) 다음 검색부터 자동 반영(검색 시점 재검증). 이미 기록된 evidence는 그 시점의 권한으로 정당했으므로 유지.
- **Retention**: A는 Task 종료 후 N년(법정 보존 기간 미확정), B/C는 A와 동일 또는 더 짧게(재생성 가능), evidence는 대화 보존 기간을 따른다 → **결정 질문 8**: N과 법적 요구(계약서·개인정보) 확인.
- **DB 팽창 추정**: 표본 45개 파일 총 5.3MB → 추출 텍스트는 원본의 5~15%(Office는 zip 압축 해제 후 텍스트만) 수준으로 예상. block+chunk 2배 저장을 가정해도 파일당 수십 KB. 이미지 위주 PPTX/PDF는 텍스트가 거의 없다.

## 6. OCR 필요 상태

parser는 `needs_ocr`(이미지 있음, 텍스트 없음)와 `empty`를 구분한다. 저장 시 `material_extractions.status=needs_ocr`로 두고 UI는 "스캔 문서 — 내용 검색 불가(OCR 미도입)"로 표시, AX 검색의 `unavailable_materials`에 사유와 함께 나열한다. OCR 도입 여부·엔진(Azure AI Document Intelligence 등)은 별도 결정.

## 7. 사용자 결정 목록 (요약)

| # | 질문 | 제안 기본값 |
|---|---|---|
| 1 | DOCX/PPTX 표는 전체 1 block, XLSX는 행 단위 | 유지 |
| 2 | XLSX 시트당 2,000행·행당 200셀 상한과 "일부 색인" 표시 | 유지, UI에 truncated 표시 |
| 3 | 숨김 시트 색인 | **제외**로 변경 검토 |
| 4 | 발표자 노트 색인 | 포함 |
| 5 | PDF 300쪽 상한, OCR 필요 문구 | 유지 |
| 6 | parser 업그레이드 시 이전 extraction 보존(superseded) | 보존(감사), chunk는 교체 |
| 7 | 원본 purge 시 evidence excerpt 처리 | redact(행 유지, 텍스트 삭제) |
| 8 | 원본·파생 retention 기간과 법적 요구 | 미정 — 사용자 확인 필요 |
| 9 | B2(block 테이블 + chunk 파생) 채택 | 채택 |
| 10 | 전사 검색 index(C2/C3) 도입 시점 | Task 범위 검색으로 충분한 동안 보류 |

## 8. 확정된 결정과 구현 상태 (2026-09-06)

사용자가 1·6·7·9를 확정했고 나머지는 위 제안 기본값을 따른다. 8(retention 기간)은 법적 확인이 필요해 여전히 미정이다.

| # | 결정 | 구현 |
|---|---|---|
| 1 | DOCX/PPTX 표는 전체 1 block, XLSX는 행 단위 — 유지 | parser 그대로, `material_blocks.kind`로 보존 |
| 6 | parser 업그레이드 시 이전 extraction 보존(superseded), chunk는 교체 | `material_extractions.parser_version`·`superseded_at`, unique는 (attachment, integrity_ref, parser_version) |
| 7 | 원본 purge 시 evidence excerpt는 redact(행 유지, 텍스트 삭제) | `purge_attachment`가 bytes·block·chunk를 지우고 evidence excerpt를 비운다 |
| 9 | B2(block 테이블 + chunk 파생) 채택 | `material_blocks` 도입, chunk는 block에서 파생되고 `block_id`로 출처를 가리킨다 |
| 8 | retention 기간 | **미정** — 법정 보존 기간 확인 뒤 별도 결정. 현재는 자동 삭제 없음 |

Azure Blob adapter와 전사 index(C2/C3)는 실행 큐 13번(운영 gate)에 남는다.
