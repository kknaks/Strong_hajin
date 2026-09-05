"""업무 관계 탐색 — how work connects, read one authorized step at a time.

This module owns no rows of its own. Every node and edge is derived from the canonical ledgers, and every node is
checked against what the asking principal may already read. Following a connection is not being handed access: an
edge to work someone may not open is dropped along with the work itself, so no title, count or existence leaks.
"""
from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from ax_workspace.modules.organization_access.domain import (
    Principal,
    TASK_READ,
    WORK_REQUEST_READ,
)

#: What may be at either end of a connection. Each is read through the module that owns it.
NODE_KINDS = ("person", "work_request", "task", "material")
#: How far one answer may reach, so a screen and a delegated turn get the same bounded thing.
DEFAULT_LIMIT = 20
MAX_LIMIT = 50


class GraphError(Exception):
    pass


class GraphAccessDenied(GraphError):
    pass


class GraphNotFound(GraphError):
    pass


class GraphSourcePort(Protocol):
    """The authorized reads this projection is built from; each is the owning module's own operation."""

    def readable_tasks(self, principal: Principal, *, query: str | None = None, limit: int = 50) -> list[dict[str, Any]]: ...
    def readable_task(self, principal: Principal, task_id: UUID) -> dict[str, Any] | None: ...
    def readable_requests(self, principal: Principal, *, query: str | None = None) -> list[dict[str, Any]]: ...
    def readable_request(self, principal: Principal, request_id: UUID) -> dict[str, Any] | None: ...
    def task_materials(self, principal: Principal, task_id: UUID) -> list[dict[str, Any]]: ...
    def person(self, member_id: str) -> dict[str, Any] | None: ...


