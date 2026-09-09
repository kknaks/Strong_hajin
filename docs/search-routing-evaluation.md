# 실제 자연어 탐색 평가

2026-09-09, 기존 `gpt-5.6-terra / low / fast`, Codex CLI 0.153.4와 실제 Conversation worker→Codex CLI→stdio MCP를 사용했다. HTTP로 사용자 메시지를 넣고 저장된 invocation·Turn receipt·정본 링크·Action preview를 검사한다. 모델이 도구를 선택하며 Tool 이름을 질문에 넣지 않는다.

Q5의 vault corpus와는 별개인 합성 데이터만 사용했다. 숫자가 긴 Task 제목과 유사 제목, 요청자·담당자·하위 업무·연결 파일, 개인/팀 자료함, 확정 회의 발화를 새 임시 SQLite/storage에 구성한다. 원본 DB·provider runtime은 실행 후 제거한다. 결과 JSON에는 합성 질문·ID·호출 인자, 검사 결과·시간·usage·답변 hash를 보존하고 답변 본문이나 provider reasoning/auth를 넣지 않는다.

## 판정 기준

| 질문군 | 기대 시작과 필수 관측 | 호출 상한 |
|---|---|---|
| 내 업무 목록 | `task_list`, Graph 호출 없음, 대상 Task 정본 링크 | 1 |
| 관계 | `graph_search` → 필요한 `graph_neighbors`, 요청자·수신자·담당·요청→Task·하위 업무·자료의 정확한 방향/ID 6종과 Task 정본 링크 | 6 |
| Task 자료 | `material_search`, 해당 artifact receipt·정본 링크·공급사/납기일 | 3 |
| 독립 자료 | `material_search`, 개인 자료함 artifact receipt·정본 링크·승인 창구/제출일 | 3 |
| 회의 발화 | `material_search`, 해당 확정 revision·발화 시간 locator·정본 링크·출시일/담당자 | 3 |
| 자료 후속 질문 | `material_search`, 같은 artifact·결제 조건, checkpoint 초기화와 현재 canonical seed | 3 |
| 생성 전 근거 | `material_search` 뒤 `task_create_self`, 같은 Turn의 근거가 있는 pending Action, 실제 Task 미생성 | 4 |
| 권한 회수 후속 | 현재 검색, checkpoint 초기화, 회수한 자료의 seed/receipt/정본 링크·이전 사실 재노출 없음 | 3 |

모든 회차는 completed, 도구/업무 오류 없음, 잘못된 UUID 없음, 무승인 Task 생성 없음도 검사한다. 첫 도구·필수 도구·관계/근거/사실·호출 상한을 모두 만족해야 pass다. 문장 exact match를 쓰지 않으며 사실 포함 검사는 일부 오답/부정 표현을 완전히 판별하는 의미 평가가 아니다. 실패 후 복구는 오류가 관측된 경우에만 별도로 집계한다.

## 최초 실행과 수정

최초에는 7종을 각3회와 권한 회수 후속1회, 총22turn 실행했다. 초기 rubric은 17/22를 통과시켰지만 독립 review가 무관한 edge만 있어도 관계를 통과시키는 허점을 찾았다. 관계6종·Task 정본 링크, 회의와 개인 자료함의 사실 검사를 보강했다. 합성 snapshot의 완료 답변 hash와 최초 화면의 edge/resource 개수를 대조해 재판정한 결과는 **14/22**다([최초 원시 지표와 재판정](evidence/routing-initial.json)). 임시 재판정 snapshot은 제거했다.

- 목록·Task 자료·독립 자료·자료 후속 질문은 각3/3이었다.
- 관계는 3/3 시작 도구가 맞았지만 화면의 자료 edge가 모두 빠졌고 한 회차는 요청자 쪽 확장도 빠졌다. Graph binding UUID를 canonical artifact UUID로 재인가하던 실제 결함을 수정했다. 활성 Task owner로 다시 읽고 detach 뒤에는 제외하는 계약을 RED→GREEN으로 고정했다.
- 회의는 1/3만 본문 검색으로 시작했다. 나머지도 정확한 발화 근거를 찾았지만 Graph를 먼저 거쳤다. 회의에서 결정한 사실도 본문 검색으로 바로 시작하도록 지침을 명확히 했다.
- “업무를 제안해줘”는 1/3만 pending Action을 만들었다. 나머지는 근거를 읽고 문장으로 제안했다. 승인용 생성안을 명시한 질문을 별도 변형으로 평가하며 원래 표현의 결과를 대체하지 않는다.
- 권한 회수 후속에서는 기존 사실·근거·seed가 재노출되지 않았으나 검색4회로 상한을 넘었다. 재검색 횟수에 최초 호출이 포함됨을 명시했다.

진단용 중간13turn은 7/13이었다([중간 지표](evidence/routing-intermediate.json)). 강화된 rubric이 자료 관계 누락을 재현했고 독립 자료1회가 웹 검색으로 우회한 점도 기록했다. 내부 자료의 웹 우회 금지, 최종 주대상의 owner 조회와 정본 링크, 승인용 생성 요청의 Action 의미를 지침/Tool 설명에 명시했다. 중간 실행 도중 마지막 생성 전 질문 전에 MCP 설명이 변경돼, 이 실행은 동일 빌드 반복 비교에 쓰지 않는다.

