# AX 구조화 답변

답변의 설명은 Markdown으로 작성하고, 실제 리소스를 가리키는 위치와 목록 배치는 별도 요소로 표현한다. 업무·회의·업무 요청·자료·보고서가 같은 계약을 사용한다. 승인 요청과 실행 결과는 기존 서버 Action 카드가 담당한다.

## 생성과 검증

Codex CLI의 output schema와 서버 `AnswerDocument` 검증을 함께 사용한다. 예시는 가상의 식별자다.

```json
{
  "body": "다음 순서로 진행하세요.\n\n{{steps}}\n\n배경은 {{meeting}}에서 확인하세요.",
  "elements": [
    {
      "key": "steps",
      "type": "resource_list",
      "ordered": true,
      "items": [{"ref": "task:example-task", "description": "변경 요청을 먼저 정리하세요."}]
    },
    {"key": "meeting", "type": "resource_reference", "ref": "meeting:example-meeting"}
  ],
  "follow_up_candidates": []
}
```

- `body`: 일반 Markdown. 참조가 필요 없으면 `elements: []`다.
- `resource_reference`: 문장·표 안에 넣는 단일 참조.
- `resource_list`: 독립 문단에 넣는 목록. 순서, 제목 아래 설명, 간격은 프론트가 렌더한다. 설명은 요소를 중첩하지 않는 Markdown이다.
- `{{key}}`: 명시적 삽입 위치. key는 영문자로 시작하는 영문·숫자·`_`·`-`, 최대 64자다. code, escape, HTML, Markdown 링크 안은 삽입 위치가 아니다.

서버는 key 중복·미정의·미사용, 잘못된 목록 위치·type·ref·추가 필드를 거절한다. 참조 문자열은 기존 도구/대화 seed의 `type:id`를 재사용한다. 모델이 ID 문자열을 생성했다는 사실만으로 참조를 인정하지 않는다. 해당 대화에서 실제 조회한 원장의 관측 기록과 현재 사용자의 읽기 권한에 모두 연결되어야 한다. 다른 대화나 모델 기억만의 ID는 사용할 수 없다.

worker는 검증된 `type:id`를 기존 `answer_resources.reference_id`로 바꾼 뒤 완료 본문과 같은 transaction에 저장한다. 현재 제목·URL·이동 대상은 리소스 소유 모듈에서 읽는다. 이름이 같거나 설명에서 이름을 줄여도 제목으로 대상을 추정하지 않는다.

## 저장·복원·실패

`conversation_messages.answer_document`는 nullable JSON이다. 새 답변은 `{"version": 1, "elements": [...]}`를 저장하고 `body`는 기존 열에 보존한다. 상세 API는 현재 권한을 다시 확인한다. 접근할 수 없는 요소의 ref는 `null`, 해당 목록 설명은 빈 문자열이 되고 화면에는 일반적인 접근 불가 문구를 표시한다. 저장본은 수정하지 않아 권한이 돌아오면 다시 표시할 수 있다. 자유 본문에 이미 적힌 과거 사실 전체의 소급 삭제는 이 계약의 범위가 아니다.

후속 답변은 같은 대화의 이전 Turn 참조를 다시 사용할 수 있다. 모델 checkpoint가 없는 경우 대화 context excerpt에 요소의 순서, 현재 제목, 재인가된 canonical ref를 함께 전달한다. 이 excerpt는 최대 4,000자로 제한된다. 자료 권한 보호를 위해 기존 경로에서 assistant exchange를 제외하는 정책은 유지한다.

구조화된 최종 JSON은 중간 agent message로 표시하지 않는다. schema와 참조 검증을 마친 결과만 정상 완료로 확정한다. 형식 오류는 `ProviderResponseInvalid`로 실패 처리하며 전체 agent를 자동 재실행하지 않는다. 사용자 재시도와 취소는 기존 Turn 흐름을 사용한다.

`answer_document`가 없는 과거 답변은 기존 Markdown/링크 호환 경로를 유지한다. 신규 답변에는 제목 자동 매칭을 적용하지 않는다. 새 요소는 서버 모델·검증·projection·frontend renderer를 함께 확장하며 임의 HTML이나 실행 command를 허용하지 않는다.

## 검증과 적용

집중 테스트는 `test_answer_documents.py`, `test_answer_resources.py`, `test_codex_cli.py`, `AssistantMarkdown.test.tsx`, 기존 채팅 lifecycle·stream·drawer 테스트다. 스키마 누락은 `local-stack` 시작 검사에서 감지한다. 기존 로컬 DB는 먼저 백업하고 `make sync-demo-schema`로 nullable 열만 추가한다. 운영 migration은 별도 절차다.

이 계약은 참조의 연결과 표시 형식을 보장한다. 자연어 질문의 조건 해석, 조회 결과 중 항목 누락, 순서 추천의 타당성까지 보장하지는 않는다. 실제 provider 검증 중 날짜 조건만으로 요청했을 때 대상 한 건을 빠뜨린 사례를 관찰했으며, 이를 표시 계약의 성공으로 간주하지 않는다.

현재 이동 동작은 기존 UI를 재사용한다. 업무·회의·요청은 상세 화면, 자료는 연결된 업무 또는 인가된 원문 URL, 보고서는 보고 화면을 연다. 모든 종류에 별도 상세 화면이 있는 것은 아니다.
