# 허용 목록 기반 로컬 자료 검색 평가

2026-09-09 Q5 평가. 개인 vault의 허용된 Markdown 96개를 격리 SQLite와 임시 storage에 적재했다. 원문과 질의 문자열은 결과 artifact에 포함하지 않았으며 외부 provider 호출은 0회다. 재현 도구는 `backend/scripts/material_corpus_profile.py`, 비식별 측정값은 [metrics JSON](material-corpus-metrics.json)에 있다.

## 대상과 실행 경계

- 허용 class: Work Brief, Literature, Permanent, Resources 각24개. 현재 후보905개 중 경로 제외10개를 먼저 걸렀다. class별 큰 파일 우선8개와 경로 hash 순서를 사용하고, 검사한 본문 중 credential/email 후보19개를 제외했다. 전체905개를 복사한 것이 아니다.
- `private`, People, Daily/Tracking, 개인·프로필·계좌/credential 후보, hidden path, symlink와1MiB 초과 파일은 제외한다. 알려진 credential prefix/키워드와 email 검사도 사용한다. 이 규칙은 보수적 후보 배제이며 완전한 익명화나 민감 정보 자동 판정을 주장하지 않는다.
- 고정 manifest는 OS 임시 디렉터리에만 보관한다. 상대 경로와 SHA-256을 검증하고 파일이 바뀌면 적재를 중단한다. Git에는 class·hash·개수·크기·순위·시간만 남긴다.
- 같은 파일을 여러 owner에 복제하지 않고 개인 자료함32개, 팀 자료함32개, Task 연결32개로 분산했다. 업로드 API, extraction worker, 실제 검색 application을 사용했다.
- 총1,632,154 bytes, 최소183·중앙9,261·최대117,454 bytes. 완성된 projection1,184 chunks. corpus hash `818ee179d8ff62b91ee56fe280aa6a340b31cb062588dfba24fd0549f57485f6`.
- 적재0.503초, 추출4.073초. 모든96개가 searchable이고 unavailable은0개였다. 평가 뒤 원본 hash와 vault Git status가 같았으며 임시 DB/storage를 제거했다.

## 검색 측정

질의는 원문에서 자동 선택한 한글 연속어절, 여러 문서에 겹치는 어절, 동의 표현 probe, 본문 날짜, 존재하지 않는 합성 문자열이다. 12자보다 긴 한글 연속열을 잘라 인위적 질의를 만들지 않는다. 각 질의를 두 번 검색하고 상위8 **chunk**를 평가했다.

| 종류 | 질의 수 | 기대 문서가 상위8에 있음 | MRR@8 | 평균 recall@8 | latency p50 / p95 |
|---|---:|---:|---:|---:|---:|
| 고유 한글 어절 | 91 | 74 (81.3%) | 0.5693 | 0.8132 | 10.48 / 16.58ms |
| 중복 한글 어절 | 12 | 10 | 0.5139 | 0.4643 | 10.57 / 14.98ms |
| 동의 표현 probe | 4 | 4 | 0.7083 | 0.1414 | 11.65 / 13.21ms |
| 본문 날짜 | 6 | 6 | 1.0000 | 1.0000 | 10.31 / 10.68ms |
| no-result | 1 | hit0개 | 해당 없음 | 해당 없음 | 10.28 / 10.43ms |

고유 어절의 상위8 누락17개 중15개는 파일을 명시하면 검색됐다. 따라서 해당15개는 미추출이 아니라 전역 후보의 순위/상한에서 제외된 경우다. 나머지2개는 본문 문자열이 projection에 그대로 있었지만 단독 질의의 분석 token과 문서 index token의 교집합이0이었다. 원문 유실과 형태 분석의 문맥 차이를 구분해야 한다.

기대 집합은 원문의 문자열 포함 여부로 만든 진단용 기준이다. 동의 표현 probe는 다른 표현이 들어 있는 문서 집합을 기대 대상으로 삼았으므로, semantic relevance의 사람 평가나 동의어 이해 정확도가 아니다. 넓은 기대 집합의 recall은 상위8 chunk 제한과 한 문서의 여러 chunk에도 영향을 받는다.

이 표는 최종 프로파일1회의 결과이며 각 질의2회는 latency 관찰이다. 고정 corpus라도 새 DB의 UUID와 동점 chunk 순서가 달라지면 순위가 달라질 수 있다. 초기 점검과 최종 실행 사이 고유 어절 hit는73–74개였다. SQLite의1,184 chunk 결과를 PostgreSQL이나 운영 규모의 성능 보장으로 확대하지 않는다.

## 권한과 locator

본인 검색 후보96개, 같은 팀의 다른 구성원 후보32개가 실제 owner 분포와 일치했다. 비인가 구성원은 검색 후보·unavailable·hit가 모두0개였고 비인가 context가 결과에 없었다.

716개 반환 hit에서 API locator의 char 범위와 projection DB 좌표, 원본 SHA가 일치했다. 별도로 manifest 원본 bytes를 UTF-8 BOM/줄바꿈 규칙에 따라 해석한 substring과 chunk text, bounded excerpt를 대조했고 실패0개였다. 이는 이번 Markdown corpus의 확인 결과이며 다른 문서 형식은 Q3의 별도 source-integrity 검증을 따른다.

## 재현과 검증

```sh
uv run --directory backend python scripts/material_corpus_profile.py manifest --output <OS-temp-directory>/manifest.json
uv run --directory backend python scripts/material_corpus_profile.py run --manifest <OS-temp-directory>/manifest.json --output <OS-temp-directory>/metrics.json
uv run --directory backend pytest tests/unit/test_material_corpus_profile.py -q
```

OS 임시 경로와 새 output 파일만 허용한다. 원문/SQL 값을 포함할 수 있는 예외 메시지와 traceback은 출력하지 않는다. 합성8개 검증이 통과했고 독립 reviewer도8개를 실행했다. prefix 후보 누락은 수정 후 제외를 검증했으며, owner count와 원본 excerpt 변이를 주입하면 `correctness_pass=false`가 됨을 독립 확인했다.

Q5의 로컬 corpus·권한·locator·품질/비용 측정을 완료했다. 검색 순위와 형태 분석의 한계는 위 수치로 남긴다. Q6에서는 별도 합성 fixture로 자연어 라우팅을 실제 provider에 반복 평가한다.
