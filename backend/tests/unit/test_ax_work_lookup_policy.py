"""E2E-12 — AX 가 업무를 만들 때 사람이 「새 업무 추가」 창에서 고르듯 프로젝트·관련 업무를 찾아 채운다.

문구가 곧 동작이다(모델은 이것만 읽는다). 그래서 무엇이 사라지면 안 되는지를 여기서 잡는다.
"""
from ax_workspace.modules.ax_execution.tool_catalog import TOOL_CATALOG
from ax_workspace.platform.claude_cli import ClaudeCliProviderAdapter
from ax_workspace.platform.codex_cli import CodexCliProviderAdapter


def test_drafting_work_looks_up_project_and_related_tasks_and_never_invents_them() -> None:
    policy = CodexCliProviderAdapter.WORK_AND_REPORT_ROUTING_POLICY
    for tool in ("task_create_self", "work_request_create", "list_projects", "graph_search", "graph_neighbors", "task_list"):
        assert f"`{tool}" in policy, tool
    for field in ("project_id", "parent_task_id", "preceding_task_ids", "reference_task_ids"):
        assert field in policy, field
    assert "연결할 프로젝트를 찾지 못했습니다" in policy
    assert "지어내지 않는다" in policy


def test_the_no_graph_first_rule_is_about_finding_people_not_about_drafting_work() -> None:
    policy = CodexCliProviderAdapter.WORK_AND_REPORT_ROUTING_POLICY
    assert "사람을 찾으려고** `graph_search`를 먼저 호출하지 않는다" in policy
    assert "관계 자체를 묻지 않은 한 `graph_search`를 먼저 호출하지 않는다" not in policy


def test_creation_tool_descriptions_ask_for_the_lookup_and_forbid_invention() -> None:
    for name in ("task_create_self", "work_request_create"):
        description = TOOL_CATALOG[name].description
        assert "Before drafting, look for related records" in description, name
        assert "Also look up" in description, name
        assert "never invent an ID" in description, name
        assert "list_projects" in description and "graph_neighbors" in description, name


def test_project_candidates_are_not_limited_to_meetings() -> None:
    project_list = TOOL_CATALOG["project_list"]
    assert "회의용" not in project_list.title
    assert "Task or WorkRequest" in project_list.description
    assert "project:<project_id>" in TOOL_CATALOG["list_projects"].description


def test_graph_search_says_its_only_arguments_and_that_there_is_no_kind_filter() -> None:
    description = TOOL_CATALOG["graph_search"].description
    assert "Arguments are exactly `query`" in description and "no kind filter" in description
    relationship = CodexCliProviderAdapter.RELATIONSHIP_POLICY
    assert "`graph_search`의 인자는 `query`(찾을 이름)와 `limit` 둘뿐이다" in relationship
    assert "graph_search 시작 종류는" not in relationship


def test_answers_may_point_at_projects_by_their_own_kind() -> None:
    policy = CodexCliProviderAdapter.ANSWER_PRESENTATION_POLICY
    assert "project:<project_id>" in policy
    assert "프로젝트를 task:로 가리키지 않는다" in policy


def test_the_claude_adapter_reads_the_same_policies() -> None:
    for name in ("RELATIONSHIP_POLICY", "WORK_AND_REPORT_ROUTING_POLICY", "ANSWER_PRESENTATION_POLICY"):
        assert getattr(ClaudeCliProviderAdapter, name) is getattr(CodexCliProviderAdapter, name), name


def test_creation_tools_propose_checklist_and_description_but_only_evidenced_ids_and_dates() -> None:
    """E2E-1 — 체크리스트·내용은 AI 가 제안으로 채우고, ID·날짜는 근거가 있을 때만 (SPEC-001 S-9 7 · WORK-009 1-2)."""
    for name in ("task_create_self", "work_request_create"):
        description = TOOL_CATALOG[name].description
        assert "Propose the content yourself" in description, name
        assert "do not leave description or checklist empty" in description, name
        assert "IDs come only from the conversation or lookup" in description, name
        assert "Never invent an ID or a date" in description, name
        # 앞 판의 「대화가 준 필드만 채우고 지어내느니 비워라」는 체크리스트·내용까지 비우게 했다.
        assert "Fill every field the conversation gives you" not in description, name
        assert "fill every field the conversation gives you" not in description, name
        assert "leave a value empty rather than inventing one" not in description, name