최종 수정 `743fa94`는 명령의 인자·권한·승인 동작을 바꾸지 않는다. Graph binding 재인가와 provider 안내를 보완한다. 관련 계약78개(33.83s), 독립 Graph/권한 회귀3개(2.85s), 최종 지침과 harness8개(0.04s)가 통과했다. 최종 실행은 코드와 지침을 고정하고 source SHA256도 기록한다.

## 고정 빌드 최종 결과

**13turn 중12pass**, 시작 도구13/13, 잘못된 UUID·도구/업무 오류·과호출·무승인 Task 생성0이다. [최종 지표](evidence/routing-final.json)는 `743fa94`의 source SHA256을 실행 전후 대조했고 임시 데이터/runtime 제거를 확인했다.

| 질문군 | pass | 시작 도구 일치 | 호출 수 | worker 실행 시간 범위 |
|---|---:|---:|---:|---:|
| 관계 | 2/3 | 3/3 | 4–6 | 22.526–25.488초 |
| 독립 자료 | 3/3 | 3/3 | 1–1 | 11.869–12.523초 |
| 회의 발화 | 3/3 | 3/3 | 1–2 | 12.892–18.973초 |
| 명시적 승인용 생성안 | 3/3 | 3/3 | 2–2 | 17.910–19.729초 |
| 권한 회수 후속 | 1/1 | 1/1 | 3–3 | 20.872–20.872초 |

관계2회차는 Task 주변4관계와 정본 링크를 확인했지만 WorkRequest의 요청자/수신자 관계로 추가 확장하지 않아 실패했다. 관측하지 않은 경로를 receipt로 만들어 보충하지 않는다. 이 모델 선택 편차는 residual이며 모든 자연어 질문의 완전한 관계 탐색을 보장하지 않는다.

회의3/3은 해당 revision·시간 locator·출시일/담당자를 확인했고, 명시적 생성안3/3은 근거가 있는 pending Action을 만들었다. 권한 회수 후속은 현재 검색3회 후 이전 자료의 seed·receipt·정본 링크·사실을 재사용하지 않았다. 원래의 모호한 “제안해줘” 표현은 최종 변형 질문3/3으로 소급 통과시키지 않는다.

Worker 실행 시간은 도구 시간만이 아니라 CLI/provider 시작·응답 시간을 포함한다. Queue wait와 도구 latency 합계는 JSON에 별도 기록했다. 최초 실행에서만 측정한 목록·Task 자료·자료 후속 질문은 각각3/3이며, 최종 빌드에서 전체7종을 다시3회 실행했다고 주장하지 않는다. 총48turn(최초22·진단13·고정13)에 도구/업무 오류가 없어 실패 후 복구 성공률은 관측 없음이다.

## 비교 한계와 재현

2026-09-06 baseline은 서로 다른 질문을 한 번씩 실행한 관찰이다. 숫자 제목을 ID로 오인한 관계 질문은8호출 중4실패 후 답을 복구했고, Tool을 명시한 대조 질문은4호출 성공과4edge를 기록했다. 이번의 자연어 반복 평가에서는 잘못된 UUID·도구/업무 오류 여부와 실제 관계/근거를 분리해 측정한다. 모델 부하·질문·fixture·코드가 달라 지연 차이를 단일 변경의 인과적 개선으로 해석하지 않는다.

합성 질문은 각3회에 불과하다. 모든 자연어 분류의 정확도, 다른 모델/CLI 버전, 한국어 semantic retrieval, 큰 corpus에서의 provider 성능을 보장하지 않는다. 오류가 한 번도 없으면 복구 성공률은 100%가 아니라 관측 없음이다. Graph·자료 규모 비용은 별도의 [Graph 평가](graph-scale-evaluation.md)와 [로컬 corpus 평가](material-corpus-evaluation.md)를 따른다.

```sh
cd backend
# 로컬 fixture만 준비하고 제거한다.
uv run python scripts/search_routing_live_profile.py --output /tmp/routing-dry-new.json
# 기존 provider를 실제 호출한다. 결과 경로는 존재하지 않는 OS 임시 경로여야 한다.
uv run python scripts/search_routing_live_profile.py --live --repeats 3 \
  --output /tmp/routing-live-new.json
# 명시적 승인안 및 문제가 관측된 질문군의 고정 빌드 재검증
uv run python scripts/search_routing_live_profile.py --live --repeats 3 \
  --focus relation independent meeting precreation --confirmable-proposal \
  --output /tmp/routing-focused-new.json
```


## Q7 계약 변경

위 Q6 live 수치는 `743fa94`의 실행 이력으로 유지한다. Q7은 Graph material ID를 artifact로 통일하고 legacy Task 검색·receipt 변환을 제거했다. live harness의 관계 기대값도 artifact ID로 바꿨다. 이 문서의 Q6 성공률을 변경 후 재측정값으로 해석하지 않는다.
