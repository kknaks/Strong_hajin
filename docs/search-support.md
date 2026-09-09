# 탐색 지원 계약

Graph는 원장을 소유하지 않는 authorized read projection이다. REST `/api/graph/*`와
MCP `graph_search(query, limit)` / `graph_neighbors(node, limit)`는 같은 application을 호출한다.
`node`는 응답의 kind와 id를 결합한 `<kind>:<id>`다. 제목에 들어 있는 숫자는 ID가 아니다.

| node kind | searchable | traversable | overview | owner read | content searchable | 권한 owner |
|---|---|---|---|---|---|---|
| person | 이름 | 소속·요청·담당 업무·참석 회의 | 연결된 사람 | Organization member directory | 아니오 | Organization & Access의 활동 구성원 명부; 연결 대상은 각각 재인가 |
| team | 조직 이름 | 직접 소속 구성원; 빈 조직도 지원 | 구성원/팀 보기 | Organization tree/unit members | 아니오 | Organization & Access의 조직 명부 |
| project | 프로젝트 이름 | 유효 참여자·읽을 수 있는 업무 | 참여 프로젝트·업무 묶기 | Project get (REST) | 아니오 | Project read grant의 프로젝트 범위 |
| task | 제목 | 담당자·프로젝트·원 요청·부모/자식·참조·자료·보고 | 읽을 수 있는 업무 | task_get | Task에 연결한 자료 본문 | Work Task read |
| work_request | 제목 | 요청자·수신자·생성 업무 | 읽을 수 있는 요청 | work_request_get | material_search: 댓글 첨부·채택 제출 근거 | WorkRequest read |
| meeting | 제목 | 소집자·참석자·후속 업무 | 읽을 수 있는 회의 | meeting_get | material_search: 녹음 확정 전사·정제본 | Meeting read; busy block은 graph node 아님 |
| material | 아니오 (evidence node) | 읽을 수 있는 Task 연결 | 화면 업무에 연결된 자료 | task_materials_list / Task material read | material_search: 현재 읽을 수 있는 모든 지원 owner 자료 | Materials + 연결 Task read |
| report | 아니오 (evidence node) | 작성자·근거 업무 | 자기 보고 중 화면 업무를 인용한 보고 | Daily report history | material_search: 본인 보고 제출본 | Reports의 본인 보고 read |

Graph material node와 본문 검색 `material_search`는 같은 canonical artifact ID를 사용한다. 연결의 역할·해제는 별도 `binding_id`가 소유한다. 본문 검색은 개인/팀 자료함도 지원한다. 전체 owner·추출 계약은 [자료 검색 owner 경계](material-search-owners.md)를 따른다. Graph 도구는 현재 Task 또는 WorkRequest read
capability가 있어야 호출할 수 있으며, 각 결과는 소유 원장 권한을 추가 적용한다.

`graph_search`는 이름/제목 검색이며 본문 검색이 아니다. person/team/project/task/work_request/meeting만
시작점이다. material/report의 제목 검색이나 conversation/action/draft node는 지원하지 않는다.
모든 node kind는 canonical reference를 알고 있을 때 한 단계 확장을 지원한다. 없는 대상과 읽을 수 없는
대상은 같은 not-found 의미로 처리한다. Graph 연결은 권한을 부여하지 않는다.

검색 응답은 기본 20, 최대 50 node이며 이웃은 기본 20, 최대 50 edge다. 추가 **허용** 결과만
`truncated`에 반영한다. overview는 기본 120, 최대 200 edge의 bounded projection이며 전체 원장이 아니다.
응답 상한은 원장 검색의 완전성이나 처리 비용 보장을 뜻하지 않는다. Q2 대량 fixture로 별도 검증한다.

Turn receipt는 실제 반환한 node/edge만 기록한다. 표시·후속 질문 시 현재 owner read로 다시 확인하며
project 배정 해제 뒤에는 해당 제목과 edge도 사라진다. 저장 당시 권한이나 provider 기억은 권한 근거가 아니다.

단순 목록은 task_list/work_request_list/meeting_list에서 멈춘다. 관계 질문은 시작점 검색 후 필요한
한 단계만 확장한다. 파일·확정 회의 전사·보고 제출본 본문은 `material_search`로 시작한다. 실시간 전사와 오디오 자체는 텍스트 검색 대상이 아니다. 도구 선택은 모델이 하고 고정 파이프라인으로 강제하지 않는다.

검증: `tests/contract/test_relation_graph.py`, `test_access_parity.py`, `test_mcp.py`,
`test_answer_resources.py`. 지원 종류는 `modules/work/graph.py`의 `NODE_KINDS`와
`SEARCHABLE_NODE_KINDS`로 discovery 설명에도 반영한다.