def test_routing_and_answer_policies_say_the_same_split() -> None:
    routing = CodexCliProviderAdapter.WORK_AND_REPORT_ROUTING_POLICY
    assert "체크리스트(첫 단계들을 순서대로)와 업무 내용(`description`)은 대화의 업무 주제와 찾아 읽은 회의·업무·자료로부터 제안해 채운다" in routing
    assert "ID(프로젝트·업무·사람)는 대화·조회가 준 것만" in routing
    assert "말하지 않은 날짜나 조회 결과에 없는 ID를 지어내지 않는다" in routing
    answer = CodexCliProviderAdapter.ANSWER_PRESENTATION_POLICY
    assert "「비워 두었으니 카드에서 보완하라」고 말하지 않는다" in answer
    assert "근거가 없어 비운 ID·날짜만 말한다" in answer
    for name in ("WORK_AND_REPORT_ROUTING_POLICY", "ANSWER_PRESENTATION_POLICY"):
        assert getattr(ClaudeCliProviderAdapter, name) is getattr(CodexCliProviderAdapter, name), name



# ---- E2E-6 — 초안 전 관련 회의·업무·자료 탐색 (SPEC-001 S-9 8 · WORK-009 Phase 3) ----------------------------

_EXCEPTION = "업무·업무 요청 초안을 준비하는 턴은 예외"


def test_creation_tools_search_meetings_tasks_and_materials_and_read_at_most_three() -> None:
    for name in ("task_create_self", "work_request_create"):
        description = TOOL_CATALOG[name].description
        assert "short topic keyword" in description and "title substring match" in description, name
        for tool in ("graph_search", "my_meeting_list", "material_search", "meeting_get", "task_get"):
            assert tool in description, (name, tool)
        assert "at most 3 of the most relevant ones" in description, name
        assert "use them as the basis for description and checklist" in description, name
        assert "name those meetings and Tasks as sources in the answer" in description, name
        assert "no related meeting or Task was found" in description, name
        assert "the related records you read" in description, name


def test_creation_tools_never_take_people_or_dates_from_what_they_looked_at() -> None:
    for name in ("task_create_self", "work_request_create"):
        description = TOOL_CATALOG[name].description
        assert "only when the conversation named that person" in description, name
        assert "never copy meeting attendees, follow-up owners or Task holders" in description, name
        assert "never turn a follow-up due candidate or a date in a record into start_date or due_date" in description, name


def test_routing_searches_before_drafting_reads_three_and_cites_sources() -> None:
    routing = CodexCliProviderAdapter.WORK_AND_REPORT_ROUTING_POLICY
    assert "초안을 만들기 전에 **관련 회의·기존 업무·자료를 찾는다**" in routing
    assert "**짧은 핵심어**" in routing and "제목 부분 일치" in routing
    for tool in ("`graph_search`", "`my_meeting_list`", "`material_search`", "`meeting_get`", "`task_get`"):
        assert tool in routing, tool
    assert "**가장 관련 높은 것만 상세를 최대 3건**" in routing
    assert "**업무 내용과 체크리스트의 근거**" in routing and "**출처(회의·업무 이름)**" in routing
    assert "「관련 회의·업무를 찾지 못해 일반 단계로 제안했습니다」" in routing
    assert "대화의 업무 주제와 찾아 읽은 회의·업무·자료로부터 제안해 채운다" in routing
    assert "**사람(담당자·참조자·결재자)은 대화가 이름을 댄 사람만**" in routing
    assert "회의 참석자·할 일 담당자·업무 담당자를 넣지 않고" in routing
    assert "회의 할 일의 마감 후보나 기록 속 날짜를 시작일·마감일로 옮기지 않는다" in routing
    # 연결은 지금처럼 한 후보 확정일 때만이다.
    assert "**한 후보로 확정될 때만**" in routing


