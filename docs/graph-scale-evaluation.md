# 대규모 합성 Graph 평가

2026-09-08, seed `scax-graph-scale-v1`. pytest가 만든 임시 SQLite에서 production application,
REST, MCP facade를 비교했다. fixture는 원장 seed이며 실제 사람·회사·vault 문서를 포함하지 않는다.

기본 profile은 100명·20팀·10프로젝트·2,000업무·1,000요청·500회의·2,000자료와
14,609개의 활성 방향 관계다. 종료된 참조와 해제된 binding은 별도로 존재한다.
실제 회의 정제→요약→후속 업무 승격과 업무 시작→보고 생성 경로를 더해 follow-up/report 근거도 검증한다.
UUID5로 대량 노드와 관계 ID를 고정하고, 두 추가 command journey의 ID는 해당 실행의 반환값으로 판정한다.

검증 항목은 tail 제목·숫자 제목·동명이인·종류 간 충돌·검색 상한·회의 truncation,
parent/reference/project/material/report/follow-up의 방향, 허용/비허용 principal,
해제된 참조/binding, 응답 상한 밖 담당 업무/자료 owner다. 기대 관계는 fixture에서 별도로 만든다.
공유 데이터의 전체 합집합을 query한 뒤 비인가 결과를 count하는 것을 통과 조건으로 쓰지 않는다.

이 profile에서 드러난 수정:

- 회의 검색은 최대 hit 수보다 하나 더 읽어 `truncated`를 정확히 계산한다.
- 담당 사람의 업무와 참석 회의를 먼저 고른 뒤 응답 상한을 적용해 일반 업무 첫 페이지 밖의 연결도 찾는다.
- 측정 당시 자료 binding ID의 활성 context를 직접 찾은 후 Task owner read를 적용했다. Q7부터 같은 조회는 artifact ID에 연결된 활성 context를 찾는다. 업무 50개 순회로 자료를 찾지 않는다.
- Task에서 연결 project를 읽을 때도 Project owner read를 거쳐 `part_of`를 반환한다.

측정값은 [graph-scale.json](evidence/graph-scale.json)에 남긴다. 제목 검색 5개 표본에서
application 호출당 SQL 3,043회, DB cursor 반환 11,746행, ORM 객체 6,341개 로드, 약 448–468ms였다.
SQLAlchemy hook을 각 호출 구간에만 설치해 센 값이다. DB 행은 cursor의 fetchone/fetchmany/fetchall에서 집계하므로 scalar/tuple 조회와 반복 조회도 포함한다. 세 fetch 모드를 3행 쿼리로 보정했다. 물리 디스크 scan row나 buffer 수는 이 지표에 포함되지 않는다.
현재 adapter는 각 원장의 authorized 목록을 만든 다음 제목을 거르므로, 50개 응답 상한이 내부 조회 비용을
제한하지 않는다. 이 결과는 SLA나 대규모 운영 적합성 보장이 아니다. PostgreSQL 물리 실행 계획과 더 큰 profile은
이 SQLite 실측으로 입증하지 않는다.

재현:

```sh
cd backend
uv run pytest tests/contract/test_relation_graph_scale.py -q \
  --junitxml=/tmp/scax-graph-scale-results.xml -o junit_family=legacy
```

Q3/Q4의 본문 전체 추출·범용 자료 권한, Q5의 실제 로컬 corpus, Q6의 live provider 라우팅은 별도 검증이다.
