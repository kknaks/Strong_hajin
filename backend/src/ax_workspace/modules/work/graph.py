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
NODE_KINDS = ("person", "team", "project", "work_request", "task", "material", "meeting", "report")
#: How far one answer may reach, so a screen and a delegated turn get the same bounded thing.
DEFAULT_LIMIT = 20
MAX_LIMIT = 50
#: The first screen is a graph, not an empty search box — bounded the same way every other answer is. It is wider
#: than one hop because a graph one can read the structure of needs more than a couple of dozen connections.
OVERVIEW_LIMIT = 120
MAX_OVERVIEW_LIMIT = 200

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
    "part_of": {"label": "이 프로젝트의 일", "inverse": "프로젝트", "provenance": "task"},
    "attended": {"label": "참석", "inverse": "참석자", "provenance": "meeting_attendee"},
    "owns_meeting": {"label": "소집", "inverse": "소집자", "provenance": "meeting"},
    "followed_up": {"label": "후속 업무", "inverse": "나온 회의", "provenance": "meeting_followup_promotion"},
    "cites": {"label": "근거로 삼은 업무", "inverse": "이 업무를 담은 보고", "provenance": "daily_report_draft"},
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
    def readable_projects(self, principal: Principal) -> list[dict[str, Any]]: ...
    def readable_meeting(self, principal: Principal, meeting_id: UUID) -> dict[str, Any] | None: ...
    def meeting_followup_tasks(self, principal: Principal, meeting_id: UUID) -> list[dict[str, Any]]: ...
    def member_units(self, member_id: str) -> list[dict[str, Any]]: ...
    def own_reports(self, principal: Principal, *, limit: int = 3) -> list[dict[str, Any]]: ...
    def material_owners(self, principal: Principal, material_id: str) -> list[dict[str, Any]]: ...
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
        if kind == "report":
            return self._report_neighbors(principal, identifier, limit)
        if kind == "material":
            return self._material_neighbors(principal, identifier, limit)
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
            node = self._task_node(child)
            nodes[self._ref(node)] = node
            edges.append(self._edge("parent_of", self._ref(center), self._ref(node)))

        for reference in task.get("references") or []:
            referenced = reference.get("task")
            if not referenced:
                continue
            node = self._task_node(referenced)
            nodes[self._ref(node)] = node
            edges.append(self._edge("refers_to", self._ref(center), self._ref(node)))

        for material in self._source.task_materials(principal, task_id):
            node = self._material_node(material)
            nodes[self._ref(node)] = node
            edges.append(self._edge("has_material", self._ref(center), self._ref(node)))

        # A report this person wrote from this work is part of how the work connects, for them.
        for report in self._source.own_reports(principal, limit=5):
            if str(task_id) not in report["task_ids"]:
                continue
            node = self._report_node(report)
            nodes[self._ref(node)] = node
            edges.append(self._edge("cites", self._ref(node), self._ref(center)))

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

    # ---- one hop from a report, and from a file ----

    def _report_neighbors(self, principal: Principal, report_id: str, limit: int) -> dict[str, Any]:
        """A report stands on the work it was written from. Only the writer's own reports are here at all."""
        report = next((row for row in self._source.own_reports(principal, limit=20) if str(row["report_id"]) == report_id), None)
        if report is None:
            raise GraphNotFound("daily report was not found")
        center = self._report_node(report)
        nodes: dict[str, dict[str, Any]] = {self._ref(center): center}
        edges: list[dict[str, Any]] = []
        self._add_person(nodes, str(principal.id))
        edges.append(self._edge("holds", f"person:{principal.id}", self._ref(center)))
        for task_id in report["task_ids"]:
            task = self._source.readable_task(principal, UUID(str(task_id)))
            if task is None:
                continue
            node = self._task_node(task)
            nodes[self._ref(node)] = node
            edges.append(self._edge("cites", self._ref(center), self._ref(node)))
        return self._bounded(center, nodes, edges, limit)

    def _material_neighbors(self, principal: Principal, material_id: str, limit: int) -> dict[str, Any]:
        owners = self._source.material_owners(principal, material_id)
        if not owners:
            raise GraphNotFound("material was not found")
        material = next(
            (
                row
                for task in owners
                for row in self._source.task_materials(principal, UUID(str(task["task_id"])))
                if str(row["material_id"]) == material_id
            ),
            None,
        )
        if material is None:
            raise GraphNotFound("material was not found")
        center = self._material_node(material)
        nodes: dict[str, dict[str, Any]] = {self._ref(center): center}
        edges: list[dict[str, Any]] = []
        for task in owners:
            node = self._task_node(task)
            nodes[self._ref(node)] = node
            edges.append(self._edge("has_material", self._ref(node), self._ref(center)))
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
        if view not in {"member", "team", "project"}:
            raise GraphError("표현 수준은 구성원 보기, 팀으로 묶기, 프로젝트로 묶기입니다")
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
            owner = str(meeting.get("owner_id") or "")
            if owner:
                # A meeting somebody called is connected to them even when nobody else was put on it.
                self._add_person(nodes, owner)
                edges.append(self._edge("owns_meeting", f"person:{owner}", self._ref(node)))
            for attendee in meeting.get("attendees") or []:
                member_id = str(attendee["member_id"])
                if member_id == owner:
                    continue
                self._add_person(nodes, member_id)
                edges.append(self._edge("attended", f"person:{member_id}", self._ref(node)))
        for report in self._source.own_reports(principal, limit=2):
            node = self._report_node(report)
            edges_for_report = [
                self._edge("cites", self._ref(node), f"task:{task_id}")
                for task_id in report["task_ids"]
                if f"task:{task_id}" in nodes
            ]
            if not edges_for_report:
                continue
            nodes[self._ref(node)] = node
            edges.extend(edges_for_report)

        answer = self._bounded(me, nodes, edges, min(max(1, int(limit)), MAX_OVERVIEW_LIMIT), cap=MAX_OVERVIEW_LIMIT)
        answer["view"] = view
        # A grouping the product has no ledger for is not offered at all, rather than invented from titles.
        # 프로젝트는 읽을 수 있는 프로젝트가 하나라도 있을 때만 표현 수준으로 제안한다.
        answer["available_views"] = ["member", "team"] + (["project"] if self._source.readable_projects(principal) else [])
        if view == "team":
            return self._as_teams(answer)
        return self._as_projects(principal, answer) if view == "project" else answer

    def _as_projects(self, principal: Principal, answer: dict[str, Any]) -> dict[str, Any]:
        """같은 답을 프로젝트 층에서 읽는다: 업무가 자기 프로젝트로 접힌다.

        팀 보기가 사람을 팀으로 접는 것과 같은 동작이되 접히는 것이 다르다 — 프로젝트는 사람이 아니라 일을 묶기
        때문이다. 프로젝트에 매달리지 않은 업무는 접히지 않고 자기 자리에 그대로 남는다. 대부분의 일이 그렇고,
        없는 프로젝트를 만들어 넣지 않는다.
        """
        readable = {str(project["project_id"]): project for project in self._source.readable_projects(principal)}
        by_ref = {self._ref(node): node for node in answer["nodes"]}
        project_of: dict[str, str] = {}
        for node in answer["nodes"]:
            if node["kind"] != "task":
                continue
            project_id = str(node.get("project_id") or "")
            # 읽을 수 없는 프로젝트로는 접지 않는다. 접었다면 그 프로젝트의 이름이 드러났을 것이다.
            if project_id not in readable:
                continue
            project_of[self._ref(node)] = f"project:{project_id}"
            by_ref[f"project:{project_id}"] = self._project_node(readable[project_id])

        def moved(ref: str) -> str:
            return project_of.get(ref, ref)

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
            "view": "project",
        }

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
            units = [unit for unit in self._source.member_units(node["id"]) if str(unit.get("parent_id") or "")]
            if not units:
                # 어디에도 묶이지 않는 사람은 자기 자리에 그대로 둔다. 없는 팀을 만들어 넣지 않는다.
                continue
            unit = units[0]
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

    def _bounded(
        self,
        center: dict[str, Any],
        nodes: dict[str, dict[str, Any]],
        edges: list[dict[str, Any]],
        limit: int,
        *,
        cap: int = MAX_LIMIT,
    ) -> dict[str, Any]:
        """One answer stays small enough to read: extra connections are counted, never silently dropped."""
        bounded = max(1, min(int(limit), cap))
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
        return {
            "kind": "task",
            "id": str(task["task_id"]),
            "title": str(task["title"]),
            "state": task.get("state"),
            # Where this sits in time, as the ledger plans it — never a synthesized date.
            "date": task.get("due_date") or task.get("start_date"),
            # 어느 프로젝트의 일인가. 프로젝트로 묶어 볼 때 이 값으로 접는다.
            "project_id": task.get("project_id"),
        }

    @staticmethod
    def _meeting_node(meeting: dict[str, Any]) -> dict[str, Any]:
        return {
            "kind": "meeting",
            "id": str(meeting["meeting_id"]),
            "title": str(meeting.get("title") or "회의"),
            "state": str(meeting.get("visibility") or ""),
            "date": str(meeting["starts_at"])[:10] if meeting.get("starts_at") else None,
        }

    @staticmethod
    def _report_node(report: dict[str, Any]) -> dict[str, Any]:
        return {
            "kind": "report",
            "id": str(report["report_id"]),
            "title": str(report["title"]),
            "state": report.get("state"),
            "date": report.get("date"),
        }

    @staticmethod
    def _material_node(material: dict[str, Any]) -> dict[str, Any]:
        return {
            "kind": "material",
            "id": str(material["material_id"]),
            "title": str(material["name"]),
            "state": str(material.get("kind") or ""),
            "date": None,
        }

    @staticmethod
    def _team_node(unit: dict[str, Any]) -> dict[str, Any]:
        return {"kind": "team", "id": str(unit["id"]), "title": str(unit.get("name") or unit["id"]), "state": None, "date": None}

    @staticmethod
    def _project_node(project: dict[str, Any]) -> dict[str, Any]:
        return {
            "kind": "project",
            "id": str(project["project_id"]),
            "title": str(project.get("name") or project["project_id"]),
            "state": project.get("state"),
            "date": project.get("ends_on") or project.get("starts_on"),
        }

    @staticmethod
    def _person_node(person: dict[str, Any]) -> dict[str, Any]:
        return {"kind": "person", "id": str(person["member_id"]), "title": str(person["display_name"]), "state": None, "date": None}

    @staticmethod
    def _request_node(request: dict[str, Any]) -> dict[str, Any]:
        return {
            "kind": "work_request",
            "id": str(request["request_id"]),
            "title": str(request["title"]),
            "state": request.get("state"),
            "date": request.get("due_date"),
        }

    def _add_person(self, nodes: dict[str, dict[str, Any]], member_id: str) -> None:
        person = self._source.person(member_id) or {"member_id": member_id, "display_name": member_id}
        node = self._person_node(person)
        nodes[self._ref(node)] = node