def test_the_brakes_keep_their_words_and_except_only_the_drafting_turn() -> None:
    """제동 문장은 원문 그대로 남고, 그 바로 뒤에 「업무 초안 턴에서 **무엇이** 풀리는지」만 붙는다 (fix1 W3·W4).

    꼬리는 금지 문장 뒤에 오지 않는다 — 「넓힌 목록으로 내 업무를 답하지 않는다」 같은 금지는 꼬리 밖에 남아
    업무 초안 턴에도 그대로 걸린다. 다른 질문 턴의 문장은 뜻이 바뀌지 않는다.
    """
    flattened = CodexCliProviderAdapter.RELATIONSHIP_POLICY.replace("\n", " ")
    brakes = (
        # (제동 원문, 그 뒤에 붙은 예외 — 풀리는 것의 이름)
        ("관계를 묻지 않은 질문에 graph를 걷지 않는다 — 이미 답이 손에 있는데 더 걷는 것은 답을 늦출 뿐이다.",
         "(" + _EXCEPTION + " — 관련 기록 탐색의 `graph_search` 한 번과 연결 탐색의 `graph_neighbors` 한 번은 부른다)"),
        ("주변 node의 상세를 일괄 조회하지 않는다.",
         "(" + _EXCEPTION + " — 관련 기록의 상세를 최대 3건까지 읽는다)"),
        ("색인 준비/실패 상태를 함께 밝힌다.",
         "(" + _EXCEPTION + " — 회의 내용을 묻는 질문이 아니어도 관련 기록 탐색으로 회의를 `graph_search`·`my_meeting_list`로 찾고"
         " `meeting_get`으로 읽는다. 이 줄의 근거 규칙은 그대로다)"),
        ("그래도 없으면 현재 접근 가능한 범위에서 결과가 없다고 말한다.",
         "(" + _EXCEPTION + " — 자료 본문 질문이 아니어도 관련 기록 탐색으로 `material_search`를 부르되 재검색 없이 한 번만 부른다."
         " 위 「최대 세 번」은 그 턴에 걸리지 않는다)"),
        ("열람 가능한 팀/프로젝트 업무를 명시적으로 묻는 질문에는 `task_list`를 사용한다",
         "(" + _EXCEPTION + " — 연결할 관련 업무를 찾을 때 `task_list`를 써도 된다)."),
    )
    for sentence, exception in brakes:
        assert sentence in flattened, sentence
        assert f"{sentence} {exception}" in flattened, sentence
    # 원래 「질문일 때만」 문장들도 원문 그대로다.
    for sentence in (
        "회의 내용을 묻는 질문은 `material_search`로 시작한다. 회의 제목도",
        "회의 ID를 찾기 위한 graph 조회를 먼저 하지 않는다.",
        "자료 본문 질문은 `material_search`로 시작한다. 소유 대상을 모르면",
        "최초 호출을 포함해 최대 세 번까지 찾고",
        "목록 하나로 답할 수 있는 질문은 소유 도구를 바로 부르고 거기서 멈춘다",
    ):
        assert sentence in flattened, sentence
    # 금지 문장은 꼬리 **밖**, 꼬리 뒤에 따로 선다 — 업무 초안 턴에도 풀리지 않는다.
    assert "써도 된다). 그렇게 넓혀 받은 목록으로 `내 업무`를 답하지 않는다" in flattened
    assert not flattened.rstrip().endswith(")")
    # 예외는 이 다섯 곳뿐이다 — 다른 문장에 번지지 않았다.
    assert flattened.count(_EXCEPTION) == 5
    task_list = TOOL_CATALOG["task_list"].description
    assert "Use only when the request explicitly asks for readable team/project work" in task_list
    assert "except before drafting a Task or WorkRequest" in task_list
    assert "except before drafting" not in TOOL_CATALOG["my_task_list"].description


def test_ids_come_from_the_conversation_or_lookup_but_dates_only_from_the_conversation() -> None:
    """fix1 W1 — ID 와 날짜를 한 문장으로 묶지 않는다. 날짜는 대화가 준 것만, 조회한 기록은 날짜의 근거가 아니다."""
    for name in ("task_create_self", "work_request_create"):
        description = TOOL_CATALOG[name].description
        assert "IDs come only from the conversation or lookup" in description, name
        assert "Dates (ISO start_date and due_date) come only from the conversation — never from a record you looked at." in description, name
        assert "IDs and dates come only" not in description, name
    routing = CodexCliProviderAdapter.WORK_AND_REPORT_ROUTING_POLICY
    assert "**ID(프로젝트·업무·사람)는 대화·조회가 준 것만** 채운다" in routing
    assert "**날짜(`start_date`·`due_date`)는 대화가 준 것만** 채운다 — 조회한 기록 속 날짜·회의 할 일의 마감 후보는 날짜의 근거가 아니다." in routing
    assert "날짜(`start_date`·`due_date`)는 대화·조회가 준 것만" not in routing


