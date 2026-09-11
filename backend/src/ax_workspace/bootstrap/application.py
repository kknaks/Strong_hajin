"""Composition root for the local workflow application."""
from __future__ import annotations

from pathlib import Path
from datetime import UTC, datetime

from typing import Any
import hashlib
import os
import sys
from uuid import UUID

from ax_workspace.bootstrap.settings import Settings
from ax_workspace.modules.ax_execution.conversations import (
    ConversationApplication,
    ConversationContextReferenceInput,
)
from ax_workspace.modules.ax_execution.actions import ActionApplication, ActionCapabilityDenied
from ax_workspace.modules.notifications import NotificationNotFound
from ax_workspace.modules.organization_access.domain import ACTION_DECIDE, Principal
from ax_workspace.modules.work.material_folders import MaterialFolderApplication
from ax_workspace.platform.material_folders import SqlAlchemyMaterialFolderRepository
from ax_workspace.modules.organization_access.administration import AccessAdministration
from ax_workspace.modules.organization_access.application import OrganizationApplication
from ax_workspace.platform.organization_access import SqlAlchemyOrganizationRepository
from ax_workspace.modules.reports.application import DailyReportAccessDenied, DailyReportApplication
from ax_workspace.modules.ax_execution.ai import AiGenerationRequest, AiProvider, ProviderFailure
from ax_workspace.platform.codex_cli import CodexCliMcpServer, CodexCliProviderAdapter
from ax_workspace.platform.conversation_jobs import ConversationJobQueue
from ax_workspace.platform.durable_jobs import MemoryDurableJobQueue, build_job_queue
from ax_workspace.platform.conversations import (
    SqlAlchemyConversationContextResolver,
    SqlAlchemyConversationRepository,
)
from ax_workspace.modules.actions.domain import ActionCenterApplication
from ax_workspace.platform.action_center import action_handlers
from ax_workspace.platform.actions import SqlAlchemyActionExecutor, SqlAlchemyActionRepository
from ax_workspace.platform.reports import SqlAlchemyDailyReportDraftWorkflow, SqlAlchemyDailyReportRepository
from ax_workspace.modules.work.materials import MaterialError, MaterialNotFound, TaskMaterialApplication
from ax_workspace.modules.work.action_materials import ActionMaterialDraftApplication, ActionMaterialError
from ax_workspace.modules.work.material_search import MaterialSearchApplication, RESOURCE_TYPES
from ax_workspace.bootstrap.material_sources import SessionMaterialOwners, readable_content_evidence
from ax_workspace.modules.work.graph import GraphApplication
from ax_workspace.modules.work.application import TaskAccessDenied, TaskApplication, TaskState
from ax_workspace.modules.work.assignments import TaskAssignmentApplication
from ax_workspace.modules.meetings.application import MeetingApplication
from ax_workspace.modules.meetings.domain import MeetingError
from ax_workspace.modules.meetings.transcription import FinalTranscriptSegment
from ax_workspace.modules.meetings.refinement import build_refinement_prompt, parse_refinement, refinement_output_schema
from ax_workspace.modules.meetings.summary import build_summary_prompt, parse_summary, summary_output_schema
from ax_workspace.modules.meetings.jobs import MeetingFinalizationJob, MeetingFinalizationQueue
from ax_workspace.modules.work.requests import WorkRequestApplication, WorkRequestError
from sqlalchemy import delete, or_, select

from ax_workspace.platform.persistence import (
    AppointmentRecord,
    AttachmentRecord,
    DecisionItemRecord,
    ConversationContentEvidenceRecord,
    EmploymentPeriodRecord,
    MaterialBlockRecord,
    MaterialChunkRecord,
    MaterialExtractionRecord,
    MemberRecord,
    OrganizationUnitRecord,
    PositionDefinitionRecord,
    make_session_factory,
)
from ax_workspace.platform.materials import LocalDirectoryMaterialStorage
from ax_workspace.platform.notifications import SqlAlchemyNotificationRepository, notification_view
from ax_workspace.platform.action_materials import SqlAlchemyActionMaterialDraftRepository
from ax_workspace.platform.native_materials import NativeMaterialRepository, NativeRevisionStorage
from ax_workspace.platform.recordings import LocalDirectoryRecordingStorage
from ax_workspace.platform.soniox import SonioxTranscriptionAdapter
from ax_workspace.modules.work.material_extraction import LexicalMaterialRetriever, MaterialExtractionJob, extraction_view
from ax_workspace.modules.work.projects import ProjectApplication
from ax_workspace.modules.work.search import folded, matches, normalize_person_reference
from ax_workspace.platform.projects import SqlAlchemyProjectRepository
from ax_workspace.platform.korean import install as install_korean_analyzer
from ax_workspace.platform.material_extraction import (
    MaterialJobQueue,
    SqlChunkIndex,
    reindex_stale_chunks,
    SqlAlchemyMaterialEvidenceRepository,
    SqlAlchemyMaterialExtractionRepository,
)
from ax_workspace.platform.work_tasks import (
    ActivityLedger,
    SqlAlchemyGraphReceiptRepository,
    SqlAlchemyTaskAssignmentRepository,
    SqlAlchemyAttachmentRepository,
    SqlAlchemyCommentRepository,
    SqlAlchemyTaskRepository,
    SqlAlchemyWorkRecordSource,
    SqlAlchemyWorkRequestRepository,
)
from ax_workspace.platform.meetings import SqlAlchemyMeetingRepository




class _SessionResourceReferences:
    """Resolves a referenced resource by asking the module that owns it, inside the caller's own session.

    Never a stored copy: if the principal may not read it now, there is no title to give them.
    """

    def __init__(self, application: "WorkflowApplication", session: Any) -> None:
        self._application = application
        self._session = session

    def title(self, principal: Principal, resource_type: str, resource_id: str) -> str | None:
        try:
            identifier = UUID(str(resource_id))
        except (TypeError, ValueError):
            return None
        try:
            if resource_type == "task":
                return str(self._application._tasks(self._session).get(principal, identifier)["title"])
            if resource_type == "meeting":
                return str(self._application._meetings(self._session).get(principal, identifier)["title"])
        except Exception:
            return None
        return None


