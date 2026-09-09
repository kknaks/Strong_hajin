# 탐색 라우팅·자료 무결성 acceptance

Work Brief `2026-09-08 - SCAX AX 탐색 라우팅과 관계 그래프 확장 검증`의 pre-merge 범위다. 각 구현 묶음에서 focused test와 독립 review를 수행했고, 마지막 자연어 평가에서 발견한 자료 관계 표시 회귀도 별도 수정했다. 운영 DDL·배포·merge와 모든 자연어 표현의 보장은 이 완료 기준에 포함하지 않는다.

| 요구 | 구현·검증 근거 |
|---|---|
| node별 search/traverse/overview/owner/content 지원 표 | [탐색 지원 계약](search-support.md), Graph·Access·MCP schema·Project 계약 |
| Tool 이름·설명·인자가 현재 지원과 일치 | 6종 Graph 시작점·8종 확장, canonical `node`; provider에 `material_search` 하나, 공개 MCP 계약과 provider 안내 검증 |
| 대량 관계의 경계·방향·권한·숫자 제목 | [대량 Graph 평가](graph-scale-evaluation.md): 기본5,630노드·14,609관계, 별도 follow-up/report journey, REST/application/MCP parity와 SQL/row 비용 |
| 원문 무음 유실 제거와 partial 공개 계약 | [추출 무결성 acceptance](material-integrity.md#q3-acceptance-대조): tail token, 표 구간/머리글 locator, 명시적 partial/no-hit, batch rollback·재시도, parser upgrade·원본 hash·admission |
| Task 미연결 자료와 여러 context | [자료 owner acceptance](material-search-owners.md#q4-acceptance-대조): Task·WR·Meeting·Report·개인/팀 자료함, canonical artifact와 허용 context 합집합, 원문 열기 |
| 검색·재검색·후속의 현재 권한 | owner/receipt/answer resource/Action preview 계약: 당시 관측 context와 현재 owner·binding·hash 교집합, 이후 새 연결로 과거 관측 복원 금지, no-hit 포함 checkpoint 초기화 |
| 실제 한국어 문서의 품질·비용·원문 보존 | [로컬 corpus 평가](material-corpus-evaluation.md): 허용96파일·1,184chunk·114질의·716locator/원문 구간 검증, 원문 read-only·임시 raw 제거·provider0회 |
| 자연어 시작 도구·ID/인자·과호출·누락·복구·시간 | [실제 라우팅 평가](search-routing-evaluation.md): 질문7종×3회와 회수 후속, 실패를 보존한 진단·수정 및 고정 빌드 재검증 |
| 실제 관측만 receipt에 보존하고 현재 비인가 대상 제외 | Graph/material 실제 delegated Turn·권한 회수·purge 회귀, PostgreSQL 동시 receipt와 publish/purge fence; Graph artifact ID·관측 binding/hash 재인가 회귀 포함 |
| 회의 발화에 실제 surface·근거 경계 존재 | 확정 raw/refinement의 native artifact·revision·segment/time·recording lineage, 오디오 원본 열기, 미확정/오디오 텍스트 제외; Meeting owner 회수·native binding 우회 차단 |

## 변경 범위와 검증의 한계

- Graph DB·embedding·외부 검색 서버를 추가하지 않았다. Task/Meeting 생성 command의 동작은 유지하고 승인용 제안 의미를 Tool 설명에 명시했다. UI는 기존 근거/정본 metadata·원문 열기의 필수 연결만 수정했다.
- 별도 Graph/자료 원장을 새로 합치지 않았다. Native Meeting/Report 원본은 소유 module에 남고 additive identity/projection/receipt가 연결된다. 운영 DB schema 반영과 과거 자료 재추출은 실행하지 않았다.
- Graph 제목 검색은 합성 규모에서 호출당 SQL3,043회와 약448–468ms를 사용했다. 현재 비용이 낮거나 대규모 운영에 충분하다는 결론은 아니다. PostgreSQL 물리 실행 계획·SLA는 별도다.
- 실제 corpus 고유 한국어 질의의 top-8 source hit는74/91이다. 17개 누락 중15개는 파일을 명시하면 회복했고2개는 표현에 따른 tokenizer 불일치였다. 품질을 완전하다고 주장하지 않는다.
- worker publish는 중단 시 rollback 후 원본부터 재시도한다. parser 라이브러리의 내부 메모리까지 일정한 스트리밍이나 중간 위치 checkpoint를 제공하지 않는다. 파일 저장소와 DB 간 분산 transaction이 없어 실패 업로드의 orphan byte가 남을 수 있으나 owner가 없어 검색/열기되지 않는다.
- 사용자가 보는 과거 대화 이력을 삭제하지 않는다. 현재 receipt/후속 provider 입력의 재인가와 과거 화면에서 이미 읽은 텍스트를 지우는 정책은 별개다. 실제 provider의 모델·문장·부하 변동은 소수 반복 평가로 제거되지 않는다.


Q7에서 구 Task 검색 API/facade와 이중 receipt 경로를 제거했다. Graph·검색·Task·답변은 artifact ID를 공유하고 연결 해제만 binding ID를 사용한다. 동일 artifact의 여러 binding, 관측 후 detach/rebind·hash 변경, 새 관측 후 후속 seed 복구는 `test_material_identity.py`가 검증한다. 위 Q6 자연어 측정은 당시 커밋의 이력이며 Q7 측정으로 간주하지 않는다.