def test_the_drafting_search_has_a_call_ceiling() -> None:
    """fix1 W2 — 검색은 도구마다 한 번, 상세 최대 3건, 연결 탐색은 필요할 때 한 번 (실물 67초/90초)."""
    routing = CodexCliProviderAdapter.WORK_AND_REPORT_ROUTING_POLICY
    assert "**탐색 호출 상한**" in routing
    assert "관련 기록 검색은 **도구마다 한 번**(`graph_search`·`my_meeting_list`·`material_search` 각 1회, 재검색 없음)" in routing
    assert "상세는 **최대 3건**" in routing
    assert "연결 탐색(`list_projects`·`graph_neighbors` 또는 `task_list`)은 필요할 때 **한 번**만 부른다" in routing
    for name in ("task_create_self", "work_request_create"):
        description = TOOL_CATALOG[name].description
        assert "Call each search once (no re-search), read at most 3 details" in description, name
        assert "look up project and link candidates once when needed" in description, name


# ── WORK-012 WP3-BE — 맥락 목록 매 턴 · 회의 생성의 자료 탐색 · 이어온 안건 `carried` (SPEC-010 §4.5 · §4.4 · WP1 W-2) ──


def test_drafting_work_looks_in_the_context_catalog_first_and_uses_tools_for_details() -> None:
    policy = CodexCliProviderAdapter.WORK_AND_REPORT_ROUTING_POLICY
    assert "「조직 맥락 목록」" in policy
    assert "목록에서 먼저, 상세는 도구로" in policy


def test_meeting_creation_searches_materials_like_task_creation() -> None:
    policy = CodexCliProviderAdapter.MEETING_CREATION_POLICY
    assert "업무 생성과 같은 기준으로 관련 회의·자료를 찾는다" in policy
    assert "`material_search`" in policy and "`my_meeting_list`" in policy
    assert "근거 자료" in policy


def test_a_continued_meeting_carries_the_unresolved_agendas_as_carried() -> None:
    """WP1 W-2 — AX 회의 생성에서 이어온 회의의 미결 안건은 `source:"carried"` (정책 문장 + 도구 설명)."""
    policy = CodexCliProviderAdapter.MEETING_CREATION_POLICY
    assert "`carried_from_meeting_id`" in policy
    assert 'source:\\"carried\\"' in policy or 'source:"carried"' in policy
    assert "결론이 나지 않은 것" in policy
    assert "이어온 회의 없이 `carried`를 쓰지 않는다" in policy
    description = TOOL_CATALOG["meeting_create"].description
    assert "carried_from_meeting_id" in description and '"carried"' in description


def test_the_place_is_a_room_not_free_text_and_existing_meetings_change_through_meeting_update() -> None:
    policy = CodexCliProviderAdapter.MEETING_CREATION_POLICY
    assert "회의실(`room_id`)로만" in policy and "`meeting_room_list`" in policy
    assert "`meeting_update`" in policy and "request.room = {room_id}" in policy
    assert "room" in TOOL_CATALOG["meeting_update"].description
    assert "people" in TOOL_CATALOG["meeting_room_list"].description


def test_the_context_catalog_rides_every_conversation_turn_for_both_providers() -> None:
    """017 — AX 대화 프롬프트에 맥락 목록을 **매 턴** 싣는다(SPEC-010 §4.5 · OQ-1002 ②) — Codex·Claude 같은 자리."""
    from ax_workspace.modules.ax_execution.ai import AiConversationRequest, AiDelegatedToolContext

    request = AiConversationRequest(
        "업무 만들어줘", None, [], AiDelegatedToolContext("mina", "x"), context_catalog="## 조직 맥락 목록(시험)\n{}"
    )
    for adapter in (CodexCliProviderAdapter, ClaudeCliProviderAdapter):
        prompt = adapter._conversation_prompt(request)
        assert "## 조직 맥락 목록(시험)" in prompt
        assert prompt.index("## 조직 맥락 목록(시험)") < prompt.index("User message:")
    bare = CodexCliProviderAdapter._conversation_prompt(
        AiConversationRequest("업무 만들어줘", None, [], AiDelegatedToolContext("mina", "x"))
    )
    assert "조직 맥락 목록(" not in bare