class GraphApplication:
    """Search for something, then walk one hop at a time. Nothing is expanded that was not asked for."""

    def __init__(self, source: GraphSourcePort) -> None:
        self._source = source

    def search(self, principal: Principal, query: str, *, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
        self._require(principal)
        text = " ".join(str(query or "").split())
        if not text:
            raise GraphError("찾을 내용을 입력하세요")
        bounded = max(1, min(int(limit), MAX_LIMIT))
        nodes = [
            *(self._task_node(row) for row in self._source.readable_tasks(principal, query=text, limit=MAX_LIMIT + 1)),
            *(self._request_node(row) for row in self._source.readable_requests(principal, query=text)),
        ]
        return {"query": text, "nodes": nodes[:bounded], "truncated": len(nodes) > bounded}

    def neighbors(self, principal: Principal, node_ref: str, *, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
        """One hop from one node, with every neighbour re-checked against this person's own access."""
        self._require(principal)
        kind, identifier = self._parse(node_ref)
        if kind == "task":
            return self._task_neighbors(principal, UUID(identifier), limit)
        if kind == "work_request":
            return self._request_neighbors(principal, UUID(identifier), limit)
        raise GraphError(f"{kind}에서는 아직 연결을 따라갈 수 없습니다")

    # ---- one hop from a Task ----

    def _task_neighbors(self, principal: Principal, task_id: UUID, limit: int) -> dict[str, Any]:
        task = self._source.readable_task(principal, task_id)
        if task is None:
            raise GraphNotFound("task was not found")
        center = self._task_node(task)
        nodes: dict[str, dict[str, Any]] = {self._ref(center): center}
        edges: list[dict[str, str]] = []

        holder = (task.get("assignee") or {}).get("member_id")
        if holder:
            self._add_person(nodes, holder)
            edges.append(self._edge("holds", f"person:{holder}", self._ref(center)))

        source_request_id = (task.get("lineage") or {}).get("source_work_request_id")
        if source_request_id:
            request = self._source.readable_request(principal, UUID(str(source_request_id)))
            if request is not None:
                node = self._request_node(request)
                nodes[self._ref(node)] = node
                edges.append(self._edge("produced", self._ref(node), self._ref(center)))

        parent = task.get("parent")
        if parent:
            readable = self._source.readable_task(principal, UUID(str(parent["task_id"])))
            if readable is not None:
                node = self._task_node(readable)
                nodes[self._ref(node)] = node
                edges.append(self._edge("parent_of", self._ref(node), self._ref(center)))

        for child in task.get("children") or []:
            node = {"kind": "task", "id": str(child["task_id"]), "title": str(child["title"]), "state": child.get("state")}
            nodes[self._ref(node)] = node
            edges.append(self._edge("parent_of", self._ref(center), self._ref(node)))

        for reference in task.get("references") or []:
            referenced = reference.get("task")
            if not referenced:
                continue
            node = {"kind": "task", "id": str(referenced["task_id"]), "title": str(referenced["title"]), "state": referenced.get("state")}
            nodes[self._ref(node)] = node
            edges.append(self._edge("refers_to", self._ref(center), self._ref(node)))

        for material in self._source.task_materials(principal, task_id):
            node = {
                "kind": "material",
                "id": str(material["material_id"]),
                "title": str(material["name"]),
                "state": str(material.get("kind") or ""),
            }
            nodes[self._ref(node)] = node
            edges.append(self._edge("has_material", self._ref(center), self._ref(node)))

        return self._bounded(center, nodes, edges, limit)

    # ---- one hop from a WorkRequest ----

    def _request_neighbors(self, principal: Principal, request_id: UUID, limit: int) -> dict[str, Any]:
        request = self._source.readable_request(principal, request_id)
        if request is None:
            raise GraphNotFound("work request was not found")
        center = self._request_node(request)
        nodes: dict[str, dict[str, Any]] = {self._ref(center): center}
        edges: list[dict[str, str]] = []

        self._add_person(nodes, str(request["requester_id"]))
        edges.append(self._edge("requested", f"person:{request['requester_id']}", self._ref(center)))
        self._add_person(nodes, str(request["assignee_id"]))
        edges.append(self._edge("asked_of", self._ref(center), f"person:{request['assignee_id']}"))

        task_id = request.get("task_id")
        if task_id:
            task = self._source.readable_task(principal, UUID(str(task_id)))
            if task is not None:
                node = self._task_node(task)
                nodes[self._ref(node)] = node
                edges.append(self._edge("produced", self._ref(center), self._ref(node)))
        return self._bounded(center, nodes, edges, limit)

    # ---- shared shaping ----

    def _bounded(self, center: dict[str, Any], nodes: dict[str, dict[str, Any]], edges: list[dict[str, str]], limit: int) -> dict[str, Any]:
        """One answer stays small enough to read: extra connections are counted, never silently dropped."""
        bounded = max(1, min(int(limit), MAX_LIMIT))
        kept = edges[:bounded]
        named = {self._ref(center)} | {row["from"] for row in kept} | {row["to"] for row in kept}
        return {
            "center": center,
            "nodes": [node for ref, node in nodes.items() if ref in named],
            "edges": kept,
            "truncated": len(edges) > bounded,
        }

    @staticmethod
    def _require(principal: Principal) -> None:
        if TASK_READ not in principal.capabilities and WORK_REQUEST_READ not in principal.capabilities:
            raise GraphAccessDenied("work read capability is required")

    @staticmethod
    def _parse(node_ref: str) -> tuple[str, str]:
        kind, _, identifier = str(node_ref or "").partition(":")
        if kind not in NODE_KINDS or not identifier:
            raise GraphError("node must be <kind>:<id>")
        return kind, identifier

    @staticmethod
    def _ref(node: dict[str, Any]) -> str:
        return f"{node['kind']}:{node['id']}"

    @staticmethod
    def _edge(kind: str, source: str, target: str) -> dict[str, str]:
        return {"kind": kind, "from": source, "to": target}

    @staticmethod
    def _task_node(task: dict[str, Any]) -> dict[str, Any]:
        return {"kind": "task", "id": str(task["task_id"]), "title": str(task["title"]), "state": task.get("state")}

    @staticmethod
    def _request_node(request: dict[str, Any]) -> dict[str, Any]:
        return {"kind": "work_request", "id": str(request["request_id"]), "title": str(request["title"]), "state": request.get("state")}

    def _add_person(self, nodes: dict[str, dict[str, Any]], member_id: str) -> None:
        person = self._source.person(member_id) or {"member_id": member_id, "display_name": member_id}
        node = {"kind": "person", "id": str(person["member_id"]), "title": str(person["display_name"]), "state": None}
        nodes[self._ref(node)] = node