class _SessionAnswerResources:
    """What a turn named, read back through the module that owns each thing — every time, for whoever is asking now."""

    def __init__(self, application: "WorkflowApplication", session: Any) -> None:
        self._source = _SessionGraphSource(application, session)
        self._application = application
        self._session = session

    def readable_material_evidence(self, principal: Principal, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return readable_content_evidence(self._application, self._session, principal, evidence)

    def resolve(self, principal: Principal, references: list[dict[str, Any]]) -> list[dict[str, Any]]:
        resolved: list[dict[str, Any]] = []
        content = self._readable_content_references(principal, references)
        for reference in references:
            kind = str(reference["resource_type"])
            identifier = str(reference["resource_id"])
            if kind == "material":
                observed = content.get((str(reference.get("turn_id")), identifier))
                if observed is None:
                    continue
                resolved.append({**reference, "title": observed["name"], "state": "available", "origin": observed["origin"],
                    "source_contexts": observed["source_contexts"], "current_version": None, "changed_since": False})
                continue
            title, state = self._read(principal, kind, identifier)
            if title is None:
                # Readable when the turn ran, not now. It leaves no title and no gap that could be counted.
                continue
            # 답이 딛고 선 회차와 지금의 회차가 다를 수 있다. 그것은 숨길 일이 아니라 말할 일이다 — 사람이
            # 링크를 열기 전에 무엇이 달라졌을 수 있는지 알아야 한다. 지금 회차를 알 수 없으면 말하지 않는다.
            seen = reference.get("resource_version")
            current = self._current_version(principal, kind, identifier)
            resolved.append({
                **reference,
                "title": title,
                "state": state,
                "current_version": current,
                "changed_since": bool(seen and current and int(current) != int(seen)),
            })
        return resolved

    def _readable_content_references(self, principal: Principal, references: list[dict[str, Any]]) -> dict:
        observations = [{**row, "material_id": row["resource_id"]} for row in references if row["resource_type"] == "material"]
        return {(str(row.get("turn_id")), row["material_id"]): row
                for row in readable_content_evidence(self._application, self._session, principal, observations)}

    def readable_material_steps(self, principal: Principal, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        observations = [{**step, "material_id": str(step["to_ref"]).partition(":")[2]}
                        for step in steps if step.get("edge_kind") == "has_material"]
        return readable_content_evidence(self._application, self._session, principal, observations)

    def _current_version(self, principal: Principal, kind: str, identifier: str) -> int | None:
        """지금의 회차. 회차를 갖지 않는 것에는 없는 것이 정상이다."""
        try:
            if kind == "task":
                task = self._source.readable_task(principal, UUID(identifier))
                return int(task["version"]) if task and task.get("version") is not None else None
            if kind == "work_request":
                request = self._source.readable_request(principal, UUID(identifier))
                return int(request["version"]) if request and request.get("version") is not None else None
            if kind == "meeting":
                meeting = self._source.readable_meeting(principal, UUID(identifier))
                return int(meeting["version"]) if meeting and meeting.get("version") is not None else None
        except Exception:
            return None
        return None

    def readable_titles(self, principal: Principal, refs: list[str]) -> dict[str, str]:
        """The name each of these still has for this person. Anything they may no longer open is simply missing."""
        people = {
            str(row["id"]): str(row["display_name"])
            for row in self._application.member_directory(principal)
        }
        units = {str(row["id"]): str(row["name"]) for row in self._application.organization_tree(principal)}
        titles: dict[str, str] = {}
        for ref in refs:
            kind, _, identifier = str(ref).partition(":")
            if kind == "person" and identifier in people:
                titles[ref] = people[identifier]
                continue
            if kind == "team" and identifier in units:
                titles[ref] = units[identifier]
                continue
            title, _state = self._read(principal, kind, identifier)
            if title is not None:
                titles[ref] = title
        return titles

    def _read(self, principal: Principal, kind: str, identifier: str) -> tuple[str | None, str | None]:
        try:
            if kind == "project":
                project = self._source.readable_project(principal, UUID(identifier))
                return (str(project["name"]), project.get("state")) if project else (None, None)
            if kind == "task":
                task = self._source.readable_task(principal, UUID(identifier))
                return (str(task["title"]), task.get("state")) if task else (None, None)
            if kind == "work_request":
                request = self._source.readable_request(principal, UUID(identifier))
                return (str(request["title"]), request.get("state")) if request else (None, None)
            if kind == "meeting":
                meeting = self._source.readable_meeting(principal, UUID(identifier))
                return (str(meeting["title"]), meeting.get("visibility")) if meeting else (None, None)
            if kind == "material":
                material = self._application._material_metadata(self._session, principal, UUID(identifier))
                return material["name"], material["state"]
            if kind == "report":
                report = self._application.daily_report_history(principal, identifier)
                return (f"{report['report_date']} 일일보고", report.get("status"))
        except Exception:
            return (None, None)
        return (None, None)


class _SessionReadableWork:
    """자료 검색이 묻는 것: 이 사람이 읽을 수 있는 업무는 무엇인가.

    답은 업무 모듈이 이미 아는 것 — 자기가 든 것, 조직 범위로 읽는 것, 함께 하는 프로젝트의 것. 자료 검색이
    그 판정을 다시 하지 않는다.
    """

    def __init__(self, application: "WorkflowApplication", session: Any) -> None:
        self._application = application
        self._session = session

    def readable_task_ids(self, principal: Principal) -> list[str]:
        rows = self._application._tasks(self._session).list_for(
            principal, include_closed=True, include_organization=True
        )
        return [str(row["task_id"]) for row in rows]

    def may_read_task(self, principal: Principal, task_id: UUID) -> bool:
        """한 업무를 두고 묻는 답과 전체를 두고 묻는 답이 같은 곳에서 나온다."""
        return str(task_id) in set(self.readable_task_ids(principal))


class _SessionGraphSource:
    """The graph's window onto the ledgers: every read is the owning module's own authorized operation."""

    def __init__(self, application: "WorkflowApplication", session: Any) -> None:
        self._application = application
        self._session = session

    def readable_tasks(self, principal: Principal, *, query: str | None = None, assignee_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        # Following connections is a read surface like any other: it shows exactly what this person may read.
        try:
            rows = self._application._tasks(self._session).list_for(
                principal, include_closed=True, include_organization=True
            )
        except TaskAccessDenied:
            return []
        return [row for row in rows
                if (not query or matches(query, str(row["title"])))
                and (assignee_id is None or str((row.get("assignee") or {}).get("member_id")) == assignee_id)][:limit]

    def readable_task(self, principal: Principal, task_id: UUID) -> dict[str, Any] | None:
        try:
            return self._application._tasks(self._session).get(principal, task_id)
        except Exception:
            return None

    def people(self, principal: Principal, *, query: str) -> list[dict[str, Any]]:
        """이름 또는 현재 소속 범위의 활성 직책으로 사람을 찾는다.

        존칭 정리는 이 서버 경계가 하고, 직책과 사람의 연결은 display name 장식이 아니라 appointment 원장을
        읽는다. 다른 조직의 같은 직책을 함께 내보내면 `우리 팀장님`이 전사 검색이 되므로 현재 principal의
        실제 membership과 그 상위 조직에 놓인 appointment만 후보가 된다.
        """
        reference = normalize_person_reference(query)
        if not reference:
            return []
        position_reference = any(
            folded(reference) == folded(str(position_name))
            for position_name in self._session.scalars(
                select(PositionDefinitionRecord.name).where(PositionDefinitionRecord.lifecycle == "active")
            )
        )
        rows = OrganizationApplication(SqlAlchemyOrganizationRepository(self._session)).member_directory(principal)
        # graph의 사람 node는 `member_id`로 말한다. 명부는 `id`로 말하므로 여기서 한 번 맞춘다.
        found = {
            str(row["id"]): {"member_id": row["id"], "display_name": row["display_name"]}
            for row in rows
            if not position_reference and matches(reference, str(row["display_name"]))
        }
        if not position_reference:
            return [found[key] for key in sorted(found)]
        now = datetime.now(UTC)
        parents = {
            str(unit_id): str(parent_id) if parent_id is not None else None
            for unit_id, parent_id in self._session.execute(
                select(OrganizationUnitRecord.id, OrganizationUnitRecord.parent_id)
            ).all()
        }
        related_organizations = set(principal.organization_scope)
        frontier = list(related_organizations)
        while frontier:
            parent = parents.get(frontier.pop())
            if parent is not None and parent not in related_organizations:
                related_organizations.add(parent)
                frontier.append(parent)
        appointments = self._session.execute(
            select(
                MemberRecord.id,
                MemberRecord.display_name,
                PositionDefinitionRecord.name,
                AppointmentRecord.organization_id,
                OrganizationUnitRecord.name,
            )
            .join(AppointmentRecord, AppointmentRecord.member_id == MemberRecord.id)
            .join(EmploymentPeriodRecord, EmploymentPeriodRecord.member_id == MemberRecord.id)
            .join(PositionDefinitionRecord, PositionDefinitionRecord.id == AppointmentRecord.position_definition_id)
            .join(OrganizationUnitRecord, OrganizationUnitRecord.id == AppointmentRecord.organization_id)
            .where(
                MemberRecord.employment_state == "active",
                MemberRecord.record_status == "active",
                EmploymentPeriodRecord.state == "active",
                EmploymentPeriodRecord.ended_at.is_(None),
                PositionDefinitionRecord.lifecycle == "active",
                PositionDefinitionRecord.organization_unit_type_id == OrganizationUnitRecord.unit_type_id,
                OrganizationUnitRecord.lifecycle == "active",
                OrganizationUnitRecord.abolished_at.is_(None),
                AppointmentRecord.organization_id.in_(related_organizations),
                AppointmentRecord.valid_from <= now,
                or_(AppointmentRecord.valid_until.is_(None), AppointmentRecord.valid_until > now),
            )
            .order_by(MemberRecord.id, AppointmentRecord.organization_id)
        ).all()
        for member_id, display_name, position_name, organization_id, organization_name in appointments:
            if folded(reference) != folded(str(position_name)):
                continue
            found[str(member_id)] = {
                "member_id": str(member_id),
                "display_name": str(display_name),
                "match": {
                    "kind": "position",
                    "label": str(position_name),
                    "organization_id": str(organization_id),
                    "organization_name": str(organization_name),
                },
            }
        return [found[key] for key in sorted(found)]

    def readable_requests(self, principal: Principal, *, query: str | None = None) -> list[dict[str, Any]]:
        try:
            rows = self._application._work_requests(self._session).list(principal)
        except Exception:
            return []
        return [row for row in rows if not query or matches(query, str(row["title"]))]

    def readable_request(self, principal: Principal, request_id: UUID) -> dict[str, Any] | None:
        try:
            return self._application._work_requests(self._session).get(principal, request_id)
        except Exception:
            return None

    def task_materials(self, principal: Principal, task_id: UUID) -> list[dict[str, Any]]:
        try:
            return self._application._materials(self._session).list(principal, task_id)
        except Exception:
            return []

    def materials_for_tasks(self, principal: Principal, task_ids: list[UUID]) -> dict[str, list[dict[str, Any]]]:
        """화면에 있는 업무들의 자료.

        업무마다 자료 모듈의 같은 인가된 읽기를 지난다. 한 번의 질의로 줄일 수는 있지만 그러면 자료의
        판정과 표현이 두 곳에 생긴다 — 첫 화면의 업무 수는 이미 상한이 있으므로 그 값을 치르지 않는다.
        """
        found: dict[str, list[dict[str, Any]]] = {}
        for task_id in task_ids:
            materials = self.task_materials(principal, task_id)
            if materials:
                found[str(task_id)] = materials
        return found

    def person(self, member_id: str) -> dict[str, Any] | None:
        name = SqlAlchemyTaskRepository(self._session).member_display_name(member_id)
        return {"member_id": member_id, "display_name": name} if name is not None else None

    def readable_projects(self, principal: Principal) -> list[dict[str, Any]]:
        """그래프가 프로젝트로 묶을 때 묻는 것. 판정은 프로젝트 모듈이 한다."""
        try:
            return self._application._projects(self._session).list(principal)
        except Exception:
            return []

    def readable_project(self, principal: Principal, project_id: UUID) -> dict[str, Any] | None:
        from ax_workspace.modules.work.projects import ProjectAccessDenied, ProjectNotFound

        try:
            return self._application._projects(self._session).get(principal, project_id)
        except (ProjectAccessDenied, ProjectNotFound):
            return None

    def readable_meetings(self, principal: Principal, *, query: str | None = None, member_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        try:
            rows = self._application._meetings(self._session).list(principal)
        except Exception:
            return []
        meetings = [row for row in rows if row.get("kind") == "meeting"]
        return [row for row in meetings
                if (not query or matches(query, str(row.get("title") or "")))
                and (member_id is None or member_id == str(row.get("owner_id"))
                     or member_id in {str(person["member_id"]) for person in row.get("attendees") or []})][:limit]

    def readable_meeting(self, principal: Principal, meeting_id: UUID) -> dict[str, Any] | None:
        try:
            return self._application._meetings(self._session).get(principal, meeting_id)
        except Exception:
            return None

    def meeting_followup_tasks(self, principal: Principal, meeting_id: UUID) -> list[dict[str, Any]]:
        """Work that came out of this meeting, read as work — a promotion nobody may open is simply not there."""
        detail = self.readable_meeting(principal, meeting_id)
        if detail is None:
            return []
        tasks: list[dict[str, Any]] = []
        seen: set[str] = set()
        for summary in detail.get("summaries") or []:
            for statement in summary.get("statements") or []:
                task_id = statement.get("promoted_task_id")
                if not task_id or str(task_id) in seen:
                    continue
                seen.add(str(task_id))
                task = self.readable_task(principal, UUID(str(task_id)))
                if task is not None:
                    tasks.append(task)
        return tasks

    def own_reports(self, principal: Principal, *, limit: int = 3) -> list[dict[str, Any]]:
        """This person's own daily reports and the work each one was written from. A report is nobody else's to read."""
        reports = self._application.daily_report_recent(principal, limit=limit)
        return [
            {
                "report_id": str(report["report_id"]),
                "title": f"{report['report_date']} 일일보고",
                "state": str(report.get("status") or ""),
                "date": str(report["report_date"]),
                "task_ids": [str(source["task_id"]) for source in report.get("source_refs") or []],
            }
            for report in reports
        ]

    def material_owners(self, principal: Principal, material_id: str) -> list[dict[str, Any]]:
        """The work this file is bound to, among the work this person may read."""
        owners: list[dict[str, Any]] = []
        for task_id in SqlAlchemyAttachmentRepository(self._session).active_task_contexts(UUID(material_id)):
            task = self.readable_task(principal, UUID(task_id))
            if task is not None:
                owners.append(task)
        return owners

    def member_units(self, member_id: str) -> list[dict[str, Any]]:
        """이 사람이 속한 단위들. 어디가 위인지도 함께 온다 — 회사와 팀은 이름이 아니라 자리로 구분한다."""
        repository = SqlAlchemyOrganizationRepository(self._session)
        profile = repository.profile_for(member_id)
        if not profile:
            return []
        units = []
        for row in profile["organizations"]:
            record = self._session.get(OrganizationUnitRecord, str(row["id"]))
            units.append({**row, "parent_id": record.parent_id if record is not None else None})
        return units

    def unit_members(self, unit_id: str) -> list[dict[str, Any]]:
        return [
            {"member_id": str(member["member_id"]), "display_name": str(member["display_name"])}
            for member in SqlAlchemyOrganizationRepository(self._session).unit_members(unit_id, include_descendants=False)
        ]

    def organization_units(self) -> list[dict[str, Any]]:
        """조직 나무 전체. 이름과 어디에 붙어 있는지까지이며, 누가 있는지는 말하지 않는다."""
        return SqlAlchemyOrganizationRepository(self._session).organization_tree()


class _SessionTaskReferences:
    """Reads a referenced Task through the Work module itself, so a pointer never becomes permission."""

    def __init__(self, application: "WorkflowApplication", session: Any) -> None:
        self._application = application
        self._session = session

    def view(self, principal: Principal, task_id: UUID) -> dict[str, Any] | None:
        try:
            task = self._application._tasks(self._session).get(principal, task_id)
        except Exception:
            return None
        if task.get("access") != "owner":
            # Reading it through a relationship is not the same as being able to point at it as your own context.
            return None
        return {
            "task_id": task["task_id"],
            "title": task["title"],
            "state": task["state"],
            "due_date": task.get("due_date"),
            "assignee": task.get("assignee"),
        }


class WorkflowApplication:
    """Transaction boundary shared by HTTP, MCP, and local rehearsal adapters."""

    def __init__(self, settings: Settings, report_provider: AiProvider | None = None) -> None:
        self._settings = settings
        self._session_factory = make_session_factory(settings.database_url)
        self._material_storage = LocalDirectoryMaterialStorage(Path(settings.materials_dir))
        self._recording_storage = LocalDirectoryRecordingStorage(Path(settings.recordings_dir))
        self._soniox = SonioxTranscriptionAdapter()
        # One in-process job store per application when the memory backend is selected (tests); postgres joins each session.
        self.memory_job_queue: MemoryDurableJobQueue | None = MemoryDurableJobQueue() if settings.job_queue_backend == "memory" else None
        self._report_provider = report_provider or create_codex_cli_provider(settings)

    def my_work(self, principal: Principal) -> list[dict[str, Any]]:
        """ERD work_inbox projection: tasks the principal currently holds an active assignment for."""
        with self._session_factory() as session:
            try:
                return self._tasks(session).list_for(principal)
            except TaskAccessDenied:
                return []

    def list_meetings(self, principal: Principal) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return self._meetings(session).list(principal)

    def get_meeting(self, principal: Principal, meeting_id: UUID) -> dict[str, Any]:
        with self._session_factory() as session:
            return self._meetings(session).get(principal, meeting_id)

    def create_meeting(self, principal: Principal, **fields: Any) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._meetings(session).create(principal, **fields)
            self._register_note_material_from_view(session, result.get("note"))
            session.commit()
            return result

    def update_meeting(self, principal: Principal, meeting_id: UUID, expected_version: int, changes: dict[str, Any]) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._meetings(session).update(principal, meeting_id, expected_version, changes)
            session.commit()
            return result

    def attach_meeting_material_link(self, principal: Principal, meeting_id: UUID, expected_version: int, *, url: str, label: str) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._meetings(session).attach_material_link(
                principal, meeting_id, expected_version, url=url, label=label
            )
            session.commit()
            return result

    def attach_meeting_material(
        self, principal: Principal, meeting_id: UUID, expected_version: int, *, name: str, content_type: str, data: bytes
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._meetings(session).attach_material(
                principal, meeting_id, expected_version, name=name, content_type=content_type, data=data
            )
            session.commit()
            return result

    def detach_meeting_material(
        self, principal: Principal, meeting_id: UUID, binding_id: UUID, expected_version: int
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._meetings(session).detach_material(principal, meeting_id, binding_id, expected_version)
            session.commit()
            return result

    def replace_meeting_material(
        self,
        principal: Principal,
        meeting_id: UUID,
        binding_id: UUID,
        expected_version: int,
        *,
        name: str,
        content_type: str,
        data: bytes,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._meetings(session).replace_material(
                principal, meeting_id, binding_id, expected_version,
                name=name, content_type=content_type, data=data,
            )
            session.commit()
            return result

    def share_meeting(self, principal: Principal, meeting_id: UUID, member_id: str, expected_version: int) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._meetings(session).share(principal, meeting_id, member_id, expected_version)
            session.commit()
            return result

    def revoke_meeting_share(self, principal: Principal, meeting_id: UUID, member_id: str, expected_version: int) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._meetings(session).revoke_share(principal, meeting_id, member_id, expected_version)
            session.commit()
            return result

    def list_notifications(self, principal: Principal) -> list[dict[str, object]]:
        with self._session_factory() as session:
            rows = SqlAlchemyNotificationRepository(session).list_for(str(principal.id))
            return [view for row in rows if (view := self._authorized_notification_view(session, principal, row)) is not None]

    def mark_notification_read(self, principal: Principal, notification_id: UUID) -> dict[str, object]:
        with self._session_factory() as session:
            repository = SqlAlchemyNotificationRepository(session)
            row = repository.for_recipient(notification_id, str(principal.id), lock=True)
            if row is None:
                raise NotificationNotFound("notification was not found")
            view = self._authorized_notification_view(session, principal, row)
            if view is None:
                raise NotificationNotFound("notification was not found")
            repository.mark_read(row)
            session.commit()
            return notification_view(
                row,
                title=str(view["resource"]["title"]),
                version=view["resource"]["version"],
            )

    def _authorized_notification_view(self, session: Any, principal: Principal, row: Any) -> dict[str, object] | None:
        try:
            if row.resource_type == "meeting":
                resource = self._meetings(session).get(principal, UUID(row.resource_id))
                title, version = resource["title"], resource["version"]
            elif row.resource_type == "work_request":
                resource = self._work_requests(session).get(principal, UUID(row.resource_id))
                title, version = resource["title"], resource["version"]
            else:
                return None
        except (MeetingError, WorkRequestError, ValueError):
            return None
        return notification_view(row, title=str(title), version=int(version))

    def create_meeting_note(self, principal: Principal, meeting_id: UUID, body: str) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._meetings(session).create_note(principal, meeting_id, body)
            self._register_note_material_from_view(session, result)
            session.commit()
            return result

    def save_meeting_note(self, principal: Principal, meeting_id: UUID, expected_version: int, body: str) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._meetings(session).save_note(principal, meeting_id, expected_version, body)
            self._register_note_material_from_view(session, result)
            session.commit()
            return result

    def finalize_meeting_note(self, principal: Principal, meeting_id: UUID, expected_version: int) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._meetings(session).finalize_note(principal, meeting_id, expected_version)
            self._register_note_material_from_view(session, result)
            session.commit()
            return result

    def start_meeting_recording(self, principal: Principal, meeting_id: UUID, purpose: str) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._meetings(session).start_recording(principal, meeting_id, purpose)
            session.commit()
            return result

    def stop_meeting_recording(
        self,
        principal: Principal,
        meeting_id: UUID,
        recording_id: UUID,
        expected_version: int,
        *,
        original_name: str,
        content_type: str,
        data: bytes,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._meetings(session).stop_recording(
                principal,
                meeting_id,
                recording_id,
                expected_version,
                original_name=original_name,
                content_type=content_type,
                data=data,
            )
            NativeMaterialRepository(session).ensure_recording(recording_id)
            self._meeting_queue(session).enqueue(MeetingFinalizationJob(recording_id))
            session.commit()
            return result

    def meeting_realtime_credential(
        self,
        principal: Principal,
        meeting_id: UUID,
        recording_id: UUID,
        max_session_duration_seconds: int,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            return self._meetings(session).issue_realtime_credential(
                principal,
                meeting_id,
                recording_id,
                max_session_duration_seconds,
            )

    def append_meeting_live_transcript(
        self, principal: Principal, meeting_id: UUID, recording_id: UUID, segments: list[Any]
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._meetings(session).append_live_transcript(principal, meeting_id, recording_id, segments)
            session.commit()
            return result

    def record_final_meeting_transcript(
        self,
        *,
        recording_id: UUID,
        provider: str,
        provider_reference: str,
        segments: list[FinalTranscriptSegment],
        finalization_lease_token: UUID | None = None,
    ) -> dict[str, Any]:
        """Internal worker seam; no HTTP caller can assert STT provenance."""
        with self._session_factory() as session:
            result = self._meetings(session).record_final_transcript(
                recording_id=recording_id,
                provider=provider,
                provider_reference=provider_reference,
                segments=segments,
                finalization_lease_token=finalization_lease_token,
            )
            self._register_native_material(session, "meeting_raw", UUID(result["transcript_revision_id"]))
            session.commit()
            return result

    def meeting_finalization_input(
        self,
        recording_id: UUID,
        *,
        lease_token: UUID,
        stale_after_seconds: int,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._meetings(session).finalization_input(
                recording_id,
                lease_token=lease_token,
                stale_after_seconds=stale_after_seconds,
            )
            session.commit()
            return result

    def fail_meeting_finalization(self, recording_id: UUID, *, lease_token: UUID, code: str) -> None:
        with self._session_factory() as session:
            self._meetings(session).fail_finalization(recording_id, lease_token=lease_token, code=code)
            session.commit()

    def retry_meeting_finalization(self, recording_id: UUID, *, lease_token: UUID, code: str) -> None:
        with self._session_factory() as session:
            self._meetings(session).retry_finalization(recording_id, lease_token=lease_token, code=code)
            session.commit()

    def record_meeting_finalization_cleanup_warning(
        self,
        recording_id: UUID,
        *,
        lease_token: UUID,
        warnings: tuple[str, ...],
    ) -> None:
        with self._session_factory() as session:
            self._meetings(session).record_finalization_cleanup_warning(
                recording_id,
                lease_token=lease_token,
                warnings=warnings,
            )
            session.commit()

    def complete_meeting_finalization(self, recording_id: UUID, *, lease_token: UUID) -> None:
        with self._session_factory() as session:
            self._meetings(session).complete_finalization(recording_id, lease_token=lease_token)
            session.commit()

    def refine_meeting_transcript(
        self,
        transcript_id: UUID,
        *,
        finalization_lease_token: UUID | None = None,
    ) -> dict[str, Any]:
        """Worker operation: read/claim plan (tx), call the provider (no tx), persist a derived revision (tx)."""
        with self._session_factory() as session:
            plan = self._meetings(session).refinement_input(transcript_id)
            session.commit()
        completed = plan.get("completed")
        if completed is not None:
            with self._session_factory() as session:
                self._register_native_material(session, "meeting_refinement", UUID(completed["refinement_revision_id"]))
                session.commit()
            return completed
        generation = self._report_provider.generate(
            AiGenerationRequest(
                prompt=build_refinement_prompt(plan["raw_segments"]),
                output_schema=refinement_output_schema(),
            )
        )
        segments = parse_refinement(generation.body)
        with self._session_factory() as session:
            result = self._meetings(session).save_refinement(
                transcript_id,
                provider_call_ref=generation.provider_run_ref,
                content_hash=hashlib.sha256(generation.body.encode("utf-8")).hexdigest(),
                segments=segments,
                finalization_lease_token=finalization_lease_token,
            )
            self._register_native_material(session, "meeting_refinement", UUID(result["refinement_revision_id"]))
            session.commit()
            return result

    def summarize_meeting_transcript(
        self,
        refinement_id: UUID,
        *,
        kind: str = "final",
        finalization_lease_token: UUID | None = None,
    ) -> dict[str, Any]:
        """Worker operation with a provider call outside every database transaction."""
        with self._session_factory() as session:
            plan = self._meetings(session).summary_input(refinement_id, kind=kind)
            session.commit()
        completed = plan.get("completed")
        if completed is not None:
            return completed
        generation = self._report_provider.generate(
            AiGenerationRequest(
                prompt=build_summary_prompt(plan["segments"], kind=kind),
                output_schema=summary_output_schema(),
            )
        )
        body, statements = parse_summary(generation.body)
        with self._session_factory() as session:
            result = self._meetings(session).save_summary(
                refinement_id,
                kind=kind,
                body=body,
                provider_call_ref=generation.provider_run_ref,
                content_hash=hashlib.sha256(generation.body.encode("utf-8")).hexdigest(),
                statements=statements,
                finalization_lease_token=finalization_lease_token,
            )
            session.commit()
            return result

    def adopt_meeting_summary(
        self,
        principal: Principal,
        meeting_id: UUID,
        summary_id: UUID,
        expected_version: int,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._meetings(session).adopt_summary(
                principal,
                meeting_id,
                summary_id,
                expected_version,
            )
            self._register_note_material_from_view(session, result.get("note"))
            session.commit()
            return result

    def assign_meeting_speaker_identity(
        self,
        principal: Principal,
        meeting_id: UUID,
        transcript_revision_id: UUID,
        **fields: Any,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._meetings(session).assign_speaker_identity(
                principal,
                meeting_id,
                transcript_revision_id,
                **fields,
            )
            session.commit()
            return result

    def organization_tree(self, principal: Principal) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return OrganizationApplication(SqlAlchemyOrganizationRepository(session)).organization_tree(principal)

    def organization_unit_members(self, principal: Principal, unit_id: str) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return OrganizationApplication(SqlAlchemyOrganizationRepository(session)).unit_members(principal, unit_id)

    def organization_member_detail(self, principal: Principal, member_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            return OrganizationApplication(SqlAlchemyOrganizationRepository(session)).member_detail(principal, member_id)

    def organization_member_history(self, principal: Principal, member_id: str, axis: str) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return OrganizationApplication(SqlAlchemyOrganizationRepository(session)).member_axis_history(
                principal, member_id, axis
            )

    def organization_activity(
        self, principal: Principal, *, unit_id: str | None = None, limit: int = 50, cursor: str | None = None
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return OrganizationApplication(SqlAlchemyOrganizationRepository(session)).organization_activity(
                principal, unit_id=unit_id, limit=limit, cursor=cursor
            )

    def my_organization_profile(self, principal: Principal) -> dict[str, Any]:
        with self._session_factory() as session:
            return OrganizationApplication(SqlAlchemyOrganizationRepository(session)).my_profile(principal)

    def set_assistant_character(
        self, principal: Principal, character_key: str, expected_version: int
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = OrganizationApplication(SqlAlchemyOrganizationRepository(session)).set_assistant_character(
                principal, character_key, expected_version
            )
            session.commit()
            return result

    def authenticated_principal(self, member_id: str) -> Principal:
        with self._session_factory() as session:
            return OrganizationApplication(SqlAlchemyOrganizationRepository(session)).authenticated_principal(member_id)

    def authenticate_with_password(self, email: str, password: str) -> Principal:
        with self._session_factory() as session:
            return OrganizationApplication(SqlAlchemyOrganizationRepository(session)).authenticate_with_password(email, password)

    def installed_access_roles(self, principal: Principal) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return AccessAdministration(SqlAlchemyOrganizationRepository(session)).installed_roles(principal)

    def member_access(self, principal: Principal, member_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            return AccessAdministration(SqlAlchemyOrganizationRepository(session)).member_access(principal, member_id)

    def grant_access_role(
        self,
        principal: Principal,
        *,
        member_id: str,
        role_id: str,
        scope_kind: str = "unit",
        scope_ref: str | None = None,
        include_descendants: bool = True,
        reason: str | None = None,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = AccessAdministration(SqlAlchemyOrganizationRepository(session)).grant_role(
                principal,
                member_id=member_id,
                role_id=role_id,
                scope_kind=scope_kind,
                scope_ref=scope_ref,
                include_descendants=include_descendants,
                reason=reason,
            )
            session.commit()
            return result

    def revoke_access_grant(self, principal: Principal, grant_id: UUID, *, reason: str | None = None) -> dict[str, Any]:
        with self._session_factory() as session:
            result = AccessAdministration(SqlAlchemyOrganizationRepository(session)).revoke_grant(
                principal, grant_id, reason=reason
            )
            session.commit()
            return result

    def set_role_capabilities(
        self,
        principal: Principal,
        role_id: str,
        capabilities: list[str],
        *,
        expected_version: int,
        reason: str | None = None,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = AccessAdministration(SqlAlchemyOrganizationRepository(session)).set_role_capabilities(
                principal, role_id, capabilities, expected_version=expected_version, reason=reason
            )
            session.commit()
            return result

    def demo_accounts(self, email_domain: str) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return OrganizationApplication(SqlAlchemyOrganizationRepository(session)).demo_accounts(email_domain)

    def member_directory(self, principal: Principal) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return OrganizationApplication(SqlAlchemyOrganizationRepository(session)).member_directory(principal)

    def generate_daily_report_draft(
        self,
        principal: Principal,
        report_date: str,
        causation_key: str | None = None,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            try:
                result = self._reports(session).generate_draft(
                    principal,
                    report_date,
                    causation_key,
                )
                session.commit()
                return result
            except ProviderFailure:
                # A failed live call is operational evidence, not a synthetic success.
                session.commit()
                raise

    def edit_daily_report(
        self,
        principal: Principal,
        report_id: str,
        draft_id: str,
        expected_version: int,
        body: str,
        include_source_refs: list[dict[str, Any]],
        exclude_source_refs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._reports(session).edit(
                principal,
                report_id,
                draft_id,
                expected_version,
                body,
                include_source_refs,
                exclude_source_refs,
            )
            session.commit()
            return result

    def submit_daily_report(
        self,
        principal: Principal,
        report_id: str,
        draft_id: str,
        expected_version: int,
        reason: str | None,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._reports(session).submit(
                principal, report_id, draft_id, expected_version, reason
            )
            self._register_native_material(session, "report_submission", UUID(result["submission_id"]))
            session.commit()
            return result

    def daily_report_history(self, principal: Principal, report_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            return self._reports(session).history(principal, report_id)

    def daily_report_recent(self, principal: Principal, *, limit: int = 3) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            try:
                return self._reports(session).recent(principal, limit=limit)
            except DailyReportAccessDenied:
                # Someone who may not read reports has none to connect; that is an answer, not a failure.
                return []

    def daily_report_status(self, principal: Principal, report_date: str) -> dict[str, Any]:
        with self._session_factory() as session:
            return self._reports(session).status_for_date(principal, report_date)

    def _reports(self, session: Any) -> DailyReportApplication:
        return DailyReportApplication(
            SqlAlchemyDailyReportRepository(session),
            SqlAlchemyDailyReportDraftWorkflow(
                session,
                SqlAlchemyWorkRecordSource(session),
                self._report_provider,
            ),
        )

    def _meetings(self, session: Any) -> MeetingApplication:
        return MeetingApplication(
            SqlAlchemyMeetingRepository(session),
            self._recording_storage,
            self._soniox,
            SqlAlchemyAttachmentRepository(session),
            _SessionResourceReferences(self, session),
            self._material_storage,
            SqlAlchemyMaterialExtractionRepository(session),
            self._material_queue(session),
        )

    def create_self_task(
        self,
        principal: Principal,
        title: str,
        causation_key: str | None = None,
        *,
        description: str | None = None,
        start_date: Any = None,
        due_date: Any = None,
        checklist: list[str] | None = None,
        reference_task_ids: list[UUID] | None = None,
        parent_task_id: UUID | None = None,
        project_id: UUID | None = None,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._tasks(session).create_self(
                principal, title, causation_key,
                description=description, start_date=start_date, due_date=due_date, checklist=checklist,
                reference_task_ids=reference_task_ids, parent_task_id=parent_task_id, project_id=project_id,
            )
            session.commit()
            return result

    def update_task(self, principal: Principal, task_id: UUID, expected_version: int, changes: dict[str, Any]) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._tasks(session).update(task_id, principal, expected_version, changes)
            session.commit()
            return result

    def attach_task_material_reference(
        self, principal: Principal, task_id: UUID, *, kind: str, resource_type: str, resource_id: str
    ) -> dict[str, Any]:
        """Point a Task at another thing inside SCAX, resolved through that thing's own authorization."""
        with self._session_factory() as session:
            result = self._materials(session).attach_reference(
                principal, task_id, kind=kind, resource_type=resource_type, resource_id=resource_id
            )
            session.commit()
            return result

    def attach_task_material_link(self, principal: Principal, task_id: UUID, *, kind: str, url: str, label: str) -> dict[str, Any]:
        """Point a Task at work that lives somewhere else. No bytes are held and no revision is pinned."""
        with self._session_factory() as session:
            result = self._materials(session).attach_link(principal, task_id, kind=kind, url=url, label=label)
            session.commit()
            return result

    def add_task_reference(self, principal: Principal, task_id: UUID, referenced_task_id: UUID) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._tasks(session).add_reference(principal, task_id, referenced_task_id)
            session.commit()
            return result

    def release_task_reference(self, principal: Principal, task_id: UUID, reference_id: UUID) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._tasks(session).release_reference(principal, task_id, reference_id)
            session.commit()
            return result

    def submit_task_completion(
        self, principal: Principal, task_id: UUID, expected_version: int, *, summary: str, output_material_ids: list[UUID] | None = None
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._tasks(session).submit_completion(
                principal, task_id, expected_version, summary=summary, output_material_ids=output_material_ids
            )
            session.commit()
            return result

    def promote_meeting_followup(
        self,
        principal: Principal,
        meeting_id: UUID,
        summary_id: UUID,
        statement_index: int,
        *,
        kind: str,
        title: str | None = None,
        assignee_id: str | None = None,
    ) -> dict[str, Any]:
        """Turn a followup candidate into ordinary work, once, keeping the meeting it came from as its context.

        The meeting module decides whether this person may act on this statement; the work modules make the work with
        their own rules. Nothing here creates a second path into either ledger.
        """
        with self._session_factory() as session:
            meetings = self._meetings(session)
            candidate = meetings.followup_candidate(principal, meeting_id, summary_id, statement_index)
            existing = candidate["promotion"]
            if existing is not None:
                return {
                    "already_promoted": True,
                    "task": self._tasks(session).get(principal, existing.task_id) if existing.task_id else None,
                    "work_request": (
                        self._work_requests(session).get(principal, existing.work_request_id)
                        if existing.work_request_id
                        else None
                    ),
                }
            wanted = " ".join(str(title or candidate["statement"].statement_text).split())[:300]
            if kind == "work_request":
                if not assignee_id:
                    raise MeetingError("요청으로 만들려면 담당 후보가 필요합니다")
                created = self._work_requests(session).create(principal, wanted, assignee_id)
                meetings.record_followup_promotion(principal, candidate, work_request_id=UUID(created["request_id"]))
                session.commit()
                return {"already_promoted": False, "work_request": created, "task": None}
            if kind != "task":
                raise MeetingError("후속 업무는 내 업무 또는 업무 요청으로만 만들 수 있습니다")
            created = self._tasks(session).create_self(principal, wanted)
            # The meeting travels with the work as the context it came from, through the ordinary material reference.
            self._materials(session).attach_reference(
                principal, UUID(created["task_id"]), kind="input", resource_type="meeting", resource_id=str(meeting_id)
            )
            meetings.record_followup_promotion(principal, candidate, task_id=UUID(created["task_id"]))
            session.commit()
            return {"already_promoted": False, "task": self._tasks(session).get(principal, UUID(created["task_id"])), "work_request": None}

    def reextract_material(self, *, attachment_id: UUID, parser_version: str) -> dict[str, Any]:
        """Read a file again with a newer parser. The older reading is superseded, never rewritten."""
        with self._session_factory() as session:
            attachment = session.get(AttachmentRecord, attachment_id)
            if attachment is None:
                raise MaterialNotFound("attachment was not found")
            extractions = SqlAlchemyMaterialExtractionRepository(session)
            extraction = extractions.request(attachment, parser_version=parser_version)
            self._material_queue(session).enqueue(MaterialExtractionJob(extraction.id, attachment.id))
            session.commit()
            return {"extraction_id": str(extraction.id), "parser_version": parser_version}

    def purge_attachment(self, principal: Principal, attachment_id: UUID, *, reason: str) -> dict[str, Any]:
        """Destroy a file's content everywhere, keeping the fact that answers once cited it.

        Bytes, blocks and chunks go. The evidence rows a delegated turn recorded stay, with their excerpts blanked:
        a person can still see that an answer stood on this file, and nobody can read what it said.
        """
        if "material.purge" not in principal.capabilities:
            raise MaterialError("자료를 완전히 삭제할 권한이 없습니다")
        if not str(reason or "").strip():
            raise MaterialError("삭제 사유가 필요합니다")
        with self._session_factory() as session:
            attachment = session.scalar(select(AttachmentRecord).where(AttachmentRecord.id == attachment_id).with_for_update())
            if attachment is None:
                raise MaterialNotFound("attachment was not found")
            if attachment.source_kind in {"native_revision", "native_recording"}:
                raise MaterialError("native revision content must be managed through its owning resource")
            extraction_ids = [
                row.id
                for row in session.scalars(
                    select(MaterialExtractionRecord).where(MaterialExtractionRecord.attachment_id == attachment.id)
                )
            ]
            if extraction_ids:
                session.execute(delete(MaterialChunkRecord).where(MaterialChunkRecord.extraction_id.in_(extraction_ids)))
                session.execute(delete(MaterialBlockRecord).where(MaterialBlockRecord.extraction_id.in_(extraction_ids)))
                for row in session.scalars(
                    select(MaterialExtractionRecord).where(MaterialExtractionRecord.id.in_(extraction_ids))
                ):
                    row.status = "purged"
                    row.chunk_count = 0
                    row.char_count = 0
            for evidence in session.scalars(select(ConversationContentEvidenceRecord).where(ConversationContentEvidenceRecord.attachment_id == attachment.id)):
                evidence.excerpt = ""
                evidence.header_context = None
            if attachment.source_kind == "file":
                self._material_storage.delete(attachment.source_ref)
            attachment.lifecycle = "purged"
            ActivityLedger(session).record(
                target_type="attachment",
                target_id=str(attachment.id),
                event_kind="attachment.purged",
                actor_id=str(principal.id),
                reason=str(reason).strip()[:500],
                safe_summary=f"자료 완전 삭제: {attachment.name}",
            )
            session.commit()
            return {"attachment_id": str(attachment.id), "state": "purged"}

    def _graph(self, session: Any) -> GraphApplication:
        return GraphApplication(_SessionGraphSource(self, session))

    def graph_overview(self, principal: Principal, *, view: str = "member", limit: int = 120) -> dict[str, Any]:
        with self._session_factory() as session:
            return GraphApplication(_SessionGraphSource(self, session)).overview(principal, view=view, limit=limit)

    def graph_search(self, principal: Principal, query: str, limit: int = 20, *, execution_id: UUID | None = None) -> dict[str, Any]:
        """Find work to walk from. In a delegated turn the hits become that turn's own record of what it looked at."""
        with self._session_factory() as session:
            result = self._graph(session).search(principal, query, limit=limit)
            if execution_id is not None and result["nodes"]:
                SqlAlchemyGraphReceiptRepository(session).record(
                    execution_id,
                    str(principal.id),
                    [
                        {"kind": "node", "node_ref": f"{row['kind']}:{row['id']}", "node_title": row["title"]}
                        for row in result["nodes"]
                    ],
                )
                session.commit()
            return result

    def graph_neighbors(self, principal: Principal, node: str, limit: int = 20, *, execution_id: UUID | None = None) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._graph(session).neighbors(principal, node, limit=limit)
            if execution_id is not None and result["edges"]:
                titles = {f"{row['kind']}:{row['id']}": row["title"] for row in result["nodes"]}
                SqlAlchemyGraphReceiptRepository(session).record(
                    execution_id,
                    str(principal.id),
                    [
                        {
                            "kind": "edge",
                            "edge_kind": row["kind"],
                            "from_ref": row["from"],
                            "from_title": titles.get(row["from"]),
                            "to_ref": row["to"],
                            "to_title": titles.get(row["to"]),
                            "source_contexts": row.get("source_contexts"),
                            "integrity_ref": row.get("integrity_ref"),
                        }
                        for row in result["edges"]
                    ],
                )
                session.commit()
            return result

    def conversation_context_pack(
        self,
        principal: Principal,
        conversation_id: UUID,
        *,
        include_exchanges: bool,
        seeds: int = 12,
        exchanges: int = 6,
    ) -> dict[str, Any]:
        """What this conversation can stand on without asking the provider to remember anything.

        The seeds are canonical ids earlier turns actually read, asked of their owning modules again for whoever is
        asking now — a follow-up that says "그중" starts from these rather than from a provider checkpoint. The
        exchanges are a bounded retelling of what was said, used when there is no checkpoint at all.
        """
        with self._session_factory() as session:
            conversations = SqlAlchemyConversationRepository(
                session,
                ConversationJobQueue(self.job_queue(session)),
                self._settings.conversation_queue_max_fragments,
            )
            conversation = conversations.conversation(conversation_id, str(principal.id))
            if conversation is None:
                return {"seeds": [], "exchanges": []}
            view = conversations.view(conversation)
            resolver = _SessionAnswerResources(self, session)
            observed_materials = list(view.get("material_evidence") or [])
            readable_materials = resolver.readable_material_evidence(principal, observed_materials)
            content_observed = bool(observed_materials) or any(tool.get("tool_name") == "material_search"
                for tool in view.get("tool_invocations") or [])
            references = list(view.get("answer_resources") or [])
            for step in view.get("graph_receipts") or []:
                for ref in (step.get("node_ref"), step.get("from_ref"), step.get("to_ref")):
                    if not ref or ":" not in str(ref):
                        continue
                    kind, _, identifier = str(ref).partition(":")
                    references.append({"resource_type": kind, "resource_id": identifier, "resource_version": None,
                        "turn_id": step["turn_id"], "source_contexts": step.get("source_contexts") or [],
                        "integrity_ref": step.get("integrity_ref")})
            seen: set[tuple[str, str]] = set()
            deduped = []
            for reference in resolver.resolve(principal, references):
                key = (str(reference["resource_type"]), str(reference["resource_id"]))
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(reference)
            resolved = deduped[-seeds:]
            pack: dict[str, Any] = {
                "seeds": [
                    {
                        "ref": f"{row['resource_type']}:{row['resource_id']}",
                        "title": str(row["title"]),
                        "version": str(row.get("resource_version") or ""),
                    }
                    for row in resolved
                ],
                "exchanges": [],
                "reset_provider_session": content_observed,
            }
            material_seeds = {}
            for row in readable_materials:
                material_seeds[row["material_id"]] = {"ref": f"material:{row['material_id']}", "title": row["name"], "version": row["integrity_ref"]}
            pack["seeds"] = (pack["seeds"] + list(material_seeds.values()))[-seeds:]
            if include_exchanges or content_observed:
                pack["exchanges"] = [
                    {
                        "turn_id": str(message["turn_id"]),
                        "role": str(message["role"]),
                        "body": str(message["body"])[:400],
                    }
                    for message in [
                        message
                        for message in view.get("messages") or []
                        if not content_observed or message["role"] == "user"
                    ][-exchanges:]
                    if message.get("turn_id") and str(message.get("body") or "").strip()
                ]
            return pack

    def record_answer_resources(
        self,
        principal: Principal,
        execution_id: UUID,
        references: list[dict[str, Any]],
    ) -> int:
        """Keep the canonical things a delegated turn just read, so its answer can point at each of them.

        Only ids and versions the authorized read already returned are kept. Nothing here decides what may be shown:
        that is asked again, of the owning module, every time the conversation is read.
        """
        if not references:
            return 0
        with self._session_factory() as session:
            written = SqlAlchemyGraphReceiptRepository(session).record_resources(
                execution_id, str(principal.id), references
            )
            session.commit()
            return written

    def task_history(self, principal: Principal, task_id: UUID) -> dict[str, Any]:
        with self._session_factory() as session:
            return self._tasks(session).history(principal, task_id)

    def task_history_diff(self, principal: Principal, task_id: UUID, before: int, after: int) -> dict[str, Any]:
        with self._session_factory() as session:
            return self._tasks(session).history_diff(principal, task_id, before, after)

    def list_task_materials(self, principal: Principal, task_id: UUID) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return self._materials(session).list(principal, task_id)

    def attach_task_material(
        self, principal: Principal, task_id: UUID, *, kind: str, name: str, content_type: str, data: bytes
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._materials(session).attach(principal, task_id, kind=kind, name=name, content_type=content_type, data=data)
            session.commit()
            return result

    def open_task_material(self, principal: Principal, task_id: UUID, material_id: UUID) -> tuple[dict[str, Any], bytes]:
        with self._session_factory() as session:
            return self._materials(session).open(principal, task_id, material_id)

    def detach_task_material(self, principal: Principal, task_id: UUID, binding_id: UUID) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._materials(session).detach(principal, task_id, binding_id)
            session.commit()
            return result

    def search_materials(self, principal: Principal, query: str, *, execution_id: UUID | None = None, **filters: Any) -> dict[str, Any]:
        with self._session_factory() as session:
            extractions = SqlAlchemyMaterialExtractionRepository(session)
            result = MaterialSearchApplication(SessionMaterialOwners(self, session), extractions,
                                               LexicalMaterialRetriever(extractions, SqlChunkIndex(session)),
                                               self._material_queue(session)).search(principal, query, **filters)
            if execution_id is not None:
                SqlAlchemyMaterialEvidenceRepository(session).record(execution_id, str(principal.id), result["query"], result["results"])
            session.commit()
            return result

    def material_metadata(self, principal: Principal, material_id: UUID) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._material_metadata(session, principal, material_id)
            session.commit()
            return result

    def _material_metadata(self, session: Any, principal: Principal, material_id: UUID) -> dict[str, Any]:
        sources = SessionMaterialOwners(self, session).sources(principal, set(RESOURCE_TYPES), material_id=material_id)
        sources = [source for source in sources if source.attachment.id == material_id and source.attachment.lifecycle != "purged"]
        if not sources:
            raise MaterialNotFound("material was not found")
        attachment = sources[0].attachment
        contexts = sorted((source.context for source in sources), key=lambda row: (row["resource_type"], row["resource_id"], row["binding_id"]))
        extraction = SqlAlchemyMaterialExtractionRepository(session).for_attachments([material_id]).get(material_id)
        return {"material_id": str(material_id), "name": self._material_name(session, principal, attachment), "content_type": attachment.content_type,
                "size_bytes": attachment.size_bytes, "integrity_ref": attachment.integrity_ref, "state": attachment.lifecycle,
                "source_contexts": contexts, "origin": contexts[0]["origin"], "extraction": extraction_view(extraction)}

    def _material_name(self, session: Any, principal: Principal, attachment: Any) -> str:
        if attachment.source_kind == "resource_ref":
            kind, _, identifier = str(attachment.source_ref).partition(":")
            return _SessionResourceReferences(self, session).title(principal, kind, identifier) or "볼 수 없는 자료"
        return attachment.name

    def _material_folders(self, session: Any) -> MaterialFolderApplication:
        return MaterialFolderApplication(SqlAlchemyMaterialFolderRepository(session),
                                         OrganizationApplication(SqlAlchemyOrganizationRepository(session)),
                                         SqlAlchemyAttachmentRepository(session), self._material_storage,
                                         SqlAlchemyMaterialExtractionRepository(session), self._material_queue(session))

    def _register_native_material(self, session: Any, kind: str, revision_id: UUID) -> None:
        binding, attachment = NativeMaterialRepository(session).ensure(kind, revision_id)
        if attachment.lifecycle == "purged" or binding.unbound_at is not None:
            return
        extraction = SqlAlchemyMaterialExtractionRepository(session).request(attachment)
        if extraction.status == "queued":
            self._material_queue(session).enqueue(MaterialExtractionJob(extraction.id, attachment.id))

    def _register_note_material_from_view(self, session: Any, note: dict[str, Any] | None) -> None:
        if not note:
            return
        current = next((version for version in note.get("versions", []) if version.get("version") == note.get("version")), None)
        if current is not None:
            self._register_native_material(session, "meeting_note", UUID(current["version_id"]))

    def open_meeting_material(self, principal: Principal, meeting_id: UUID, material_id: UUID) -> tuple[dict[str, Any], bytes]:
        return self._open_native_material(principal, "meeting", meeting_id, material_id)

    def open_report_material(self, principal: Principal, report_id: UUID, material_id: UUID) -> tuple[dict[str, Any], bytes]:
        return self._open_native_material(principal, "report", report_id, material_id)

    def _open_native_material(self, principal: Principal, resource_type: str, resource_id: UUID, material_id: UUID) -> tuple[dict[str, Any], bytes]:
        with self._session_factory() as session:
            sources = SessionMaterialOwners(self, session).sources(principal, {resource_type}, resource_type=resource_type, resource_id=str(resource_id), material_id=material_id)
            attachment = next((source.attachment for source in sources if source.attachment.id == material_id), None)
            if attachment is None:
                raise MaterialNotFound("material was not found")
            if attachment.source_kind == "native_recording":
                recording = NativeMaterialRepository(session).recording_source(UUID(attachment.source_ref.rsplit(":", 1)[-1]))
                data = self._recording_storage.get(recording.storage_key)
            elif attachment.source_kind == "native_revision":
                data = NativeRevisionStorage(self._session_factory).get(attachment.source_ref)
            elif attachment.source_kind == "file":
                if attachment.lifecycle == "purged":
                    raise MaterialNotFound("이 자료는 완전히 삭제되어 더 이상 내려받을 수 없습니다")
                try:
                    data = self._material_storage.get(attachment.source_ref)
                except FileNotFoundError as error:
                    raise MaterialNotFound("자료 원본을 찾을 수 없습니다") from error
            else:
                raise MaterialError("only file attachments have downloadable content")
            if f"sha256:{hashlib.sha256(data).hexdigest()}" != attachment.integrity_ref:
                raise MaterialError("native revision integrity does not match the registered material")
            result = {"material_id": str(attachment.id), "name": attachment.name, "content_type": attachment.content_type}
            session.commit()
            return result, data

    def create_material_folder(self, principal: Principal, **fields: Any) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._material_folders(session).create(principal, **fields)
            session.commit()
            return result

    def list_material_folders(self, principal: Principal) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return self._material_folders(session).list_for(principal)

    def list_folder_materials(self, principal: Principal, folder_id: UUID) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return self._material_folders(session).materials(principal, folder_id)

    def upload_folder_material(self, principal: Principal, folder_id: UUID, **fields: Any) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._material_folders(session).upload(principal, folder_id, **fields)
            session.commit()
            return result

    def open_folder_material(self, principal: Principal, folder_id: UUID, material_id: UUID) -> tuple[dict[str, Any], bytes]:
        with self._session_factory() as session:
            return self._material_folders(session).open(principal, folder_id, material_id)

    def detach_folder_material(self, principal: Principal, folder_id: UUID, material_id: UUID) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._material_folders(session).detach(principal, folder_id, material_id)
            session.commit()
            return result

    def archive_material_folder(self, principal: Principal, folder_id: UUID) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._material_folders(session).archive(principal, folder_id)
            session.commit()
            return result

    def reindex_material_search(self, *, limit: int = 500) -> int:
        """분석 규칙이 바뀌었을 때 옛 색인을 다시 만든다. 여러 번 불러도 한 번 부른 것과 같다."""
        with self._session_factory() as session:
            written = reindex_stale_chunks(session, limit=limit)
            session.commit()
            return written

    def job_queue(self, session: Any):
        """The shared durable job transport bound to this session (postgres) or this application (memory)."""
        return build_job_queue(self._settings.job_queue_backend, session, self.memory_job_queue)

    def _meeting_queue(self, session: Any) -> MeetingFinalizationQueue:
        return MeetingFinalizationQueue(self.job_queue(session))

    def _material_queue(self, session: Any) -> MaterialJobQueue:
        return MaterialJobQueue(self.job_queue(session))

    def _materials(self, session: Any) -> TaskMaterialApplication:
        extractions = SqlAlchemyMaterialExtractionRepository(session)
        return TaskMaterialApplication(
            SqlAlchemyTaskRepository(session),
            SqlAlchemyAttachmentRepository(session),
            self._material_storage,
            extractions,
            self._material_queue(session),
            _SessionResourceReferences(self, session),
            _SessionReadableWork(self, session),
        )

    def reassign_task(self, principal: Principal, task_id: UUID, expected_version: int, assignee_id: str, reason: str | None = None) -> dict[str, Any]:
        """Put someone else on work that is already underway. Its own command, never a Task field edit."""
        with self._session_factory() as session:
            result = self._assignments(session).reassign(principal, task_id, expected_version, assignee_id, reason)
            session.commit()
            return result

    def assign_task(self, principal: Principal, title: str, assignee_id: str, **fields: Any) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._assignments(session).assign(principal, title, assignee_id, **fields)
            session.commit()
            return result

    def task_assignment_candidates(self, principal: Principal) -> list[dict[str, str]]:
        with self._session_factory() as session:
            return self._assignments(session).candidates(principal)

    def plan_project_work(self, principal: Principal, project_id: UUID, title: str, **fields: Any) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._assignments(session).plan_project_work(principal, project_id, title, **fields)
            session.commit()
            return result

    def task_assignment_inbox(self, principal: Principal) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return self._assignments(session).inbox(principal)

    def sent_task_assignments(self, principal: Principal) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return self._assignments(session).sent(principal)

    def accept_task_assignment(self, principal: Principal, assignment_id: UUID) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._assignments(session).accept(principal, assignment_id)
            session.commit()
            return result

    def decline_task_assignment(self, principal: Principal, assignment_id: UUID, reason: str) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._assignments(session).decline(principal, assignment_id, reason)
            session.commit()
            return result

    def _assignments(self, session: Any) -> TaskAssignmentApplication:
        return TaskAssignmentApplication(
            SqlAlchemyTaskAssignmentRepository(session),
            OrganizationApplication(SqlAlchemyOrganizationRepository(session)),
            self._tasks(session),
            self._projects(session),
        )

    # ---- 프로젝트: 부서를 가로질러 묶이는 일 ----

    def create_project(self, principal: Principal, **fields: Any) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._projects(session).create(principal, **fields)
            session.commit()
            return result

    def list_projects(self, principal: Principal) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return self._projects(session).list(principal)

    def get_project(self, principal: Principal, project_id: UUID) -> dict[str, Any]:
        with self._session_factory() as session:
            return self._projects(session).get(principal, project_id)

    def project_participation_history(self, principal: Principal, project_id: UUID) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return self._projects(session).participation_history(principal, project_id)

    def assign_to_project(self, principal: Principal, project_id: UUID, member_id: str, **fields: Any) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._projects(session).assign(principal, project_id, member_id, **fields)
            session.commit()
            return result

    def release_from_project(
        self,
        principal: Principal,
        project_id: UUID,
        member_id: str,
        *,
        assignment_id: UUID | None = None,
        reason: str | None = None,
    ) -> None:
        with self._session_factory() as session:
            self._projects(session).release(
                principal,
                project_id,
                member_id,
                assignment_id=assignment_id,
                reason=reason,
            )
            session.commit()

    def list_tasks(
        self, principal: Principal, *, include_closed: bool = False, include_organization: bool = True
    ) -> list[dict[str, Any]]:
        """이 사람이 읽을 수 있는 업무. `include_organization=False`면 자기가 든 것까지다.

        둘은 다른 질문이라 부르는 쪽이 고른다 — 읽을 수 있다는 것과 자기가 해야 한다는 것은 같지 않다.
        """
        with self._session_factory() as session:
            return self._tasks(session).list_for(
                principal, include_closed=include_closed, include_organization=include_organization
            )

    def get_task(self, principal: Principal, task_id: UUID) -> dict[str, Any]:
        with self._session_factory() as session:
            return self._tasks(session).get(principal, task_id)

    def create_work_request(
        self,
        principal: Principal,
        title: str,
        assignee_id: str,
        causation_key: str | None = None,
        *,
        description: str | None = None,
        due_date: Any = None,
        cc_member_ids: list[str] | None = None,
        checklist: list[str] | None = None,
        reference_task_ids: list[UUID] | None = None,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._work_requests(session).create(
                principal, title, assignee_id, causation_key,
                description=description, due_date=due_date, cc_member_ids=cc_member_ids, checklist=checklist,
                reference_task_ids=reference_task_ids,
            )
            session.commit()
            return result

    def work_request_assignee_candidates(self, principal: Principal) -> list[dict[str, str]]:
        with self._session_factory() as session:
            return self._work_requests(session).assignee_candidates(principal)

    def list_work_requests(self, principal: Principal) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return self._work_requests(session).list(principal)

    def get_work_request(self, principal: Principal, request_id: UUID) -> dict[str, Any]:
        with self._session_factory() as session:
            return self._work_requests(session).get(principal, request_id)

    def accept_work_request(
        self, principal: Principal, request_id: UUID, expected_version: int
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._work_requests(session).accept(
                principal, request_id, expected_version
            )
            session.commit()
            return result

    def reject_work_request(
        self, principal: Principal, request_id: UUID, expected_version: int, reason: str
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._work_requests(session).reject(
                principal, request_id, expected_version, reason
            )
            session.commit()
            return result

    def resubmit_work_request(self, principal: Principal, request_id: UUID, expected_version: int, **changes: Any) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._work_requests(session).resubmit(principal, request_id, expected_version, **changes)
            session.commit()
            return result

    def amend_work_request(self, principal: Principal, request_id: UUID, expected_version: int, **changes: Any) -> dict[str, Any]:
        """The requester's own improvement to a request nobody has judged yet."""
        with self._session_factory() as session:
            result = self._work_requests(session).amend(principal, request_id, expected_version, **changes)
            session.commit()
            return result

    def work_request_cc_candidates(self, principal: Principal) -> list[dict[str, str]]:
        with self._session_factory() as session:
            return self._work_requests(session).cc_candidates(principal)

    def attach_to_work_request_comment(self, principal: Principal, request_id: UUID, comment_id: UUID, **file: Any) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._work_requests(session).attach_to_comment(principal, request_id, comment_id, **file)
            session.commit()
            return result

    def add_work_request_evidence(self, principal: Principal, request_id: UUID, **file: Any) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._work_requests(session).add_evidence(principal, request_id, **file)
            session.commit()
            return result

    def open_work_request_attachment(self, principal: Principal, request_id: UUID, attachment_id: UUID) -> tuple[dict[str, Any], bytes]:
        with self._session_factory() as session:
            return self._work_requests(session).open_attachment(principal, request_id, attachment_id)

    def add_work_request_comment(self, principal: Principal, request_id: UUID, body: str, idempotency_key: str | None = None) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._work_requests(session).add_comment(principal, request_id, body, idempotency_key=idempotency_key)
            session.commit()
            return result

    def pending_action_items(self, principal: Principal) -> list[dict[str, Any]]:
        """Every judgement this principal owes right now, whatever raised it."""
        with self._session_factory() as session:
            return self._action_center(session).pending(principal)

    def action_item_detail(self, principal: Principal, action_item_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            return self._action_center(session).detail(principal, action_item_id)

    def normalize_action_command(self, principal: Principal, action_item_id: str, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        """The canonical form of a command, for a caller that must store or compare it before it runs."""
        with self._session_factory() as session:
            return self._action_center(session).normalize(principal, action_item_id, command, payload)

    def run_action_command(self, principal: Principal, action_item_id: str, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._action_center(session).execute(principal, action_item_id, command, payload)
            session.commit()
            return result

    def stage_action_material_link(
        self, principal: Principal, action_item_id: UUID, *, url: str, label: str
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._action_materials(session).stage_link(principal, action_item_id, url=url, label=label)
            session.commit()
            return result

    def stage_action_material_file(
        self,
        principal: Principal,
        action_item_id: UUID,
        *,
        name: str,
        content_type: str,
        data: bytes,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            reserved = self._action_materials(session).reserve_file(
                principal, action_item_id, name=name, content_type=content_type, data=data
            )
            session.commit()
        draft_id = UUID(reserved["material_draft_id"])
        try:
            self._material_storage.put(str(reserved["_storage_key"]), data, content_type or "application/octet-stream")
            with self._session_factory() as session:
                result = self._action_materials(session).complete_file(principal, action_item_id, draft_id)
                session.commit()
                return result
        except Exception as error:
            # The reservation is intentionally durable before storage I/O. If the
            # database is temporarily unavailable here, leave it as ``uploading``
            # so expiry reconciliation can still discover it; do not let cleanup
            # failure mask the upload error or prevent best-effort byte removal.
            try:
                with self._session_factory() as session:
                    self._action_materials(session).fail_file(principal, action_item_id, draft_id)
                    session.commit()
            except Exception:
                pass
            try:
                self._material_storage.delete(str(reserved["_storage_key"]))
            except Exception:
                pass
            if isinstance(error, ActionMaterialError):
                raise
            raise ActionMaterialError("file upload failed") from error

    def discard_action_material_draft(
        self, principal: Principal, action_item_id: UUID, material_draft_id: UUID
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._action_materials(session).discard(principal, action_item_id, material_draft_id)
            session.commit()
            return result

    def reconcile_action_material_drafts(self, *, limit: int = 100) -> int:
        with self._session_factory() as session:
            cleaned = self._action_materials(session).reconcile(limit=limit)
            session.commit()
            return cleaned

    def _action_center(self, session: Any) -> ActionCenterApplication:
        work_requests = self._work_requests(session)
        return ActionCenterApplication(
            action_handlers(
                session,
                evidence_reader=lambda principal, turn_id: self._action_material_evidence(session, principal, turn_id),
                work_requests=work_requests,
                actions=self._actions(session, work_requests=work_requests),
                assignments=self._assignments(session),
                tasks=self._tasks(session),
                material_drafts=self._action_materials(session),
            )
        )

    def work_request_timeline(self, principal: Principal, request_id: UUID) -> dict[str, Any]:
        with self._session_factory() as session:
            return self._work_requests(session).timeline(principal, request_id)

    def negotiate_work_request(
        self, principal: Principal, request_id: UUID, expected_version: int, conditions: dict[str, Any]
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._work_requests(session).negotiate(
                principal, request_id, expected_version, conditions
            )
            session.commit()
            return result

    def work_request_inbox(self, principal: Principal) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return self._work_requests(session).inbox(principal)

    def create_conversation(self, principal: Principal, title: str) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._conversations(session).create(principal, title)
            session.commit()
            return result

    def conversations(self, principal: Principal) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return self._conversations(session).list(principal)

    def conversation(self, principal: Principal, conversation_id: UUID) -> dict[str, Any]:
        with self._session_factory() as session:
            return self._conversations(session).get(principal, conversation_id)

    def accept_conversation_message(
        self,
        principal: Principal,
        body: str,
        conversation_id: UUID,
        context: list[Any],
        idempotency_key: str | None,
        follow_up_candidate_id: UUID | None = None,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            references = [
                ConversationContextReferenceInput(
                    resource_type=item.resource_type,
                    resource_id=str(item.resource_id),
                    resource_version=item.resource_version,
                    included=item.included,
                )
                for item in context
            ]
            result = self._conversations(session).accept_message(
                principal,
                conversation_id,
                body,
                references,
                idempotency_key,
                follow_up_candidate_id,
            )
            session.commit()
            return result

    def retry_conversation_turn(self, principal: Principal, conversation_id: UUID, turn_id: UUID) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._conversations(session).retry(principal, conversation_id, turn_id)
            session.commit()
            return result

    def cancel_conversation_turn(
        self, principal: Principal, conversation_id: UUID, expected_version: int
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._conversations(session).cancel(principal, conversation_id, expected_version)
            session.commit()
            return result

    def propose_action(
        self,
        principal: Principal,
        execution_id: UUID,
        action_type: str,
        title: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._actions(session).propose(
                principal,
                execution_id,
                action_type,
                title,
                payload,
            )
            session.commit()
            return result

    def actions(self, principal: Principal) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return self._actions(session).list(principal)

    def decide_action(
        self,
        principal: Principal,
        action_id: UUID,
        expected_version: int,
        decision: str,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            action = SqlAlchemyActionRepository(session).action(action_id, str(principal.id))
            # The old chat endpoint remains a compatibility surface. A Task proposal raised under the canonical
            # ledger still goes through its atomic confirm operation, so this route cannot create or assign a Task
            # without the selected Submission and ReviewDecision lineage.
            if (
                action is not None
                and action.action_type in {"task.create_self", "task.assign", "meeting.create"}
                and session.get(DecisionItemRecord, action.id) is not None
            ):
                if ACTION_DECIDE not in principal.capabilities:
                    raise ActionCapabilityDenied(f"{ACTION_DECIDE} capability is required")
                center = self._action_center(session)
                detail = center.detail(principal, str(action_id))
                command = "confirm" if decision == "approve" else decision
                payload: dict[str, Any] = {"expected_version": expected_version}
                if command == "confirm":
                    payload["base_submission_version"] = detail["submission_version"]
                center.execute(principal, str(action_id), command, payload)
                result = SqlAlchemyActionRepository(session).view(action, principal)
            else:
                result = self._actions(session).decide(
                    principal,
                    action_id,
                    expected_version,
                    decision,
                )
            session.commit()
            return result

    def _tasks(self, session: Any) -> TaskApplication:
        """Tasks with the request module attached, so a Task's origin can name a requester it is allowed to name."""
        return TaskApplication(
            SqlAlchemyTaskRepository(session),
            SqlAlchemyWorkRequestRepository(session),
            SqlAlchemyActionRepository(session, evidence_reader=lambda principal, turn_id: self._action_material_evidence(session, principal, turn_id)),
            SqlAlchemyAttachmentRepository(session),
            SqlAlchemyOrganizationRepository(session),
            self._projects(session),
        )

    def _projects(self, session: Any) -> ProjectApplication:
        return ProjectApplication(SqlAlchemyProjectRepository(session))

    def _work_requests(self, session: Any) -> WorkRequestApplication:
        return WorkRequestApplication(
            SqlAlchemyWorkRequestRepository(session),
            OrganizationApplication(SqlAlchemyOrganizationRepository(session)),
            SqlAlchemyCommentRepository(session),
            SqlAlchemyAttachmentRepository(session),
            self._material_storage,
            _SessionTaskReferences(self, session),
            SqlAlchemyMaterialExtractionRepository(session),
            self._material_queue(session),
        )

    def _conversations(self, session: Any) -> ConversationApplication:
        return ConversationApplication(
            SqlAlchemyConversationRepository(
                session,
                ConversationJobQueue(self.job_queue(session)),
                self._settings.conversation_queue_max_fragments,
                evidence_reader=lambda principal, turn_id: self._action_material_evidence(session, principal, turn_id),
                work_requests=self._work_requests(session),
            ),
            SqlAlchemyConversationContextResolver(session),
            _SessionAnswerResources(self, session),
        )

    def _action_material_evidence(self, session: Any, principal: Principal, turn_id: UUID) -> list[dict[str, Any]]:
        evidence = [{"evidence_id": str(row.id), "attachment_id": str(row.attachment_id),
                     "material_id": str(row.attachment_id), "chunk_id": str(row.chunk_id), "name": row.name,
                     "integrity_ref": row.integrity_ref, "source_contexts": row.source_contexts,
                     "source_locator": row.source_locator, "page": row.page, "excerpt": row.excerpt}
                    for row in session.scalars(select(ConversationContentEvidenceRecord).where(
                        ConversationContentEvidenceRecord.turn_id == turn_id).order_by(
                            ConversationContentEvidenceRecord.rank, ConversationContentEvidenceRecord.id))]
        return readable_content_evidence(self, session, principal, evidence)

    def _actions(
        self,
        session: Any,
        *,
        work_requests: WorkRequestApplication | None = None,
    ) -> ActionApplication:
        work_requests = work_requests or self._work_requests(session)
        return ActionApplication(
            SqlAlchemyActionRepository(
                session,
                evidence_reader=lambda principal, turn_id: self._action_material_evidence(session, principal, turn_id),
                work_requests=work_requests,
            ),
            SqlAlchemyActionExecutor(
                session,
                self._report_provider,
                self._action_materials(session),
                work_requests=work_requests,
                evidence_reader=lambda principal, turn_id: self._action_material_evidence(session, principal, turn_id),
            ),
        )

    def _action_materials(self, session: Any) -> ActionMaterialDraftApplication:
        return ActionMaterialDraftApplication(
            SqlAlchemyActionMaterialDraftRepository(session),
            self._material_storage,
            SqlAlchemyAttachmentRepository(session),
            SqlAlchemyMaterialExtractionRepository(session),
            self._material_queue(session),
        )

    def add_task_checklist_item(
        self, principal: Principal, task_id: UUID, text: str, expected_task_version: int | None = None
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._tasks(session).add_checklist_item(principal, task_id, text, expected_task_version=expected_task_version)
            session.commit()
            return result

    def update_task_checklist_item(self, principal: Principal, task_id: UUID, item_id: UUID, **fields: Any) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._tasks(session).update_checklist_item(principal, task_id, item_id, **fields)
            session.commit()
            return result

    def archive_task_checklist_item(self, principal: Principal, task_id: UUID, item_id: UUID, **fields: Any) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._tasks(session).archive_checklist_item(principal, task_id, item_id, **fields)
            session.commit()
            return result

    def reorder_task_checklist(self, principal: Principal, task_id: UUID, item_ids: list[UUID], **fields: Any) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._tasks(session).reorder_checklist(principal, task_id, item_ids, **fields)
            session.commit()
            return result

    def transition_task(self, task_id: UUID, principal: Principal, target: TaskState, reason: str | None = None, expected_version: int = 0) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._tasks(session).transition(task_id, principal, target, reason, expected_version)
            session.commit()
            return result


def create_auth_session_store(settings: Settings):
    """Login sessions live beside the operational database; the entrypoint only sees this factory."""
    from ax_workspace.platform.auth_sessions import SqlAlchemyAuthSessionStore
    from ax_workspace.platform.persistence import make_session_factory

    return SqlAlchemyAuthSessionStore(make_session_factory(settings.database_url))


def create_workflow_application(
    settings: Settings,
    report_provider: AiProvider | None = None,
) -> WorkflowApplication:
    # 문서와 질문에 같은 분석을 적용한다 — 색인은 이미 그 규칙으로 만들어져 있다.
    install_korean_analyzer()
    return WorkflowApplication(settings, report_provider)


def create_scax_mcp_server(settings: Settings) -> CodexCliMcpServer:
    """Address the same MCP entrypoint from source Python or the protected dispatcher."""
    protected_executable = os.getenv("SCAX_RUNTIME_EXECUTABLE")
    if protected_executable:
        command = protected_executable
        arguments = ("mcp",)
    else:
        command = sys.executable
        arguments = ("-m", "ax_workspace.entrypoints.mcp")
    return CodexCliMcpServer(
        command=command,
        arguments=arguments,
        environment={
            "AX_PROFILE": str(settings.profile),
            "DATABASE_URL": settings.database_url,
        },
    )


def create_codex_cli_provider(settings: Settings) -> CodexCliProviderAdapter:
    """Compose the isolated CLI adapter with exactly one server-bound SCAX MCP."""
    return CodexCliProviderAdapter(scax_mcp_server=create_scax_mcp_server(settings))
