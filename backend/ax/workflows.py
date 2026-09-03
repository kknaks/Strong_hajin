from __future__ import annotations

from collections import defaultdict, deque
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ax.auth import Principal


class NodeKind(StrEnum):
    START = "start"
    TOOL = "tool"
    HUMAN_GATE = "human_gate"
    JOIN = "join"
    END = "end"


class WorkflowNode(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    kind: NodeKind
    label: str
    required_capability: str | None = None
    join_policy: str | None = None

    @model_validator(mode="after")
    def validate_join_policy(self) -> "WorkflowNode":
        if self.kind is NodeKind.JOIN and self.join_policy != "all":
            raise ValueError("join nodes must explicitly use join_policy='all'")
        if self.kind is not NodeKind.JOIN and self.join_policy is not None:
            raise ValueError("only join nodes may have a join_policy")
        return self


class WorkflowEdge(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    target: str


class WorkflowDefinitionVersion(BaseModel):
    """Code-owned, immutable executable definition; database records refer to this version."""

    model_config = ConfigDict(frozen=True)

    workflow_id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    version: str = Field(pattern=r"^\d{4}-\d{2}-[a-z0-9.-]+$")
    title: str
    description: str
    required_scope: str
    required_capabilities: frozenset[str] = frozenset()
    input_schema: dict[str, object]
    nodes: tuple[WorkflowNode, ...]
    edges: tuple[WorkflowEdge, ...]

    @model_validator(mode="after")
    def validate_graph(self) -> "WorkflowDefinitionVersion":
        node_ids = {node.id for node in self.nodes}
        if len(node_ids) != len(self.nodes):
            raise ValueError("workflow node ids must be unique")
        starts = [node for node in self.nodes if node.kind is NodeKind.START]
        ends = [node for node in self.nodes if node.kind is NodeKind.END]
        if len(starts) != 1 or len(ends) != 1:
            raise ValueError("workflow graph must have exactly one start and one end node")
        if not self.edges:
            raise ValueError("workflow graph must have at least one edge")
        adjacency: dict[str, set[str]] = defaultdict(set)
        reverse: dict[str, set[str]] = defaultdict(set)
        for edge in self.edges:
            if edge.source not in node_ids or edge.target not in node_ids:
                raise ValueError("workflow edge references an unknown node")
            if edge.source == edge.target:
                raise ValueError("workflow graph cannot contain a self edge")
            adjacency[edge.source].add(edge.target)
            reverse[edge.target].add(edge.source)
        reachable = _walk(starts[0].id, adjacency)
        if reachable != node_ids:
            raise ValueError("every workflow node must be reachable from start")
        can_reach_end = _walk(ends[0].id, reverse)
        if can_reach_end != node_ids:
            raise ValueError("every workflow node must lead to end")
        if _contains_cycle(node_ids, adjacency):
            raise ValueError("workflow graph must be acyclic")
        return self

    def is_visible_to(self, principal: Principal) -> bool:
        return self.required_scope in principal.organization_scope and self.required_capabilities.issubset(
            principal.capabilities
        )


def _walk(origin: str, graph: dict[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    queue = deque([origin])
    while queue:
        node = queue.popleft()
        if node in seen:
            continue
        seen.add(node)
        queue.extend(graph[node] - seen)
    return seen


def _contains_cycle(nodes: set[str], adjacency: dict[str, set[str]]) -> bool:
    incoming = {node: 0 for node in nodes}
    for targets in adjacency.values():
        for target in targets:
            incoming[target] += 1
    queue = deque(node for node, count in incoming.items() if count == 0)
    visited = 0
    while queue:
        node = queue.popleft()
        visited += 1
        for target in adjacency[node]:
            incoming[target] -= 1
            if incoming[target] == 0:
                queue.append(target)
    return visited != len(nodes)


def _linear_definition(
    workflow_id: str,
    title: str,
    description: str,
    scope: str,
    capabilities: frozenset[str],
) -> WorkflowDefinitionVersion:
    return WorkflowDefinitionVersion(
        workflow_id=workflow_id,
        version="2026-09-demo.1",
        title=title,
        description=description,
        required_scope=scope,
        required_capabilities=capabilities,
        input_schema={"type": "object", "additionalProperties": False},
        nodes=(
            WorkflowNode(id="start", kind=NodeKind.START, label="시작"),
            WorkflowNode(id="collect", kind=NodeKind.TOOL, label="증거 수집"),
            WorkflowNode(id="confirm", kind=NodeKind.HUMAN_GATE, label="사람 확인"),
            WorkflowNode(id="effect", kind=NodeKind.TOOL, label="업무 반영"),
            WorkflowNode(id="end", kind=NodeKind.END, label="완료"),
        ),
        edges=(
            WorkflowEdge(source="start", target="collect"),
            WorkflowEdge(source="collect", target="confirm"),
            WorkflowEdge(source="confirm", target="effect"),
            WorkflowEdge(source="effect", target="end"),
        ),
    )


def catalog_definitions() -> tuple[WorkflowDefinitionVersion, ...]:
    """All demo definitions are version-pinned in code before they are seeded."""
    common = frozenset({"work.read"})
    linear = (
        _linear_definition("daily-report", "개인 일일보고", "WorkRecord 기반 보고서를 작성하고 제출합니다.", "scax", frozenset({"work.read", "daily_report.submit"})),
        _linear_definition("team-daily-rollup", "팀 일일보고 취합", "팀의 일일보고를 검토해 취합합니다.", "scax", frozenset({"report.review"})),
        _linear_definition("weekly-report", "주간 업무보고", "주간 업무 증거를 정리하고 확인합니다.", "scax", common),
        _linear_definition("monthly-close", "월 마감 자료 취합", "마감 자료를 수집하고 승인합니다.", "scax", frozenset({"report.review"})),
        _linear_definition("onboarding", "신규 입사 준비", "입사 준비 작업을 확인합니다.", "people", frozenset({"team.manage"})),
        _linear_definition("offboarding", "퇴사 처리", "퇴사 처리 작업을 확인합니다.", "people", frozenset({"team.manage"})),
        _linear_definition("customer-visit-report", "고객 방문 결과 보고", "방문 결과를 기록하고 제출합니다.", "scax", common),
    )
    meeting_followups = WorkflowDefinitionVersion(
        workflow_id="meeting-followups",
        version="2026-09-demo.1",
        title="회의 후속업무 정리",
        description="회의 증거에서 후보를 선택하고, 수행자의 수락 뒤에만 내 업무로 반영합니다.",
        required_scope="scax",
        required_capabilities=frozenset({"meeting.followup.request"}),
        input_schema={"type": "object", "additionalProperties": False},
        nodes=(
            WorkflowNode(id="start", kind=NodeKind.START, label="시작"),
            WorkflowNode(id="collect", kind=NodeKind.TOOL, label="회의 증거 조회"),
            WorkflowNode(id="choose-assignment", kind=NodeKind.HUMAN_GATE, label="후속 업무 선택"),
            WorkflowNode(id="create-assignment", kind=NodeKind.TOOL, label="수락 대기 업무 생성"),
            WorkflowNode(id="accept-assignment", kind=NodeKind.HUMAN_GATE, label="수행자 수락", required_capability="task.accept"),
            WorkflowNode(id="effect", kind=NodeKind.TOOL, label="내 업무 반영"),
            WorkflowNode(id="end", kind=NodeKind.END, label="완료"),
        ),
        edges=(
            WorkflowEdge(source="start", target="collect"),
            WorkflowEdge(source="collect", target="choose-assignment"),
            WorkflowEdge(source="choose-assignment", target="create-assignment"),
            WorkflowEdge(source="create-assignment", target="accept-assignment"),
            WorkflowEdge(source="accept-assignment", target="effect"),
            WorkflowEdge(source="effect", target="end"),
        ),
    )
    contract = WorkflowDefinitionVersion(
        workflow_id="contract-review",
        version="2026-09-demo.1",
        title="계약 검토와 병렬 승인",
        description="법무와 재무의 독립 판단을 모두 받은 뒤에만 후속 effect를 실행합니다.",
        required_scope="scax",
        required_capabilities=frozenset({"contract.legal_review", "contract.finance_review"}),
        input_schema={"type": "object", "required": ["contract_id"]},
        nodes=(
            WorkflowNode(id="start", kind=NodeKind.START, label="시작"),
            WorkflowNode(id="extract", kind=NodeKind.TOOL, label="계약 증거 추출"),
            WorkflowNode(id="legal", kind=NodeKind.HUMAN_GATE, label="법무 판단", required_capability="contract.legal_review"),
            WorkflowNode(id="finance", kind=NodeKind.HUMAN_GATE, label="재무 판단", required_capability="contract.finance_review"),
            WorkflowNode(id="join", kind=NodeKind.JOIN, label="모든 판단 대기", join_policy="all"),
            WorkflowNode(id="effect", kind=NodeKind.TOOL, label="승인 결과 반영"),
            WorkflowNode(id="end", kind=NodeKind.END, label="완료"),
        ),
        edges=(
            WorkflowEdge(source="start", target="extract"),
            WorkflowEdge(source="extract", target="legal"),
            WorkflowEdge(source="extract", target="finance"),
            WorkflowEdge(source="legal", target="join"),
            WorkflowEdge(source="finance", target="join"),
            WorkflowEdge(source="join", target="effect"),
            WorkflowEdge(source="effect", target="end"),
        ),
    )
    return (*linear, meeting_followups, contract)
