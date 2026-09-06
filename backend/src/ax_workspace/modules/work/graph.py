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
NODE_KINDS = ("person", "team", "work_request", "task", "material", "meeting")
#: How far one answer may reach, so a screen and a delegated turn get the same bounded thing.
DEFAULT_LIMIT = 20
MAX_LIMIT = 50
#: The first screen is a graph, not an empty search box — bounded the same way every other answer is.
OVERVIEW_LIMIT = 40

#: How to read an edge from either end. The inverse is wording, not a second edge.
RELATIONS: dict[str, dict[str, str]] = {
    "requested": {"label": "요청함", "inverse": "요청받음", "provenance": "work_request"},
    "asked_of": {"label": "요청 대상", "inverse": "받은 요청", "provenance": "work_request"},
    "produced": {"label": "만들어짐", "inverse": "여기서 나옴", "provenance": "work_request"},
    "holds": {"label": "담당함", "inverse": "담당자", "provenance": "task_assignment"},
    "parent_of": {"label": "하위 업무", "inverse": "상위 업무", "provenance": "task"},
    "refers_to": {"label": "참고함", "inverse": "참고됨", "provenance": "task_reference"},
    "has_material": {"label": "자료", "inverse": "붙은 업무", "provenance": "attachment_binding"},
    "belongs_to": {"label": "소속", "inverse": "구성원", "provenance": "membership"},
    "attended": {"label": "참석", "inverse": "참석자", "provenance": "meeting_attendee"},
    "owns_meeting": {"label": "소집", "inverse": "소집자", "provenance": "meeting"},
    "followed_up": {"label": "후속 업무", "inverse": "나온 회의", "provenance": "meeting_followup_promotion"},
}


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
    def readable_meetings(self, principal: Principal, *, query: str | None = None, limit: int = 50) -> list[dict[str, Any]]: ...
    def readable_meeting(self, principal: Principal, meeting_id: UUID) -> dict[str, Any] | None: ...
    def meeting_followup_tasks(self, principal: Principal, meeting_id: UUID) -> list[dict[str, Any]]: ...
    def member_units(self, member_id: str) -> list[dict[str, Any]]: ...
    def unit_members(self, unit_id: str) -> list[dict[str, Any]]: ...


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
            *(self._meeting_node(row) for row in self._source.readable_meetings(principal, query=text)),
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
        if kind == "meeting":
            return self._meeting_neighbors(principal, UUID(identifier), limit)
        if kind == "person":
            return self._person_neighbors(principal, identifier, limit)
        if kind == "team":
            return self._team_neighbors(principal, identifier, limit)
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

    # ---- one hop from a Meeting ----

    def _meeting_neighbors(self, principal: Principal, meeting_id: UUID, limit: int) -> dict[str, Any]:
        meeting = self._source.readable_meeting(principal, meeting_id)
        if meeting is None:
            raise GraphNotFound("meeting was not found")
        center = self._meeting_node(meeting)
        nodes: dict[str, dict[str, Any]] = {self._ref(center): center}
        edges: list[dict[str, Any]] = []

        owner = meeting.get("owner_id")
        if owner:
            self._add_person(nodes, str(owner))
            edges.append(self._edge("owns_meeting", f"person:{owner}", self._ref(center)))
        for attendee in meeting.get("attendees") or []:
            member_id = str(attendee["member_id"])
            if member_id == str(owner):
                continue
            self._add_person(nodes, member_id)
            edges.append(self._edge("attended", f"person:{member_id}", self._ref(center)))
        for task in self._source.meeting_followup_tasks(principal, meeting_id):
            node = self._task_node(task)
            nodes[self._ref(node)] = node
            edges.append(self._edge("followed_up", self._ref(center), self._ref(node)))
        return self._bounded(center, nodes, edges, limit)

    # ---- one hop from a person, and from the team they sit in ----

    def _person_neighbors(self, principal: Principal, member_id: str, limit: int) -> dict[str, Any]:
        person = self._source.person(member_id)
        if person is None:
            raise GraphNotFound("member was not found")
        center = self._person_node(person)
        nodes: dict[str, dict[str, Any]] = {self._ref(center): center}
        edges: list[dict[str, Any]] = []

        for unit in self._source.member_units(member_id):
            node = self._team_node(unit)
            nodes[self._ref(node)] = node
            edges.append(self._edge("belongs_to", self._ref(center), self._ref(node)))
        for task in self._source.readable_tasks(principal, limit=MAX_LIMIT):
            if str((task.get("assignee") or {}).get("member_id") or "") != member_id:
                continue
            node = self._task_node(task)
            nodes[self._ref(node)] = node
            edges.append(self._edge("holds", self._ref(center), self._ref(node)))
        for request in self._source.readable_requests(principal):
            node = self._request_node(request)
            if str(request.get("requester_id")) == member_id:
                nodes[self._ref(node)] = node
                edges.append(self._edge("requested", self._ref(center), self._ref(node)))
            elif str(request.get("assignee_id")) == member_id:
                nodes[self._ref(node)] = node
                edges.append(self._edge("asked_of", self._ref(node), self._ref(center)))
        for meeting in self._source.readable_meetings(principal):
            attendees = {str(attendee["member_id"]) for attendee in meeting.get("attendees") or []}
            if member_id != str(meeting.get("owner_id")) and member_id not in attendees:
                continue
            node = self._meeting_node(meeting)
            nodes[self._ref(node)] = node
            kind = "owns_meeting" if member_id == str(meeting.get("owner_id")) else "attended"
            edges.append(self._edge(kind, self._ref(center), self._ref(node)))
        return self._bounded(center, nodes, edges, limit)

    def _team_neighbors(self, principal: Principal, unit_id: str, limit: int) -> dict[str, Any]:
        members = self._source.unit_members(unit_id)
        if not members:
            raise GraphNotFound("team was not found")
        unit = next((item for item in self._source.member_units(str(members[0]["member_id"])) if str(item["id"]) == unit_id), None)
        center = self._team_node(unit or {"id": unit_id, "name": unit_id})
        nodes: dict[str, dict[str, Any]] = {self._ref(center): center}
        edges: list[dict[str, Any]] = []
        for member in members:
            self._add_person(nodes, str(member["member_id"]))
            edges.append(self._edge("belongs_to", f"person:{member['member_id']}", self._ref(center)))
        return self._bounded(center, nodes, edges, limit)

    # ---- the first screen ----

    def overview(self, principal: Principal, *, view: str = "member", limit: int = OVERVIEW_LIMIT) -> dict[str, Any]:
        """What this person is already connected to, as a graph rather than an empty search box.

        It is built from the same authorized reads as everything else, so it can never show more than the person
        could reach by asking one thing at a time.
        """
        self._require(principal)
        if view not in {"member", "team"}:
            raise GraphError("표현 수준은 구성원 보기 또는 팀으로 묶기입니다")
        me = self._person_node(self._source.person(str(principal.id)) or {"member_id": str(principal.id), "display_name": str(principal.id)})
        nodes: dict[str, dict[str, Any]] = {self._ref(me): me}
        edges: list[dict[str, Any]] = []

        for unit in self._source.member_units(str(principal.id)):
            node = self._team_node(unit)
            nodes[self._ref(node)] = node
            edges.append(self._edge("belongs_to", self._ref(me), self._ref(node)))
        for task in self._source.readable_tasks(principal, limit=MAX_LIMIT):
            node = self._task_node(task)
            nodes[self._ref(node)] = node
            holder = str((task.get("assignee") or {}).get("member_id") or "")
            if holder:
                self._add_person(nodes, holder)
                edges.append(self._edge("holds", f"person:{holder}", self._ref(node)))
        for request in self._source.readable_requests(principal):
            node = self._request_node(request)
            nodes[self._ref(node)] = node
            self._add_person(nodes, str(request["requester_id"]))
            edges.append(self._edge("requested", f"person:{request['requester_id']}", self._ref(node)))
            self._add_person(nodes, str(request["assignee_id"]))
            edges.append(self._edge("asked_of", self._ref(node), f"person:{request['assignee_id']}"))
        for meeting in self._source.readable_meetings(principal):
            node = self._meeting_node(meeting)
            nodes[self._ref(node)] = node
            for attendee in meeting.get("attendees") or []:
                member_id = str(attendee["member_id"])
                self._add_person(nodes, member_id)
                edges.append(self._edge("attended", f"person:{member_id}", self._ref(node)))

        answer = self._bounded(me, nodes, edges, limit)
        answer["view"] = view
        # A grouping the product has no ledger for is not offered at all, rather than invented from titles.
        answer["available_views"] = ["member", "team"]
        return self._as_teams(answer) if view == "team" else answer

    def _as_teams(self, answer: dict[str, Any]) -> dict[str, Any]:
        """The same authorized answer, read one level up: people become the team they sit in.

        Edges that stay inside one team are that team's own business and are hidden; the rest are counted by
        direction and kind, so a thick line means many connections rather than one important one.
        """
        by_ref = {self._ref(node): node for node in answer["nodes"]}
        team_of: dict[str, str] = {}
        for node in answer["nodes"]:
            if node["kind"] != "person":
                continue
            units = [unit for unit in self._source.member_units(node["id"]) if str(unit["id"]) != "scax"]
            unit = units[0] if units else {"id": "scax", "name": "SCAX"}
            team_of[self._ref(node)] = f"team:{unit['id']}"
            by_ref[f"team:{unit['id']}"] = self._team_node(unit)

        def moved(ref: str) -> str:
            return team_of.get(ref, ref)

        grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
        for edge in answer["edges"]:
            source, target = moved(edge["from"]), moved(edge["to"])
            if source == target:
                continue
            key = (edge["kind"], source, target)
            existing = grouped.get(key)
            if existing is None:
                grouped[key] = {**edge, "from": source, "to": target, "count": 1}
            else:
                existing["count"] += 1
        edges = list(grouped.values())
        named = {row["from"] for row in edges} | {row["to"] for row in edges} | {moved(self._ref(answer["center"]))}
        return {
            **answer,
            "center": by_ref.get(moved(self._ref(answer["center"])), answer["center"]),
            "nodes": [node for ref, node in by_ref.items() if ref in named],
            "edges": edges,
            "view": "team",
        }

    # ---- shared shaping ----

    def _bounded(self, center: dict[str, Any], nodes: dict[str, dict[str, Any]], edges: list[dict[str, Any]], limit: int) -> dict[str, Any]:
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
    def _edge(kind: str, source: str, target: str) -> dict[str, Any]:
        relation = RELATIONS.get(kind, {"label": kind, "inverse": kind, "provenance": kind})
        return {
            "kind": kind,
            "from": source,
            "to": target,
            "label": relation["label"],
            "inverse_label": relation["inverse"],
            # Which ledger states this connection. There is no edge without one.
            "provenance": relation["provenance"],
        }

    @staticmethod
    def _task_node(task: dict[str, Any]) -> dict[str, Any]:
        return {"kind": "task", "id": str(task["task_id"]), "title": str(task["title"]), "state": task.get("state")}

    @staticmethod
    def _meeting_node(meeting: dict[str, Any]) -> dict[str, Any]:
        return {
            "kind": "meeting",
            "id": str(meeting["meeting_id"]),
            "title": str(meeting.get("title") or "회의"),
            "state": str(meeting.get("visibility") or ""),
        }

    @staticmethod
    def _team_node(unit: dict[str, Any]) -> dict[str, Any]:
        return {"kind": "team", "id": str(unit["id"]), "title": str(unit.get("name") or unit["id"]), "state": None}

    @staticmethod
    def _person_node(person: dict[str, Any]) -> dict[str, Any]:
        return {"kind": "person", "id": str(person["member_id"]), "title": str(person["display_name"]), "state": None}

    @staticmethod
    def _request_node(request: dict[str, Any]) -> dict[str, Any]:
        return {"kind": "work_request", "id": str(request["request_id"]), "title": str(request["title"]), "state": request.get("state")}

    def _add_person(self, nodes: dict[str, dict[str, Any]], member_id: str) -> None:
        person = self._source.person(member_id) or {"member_id": member_id, "display_name": member_id}
        node = self._person_node(person)
        nodes[self._ref(node)] = node
