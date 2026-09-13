"""Authorized graph nodes, ledger edges, and optional grouping projections."""
from typing_extensions import NotRequired, TypedDict


class GraphPositionMatch(TypedDict):
    kind: str
    label: str
    organization_id: str
    organization_name: str


class GraphNodeView(TypedDict):
    kind: str
    id: str
    title: str
    state: str | None
    date: str | None
    project_id: NotRequired[str | None]
    folded: NotRequired[int]
    match: NotRequired[GraphPositionMatch]


class GraphMaterialContext(TypedDict):
    resource_type: str
    resource_id: str
    binding_id: str
    role: str


GraphEdgeView = TypedDict('GraphEdgeView', {
    'kind': str, 'from': str, 'to': str, 'label': str,
    'inverse_label': str, 'provenance': str,
    'count': NotRequired[int], 'integrity_ref': NotRequired[str],
    'source_contexts': NotRequired[list[GraphMaterialContext]],
})


class GraphSearchResult(TypedDict):
    query: str
    nodes: list[GraphNodeView]
    truncated: bool


class GraphNeighborsResult(TypedDict):
    center: GraphNodeView
    nodes: list[GraphNodeView]
    edges: list[GraphEdgeView]
    truncated: bool


class GraphOverviewResult(GraphNeighborsResult):
    view: str
    available_views: list[str]
