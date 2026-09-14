"""Composition root for the local workflow application."""
from __future__ import annotations

from ax_workspace.modules.ax_execution.browser_interactions import BrowserRecordingRequest, BrowserFileRequest, BrowserInteractionApplication, BrowserInteractionConflict, BrowserInteractionResult
from ax_workspace.bootstrap.browser_interactions import SessionBrowserFileTargets
from ax_workspace.platform.browser_interactions import SqlAlchemyBrowserInteractionRepository

from ax_workspace.modules.ax_execution.result_contracts import ActionMaterialDraftView
from ax_workspace.modules.organization_access.commands import AssistantCharacterResult
from ax_workspace.modules.ax_execution.conversation_commands import ConversationMessageResult
from ax_workspace.modules.ax_execution.conversation_commands import ConversationRetryResult
from ax_workspace.modules.work.folder_commands import FolderArchiveResult
from ax_workspace.modules.work.folder_commands import FolderDetachResult
from ax_workspace.modules.work.folder_commands import FolderView
from ax_workspace.modules.work.project_commands import ProjectReleaseResult

from ax_workspace.modules.work.material_query_results import FolderMaterialView
from ax_workspace.modules.work.graph_results import GraphNeighborsResult
from ax_workspace.modules.work.graph_results import GraphOverviewResult
from ax_workspace.modules.work.graph_results import GraphSearchResult
from ax_workspace.modules.work.material_query_results import MaterialMetadataResult
from ax_workspace.modules.work.material_query_results import MaterialSearchResult

from ax_workspace.modules.organization_access.results import InstalledRoleView, MemberAccessView, MemberAxisHistoryView, MemberCandidateView, MemberDetailView, MemberDirectoryView, MyOrganizationProfileView, OrganizationActivityView, OrganizationUnitView, UnitMemberView

from ax_workspace.modules.reports.results import ReportHistoryResult, ReportStatusResult, ReportRecentView
from ax_workspace.modules.reports.results import ReportGeneratedResult

from ax_workspace.modules.ax_execution.conversation_results import ConversationView

from ax_workspace.modules.work.material_results import TaskMaterialView, TaskMaterialResult
from ax_workspace.modules.work.task_results import TaskCompletionResult, TaskReferenceResult, TaskReferenceReleaseResult

from ax_workspace.modules.work.project_results import ProjectView, ProjectDetailResult, ProjectAssignmentView, ProjectParticipationView

from ax_workspace.modules.work.request_results import WorkRequestDetailResult, WorkRequestHistoryResult, WorkRequestEvidenceResult
from ax_workspace.modules.actions.results import ActionDiscussionView

from ax_workspace.modules.work.task_results import TaskDetailResult, TaskHistoryDiffResult, TaskHistoryResult, TaskListEntry

from ax_workspace.modules.actions.results import ActionEnvelopeResult, ActionDetailResult

from ax_workspace.modules.ax_execution.result_contracts import ActionProposalResult

from pathlib import Path
from datetime import UTC, datetime, timedelta
from time import monotonic, sleep

from typing import Any
import asyncio
import hashlib
import json
import os
import sys
import threading
from uuid import UUID, uuid4
import logging

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.modules.ax_execution.answer_documents import answer_context_excerpt
from ax_workspace.modules.ax_execution.conversations import (
    DEFAULT_CONVERSATION_LIST_LIMIT,
    DEFAULT_MESSAGE_WINDOW,
    ConversationApplication,
    ConversationContextReferenceInput,
)
from ax_workspace.modules.ax_execution.actions import ActionApplication, ActionCapabilityDenied
from ax_workspace.modules.notifications import NotificationApplication, NotificationView, notification_view
from ax_workspace.modules.errors import ResourceNotFound
from ax_workspace.modules.organization_access.domain import ACTION_DECIDE, DAILY_REPORT_READ, Principal
from ax_workspace.modules.work.material_folders import MaterialFolderApplication
from ax_workspace.platform.material_folders import SqlAlchemyMaterialFolderRepository
from ax_workspace.modules.organization_access.administration import AccessAdministration
from ax_workspace.modules.organization_access.application import OrganizationApplication
from ax_workspace.platform.organization_access import SqlAlchemyOrganizationRepository
from ax_workspace.modules.reports.application import DailyReportAccessDenied, DailyReportApplication
from ax_workspace.modules.reports.jobs import DailyReportGenerationJob, DailyReportGenerationQueue
from ax_workspace.modules.reports.results import ReportDraftResult, ReportSubmissionResult
from ax_workspace.modules.ax_execution.ai import (
    AiConversationRequest,
    AiDelegatedToolContext,
    AiProvider,
    ProviderFailure,
)
from ax_workspace.platform.codex_cli import CodexCliMcpServer, CodexCliProviderAdapter
from ax_workspace.platform.claude_cli import ClaudeCliProviderAdapter
from ax_workspace.platform.conversation_jobs import ConversationJobQueue
from ax_workspace.platform.durable_jobs import MemoryDurableJobQueue, build_job_queue
from ax_workspace.platform.conversations import (
    SqlAlchemyConversationContextResolver,
    SqlAlchemyConversationRepository,
)
from ax_workspace.modules.actions.domain import ActionCenterApplication, ActionError
from ax_workspace.platform.action_center import action_handlers
from ax_workspace.platform.actions import (
    POST_COMMIT_RECEIPT_PENDING,
    ActionServices,
    SqlAlchemyActionExecutor,
    SqlAlchemyActionRepository,
    action_result_view,
)
from ax_workspace.platform.reports import SqlAlchemyDailyReportDraftWorkflow, SqlAlchemyDailyReportRepository
from ax_workspace.modules.work.materials import MaterialError, MaterialNotFound, TaskMaterialApplication
from ax_workspace.modules.work.action_materials import ActionMaterialDraftApplication, ActionMaterialError
from ax_workspace.modules.work.material_search import MaterialSearchApplication, RESOURCE_TYPES
from ax_workspace.bootstrap.material_sources import SessionMaterialOwners, readable_content_evidence
from ax_workspace.modules.work.graph import GraphApplication
from ax_workspace.modules.work.application import TaskAccessDenied, TaskApplication, TaskNotFound, TaskState
from ax_workspace.modules.work.assignments import TaskAssignmentApplication
from ax_workspace.modules.work.task_results import (
    ChecklistMutationResult,
    ChecklistOrderResult,
    TaskAssignmentResult,
    TaskMutationResult,
)
from ax_workspace.modules.meetings.application import MeetingApplication
from ax_workspace.modules.meetings.commands import (
    MeetingAgendaDraftInput,
    MeetingAgendaPatch,
    MeetingInfoPatch,
    MeetingNoteCreateCommand,
    MeetingNoteFinalizeCommand,
    MeetingNoteSaveCommand,
    MeetingMemoInput,
    MeetingReservationInput,
    MeetingShareCommand,
    MeetingShareManyInput,
    MeetingTodoPromotionInput,
    MeetingUpdateCommand,
)
from ax_workspace.modules.meetings.domain import MeetingAccessDenied, MeetingError, MeetingVersionConflict
from ax_workspace.modules.meetings.batch import (
    CAUSE_AGENDA_SWITCH,
    CAUSE_TRANSCRIPT,
    OUTPUT_SCHEMA as BATCH_OUTPUT_SCHEMA,
    BatchAgenda,
)
from ax_workspace.modules.meetings.batch_service import BatchInput, MeetingBatchService
from ax_workspace.modules.meetings.export import export_filename as _export_filename, render_meeting_html
from ax_workspace.modules.meetings.materials import (
    MaterialUpload,
    MeetingMaterialApplication,
)
from ax_workspace.modules.meetings.finalize import FINAL_OUTPUT_SCHEMA, FinalNotes, normalize_title
from ax_workspace.modules.meetings.finalize_service import MeetingFinalizeService
from ax_workspace.modules.meetings.rooms import (
    STATUS_BOOKED,
    STATUS_CANCELLED,
    STATUS_NEEDS_VERIFICATION,
    MeetingRoom,
    MeetingRoomGateway,
    RoomAuthFailed,
    RoomBookingRefused,
    RoomCreationIdempotencyConflict,
    RoomCreationIdempotencyRequired,
    RoomGatewayUnavailable,
    RoomOutcomeUnknown,
    RoomReservation,
    RoomReservationError,
    RoomUnavailable,
    build_reservation,
    choose_replacement,
    failure_reason,
    local_slot,
    map_participants,
)
from ax_workspace.modules.meetings.stream_service import MeetingStreamService
from ax_workspace.modules.work.request_results import WorkRequestMutationResult
from ax_workspace.modules.work.requests import WorkRequestAccessDenied, WorkRequestApplication, WorkRequestError
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError

from ax_workspace.platform.persistence import (
    ActionItemRecord,
    AppointmentRecord,
    AttachmentRecord,
    DecisionItemRecord,
    ConversationContentEvidenceRecord,
    EmploymentPeriodRecord,
    MaterialBlockRecord,
    MaterialChunkRecord,
    MaterialExtractionRecord,
    MeetingRecord,
    MeetingRoomCreationAttemptRecord,
    MemberRecord,
    OrganizationUnitRecord,
    PositionDefinitionRecord,
    make_session_factory,
)
from ax_workspace.platform.materials import LocalDirectoryMaterialStorage
from ax_workspace.platform.notifications import SqlAlchemyNotificationRepository
from ax_workspace.platform.action_materials import SqlAlchemyActionMaterialDraftRepository
from ax_workspace.platform.native_materials import MEETING_TRANSCRIPT, NativeMaterialRepository, NativeRevisionStorage
from ax_workspace.platform.recordings import LocalDirectoryRecordingStorage
from ax_workspace.platform.soniox import SonioxFileTranscriber, SonioxRealtimeConnector
from ax_workspace.platform.the_connect import TheConnectGateway
from ax_workspace.modules.work.material_extraction import LexicalMaterialRetriever, MaterialExtractionJob, extraction_view
from ax_workspace.modules.work.projects import ProjectApplication, ProjectAccessDenied
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




# Read projections omit resources only for explicit visibility refusals. A storage
# failure must reach the caller as a failure, not fabricate an empty graph/answer.
_READ_DENIALS = (ResourceNotFound, TaskAccessDenied, TaskNotFound, MaterialNotFound, WorkRequestAccessDenied,
                 MeetingAccessDenied, DailyReportAccessDenied, ProjectAccessDenied)


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
                head = self._application._meetings(self._session).get(principal, identifier)["meeting"]
                return str(head.get("title") or "제목 없는 회의")
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

    #: Kind -> (owning fetch, title field, state field, has a comparable version). One fetch derives every field
    #: this projection needs instead of the two separate reads `_read`/`_current_version` each did on their own.
    _EAGER_LOOKUP: dict[str, tuple[str, str, str, bool]] = {
        "task": ("readable_task", "title", "state", True),
        "work_request": ("readable_request", "title", "state", True),
        "meeting": ("readable_meeting", "title", "status", False),
        "project": ("readable_project", "name", "state", False),
    }

    def resolve(self, principal: Principal, references: list[dict[str, Any]]) -> list[dict[str, Any]]:
        resolved: list[dict[str, Any]] = []
        content = self._readable_content_references(principal, references)
        # Several turns can cite the same task/meeting/request/project. Read its authorized detail once for this
        # whole projection, including its version, rather than re-reading it (twice, previously) per citation.
        cache: dict[tuple[str, str], dict[str, Any] | None] = {}
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
            lookup = self._EAGER_LOOKUP.get(kind)
            if lookup is None:
                continue
            fetch_name, title_field, state_field, has_version = lookup
            cache_key = (kind, identifier)
            if cache_key not in cache:
                try:
                    parsed_id = UUID(identifier)
                    cache[cache_key] = getattr(self._source, fetch_name)(principal, parsed_id)
                except (TypeError, ValueError):
                    cache[cache_key] = None
            row = cache[cache_key]
            if row is None:
                # Readable when the turn ran, not now. It leaves no title and no gap that could be counted.
                continue
            title = str(row.get(title_field) or "제목 없는 회의") if kind == "meeting" else str(row[title_field])
            state = row.get(state_field)
            current = int(row["version"]) if has_version and row.get("version") is not None else None
            # 답이 딛고 선 회차와 지금의 회차가 다를 수 있다. 그것은 숨길 일이 아니라 말할 일이다 — 사람이
            # 링크를 열기 전에 무엇이 달라졌을 수 있는지 알아야 한다. 지금 회차를 알 수 없으면 말하지 않는다.
            seen = reference.get("resource_version")
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
            parsed_id = UUID(identifier)
        except (TypeError, ValueError):
            return None
        try:
            if kind == "task":
                task = self._source.readable_task(principal, parsed_id)
                return int(task["version"]) if task and task.get("version") is not None else None
            if kind == "work_request":
                request = self._source.readable_request(principal, parsed_id)
                return int(request["version"]) if request and request.get("version") is not None else None
            if kind == "meeting":
                # 회의록은 판을 쌓지 않는다 — 덮어쓰기이므로 견줄 판 번호가 없다 (SPEC-004 §11.1).
                return None
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
            parsed_id = UUID(identifier)
        except (TypeError, ValueError):
            return (None, None)
        try:
            if kind == "project":
                project = self._source.readable_project(principal, parsed_id)
                return (str(project["name"]), project.get("state")) if project else (None, None)
            if kind == "task":
                task = self._source.readable_task(principal, parsed_id)
                return (str(task["title"]), task.get("state")) if task else (None, None)
            if kind == "work_request":
                request = self._source.readable_request(principal, parsed_id)
                return (str(request["title"]), request.get("state")) if request else (None, None)
            if kind == "meeting":
                meeting = self._source.readable_meeting(principal, UUID(identifier))
                return (str(meeting.get("title") or "제목 없는 회의"), meeting.get("status")) if meeting else (None, None)
            if kind == "material":
                material = self._application._material_metadata(self._session, principal, parsed_id)
                return material["name"], material["state"]
            if kind == "report":
                report = self._application.daily_report_history(principal, identifier)
                return (f"{report['report_date']} 일일보고", report.get("status"))
        except _READ_DENIALS:
            return (None, None)
        return (None, None)


logger = logging.getLogger(__name__)
_MEETING_RECEIPT_MIN_LEASE_SECONDS = 30.0
_MEETING_RECEIPT_LEASE_MARGIN_SECONDS = 10.0


class _MeetingMaterialGate:
    """자료가 회의에 묻는 것 — 이 사람이 이 회의를 여는가, 참석자인가. 판정은 회의 모듈이 소유한다."""

    def __init__(self, application: "WorkflowApplication", session: Any) -> None:
        self._application = application
        self._session = session

    def readable(self, principal: Principal, meeting_id: UUID) -> Any:
        return self._application._meetings(self._session)._readable(principal, meeting_id)

    def is_attendee(self, principal: Principal, meeting: Any) -> bool:
        return self._application._meetings(self._session)._is_attendee(principal, meeting)


class _SessionFinalizeGateway:
    """합성이 원장에 묻는 것. provider 호출 중에는 트랜잭션을 열지 않는다."""

    def __init__(self, application: "WorkflowApplication") -> None:
        self._application = application

    def load_input(self, meeting_id: str) -> dict[str, Any] | None:
        with self._application._session_factory() as session:
            return self._application._meetings(session).finalize_input(UUID(meeting_id))

    def existing_task_titles(self, persona_id: str) -> set[str]:
        """이미 있는 업무의 제목 — 후보 중복을 막는 근거다. **검사 시점에** 읽는다 (SCAX-SPEC-004 §8.1)."""
        principal = self._application.authenticated_principal(persona_id)
        return {
            normalize_title(str(task.get("title") or ""))
            for task in self._application.list_tasks(principal, include_closed=True)
        }

    def commit_success(self, meeting_id: str, notes: FinalNotes, *, cold_start: bool) -> None:
        with self._application._session_factory() as session:
            self._application._meetings(session).commit_finalized(UUID(meeting_id), notes)
            session.commit()
        logger.info("회의 %s 합성 완료%s", meeting_id, " (콜드 스타트)" if cold_start else "")

    def commit_failure(self, meeting_id: str, reason: str) -> None:
        with self._application._session_factory() as session:
            self._application._meetings(session).fail_finalize(UUID(meeting_id), reason)
            session.commit()
        logger.warning("회의 %s 합성 실패: %s", meeting_id, reason)

    # --- ① 재전사가 원장에 묻는 것 (D44) ---------------------------------------

    def recording_for(self, meeting_id: str):
        with self._application._session_factory() as session:
            return self._application._meetings(session).recording_for_retranscribe(UUID(meeting_id))

    def replace_transcript(self, meeting_id: str, blocks: list) -> int:
        with self._application._session_factory() as session:
            written = self._application._meetings(session).replace_transcript(UUID(meeting_id), blocks)
            session.commit()
            return written

    def set_transcript_source(self, meeting_id: str, source: str) -> None:
        with self._application._session_factory() as session:
            self._application._meetings(session).set_transcript_source(UUID(meeting_id), source)
            session.commit()


class _CodexFinalizeAgent:
    """합성은 회의 중 배치와 **같은 세션**으로 돈다. 세션이 없으면 새로 열어 한 번에 돈다(콜드 스타트)."""

    def __init__(self, application: "WorkflowApplication") -> None:
        self._application = application

    def run_final(self, *, persona_id: str, session_ref: str | None, prompt: str) -> str:
        provider = self._application.meeting_batch_provider(self._application._settings.meeting_ai_tool_registry)
        return provider.converse(
            AiConversationRequest(
                prompt=prompt,
                provider_session_ref=session_ref,
                context_references=[],
                delegated_tool_context=AiDelegatedToolContext(
                    principal_id=persona_id, causation_id=f"meeting-finalize:{persona_id}"
                ),
                # 합성도 같은 자리에 자기 스키마를 건다 — 최종 전용 필드가 들어 있는 한 벌이다.
                output_schema=FINAL_OUTPUT_SCHEMA,
            )
        ).body


class _SessionBatchGateway:
    """배치가 원장에 묻는 것 — 단계마다 세션을 열고 끝에 커밋한다 (배치는 요청 경계 밖이다)."""

    def __init__(self, application: "WorkflowApplication") -> None:
        self._application = application

    def warm_start_context(self, meeting_id: str) -> dict[str, Any] | None:
        with self._application._session_factory() as session:
            return self._application._meetings(session).warm_start_context(UUID(meeting_id))

    def record_session(self, meeting_id: str, *, session_ref: str, persona_id: str) -> None:
        with self._application._session_factory() as session:
            self._application._meetings(session).record_ai_session(
                UUID(meeting_id), session_ref=session_ref, persona_id=persona_id
            )
            session.commit()

    def pending_chars(self, meeting_id: str) -> int:
        with self._application._session_factory() as session:
            return self._application._meetings(session).pending_batch_chars(UUID(meeting_id))

    def load_batch_input(self, meeting_id: str) -> BatchInput | None:
        with self._application._session_factory() as session:
            raw = self._application._meetings(session).batch_input(UUID(meeting_id))
            if raw is None:
                return None
            # 업무 화이트리스트는 **검사 시점에** 읽는다 — 제출 뒤에 생긴 업무를 AI 가 도구로 보고 가리켰다면
            # 그것은 올바른 참조다 (SCAX-SPEC-004 §7.1 검증).
            principal = self._application.authenticated_principal(raw["persona_id"])
            allowed = {
                str(task["task_id"])
                for task in self._application.list_tasks(principal)
            }
        return BatchInput(
            seq=raw["seq"],
            session_ref=raw["session_ref"],
            persona_id=raw["persona_id"],
            blocks=raw["blocks"],
            memos=raw["memos"],
            from_seq=raw["from_seq"],
            to_seq=raw["to_seq"],
            covered_ms=raw["covered_ms"],
            allowed_task_ids=allowed,
        )

    def record_run(
        self, meeting_id: str, *, seq: int, status: str, cause: str, from_seq: int, to_seq: int, reason: str | None
    ) -> None:
        with self._application._session_factory() as session:
            self._application._meetings(session).record_batch_run(
                UUID(meeting_id), seq=seq, status=status, cause=cause, from_seq=from_seq, to_seq=to_seq, reason=reason
            )
            session.commit()

    def replace_ai_track(self, meeting_id: str, agendas: list[BatchAgenda]) -> list[dict[str, Any]]:
        with self._application._session_factory() as session:
            tree = self._application._meetings(session).replace_ai_track(UUID(meeting_id), agendas)
            session.commit()
            return tree

    def push(self, meeting_id: str, *, seq: int, agendas: list[dict[str, Any]]) -> None:
        """커밋 직후 — AI 트랙만 실어 구독자에게 민다. 세션이 없으면 건너뛴다.

        안건마다 `todos` 가 함께 간다 (D46) — 회의 중에 선 것은 전부 `provisional` 이고 화면은
        그것을 읽기 전용으로 그린다. 줄만 트랙으로 거르고 나머지는 상세와 같은 모양 그대로다.
        """
        payload = [
            {**agenda, "lines": [line for line in agenda["lines"] if line["track"] == "ai"]}
            for agenda in agendas
        ]
        self._application.schedule_push_ai_batch(meeting_id, seq=seq, agendas=payload)


class _CodexBatchAgent:
    """provider 경계. 회의 배치는 **MCP 도구를 붙인 대화 세션**으로 돈다 — 한 번 던지고 받는 생성이 아니다."""

    def __init__(self, application: "WorkflowApplication") -> None:
        self._application = application

    def open_session(self, *, persona_id: str, prompt: str, tools: tuple[str, ...]) -> str | None:
        # 웜스타트는 형식을 예고만 한다 — 「준비됨」 한 마디에 스키마를 걸지 않는다.
        result = self._converse(persona_id, prompt, tools, session_ref=None, schema=None)
        return result.provider_session_ref

    def run_batch(self, *, persona_id: str, session_ref: str, prompt: str, tools: tuple[str, ...]) -> str:
        return self._converse(
            persona_id, prompt, tools, session_ref=session_ref, schema=BATCH_OUTPUT_SCHEMA
        ).body

    def _converse(
        self, persona_id: str, prompt: str, tools: tuple[str, ...], *, session_ref: str | None, schema: dict | None
    ) -> Any:
        provider = self._application.meeting_batch_provider(tools)
        return provider.converse(
            AiConversationRequest(
                prompt=prompt,
                provider_session_ref=session_ref,
                context_references=[],
                delegated_tool_context=AiDelegatedToolContext(
                    principal_id=persona_id, causation_id=f"meeting-batch:{persona_id}"
                ),
                # 대화로 돌아도 출력은 스키마 안에서만 나온다 (SCAX-SPEC-004 §7.2-6).
                output_schema=schema,
            )
        )


class _SessionStreamGateway:
    """스트림이 원장에 묻는 것 — 단계마다 세션을 열고 끝에 커밋한다 (WS 는 요청 경계가 없다).

    회의 열람·상태 판정도 여기가 아니라 `MeetingApplication` 이 한다. 이 클래스는 세션을 여닫을 뿐이다.
    """

    def __init__(self, application: "WorkflowApplication") -> None:
        self._application = application

    def admit(self, member_id: str, meeting_id: str) -> Any | None:
        with self._application._session_factory() as session:
            principal = self._application.authenticated_principal(member_id)
            admission = self._application._meetings(session).stream_admission(principal, UUID(meeting_id))
            session.commit()
            return admission

    def append_block(self, meeting_id: str, block: Any) -> str:
        with self._application._session_factory() as session:
            block_id = self._application._meetings(session).append_transcript_block(
                UUID(meeting_id),
                speaker_label=block.speaker_label,
                at_ms=block.at_ms,
                end_ms=block.end_ms,
                text=block.content,
            )
            session.commit()
        # 블록이 닫히는 자리가 배치 트리거의 자리다 (SCAX-SPEC-004 §7.1 트리거 ①).
        self._application.meeting_batch.schedule(meeting_id, CAUSE_TRANSCRIPT)
        return block_id

    def ready_state(self, meeting_id: str) -> tuple[int, int]:
        with self._application._session_factory() as session:
            return self._application._meetings(session).transcript_ready_state(UUID(meeting_id))

    def note_recording_file(self, meeting_id: str, storage_key: str, *, content_type: str) -> None:
        with self._application._session_factory() as session:
            self._application._meetings(session).note_recording_file(
                UUID(meeting_id), storage_key, content_type=content_type
            )
            session.commit()


class _SessionReadableWork:
    """자료 검색이 묻는 것: 이 사람이 읽을 수 있는 업무는 무엇인가.

    답은 업무 모듈이 이미 아는 것 — 자기가 든 것, 조직 범위로 읽는 것, 함께 하는 프로젝트의 것. 자료 검색이
    그 판정을 다시 하지 않는다.
    """

    def __init__(self, application: "WorkflowApplication", session: Any) -> None:
        self._application = application
        self._session = session

    def readable_task_ids(self, principal: Principal) -> list[str]:
        rows = self._application._tasks(self._session).readable_tasks(principal, include_closed=True)
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
            rows = self._application._tasks(self._session).readable_tasks(principal, include_closed=True)
        except TaskAccessDenied:
            return []
        return [row for row in rows
                if (not query or matches(query, str(row["title"])))
                and (assignee_id is None or str((row.get("assignee") or {}).get("member_id")) == assignee_id)][:limit]

    def readable_task(self, principal: Principal, task_id: UUID) -> dict[str, Any] | None:
        try:
            return self._application._tasks(self._session).get(principal, task_id)
        except _READ_DENIALS:
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
        except _READ_DENIALS:
            return []
        return [row for row in rows if not query or matches(query, str(row["title"]))]

    def readable_request(self, principal: Principal, request_id: UUID) -> dict[str, Any] | None:
        try:
            return self._application._work_requests(self._session).get(principal, request_id)
        except _READ_DENIALS:
            return None

    def task_materials(self, principal: Principal, task_id: UUID) -> list[dict[str, Any]]:
        try:
            return self._application._materials(self._session).list(principal, task_id)
        except _READ_DENIALS:
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
        except _READ_DENIALS:
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
        except _READ_DENIALS:
            return []
        meetings = [row for row in rows if row.get("kind") == "meeting"]
        return [row for row in meetings
                if (not query or matches(query, str(row.get("title") or "")))
                and (member_id is None or member_id == str(row.get("created_by"))
                     or member_id in {str(person["member_id"]) for person in row.get("attendees") or []})][:limit]

    def readable_meeting(self, principal: Principal, meeting_id: UUID) -> dict[str, Any] | None:
        """관계 그래프가 읽는 납작한 회의 머리. 안건·줄은 그래프의 관심이 아니다."""
        try:
            detail = self._application._meetings(self._session).get(principal, meeting_id)
        except Exception:
            return None
        head = dict(detail["meeting"])
        return {**head, "kind": "meeting", "owner_id": head.get("created_by")}

    def meeting_followup_tasks(self, principal: Principal, meeting_id: UUID) -> list[dict[str, Any]]:
        """회의에서 나온 일. 승격은 요약 문장이 아니라 안건의 「다음 할 일」에서 일어난다 — SCAX-WP-004 가 세운다.

        그 표면이 서기 전까지 회의–업무 간선은 없다. 없는 것을 있는 척하지 않는다.
        """
        del principal, meeting_id
        return []

    def own_reports(self, principal: Principal, *, limit: int = 3) -> list[dict[str, Any]]:
        """This person's own daily reports and the work each one was written from. A report is nobody else's to read."""
        if DAILY_REPORT_READ not in principal.capabilities:
            return []
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
        except _READ_DENIALS:
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
        # 테스트가 provider 경계에서 대역을 끼우는 자리. 비어 있으면 실제 Soniox 어댑터를 만든다.
        self._stt_connector: Any | None = None
        # 배치도 요청 경계가 없다. provider 는 호출마다 도구 목록이 달라 factory 로 만든다.
        self._meeting_batch = MeetingBatchService(
            gateway=_SessionBatchGateway(self),
            agent=_CodexBatchAgent(self),
            tools=settings.meeting_ai_tool_registry,
            chars=settings.meeting_batch_chars,
            switch_min_chars=settings.meeting_batch_switch_min_chars,
            max_wait_seconds=settings.meeting_batch_max_wait_seconds,
        )
        # 종료 뒤 재전사가 쓰는 어댑터 (D44). **시험에서는 비워 둔다** — 개발자 환경에 키가 있다고 해서
        # 스위트가 실물을 부르면 안 된다. 대역은 이 자리에 끼운다.
        self._file_transcriber: Any = (
            None if settings.profile is RuntimeProfile.TEST else SonioxFileTranscriber()
        )
        # 합성은 배치와 **같은 lock** 을 잡는다 — 회의당 provider 호출은 언제나 하나다.
        self._meeting_finalize = MeetingFinalizeService(
            gateway=_SessionFinalizeGateway(self),
            agent=_CodexFinalizeAgent(self),
            lock_for=self._meeting_batch._lock_for,
            transcriber=self._file_transcriber,
        )
        # 시험이 대역을 끼우면 서비스가 그것을 쓴다 — 조립층이 한 번 더 건드리지 않게 같은 자리를 가리킨다.
        # 스트림은 요청 경계가 없다 — 단계마다 세션을 여는 gateway 를 끼워 준다.
        self._meeting_stream = MeetingStreamService(
            gateway=_SessionStreamGateway(self),
            connector_factory=lambda: self._stt_connector or SonioxRealtimeConnector(),
            store=self._recording_storage,
        )
        # One in-process job store per application when the memory backend is selected (tests); postgres joins each session.
        self.memory_job_queue: MemoryDurableJobQueue | None = MemoryDurableJobQueue() if settings.job_queue_backend == "memory" else None
        self._report_provider = report_provider or create_conversation_provider(settings)
        # 회의실 예약 시스템. **시험이 대역을 끼우는 자리**이고, 비어 있으면 계정이 갖춰졌을 때만 실물을 만든다.
        self._room_gateway: MeetingRoomGateway | None = None
        self._room_sync_locks: dict[UUID, threading.RLock] = {}
        self._room_sync_locks_guard = threading.Lock()
        self._room_creation_locks: dict[tuple[str, str], threading.RLock] = {}
        self._room_creation_locks_guard = threading.Lock()

    # ------------------------------------------------------------------ 회의실 예약 (SCAX-WP-007)

    @property
    def room_gateway(self) -> MeetingRoomGateway | None:
        """예약 시스템으로 가는 문. 계정이 없으면 **문이 없다** — 부팅은 멀쩡하고 예약만 사유를 남긴다."""
        if self._room_gateway is None and self._settings.room_booking_configured:
            self._room_gateway = TheConnectGateway(
                base_url=self._settings.room_booking_base_url,
                email=self._settings.room_booking_email,
                password=self._settings.room_booking_password,
                company_id=self._settings.room_booking_company_id,
                notify=self._settings.room_booking_notify,
                timeout_seconds=self._settings.room_booking_timeout_seconds,
            )
        return self._room_gateway

    def meeting_rooms(
        self, *, starts_at: datetime | None = None, ends_at: datetime | None = None
    ) -> list[dict[str, Any]]:
        """고를 수 있는 회의실. 예약 시스템이 없거나 닿지 않으면 **빈 목록**이다 —
        화면은 「회의실 선택 안 함」만 보이고, 없는 방을 지어내지 않는다.

        시간을 주면 **그 시간에 비어 있는 방만** 내고 `available: true` 를 붙인다 (D36-3) —
        예약이 거절된 뒤 모달이 회의실 칸만 다시 그리는 자리다. 시간이 없으면 전체 목록 그대로다.
        """
        # Resolve the lazy adapter, then keep the concrete outbound owner visible to
        # the operation inventory just as the other composition-root methods do.
        _ = self.room_gateway
        gateway = self._room_gateway
        if gateway is None:
            return []
        try:
            if starts_at is None or ends_at is None:
                return [room.view() for room in gateway.rooms()]
            slot = local_slot(starts_at, ends_at)
            return [{**room.view(), "available": True} for room in gateway.available(*slot)]
        except RoomReservationError as error:
            logger.warning("회의실 목록을 읽지 못했습니다: %s", failure_reason(error))
            return []

    def _reserve_room(self, source: dict[str, Any], room_id: int) -> RoomReservation:
        """**회의를 세우기 전에** 자리를 잡는다. 잡지 못하면 `RoomReservationError` 가 올라간다 (D36-2).

        고른 방이 찼으면 곧장 거절하지 않는다 — 그 시간에 가능한 방 중 **정원을 감당하는 가장 작은
        방**으로 한 번 더 시도하고, 그것이 잡히면 「대체됐다」는 사실을 함께 돌려준다 (D36-1).
        대체할 방이 없으면 가능한 방 목록을 실은 채로 거절이 올라간다.
        """
        gateway = self.room_gateway
        if gateway is None:
            raise RoomAuthFailed("예약 시스템 계정이 설정되어 있지 않습니다")
        mapping = map_participants(source["inside"], gateway.members())
        # 계정을 못 찾은 사내 참석자는 **사라지지 않는다** — 사외 참석자와 같이 표시 문자열로 간다.
        outside_names = (*mapping.unmatched, *source["outside"])

        def _request(target: int):
            return build_reservation(
                room_id=target,
                starts_at=source["starts_at"],
                ends_at=source["ends_at"],
                title=source["title"],
                booker_name=source["owner_name"],
                participant_emails=tuple(mapping.emails),
                outside_names=outside_names,
            )

        try:
            booked = gateway.create(_request(room_id))
        except RoomOutcomeUnknown as error:
            raise RoomOutcomeUnknown(
                str(error), room_id=room_id, room_name=self._room_name(gateway, room_id)
            ) from error
        except RoomUnavailable as refused:
            requested = self._room_name(gateway, room_id)
            replacement = choose_replacement(
                list(refused.available), people=source["people"], exclude_room_id=room_id
            )
            if replacement is None:
                # 대체할 방이 없다 — 가능한 방 목록을 그대로 이고 올라간다. 회의는 서지 않는다.
                raise RoomUnavailable(str(refused), available=refused.available) from refused
            logger.info("고른 회의실이 차서 %s 로 대체합니다", replacement.name)
            try:
                booked = gateway.create(_request(replacement.room_id))
            except RoomOutcomeUnknown as error:
                raise RoomOutcomeUnknown(
                    str(error),
                    room_id=replacement.room_id,
                    room_name=replacement.name,
                    replaced=True,
                    requested_room_name=requested,
                ) from error
            return RoomReservation(
                status=STATUS_BOOKED,
                room_id=booked.room_id,
                room_name=booked.room_name,
                external_id=booked.external_id,
                replaced=True,
                requested_room_name=requested,
            )
        return RoomReservation(
            status=STATUS_BOOKED,
            room_id=booked.room_id,
            room_name=booked.room_name,
            external_id=booked.external_id,
        )

    def _room_creation_lock(self, owner_id: str, request_key: str) -> threading.RLock:
        identity = (owner_id, request_key)
        with self._room_creation_locks_guard:
            try:
                lock = self._room_creation_locks[identity]
            except KeyError:
                lock = threading.RLock()
                self._room_creation_locks[identity] = lock
            return lock

    @staticmethod
    def _room_creation_fingerprint(request: MeetingReservationInput) -> str:
        return WorkflowApplication._room_creation_payload_fingerprint(
            request.model_dump(mode="json")
        )

    @staticmethod
    def _room_creation_payload_fingerprint(payload: dict[str, Any]) -> str:
        canonical = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    @staticmethod
    def _room_creation_request_key(value: str | None) -> str:
        key = str(value or "").strip()
        if not key or len(key) > 200:
            raise RoomCreationIdempotencyRequired("회의실 예약에는 200자 이하 Idempotency-Key가 필요합니다")
        return key

    @staticmethod
    def _unknown_creation_reservation(error: RoomOutcomeUnknown, room_id: int) -> RoomReservation:
        return RoomReservation(
            status=STATUS_NEEDS_VERIFICATION,
            room_id=error.room_id if error.room_id is not None else room_id,
            room_name=error.room_name,
            reason=error.reason,
            replaced=error.replaced,
            requested_room_name=error.requested_room_name,
        )

    @staticmethod
    def _stored_room_refusal(attempt: MeetingRoomCreationAttemptRecord) -> RoomBookingRefused:
        rooms = tuple(
            MeetingRoom(
                room_id=int(room["room_id"]),
                name=str(room["name"]),
                capacity=int(room["capacity"]),
            )
            for room in (attempt.available_rooms or [])
        )
        error_type = {
            RoomUnavailable.reason: RoomUnavailable,
            RoomAuthFailed.reason: RoomAuthFailed,
            RoomGatewayUnavailable.reason: RoomGatewayUnavailable,
        }.get(attempt.failure_reason, RoomReservationError)
        cause = error_type(attempt.failure_message or attempt.failure_reason or "회의실 예약 실패", available=rooms)
        return RoomBookingRefused(cause, [room.view() for room in rooms])

    def _resolve_room_creation_attempt(
        self,
        principal: Principal,
        request_key: str,
        request: MeetingReservationInput,
        source: dict[str, Any],
        *,
        payload_fingerprint: str | None = None,
        request_payload: dict[str, Any] | None = None,
    ) -> RoomReservation:
        """Call the provider at most once for one durable owner/key/payload tuple."""
        owner_id = str(principal.id)
        fingerprint = payload_fingerprint or self._room_creation_fingerprint(request)
        durable_payload = request_payload or request.model_dump(mode="json")
        created_attempt = False
        with self._room_creation_lock(owner_id, request_key):
            with self._session_factory() as session:
                attempt = session.scalar(
                    select(MeetingRoomCreationAttemptRecord).where(
                        MeetingRoomCreationAttemptRecord.owner_id == owner_id,
                        MeetingRoomCreationAttemptRecord.request_key == request_key,
                    )
                )
                if attempt is None:
                    now = datetime.now(UTC)
                    session.add(
                        MeetingRoomCreationAttemptRecord(
                            owner_id=owner_id,
                            request_key=request_key,
                            payload_fingerprint=fingerprint,
                            request_payload=durable_payload,
                            status="pending",
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    try:
                        session.commit()
                        created_attempt = True
                    except IntegrityError:
                        session.rollback()

            with self._session_factory() as session:
                attempt = session.scalar(
                    select(MeetingRoomCreationAttemptRecord)
                    .where(
                        MeetingRoomCreationAttemptRecord.owner_id == owner_id,
                        MeetingRoomCreationAttemptRecord.request_key == request_key,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if attempt is None:  # pragma: no cover - only external deletion can violate the claim
                    raise RoomReservationError("회의실 예약 시도 원장을 찾지 못했습니다")
                if attempt.payload_fingerprint != fingerprint:
                    raise RoomCreationIdempotencyConflict(
                        "같은 Idempotency-Key를 다른 회의실 예약에 사용할 수 없습니다"
                    )
                if attempt.status == "refused":
                    raise self._stored_room_refusal(attempt)
                if attempt.status == "compensated":
                    attempt.status = "pending"
                    attempt.reservation = None
                    attempt.failure_reason = None
                    attempt.failure_message = None
                    attempt.updated_at = datetime.now(UTC)
                    # 재시도를 먼저 durable 하게 남긴다 — 여기서 멈추면 다음 요청은 `pending` 을 보고
                    # provider 를 다시 부르지 않는다. 그 commit 이 행 잠금을 풀므로, 외부 호출로
                    # 들어가기 전에 같은 행을 다시 claim 한다. 그러지 않으면 잠금 밖에서 POST 하게 되고
                    # 중복 억제가 프로세스 안의 lock 하나에만 기대게 된다.
                    session.commit()
                    created_attempt = True
                    attempt = session.scalar(
                        select(MeetingRoomCreationAttemptRecord)
                        .where(
                            MeetingRoomCreationAttemptRecord.owner_id == owner_id,
                            MeetingRoomCreationAttemptRecord.request_key == request_key,
                        )
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                    if attempt is None:  # pragma: no cover - only external deletion can violate the claim
                        raise RoomReservationError("회의실 예약 시도 원장을 찾지 못했습니다")
                    if attempt.status == "refused":
                        raise self._stored_room_refusal(attempt)
                if attempt.status in {STATUS_BOOKED, STATUS_NEEDS_VERIFICATION}:
                    restored = RoomReservation.restored(attempt.reservation)
                    if restored is None:  # pragma: no cover - corrupt persistence is not a retry signal
                        raise RoomReservationError("저장된 회의실 예약 결과를 읽지 못했습니다")
                    return restored

                if not created_attempt:
                    reservation = RoomReservation(
                        status=STATUS_NEEDS_VERIFICATION,
                        room_id=request.room_id,
                        room_name=self._room_name(self.room_gateway, request.room_id) if self.room_gateway else None,
                        reason=RoomOutcomeUnknown.reason,
                    )
                    attempt.status = STATUS_NEEDS_VERIFICATION
                    attempt.reservation = reservation.stored()
                    attempt.updated_at = datetime.now(UTC)
                    session.commit()
                    return reservation

                try:
                    reservation = self._reserve_room(source, request.room_id)
                except RoomOutcomeUnknown as error:
                    reservation = self._unknown_creation_reservation(error, request.room_id)
                    attempt.status = STATUS_NEEDS_VERIFICATION
                    attempt.reservation = reservation.stored()
                    attempt.updated_at = datetime.now(UTC)
                    session.commit()
                    return reservation
                except RoomReservationError as error:
                    available = self._available_room_views(error, source)
                    attempt.status = "refused"
                    attempt.failure_reason = error.reason
                    attempt.failure_message = str(error)
                    attempt.available_rooms = available
                    attempt.updated_at = datetime.now(UTC)
                    session.commit()
                    raise RoomBookingRefused(error, available) from error

                attempt.status = STATUS_BOOKED
                attempt.reservation = reservation.stored()
                attempt.updated_at = datetime.now(UTC)
                session.commit()
                return reservation

    @staticmethod
    def _room_name(gateway: MeetingRoomGateway, room_id: int) -> str | None:
        """원래 고른 방의 이름. 목록을 못 읽어도 대체 자체를 막지는 않는다 — 이름만 비운다."""
        try:
            return next((room.name for room in gateway.rooms() if room.room_id == room_id), None)
        except RoomReservationError:
            return None

    def _available_room_views(self, error: RoomReservationError, source: dict[str, Any]) -> list[dict[str, Any]]:
        """거절과 함께 낼 「그 시간에 가능한 방」. 닿지도 못한 실패는 알 길이 없으므로 빈 목록이다."""
        if error.available:
            return [room.view() for room in error.available]
        gateway = self.room_gateway
        if gateway is None or isinstance(error, RoomAuthFailed | RoomGatewayUnavailable):
            return []
        try:
            return [room.view() for room in gateway.available(*local_slot(source["starts_at"], source["ends_at"]))]
        except RoomReservationError:
            return []

    def _sync_room_reservation(
        self,
        meeting_id: UUID,
        *,
        cancelled: bool,
    ) -> dict[str, Any] | None:
        """회의가 옮겨지거나 거둬지면 잡아 둔 자리도 따라간다. **실패해도 회의 동작은 그대로다.**"""
        with self._room_sync_lock(meeting_id):
            return self._sync_room_reservation_locked(meeting_id, cancelled=cancelled)

    def _sync_room_reservation_locked(
        self,
        meeting_id: UUID,
        *,
        cancelled: bool,
    ) -> dict[str, Any] | None:
        gateway = self.room_gateway
        with self._session_factory() as session:
            # PostgreSQL의 row lock은 다른 프로세스의 시간 변경과 recovery까지 같은 순서로 세운다.
            # 로컬 RLock은 SELECT FOR UPDATE가 no-op인 SQLite와 한 프로세스의 중복 replay를 맡는다.
            meeting = session.scalar(
                select(MeetingRecord).where(MeetingRecord.id == meeting_id).with_for_update()
            )
            if meeting is None:
                return None
            source = self._meetings(session).reservation_input(meeting_id)
            reservation: RoomReservation | None = source["reservation"]
            if reservation is None or reservation.status != STATUS_BOOKED or not reservation.external_id:
                return None
            try:
                if gateway is None:
                    raise RoomAuthFailed("예약 시스템 계정이 설정되어 있지 않습니다")
                if cancelled:
                    gateway.cancel(reservation.external_id)
                    settled = RoomReservation(
                        status=STATUS_CANCELLED,
                        room_id=reservation.room_id,
                        room_name=reservation.room_name,
                    )
                else:
                    # 옮긴 시간을 사옥 지역 시각으로 환산하려고 같은 함수를 쓴다 — 참석자는 싣지 않는다.
                    # (PUT 에 참석자를 실으면 그쪽이 조용히 망가뜨린다: `platform/the_connect.update` 주석.)
                    moved = build_reservation(
                        room_id=reservation.room_id or 0,
                        starts_at=source["starts_at"],
                        ends_at=source["ends_at"],
                        title=source["title"],
                        booker_name=source["owner_name"],
                        participant_emails=(),
                        outside_names=(),
                    )
                    gateway.update(
                        reservation.external_id,
                        date=moved.date,
                        start=moved.start,
                        end=moved.end,
                        room_id=reservation.room_id,
                    )
                    session.commit()
                    return {}
            except RoomOutcomeUnknown as error:
                # PUT/DELETE가 provider에 반영된 뒤 응답만 끊겼을 수 있다. 확인 API가 없으므로
                # 미실행으로 단정하거나 자동 재호출하지 않고 사람이 대조할 상태로 고정한다 (D11).
                logger.warning("회의 %s 예약 동기화 결과를 확인해야 합니다", meeting_id)
                settled = RoomReservation(
                    status=STATUS_NEEDS_VERIFICATION,
                    room_id=reservation.room_id,
                    room_name=reservation.room_name,
                    external_id=reservation.external_id,
                    reason=failure_reason(error),
                )
            except RoomReservationError as error:
                # 회의는 이미 옮겨졌거나 취소됐다. 자리만 어긋난 채로 **사실을 남긴다**.
                logger.warning("회의 %s 예약 동기화 실패: %s", meeting_id, failure_reason(error))
                settled = RoomReservation(
                    status=reservation.status,
                    room_id=reservation.room_id,
                    room_name=reservation.room_name,
                    external_id=reservation.external_id,
                    reason=failure_reason(error),
                )
            self._meetings(session).attach_reservation(
                # 거둔 자리는 장소를 비우고, 옮기다 어긋난 자리는 방 이름을 그대로 둔다.
                meeting_id, settled, location=None if cancelled else settled.room_name
            )
            session.commit()
        return {
            "location": None if cancelled else settled.room_name,
            "room_reservation": settled.view(),
        }

    def _room_sync_lock(self, meeting_id: UUID) -> threading.RLock:
        with self._room_sync_locks_guard:
            try:
                lock = self._room_sync_locks[meeting_id]
            except KeyError:
                lock = threading.RLock()
                self._room_sync_locks[meeting_id] = lock
            return lock

    def my_work(self, principal: Principal, *, include_closed: bool = False) -> list[TaskListEntry]:
        """Tasks the principal currently holds an active assignment for."""
        with self._session_factory() as session:
            return self._tasks(session).my_work(principal, include_closed=include_closed)

    def list_meetings(self, principal: Principal) -> list[dict[str, Any]]:
        """캘린더·MCP가 읽는 투영. 회의 화면은 `meeting_board`를 읽는다."""
        with self._session_factory() as session:
            result = self._meetings(session).list(principal)
            # 조회 시점 자동 취소 판정이 상태를 옮겼을 수 있다 — 읽기지만 그 판정을 휘발시키지 않는다.
            session.commit()
            return result

    def meeting_board(self, principal: Principal, *, cursor: str | None = None) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._meetings(session).board(principal, cursor=cursor)
            # 조회 시점 자동 취소 판정이 상태를 옮겼을 수 있다 — 읽기지만 쓰기를 남긴다.
            session.commit()
            return result

    def my_meetings(self, principal: Principal) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            result = self._meetings(session).my_meetings(principal)
            session.commit()
            return result

    def get_meeting(self, principal: Principal, meeting_id: UUID) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._meetings(session).get(principal, meeting_id)
            session.commit()
            return result

    def create_meeting(
        self,
        principal: Principal,
        *,
        room_id: int | None = None,
        idempotency_key: str | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        """회의를 세운다. 회의실을 골랐으면 **자리를 먼저 잡고, 잡힌 뒤에** 회의를 세운다 (D36-2).

        자리를 못 잡으면 회의도 서지 않는다. provider POST 전에는 로컬 규칙과 durable attempt를 먼저
        확정하고, 응답을 잃으면 같은 key로 다시 보내지 않은 채 확인 필요 회의를 남긴다. 예약 뒤 로컬
        저장이 실패해도 attempt에 성공 결과가 남으므로 재요청은 provider를 부르지 않고 저장부터 잇는다.
        """
        if room_id is None:
            with self._session_factory() as session:
                result = self._meetings(session).create(principal, **fields)
                session.commit()
                return result

        request_key = self._room_creation_request_key(idempotency_key)
        request = MeetingReservationInput.model_validate({**fields, "room_id": room_id})
        fingerprint = self._room_creation_fingerprint(request)
        with self._session_factory() as session:
            meetings = self._meetings(session)
            meetings.validate_creation(principal, **request.values())
            source = meetings.reservation_draft(
                principal,
                title=request.title,
                starts_at=request.starts_at,
                ends_at=request.ends_at,
                attendee_ids=request.attendee_ids,
                external_attendees=request.external_attendees,
            )
        with self._room_creation_lock(str(principal.id), request_key):
            reservation = self._resolve_room_creation_attempt(principal, request_key, request, source)
            with self._session_factory() as session:
                attempt = session.scalar(
                    select(MeetingRoomCreationAttemptRecord)
                    .where(
                        MeetingRoomCreationAttemptRecord.owner_id == str(principal.id),
                        MeetingRoomCreationAttemptRecord.request_key == request_key,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if attempt is None or attempt.payload_fingerprint != fingerprint:  # pragma: no cover - guarded above
                    raise RoomCreationIdempotencyConflict("회의실 예약 시도 원장이 바뀌었습니다")
                meetings = self._meetings(session)
                if attempt.meeting_id is not None:
                    return meetings.get(principal, attempt.meeting_id)
                created = meetings.create(principal, **request.values())
                meeting_id = UUID(created["meeting"]["meeting_id"])
                meetings.attach_reservation(meeting_id, reservation, location=reservation.room_name)
                attempt.meeting_id = meeting_id
                attempt.updated_at = datetime.now(UTC)
                session.commit()
        with self._session_factory() as session:
            return self._meetings(session).get(principal, meeting_id)

    def _release_orphan_reservation(self, reservation: RoomReservation) -> None:
        gateway = self.room_gateway
        if gateway is None or not reservation.external_id:
            return
        try:
            gateway.cancel(reservation.external_id)
        except RoomReservationError as error:
            # 거두지도 못했다. 회의는 어차피 서지 않으므로 사실만 남긴다 — 사람이 예약 시스템에서 지운다.
            logger.warning("주인 없는 회의실 예약을 거두지 못했습니다: %s", failure_reason(error))

    def _compensate_room_creation_attempt(
        self,
        owner_id: str,
        request_key: str,
        reservation: RoomReservation,
    ) -> None:
        """Cancel a known booking after Action rollback and record whether retry is safe."""
        gateway = self.room_gateway
        if gateway is None or reservation.status != STATUS_BOOKED or not reservation.external_id:
            return
        pending = RoomReservation(
            status=STATUS_NEEDS_VERIFICATION,
            room_id=reservation.room_id,
            room_name=reservation.room_name,
            external_id=reservation.external_id,
            reason="reservation_compensation_pending",
            replaced=reservation.replaced,
            requested_room_name=reservation.requested_room_name,
        )
        # This write must become durable before DELETE. A crash at any later point can
        # then only replay a needs-verification receipt, never a booked reservation
        # that may already have been cancelled.
        self._persist_room_creation_compensation(
            owner_id,
            request_key,
            status=STATUS_NEEDS_VERIFICATION,
            reservation=pending,
            failure_reason=pending.reason,
        )
        try:
            gateway.cancel(reservation.external_id)
        except RoomReservationError as error:
            uncertain = RoomReservation(
                status=STATUS_NEEDS_VERIFICATION,
                room_id=reservation.room_id,
                room_name=reservation.room_name,
                external_id=reservation.external_id,
                reason=failure_reason(error),
                replaced=reservation.replaced,
                requested_room_name=reservation.requested_room_name,
            )
            self._persist_room_creation_compensation(
                owner_id,
                request_key,
                status=STATUS_NEEDS_VERIFICATION,
                reservation=uncertain,
                failure_reason=uncertain.reason,
            )
        else:
            cancelled = RoomReservation(
                status=STATUS_CANCELLED,
                room_id=reservation.room_id,
                room_name=reservation.room_name,
                replaced=reservation.replaced,
                requested_room_name=reservation.requested_room_name,
            )
            self._persist_room_creation_compensation(
                owner_id,
                request_key,
                status="compensated",
                reservation=cancelled,
                failure_reason=None,
            )

    def _persist_room_creation_compensation(
        self,
        owner_id: str,
        request_key: str,
        *,
        status: str,
        reservation: RoomReservation,
        failure_reason: str | None,
    ) -> None:
        with self._room_creation_lock(owner_id, request_key):
            with self._session_factory() as attempt_session:
                attempt = attempt_session.scalar(
                    select(MeetingRoomCreationAttemptRecord)
                    .where(
                        MeetingRoomCreationAttemptRecord.owner_id == owner_id,
                        MeetingRoomCreationAttemptRecord.request_key == request_key,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if attempt is None or attempt.meeting_id is not None:
                    return
                attempt.status = status
                attempt.reservation = reservation.stored()
                attempt.failure_reason = failure_reason
                attempt.failure_message = None
                attempt.updated_at = datetime.now(UTC)
                attempt_session.commit()

    def quick_start_meeting(self, principal: Principal) -> dict[str, Any]:
        """[회의 시작] 바로 시작 — 세우자마자 「진행 중」이므로 웜스타트도 여기서 건다.

        `start_meeting` 과 **같은 자리**다: 전이를 커밋한 뒤 백그라운드로 연다. 이 한 줄이 없으면
        바로 시작한 회의에는 AI 세션이 서지 않아 전사도 메모도 배치를 한 번도 돌리지 못한다.
        """
        with self._session_factory() as session:
            result = self._meetings(session).quick_start(principal)
            session.commit()
        self._meeting_batch.launch_warm_start(result["meeting"]["meeting_id"])
        return result

    def update_meeting(self, principal: Principal, meeting_id: UUID, changes: dict[str, Any]) -> dict[str, Any]:
        """정보를 고치고, 잡아 둔 자리가 있으면 시간을 따라 옮긴다 — **옮기다 실패해도 편집은 남는다.**"""
        with self._session_factory() as session:
            result = self._meetings(session).update_info(principal, meeting_id, changes)
            session.commit()
        if not {"starts_at", "ends_at"} & set(changes):
            return result
        self._sync_room_reservation(meeting_id, cancelled=False)
        with self._session_factory() as session:
            return self._meetings(session).get(principal, meeting_id)

    def cancel_meeting(self, principal: Principal, meeting_id: UUID) -> None:
        """회의를 취소하면 잡아 둔 자리도 거둔다 — 거두지 못해도 회의 취소는 그대로 선다."""
        with self._session_factory() as session:
            self._meetings(session).cancel(principal, meeting_id)
            session.commit()
        self._sync_room_reservation(meeting_id, cancelled=True)

    def delete_meeting_note(self, principal: Principal, meeting_id: UUID) -> None:
        with self._session_factory() as session:
            self._meetings(session).delete_note(principal, meeting_id)
            session.commit()

    def start_meeting(self, principal: Principal, meeting_id: UUID) -> dict[str, Any]:
        """[회의 시작] — 전이만 하고 즉시 답한다. AI 세션은 **커밋 뒤 백그라운드**로 연다 (SPEC-004 §7.1 웜스타트).

        여기서 기다리면 「회의 시작」이 provider 를 기다리게 된다. 열기가 실패해도 회의는 정상이다.
        """
        with self._session_factory() as session:
            result = self._meetings(session).start(principal, meeting_id)
            session.commit()
        self._meeting_batch.launch_warm_start(str(meeting_id))
        return result

    def end_meeting(self, principal: Principal, meeting_id: UUID) -> dict[str, Any]:
        """[회의 종료] — 상태를 옮기고, 살아 있는 스트림이 있으면 서버가 닫는다 (SCAX-SPEC-004 §5.3 정상 종료).

        합성 job 은 SCAX-WP-004 가 이 표면을 소비해 얹는다.
        """
        with self._session_factory() as session:
            result = self._meetings(session).end(principal, meeting_id)
            # 합성은 사람이 기다릴 일이 아니다 — 잡으로 넘기고 즉시 답한다 (SCAX-SPEC-004 §8-1).
            self._enqueue_finalize(session, meeting_id)
            session.commit()
        return result

    async def close_meeting_stream(self, meeting_id: UUID) -> bool:
        return await self._meeting_stream.close_for_end(str(meeting_id))

    def add_meeting_agenda(self, principal: Principal, meeting_id: UUID, title: str) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._meetings(session).add_agenda(principal, meeting_id, title)
            session.commit()
        # 방에 붙은 모두가 새 안건을 그 자리에서 본다 (D45) — 커밋 뒤에 민다: 저장되지 않은 안건이
        # 남의 화면에 먼저 서면 안 된다. 회의 중이 아니면 방이 없고, 그때는 조용히 건너뛴다.
        self._meeting_stream.push_agenda_added_threadsafe(str(meeting_id), agenda=result)
        return result

    def update_meeting_agenda(self, principal: Principal, meeting_id: UUID, agenda_id: UUID, changes: dict[str, Any]) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._meetings(session).update_agenda(principal, meeting_id, agenda_id, changes)
            session.commit()
            return result

    def remove_meeting_agenda(self, principal: Principal, meeting_id: UUID, agenda_id: UUID) -> None:
        with self._session_factory() as session:
            self._meetings(session).remove_agenda(principal, meeting_id, agenda_id)
            session.commit()

    def append_meeting_line(
        self, principal: Principal, meeting_id: UUID, agenda_id: UUID, *, track: str, text: str
    ) -> dict[str, Any]:
        """트랙을 골라 줄 하나. 시나리오 seed 와 합성이 쓰는 낮은 표면이다."""
        with self._session_factory() as session:
            result = self._meetings(session).append_line(principal, meeting_id, agenda_id, track=track, text=text)
            session.commit()
            return result

    def write_meeting_memo(self, principal: Principal, meeting_id: UUID, agenda_id: UUID, text: str) -> dict[str, Any]:
        """메모 한 줄. 커밋 뒤 배치 트리거를 평가한다 — 메모는 AI 가 읽을 입력이기도 하다."""
        with self._session_factory() as session:
            result = self._meetings(session).write_memo(principal, meeting_id, agenda_id, text)
            session.commit()
        # 방에 붙은 모두가 그 줄을 **그 자리에서** 본다 — 참여자는 폴링하지 않는다 (사용자 결정 2026-09-11).
        # 커밋 뒤에 민다: 저장되지 않은 줄이 남의 화면에 먼저 서면 안 된다.
        self._meeting_stream.push_memo_line_threadsafe(
            str(meeting_id), agenda_id=str(agenda_id), line=result
        )
        # 메모가 안건을 옮겼다는 것은 화제가 바뀌었다는 뜻이다 — 안건 전환 트리거의 자리다.
        self._meeting_batch.schedule(str(meeting_id), CAUSE_AGENDA_SWITCH)
        return result

    # --- 회의 자료 (SCAX-WP-005) ---------------------------------------------------

    def _meeting_materials(self, session: Any) -> MeetingMaterialApplication:
        extractions = SqlAlchemyMaterialExtractionRepository(session)
        return MeetingMaterialApplication(
            _MeetingMaterialGate(self, session),
            SqlAlchemyAttachmentRepository(session),
            self._material_storage,
            extractions,
            MaterialJobQueue(self.job_queue(session)),
        )

    def list_meeting_materials(self, principal: Principal, meeting_id: UUID) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return self._meeting_materials(session).list(principal, meeting_id)

    def attach_meeting_materials(
        self, principal: Principal, meeting_id: UUID, uploads: list[MaterialUpload]
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            attached, failed = self._meeting_materials(session).attach(principal, meeting_id, uploads)
            session.commit()
        return {
            "attached": attached,
            "failed": [{"name": row.name, "reason": row.reason} for row in failed],
        }

    def open_meeting_material(self, principal: Principal, meeting_id: UUID, material_id: UUID) -> tuple[dict[str, Any], bytes]:
        with self._session_factory() as session:
            return self._meeting_materials(session).open(principal, meeting_id, material_id)

    def detach_meeting_material(self, principal: Principal, meeting_id: UUID, material_id: UUID) -> None:
        with self._session_factory() as session:
            self._meeting_materials(session).detach(principal, meeting_id, material_id)
            session.commit()

    def meeting_viewers(self, principal: Principal, meeting_id: UUID) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return self._meetings(session).viewers(principal, meeting_id)

    def share_meeting_with(self, principal: Principal, meeting_id: UUID, member_ids: list[str]) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            result = self._meetings(session).share_many(principal, meeting_id, member_ids)
            session.commit()
            return result

    def share_meeting(self, principal: Principal, meeting_id: UUID, member_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._meetings(session).share(principal, meeting_id, member_id)
            session.commit()
            return result

    def revoke_meeting_share(self, principal: Principal, meeting_id: UUID, member_id: str) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            result = self._meetings(session).revoke_share(principal, meeting_id, member_id)
            session.commit()
            return result

    # --- 알림 (main #3) ------------------------------------------------------------

    def _notifications(self, session: Any) -> NotificationApplication:
        return NotificationApplication(SqlAlchemyNotificationRepository(session), self._meetings(session), self._work_requests(session))

    def list_notifications(self, principal: Principal) -> list[NotificationView]:
        with self._session_factory() as session:
            return self._notifications(session).list(principal)

    def mark_notification_read(self, principal: Principal, notification_id: UUID) -> NotificationView:
        with self._session_factory() as session:
            result = self._notifications(session).mark_read(principal, notification_id)
            session.commit()
            return result

    def _authorized_notification_view(self, session: Any, principal: Principal, row: Any) -> dict[str, object] | None:
        """알림 한 줄을 **지금 읽을 수 있는지 다시 물어** 낸다 — 알림이 권한을 넘겨주지 않는다.

        회의는 판을 쌓지 않으므로 상세에 `version` 이 없다(SCAX-SPEC-004) — 그 자리는 `None` 이고,
        제목은 사람이 지은 것이 먼저, 없으면 합성이 낸 후보다. 읽지 못하는 자원이면 그 줄을 내지 않는다.
        """
        try:
            if row.resource_type == "meeting":
                meeting = self._meetings(session).get(principal, UUID(row.resource_id))["meeting"]
                title = meeting.get("title") or meeting.get("title_candidate") or ""
                version = None
            elif row.resource_type == "work_request":
                resource = self._work_requests(session).get(principal, UUID(row.resource_id))
                title, version = resource["title"], resource["version"]
            else:
                return None
        except (MeetingError, WorkRequestError, ValueError):
            return None
        return notification_view(row, title=str(title), version=None if version is None else int(version))

    # --- 회의 스트림 (SCAX-WP-002) ------------------------------------------------

    def promote_meeting_todo(
        self,
        principal: Principal,
        meeting_id: UUID,
        todo_id: UUID,
        *,
        assignee_id: str,
        title: str | None = None,
        description: str | None = None,
        due_date: Any = None,
        checklist: list[str] | None = None,
    ) -> dict[str, Any]:
        """후보 하나를 업무 요청으로 보낸다 — **갈래를 두지 않는다** (SCAX-SPEC-004 §9-5).

        모달에서 고친 값이 우선이고 비면 후보값이 그대로 간다. 출처 두 id 는 `reference` 에서 나온다.
        요청을 만드는 것은 work 모듈이다 — 회의는 무엇을 넘기는지까지다.
        """
        with self._session_factory() as session:
            meetings = self._meetings(session)
            meeting, todo = meetings.todo_for_promotion(principal, meeting_id, todo_id)
            reference = dict(todo.reference or {})
            created = self._work_requests(session).create(
                principal,
                (title or todo.title).strip(),
                assignee_id,
                None,
                description=description if description is not None else todo.description,
                due_date=due_date if due_date is not None else todo.due_candidate,
                checklist=checklist if checklist is not None else list(todo.checklist_candidate or []),
                source_meeting_id=meeting.id,
                source_agenda_id=UUID(str(reference.get("agenda_id") or todo.agenda_id)),
                # 담당이 자기 자신이어도 요청으로 간다 — Task 를 바로 세우는 갈래가 없다 (SPEC-004 §9-5).
                allow_self_assignment=True,
                # **보내는 쪽은 시스템(회의)이고 누른 사람은 여기 남는다** (D40). 이 한 값이 요청자·조직 경계·
                # cc·진행 기록 첫 줄을 한꺼번에 정한다 — 회의 참석자 예외를 따로 실어 보낼 일이 없어졌다.
                promoted_by_member_id=str(principal.id),
            )
            view = meetings.link_promoted_todo(todo, work_request_id=UUID(str(created["request_id"])))
            session.commit()
            return view

    def remove_meeting_todo(self, principal: Principal, meeting_id: UUID, todo_id: UUID) -> None:
        with self._session_factory() as session:
            self._meetings(session).remove_todo(principal, meeting_id, todo_id)
            session.commit()

    def export_meeting(self, principal: Principal, meeting_id: UUID) -> dict[str, str]:
        """마지막 저장분을 HTML 한 벌로 — 회의 정보 · 안건별 줄 · 다음 할 일 (SCAX-SPEC-004 §8-10)."""
        with self._session_factory() as session:
            document = self._meetings(session).export(principal, meeting_id)
            session.commit()
        return {"filename": _export_filename(document["meeting"]), "html": render_meeting_html(document)}

    @property
    def meeting_finalize(self) -> MeetingFinalizeService:
        return self._meeting_finalize

    def retry_meeting_finalize(self, principal: Principal, meeting_id: UUID) -> dict[str, Any]:
        """[다시 시도] — 「실패」에서만 상태를 되돌리고 잡을 다시 건다."""
        with self._session_factory() as session:
            result = self._meetings(session).retry_finalize(principal, meeting_id)
            self._enqueue_finalize(session, meeting_id)
            session.commit()
        return result

    def _enqueue_finalize(self, session: Any, meeting_id: UUID) -> None:
        """합성 잡 하나 — 회의당 idempotency key 가 회차마다 갈린다(재시도가 새 배달이다)."""
        from ax_workspace.modules.jobs.domain import JOB_KIND_MEETING_FINALIZE, JobEnvelope

        self.job_queue(session).enqueue(
            JobEnvelope(
                kind=JOB_KIND_MEETING_FINALIZE,
                ordering_key=str(meeting_id),
                idempotency_key=f"{JOB_KIND_MEETING_FINALIZE}:{meeting_id}:{uuid4().hex[:8]}",
                payload={"meeting_id": str(meeting_id)},
            )
        )

    def finalize_meeting(self, meeting_id: UUID) -> bool:
        """worker 가 부르는 진입점. 성공하면 True."""
        return self._meeting_finalize.run(str(meeting_id))

    def fail_meeting_finalize(self, meeting_id: UUID, reason: str) -> None:
        """합성이 **예외로** 끝나 상한까지 온 배달이 회의를 「실패」로 보내는 자리.

        여기까지 왔다는 것은 합성이 제 실패를 다루지 못했다는 뜻이다 — 회의를 「정리 중」에 두고
        떠나지 않는다 (SPEC-004 §5.1 · §8-8).
        """
        with self._session_factory() as session:
            self._meetings(session).fail_finalize(meeting_id, reason)
            session.commit()

    @property
    def meeting_batch(self) -> MeetingBatchService:
        """회의당 하나의 배치를 지키는 자리 — 프로세스 하나에 하나다."""
        return self._meeting_batch

    def meeting_batch_provider(self, tools: tuple[str, ...]) -> AiProvider:
        """배치가 쓰는 provider — 레지스트리가 준 도구만 여는 어댑터다.

        시험이 `_report_provider` 를 대역으로 갈아 끼우면 그 대역이 그대로 온다: 경계는 하나다.
        """
        if not isinstance(self._report_provider, (CodexCliProviderAdapter, ClaudeCliProviderAdapter)):
            return self._report_provider
        return create_conversation_provider(self._settings, enabled_tools=tools)

    def schedule_push_ai_batch(self, meeting_id: str, *, seq: int, agendas: list[dict[str, Any]]) -> None:
        """적재 커밋 직후 push. 스트림이 다른 event loop 에 살아 있어도 그 루프에서 깨운다."""
        self._meeting_stream.push_ai_batch_threadsafe(meeting_id, seq=seq, agendas=agendas)

    @property
    def meeting_stream(self) -> MeetingStreamService:
        """회의당 하나의 업스트림을 들고 있는 레지스트리 — 프로세스 하나에 하나다."""
        return self._meeting_stream

    def meeting_transcript(self, principal: Principal, meeting_id: UUID) -> list[dict[str, Any]]:
        """「스크립트」 탭이 읽는 원문 — 확정 발화뿐이다."""
        with self._session_factory() as session:
            return self._meetings(session).transcript(principal, meeting_id)

    def push_meeting_ai_batch(self, meeting_id: UUID, *, seq: int, agendas: list[dict[str, Any]]) -> Any:
        """AI 트랙 전체를 구독자에게 밀어 주는 통로. SCAX-WP-003 이 부른다 — 지금은 호출자가 없다."""
        return self._meeting_stream.push_ai_batch(str(meeting_id), seq=seq, agendas=agendas)

    def unprocessed_meeting_transcript(self, meeting_id: UUID, *, after_seq: int = 0) -> list[dict[str, Any]]:
        """배치가 아직 읽지 않은 확정 블록. SCAX-WP-003 의 커서 — 지금은 호출자가 없다."""
        with self._session_factory() as session:
            return self._meetings(session).unprocessed_transcript_cursor(meeting_id, after_seq=after_seq)

    def organization_tree(self, principal: Principal) -> list[OrganizationUnitView]:
        with self._session_factory() as session:
            return OrganizationApplication(SqlAlchemyOrganizationRepository(session)).organization_tree(principal)

    def organization_unit_members(self, principal: Principal, unit_id: str) -> list[UnitMemberView]:
        with self._session_factory() as session:
            return OrganizationApplication(SqlAlchemyOrganizationRepository(session)).unit_members(principal, unit_id)

    def organization_member_detail(self, principal: Principal, member_id: str) -> MemberDetailView:
        with self._session_factory() as session:
            return OrganizationApplication(SqlAlchemyOrganizationRepository(session)).member_detail(principal, member_id)

    def organization_member_history(self, principal: Principal, member_id: str, axis: str) -> list[MemberAxisHistoryView]:
        with self._session_factory() as session:
            return OrganizationApplication(SqlAlchemyOrganizationRepository(session)).member_axis_history(
                principal, member_id, axis
            )

    def organization_activity(
        self, principal: Principal, *, unit_id: str | None = None, limit: int = 50, cursor: str | None = None
    ) -> list[OrganizationActivityView]:
        with self._session_factory() as session:
            return OrganizationApplication(SqlAlchemyOrganizationRepository(session)).organization_activity(
                principal, unit_id=unit_id, limit=limit, cursor=cursor
            )

    def my_organization_profile(self, principal: Principal) -> MyOrganizationProfileView:
        with self._session_factory() as session:
            return OrganizationApplication(SqlAlchemyOrganizationRepository(session)).my_profile(principal)

    def set_assistant_character(
        self, principal: Principal, character_key: str, expected_version: int
    ) -> AssistantCharacterResult:
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

    def installed_access_roles(self, principal: Principal) -> list[InstalledRoleView]:
        with self._session_factory() as session:
            return AccessAdministration(SqlAlchemyOrganizationRepository(session)).installed_roles(principal)

    def member_access(self, principal: Principal, member_id: str) -> MemberAccessView:
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

    def member_directory(self, principal: Principal) -> list[MemberDirectoryView]:
        with self._session_factory() as session:
            return OrganizationApplication(SqlAlchemyOrganizationRepository(session)).member_directory(principal)

    def generate_daily_report_draft(
        self,
        principal: Principal,
        report_date: str,
        causation_key: str | None = None,
    ) -> ReportGeneratedResult:
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

    def request_daily_report_draft(
        self,
        principal: Principal,
        report_date: str,
        causation_key: str | None = None,
    ) -> ReportStatusResult:
        with self._session_factory() as session:
            reports = self._reports(session)
            generation = reports.request_generation(principal, report_date, causation_key)
            if generation.state in {"queued", "running"}:
                self._report_queue(session).enqueue(
                    DailyReportGenerationJob(generation.id, str(principal.id))
                )
            result = reports.status_for_date(principal, report_date)
            session.commit()
            return result

    def daily_report_generation_input(
        self,
        generation_id: UUID,
        *,
        attempt: int,
        lease_token: UUID,
        stale_after_seconds: int,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            repository = SqlAlchemyDailyReportRepository(session)
            generation = repository.generation(generation_id)
            principal = (
                SqlAlchemyOrganizationRepository(session).principal_for(generation.owner_id)
                if generation is not None
                else None
            )
            result = self._reports(session).generation_input(
                principal,
                generation_id,
                attempt=attempt,
                lease_token=lease_token,
                stale_after_seconds=stale_after_seconds,
            )
            session.commit()
            return result

    def execute_daily_report_generation(
        self,
        generation_id: UUID,
        *,
        lease_token: UUID,
    ) -> ReportGeneratedResult:
        with self._session_factory() as session:
            repository = SqlAlchemyDailyReportRepository(session)
            generation = repository.generation(generation_id)
            principal = (
                SqlAlchemyOrganizationRepository(session).principal_for(generation.owner_id)
                if generation is not None
                else None
            )
            if principal is None:
                raise DailyReportAccessDenied("daily report generation actor is no longer authorized")
            try:
                result = self._reports(session).execute_generation(
                    principal, generation_id, lease_token=lease_token
                )
                session.commit()
                return result
            except ProviderFailure:
                session.commit()
                raise

    def heartbeat_daily_report_generation(
        self,
        generation_id: UUID,
        *,
        lease_token: UUID,
        stage_timeout_seconds: int,
        total_timeout_seconds: int,
    ) -> bool:
        with self._session_factory() as session:
            active = self._reports(session).heartbeat_generation(
                generation_id,
                lease_token=lease_token,
                stage_timeout_seconds=stage_timeout_seconds,
                total_timeout_seconds=total_timeout_seconds,
            )
            session.commit()
            return active

    def retry_daily_report_generation(
        self, generation_id: UUID, *, lease_token: UUID, code: str
    ) -> None:
        with self._session_factory() as session:
            self._reports(session).retry_generation(generation_id, lease_token=lease_token, code=code)
            session.commit()

    def fail_daily_report_generation(
        self, generation_id: UUID, *, lease_token: UUID, code: str
    ) -> None:
        with self._session_factory() as session:
            self._reports(session).fail_generation(generation_id, lease_token=lease_token, code=code)
            session.commit()

    def mark_daily_report_generation_needs_verification(
        self, generation_id: UUID, *, lease_token: UUID, code: str
    ) -> None:
        with self._session_factory() as session:
            self._reports(session).mark_generation_needs_verification(
                generation_id, lease_token=lease_token, code=code
            )
            session.commit()

    def abandon_daily_report_generation(self, generation_id: UUID, *, lease_token: UUID) -> str:
        with self._session_factory() as session:
            outcome = self._reports(session).abandon_generation(generation_id, lease_token=lease_token)
            session.commit()
            return outcome

    def edit_daily_report(
        self,
        principal: Principal,
        report_id: str,
        draft_id: str,
        expected_version: int,
        body: str,
        include_source_refs: list[dict[str, Any]],
        exclude_source_refs: list[dict[str, Any]],
    ) -> ReportDraftResult:
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
    ) -> ReportSubmissionResult:
        with self._session_factory() as session:
            result = self._reports(session).submit(
                principal, report_id, draft_id, expected_version, reason
            )
            session.commit()
            return result

    def daily_report_history(self, principal: Principal, report_id: str) -> ReportHistoryResult:
        with self._session_factory() as session:
            return self._reports(session).history(principal, report_id)

    def daily_report_recent(self, principal: Principal, *, limit: int = 3) -> list[ReportRecentView]:
        with self._session_factory() as session:
            return self._reports(session).recent(principal, limit=limit)

    def daily_report_status(self, principal: Principal, report_date: str) -> ReportStatusResult:
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
            publish_submission=lambda revision_id: self._register_native_material(session, "report_submission", revision_id),
        )

    def _meetings(self, session: Any) -> MeetingApplication:
        return MeetingApplication(
            SqlAlchemyMeetingRepository(session),
            recordings=self._recording_storage,
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
    ) -> TaskMutationResult:
        with self._session_factory() as session:
            result = self._tasks(session).create_self(
                principal, title, causation_key,
                description=description, start_date=start_date, due_date=due_date, checklist=checklist,
                reference_task_ids=reference_task_ids, parent_task_id=parent_task_id, project_id=project_id,
            )
            session.commit()
            return result

    def update_task(self, principal: Principal, task_id: UUID, expected_version: int, changes: dict[str, Any]) -> TaskMutationResult:
        with self._session_factory() as session:
            result = self._tasks(session).update(task_id, principal, expected_version, changes)
            session.commit()
            return result

    def attach_task_material_reference(
        self, principal: Principal, task_id: UUID, *, kind: str, resource_type: str, resource_id: str
    ) -> TaskMaterialResult:
        """Point a Task at another thing inside SCAX, resolved through that thing's own authorization."""
        with self._session_factory() as session:
            result = self._materials(session).attach_reference(
                principal, task_id, kind=kind, resource_type=resource_type, resource_id=resource_id
            )
            session.commit()
            return result

    def attach_task_material_link(self, principal: Principal, task_id: UUID, *, kind: str, url: str, label: str) -> TaskMaterialResult:
        """Point a Task at work that lives somewhere else. No bytes are held and no revision is pinned."""
        with self._session_factory() as session:
            result = self._materials(session).attach_link(principal, task_id, kind=kind, url=url, label=label)
            session.commit()
            return result

    def add_task_reference(self, principal: Principal, task_id: UUID, referenced_task_id: UUID) -> TaskReferenceResult:
        with self._session_factory() as session:
            result = self._tasks(session).add_reference(principal, task_id, referenced_task_id)
            session.commit()
            return result

    def release_task_reference(self, principal: Principal, task_id: UUID, reference_id: UUID) -> TaskReferenceReleaseResult:
        with self._session_factory() as session:
            result = self._tasks(session).release_reference(principal, task_id, reference_id)
            session.commit()
            return result

    def submit_task_completion(
        self, principal: Principal, task_id: UUID, expected_version: int, *, summary: str, output_material_ids: list[UUID] | None = None
    ) -> TaskCompletionResult:
        with self._session_factory() as session:
            result = self._tasks(session).submit_completion(
                principal, task_id, expected_version, summary=summary, output_material_ids=output_material_ids
            )
            session.commit()
            return result

    def reextract_material(self, *, attachment_id: UUID, parser_version: str) -> dict[str, Any]:
        """Read a file again with a newer parser. The older reading is superseded, never rewritten."""
        with self._session_factory() as session:
            attachment = session.get(AttachmentRecord, attachment_id)
            if attachment is None:
                raise MaterialNotFound("attachment was not found")
            extractions = SqlAlchemyMaterialExtractionRepository(session)
            extraction = extractions.request(attachment, parser_version=parser_version)
            self._material_queue(session).enqueue(MaterialExtractionJob(extraction.id, attachment.id, attachment.uploaded_by))
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

    def graph_overview(
        self,
        principal: Principal,
        *,
        view: str = "member",
        limit: int = 120,
        execution_id: UUID | None = None,
    ) -> GraphOverviewResult:
        with self._session_factory() as session:
            result = self._graph(session).overview(principal, view=view, limit=limit)
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

    def graph_search(self, principal: Principal, query: str, limit: int = 20, *, execution_id: UUID | None = None) -> GraphSearchResult:
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

    def graph_neighbors(self, principal: Principal, node: str, limit: int = 20, *, execution_id: UUID | None = None) -> GraphNeighborsResult:
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
                actions=self._action_repository(session),
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
            readable_references = resolver.resolve(principal, references)
            for reference in readable_references:
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
                        "body": answer_context_excerpt(str(message["body"]), message.get("answer_document"), readable_references),
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

    def task_history(self, principal: Principal, task_id: UUID) -> TaskHistoryResult:
        with self._session_factory() as session:
            return self._tasks(session).history(principal, task_id)

    def task_history_diff(self, principal: Principal, task_id: UUID, before: int, after: int) -> TaskHistoryDiffResult:
        with self._session_factory() as session:
            return self._tasks(session).history_diff(principal, task_id, before, after)

    def list_task_materials(self, principal: Principal, task_id: UUID) -> list[TaskMaterialView]:
        with self._session_factory() as session:
            return self._materials(session).list(principal, task_id)

    def attach_task_material(
        self, principal: Principal, task_id: UUID, *, kind: str, name: str, content_type: str, data: bytes
    ) -> TaskMaterialResult:
        with self._session_factory() as session:
            result = self._materials(session).attach(principal, task_id, kind=kind, name=name, content_type=content_type, data=data)
            session.commit()
            return result

    def open_task_material(self, principal: Principal, task_id: UUID, material_id: UUID) -> tuple[dict[str, Any], bytes]:
        with self._session_factory() as session:
            return self._materials(session).open(principal, task_id, material_id)

    def detach_task_material(self, principal: Principal, task_id: UUID, binding_id: UUID) -> TaskMaterialResult:
        with self._session_factory() as session:
            result = self._materials(session).detach(principal, task_id, binding_id)
            session.commit()
            return result

    def search_materials(self, principal: Principal, query: str, *, execution_id: UUID | None = None, **filters: Any) -> MaterialSearchResult:
        with self._session_factory() as session:
            extractions = SqlAlchemyMaterialExtractionRepository(session)
            result = MaterialSearchApplication(SessionMaterialOwners(self, session), extractions,
                                               LexicalMaterialRetriever(extractions, SqlChunkIndex(session))).search(principal, query, **filters)
            if execution_id is not None:
                SqlAlchemyMaterialEvidenceRepository(session).record(execution_id, str(principal.id), result["query"], result["results"])
            session.commit()
            return result

    def material_metadata(self, principal: Principal, material_id: UUID) -> MaterialMetadataResult:
        with self._session_factory() as session:
            result = self._material_metadata(session, principal, material_id)
            session.commit()
            return result

    def _material_metadata(self, session: Any, principal: Principal, material_id: UUID) -> MaterialMetadataResult:
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

    def backfill_native_materials(self, principal: Principal, *, limit: int = 100) -> int:
        """Explicit maintenance for legacy revisions within the caller's current read scope."""
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        from ax_workspace.modules.organization_access.domain import MEETING_READ, DAILY_REPORT_READ

        with self._session_factory() as session:
            principal = SessionMaterialOwners(self, session).current_principal(principal)
            if principal is None:
                return 0
            native = NativeMaterialRepository(session)

            def revisions():
                if MEETING_READ in principal.capabilities:
                    meetings = self._meetings(session)
                    for meeting in meetings.readable_rows(principal):
                        yield {
                            "kind": MEETING_TRANSCRIPT,
                            "revision_id": meeting["meeting_id"],
                        }
                if DAILY_REPORT_READ in principal.capabilities:
                    for revision in self._reports(session).material_revisions(principal, include_history=True):
                        yield {**revision, "kind": "report_submission"}

            registered = 0
            for revision in revisions():
                kind, identifier = revision["kind"], UUID(revision["revision_id"])
                if native.registered(kind, identifier) is not None:
                    continue
                try:
                    self._register_native_material(session, kind, identifier)
                except ValueError:
                    # A current meeting has no native source until at least one
                    # transcript block has settled.
                    continue
                registered += 1
                if registered == limit:
                    break
            session.commit()
            return registered

    def _register_native_material(self, session: Any, kind: str, revision_id: UUID) -> None:
        binding, attachment = NativeMaterialRepository(session).ensure(kind, revision_id)
        if attachment.lifecycle == "purged" or binding.unbound_at is not None:
            return
        extraction = SqlAlchemyMaterialExtractionRepository(session).request(attachment)
        if extraction.status == "queued":
            self._material_queue(session).enqueue(MaterialExtractionJob(extraction.id, attachment.id))

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

    def create_material_folder(self, principal: Principal, **fields: Any) -> FolderView:
        with self._session_factory() as session:
            result = self._material_folders(session).create(principal, **fields)
            session.commit()
            return result

    def list_material_folders(self, principal: Principal) -> list[FolderView]:
        with self._session_factory() as session:
            return self._material_folders(session).list_for(principal)

    def list_folder_materials(self, principal: Principal, folder_id: UUID) -> list[FolderMaterialView]:
        with self._session_factory() as session:
            return self._material_folders(session).materials(principal, folder_id)

    def upload_folder_material(self, principal: Principal, folder_id: UUID, **fields: Any) -> FolderMaterialView:
        with self._session_factory() as session:
            result = self._material_folders(session).upload(principal, folder_id, **fields)
            session.commit()
            return result

    def open_folder_material(self, principal: Principal, folder_id: UUID, material_id: UUID) -> tuple[FolderMaterialView, bytes]:
        with self._session_factory() as session:
            return self._material_folders(session).open(principal, folder_id, material_id)

    def detach_folder_material(self, principal: Principal, folder_id: UUID, material_id: UUID) -> FolderDetachResult:
        with self._session_factory() as session:
            result = self._material_folders(session).detach(principal, folder_id, material_id)
            session.commit()
            return result

    def archive_material_folder(self, principal: Principal, folder_id: UUID) -> FolderArchiveResult:
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


    def _report_queue(self, session: Any) -> DailyReportGenerationQueue:
        return DailyReportGenerationQueue(self.job_queue(session))

    def _material_queue(self, session: Any) -> MaterialJobQueue:
        return MaterialJobQueue(self.job_queue(session))

    def _browser_interactions(self, session: Any) -> BrowserInteractionApplication:
        return BrowserInteractionApplication(SqlAlchemyBrowserInteractionRepository(session), SessionBrowserFileTargets(self, session), self._settings.web_origin)

    def request_file_attachment(self, principal: Principal, request: BrowserFileRequest) -> BrowserInteractionResult:
        with self._session_factory() as session:
            result = self._browser_interactions(session).request_file(principal, request)
            session.commit()
            return result

    def browser_interaction(self, principal: Principal, interaction_id: UUID) -> BrowserInteractionResult:
        with self._session_factory() as session:
            return self._browser_interactions(session).get(principal, interaction_id)

    def request_recording(self, principal: Principal, request: BrowserRecordingRequest) -> BrowserInteractionResult:
        with self._session_factory() as session:
            result = self._browser_interactions(session).request_recording(principal, request)
            session.commit()
            return result

    def start_browser_recording(self, principal: Principal, interaction_id: UUID, capture_id: UUID) -> BrowserInteractionResult:
        try:
            with self._session_factory() as session:
                result = self._browser_interactions(session).start_recording(principal, interaction_id, capture_id)
                session.commit()
                return result
        except BrowserInteractionConflict:
            raise
        except Exception as error:
            with self._session_factory() as session:
                recovered = self._browser_interactions(session).recover_recording_start_failure(
                    principal, interaction_id, capture_id, denied=isinstance(error, (ResourceNotFound, MeetingAccessDenied)))
                session.commit()
                if recovered is not None:
                    return recovered
            raise

    def stop_browser_recording(self, principal: Principal, interaction_id: UUID, capture_id: UUID, *, name: str, content_type: str, data: bytes) -> BrowserInteractionResult:
        try:
            with self._session_factory() as session:
                result = self._browser_interactions(session).stop_recording(principal, interaction_id, capture_id, name=name, content_type=content_type, data=data)
                session.commit()
                return result
        except BrowserInteractionConflict:
            raise
        except Exception:
            with self._session_factory() as session:
                recovered = self._browser_interactions(session).recover_recording_upload_failure(principal, interaction_id, capture_id, name=name, content_type=content_type, data=data)
                session.commit()
                if recovered is not None:
                    return recovered
            raise

    def upload_browser_file(self, principal: Principal, interaction_id: UUID, *, name: str, content_type: str, data: bytes) -> BrowserInteractionResult:
        try:
            with self._session_factory() as session:
                service = self._browser_interactions(session)
                reservation_id = service.prepare_action_upload(principal, interaction_id, name=name, content_type=content_type, data=data)
                if reservation_id is None:
                    result = service.upload(principal, interaction_id, name=name, content_type=content_type, data=data)
                    session.commit()
                    return result
                # The action file reservation survives process loss before storage I/O.
                session.commit()
            with self._session_factory() as session:
                result = self._browser_interactions(session).upload_reserved_action_file(principal, interaction_id, name=name, content_type=content_type, data=data)
                session.commit()
                return result
        except BrowserInteractionConflict:
            raise
        except Exception as error:
            # Roll back the owning effect first, then preserve the failed browser attempt.
            # A commit whose response was lost may already have completed; never overwrite it.
            with self._session_factory() as session:
                recovered = self._browser_interactions(session).recover_upload_failure(
                    principal, interaction_id,
                    denied=isinstance(
                        error,
                        (
                            ResourceNotFound,
                            TaskAccessDenied,
                            TaskNotFound,
                            WorkRequestAccessDenied,
                            MeetingAccessDenied,
                            ActionCapabilityDenied,
                        ),
                    ),
                    name=name, content_type=content_type, data=data,
                )
                session.commit()
                if recovered is not None:
                    return recovered
            raise

    def interrupt_browser_interaction(self, principal: Principal, interaction_id: UUID, status: str) -> BrowserInteractionResult:
        with self._session_factory() as session:
            result = self._browser_interactions(session).interrupt(principal, interaction_id, status)
            session.commit()
            return result

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

    def reassign_task(self, principal: Principal, task_id: UUID, expected_version: int, assignee_id: str, reason: str | None = None) -> TaskAssignmentResult:
        """Put someone else on work that is already underway. Its own command, never a Task field edit."""
        with self._session_factory() as session:
            result = self._assignments(session).reassign(principal, task_id, expected_version, assignee_id, reason)
            session.commit()
            return result

    def assign_task(self, principal: Principal, title: str, assignee_id: str, **fields: Any) -> TaskAssignmentResult:
        with self._session_factory() as session:
            result = self._assignments(session).assign(principal, title, assignee_id, **fields)
            session.commit()
            return result

    def task_assignment_candidates(self, principal: Principal) -> list[MemberCandidateView]:
        with self._session_factory() as session:
            return self._assignments(session).candidates(principal)

    def plan_project_work(self, principal: Principal, project_id: UUID, title: str, **fields: Any) -> TaskMutationResult:
        with self._session_factory() as session:
            result = self._assignments(session).plan_project_work(principal, project_id, title, **fields)
            session.commit()
            return result

    def task_assignment_inbox(self, principal: Principal) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return self._assignments(session).inbox(principal)

    def sent_task_assignments(self, principal: Principal) -> list[TaskAssignmentResult]:
        with self._session_factory() as session:
            return self._assignments(session).sent(principal)

    def accept_task_assignment(self, principal: Principal, assignment_id: UUID) -> TaskAssignmentResult:
        with self._session_factory() as session:
            result = self._assignments(session).accept(principal, assignment_id)
            session.commit()
            return result

    def decline_task_assignment(self, principal: Principal, assignment_id: UUID, reason: str) -> TaskAssignmentResult:
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

    def create_project(self, principal: Principal, **fields: Any) -> ProjectView:
        with self._session_factory() as session:
            result = self._projects(session).create(principal, **fields)
            session.commit()
            return result

    def list_projects(self, principal: Principal) -> list[ProjectView]:
        with self._session_factory() as session:
            return self._projects(session).list(principal)

    def get_project(self, principal: Principal, project_id: UUID) -> ProjectDetailResult:
        with self._session_factory() as session:
            return self._projects(session).get(principal, project_id)

    def project_participation_history(self, principal: Principal, project_id: UUID) -> list[ProjectParticipationView]:
        with self._session_factory() as session:
            return self._projects(session).participation_history(principal, project_id)

    def assign_to_project(self, principal: Principal, project_id: UUID, member_id: str, **fields: Any) -> ProjectAssignmentView:
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
    ) -> ProjectReleaseResult:
        with self._session_factory() as session:
            result = self._projects(session).release(
                principal,
                project_id,
                member_id,
                assignment_id=assignment_id,
                reason=reason,
            )
            session.commit()
            return result

    def list_tasks(self, principal: Principal, *, include_closed: bool = False) -> list[TaskListEntry]:
        """The explicitly requested set of work this person may currently read."""
        with self._session_factory() as session:
            return self._tasks(session).readable_tasks(principal, include_closed=include_closed)

    def get_task(self, principal: Principal, task_id: UUID) -> TaskDetailResult:
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
        source_meeting_id: UUID | None = None,
        source_agenda_id: UUID | None = None,
        allow_self_assignment: bool = False,
        promoted_by_member_id: str | None = None,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._work_requests(session).create(
                principal, title, assignee_id, causation_key,
                description=description, due_date=due_date, cc_member_ids=cc_member_ids, checklist=checklist,
                reference_task_ids=reference_task_ids,
                source_meeting_id=source_meeting_id, source_agenda_id=source_agenda_id,
                allow_self_assignment=allow_self_assignment,
                promoted_by_member_id=promoted_by_member_id,
            )
            session.commit()
            return result

    def work_request_assignee_candidates(self, principal: Principal) -> list[MemberCandidateView]:
        with self._session_factory() as session:
            return self._work_requests(session).assignee_candidates(principal)

    def list_work_requests(self, principal: Principal) -> list[WorkRequestMutationResult]:
        with self._session_factory() as session:
            return self._work_requests(session).list(principal)

    def get_work_request(self, principal: Principal, request_id: UUID) -> WorkRequestDetailResult:
        with self._session_factory() as session:
            return self._work_requests(session).get(principal, request_id)

    def accept_work_request(
        self, principal: Principal, request_id: UUID, expected_version: int
    ) -> WorkRequestMutationResult:
        with self._session_factory() as session:
            result = self._work_requests(session).accept(
                principal, request_id, expected_version
            )
            session.commit()
            return result

    def reject_work_request(
        self, principal: Principal, request_id: UUID, expected_version: int, reason: str
    ) -> WorkRequestMutationResult:
        with self._session_factory() as session:
            result = self._work_requests(session).reject(
                principal, request_id, expected_version, reason
            )
            session.commit()
            return result

    def resubmit_work_request(self, principal: Principal, request_id: UUID, expected_version: int, **changes: Any) -> WorkRequestMutationResult:
        with self._session_factory() as session:
            result = self._work_requests(session).resubmit(principal, request_id, expected_version, **changes)
            session.commit()
            return result

    def amend_work_request(self, principal: Principal, request_id: UUID, expected_version: int, **changes: Any) -> WorkRequestMutationResult:
        """The requester's own improvement to a request nobody has judged yet."""
        with self._session_factory() as session:
            result = self._work_requests(session).amend(principal, request_id, expected_version, **changes)
            session.commit()
            return result

    def work_request_cc_candidates(self, principal: Principal) -> list[MemberCandidateView]:
        with self._session_factory() as session:
            return self._work_requests(session).cc_candidates(principal)

    def attach_to_work_request_comment(self, principal: Principal, request_id: UUID, comment_id: UUID, **file: Any) -> ActionDiscussionView:
        with self._session_factory() as session:
            result = self._work_requests(session).attach_to_comment(principal, request_id, comment_id, **file)
            session.commit()
            return result

    def add_work_request_evidence(self, principal: Principal, request_id: UUID, **file: Any) -> WorkRequestEvidenceResult:
        with self._session_factory() as session:
            result = self._work_requests(session).add_evidence(principal, request_id, **file)
            session.commit()
            return result

    def open_work_request_attachment(self, principal: Principal, request_id: UUID, attachment_id: UUID) -> tuple[dict[str, Any], bytes]:
        with self._session_factory() as session:
            return self._work_requests(session).open_attachment(principal, request_id, attachment_id)

    def add_work_request_comment(self, principal: Principal, request_id: UUID, body: str, idempotency_key: str | None = None) -> ActionDiscussionView:
        with self._session_factory() as session:
            result = self._work_requests(session).add_comment(principal, request_id, body, idempotency_key=idempotency_key)
            session.commit()
            return result

    def pending_action_items(self, principal: Principal) -> list[ActionEnvelopeResult]:
        """Every judgement this principal owes right now, whatever raised it."""
        with self._session_factory() as session:
            return self._action_center(session).pending(principal)

    def action_item_detail(self, principal: Principal, action_item_id: str) -> ActionDetailResult:
        with self._session_factory() as session:
            return self._action_center(session).detail(principal, action_item_id)

    def normalize_action_command(self, principal: Principal, action_item_id: str, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        """The canonical form of a command, for a caller that must store or compare it before it runs."""
        with self._session_factory() as session:
            return self._action_center(session).normalize(principal, action_item_id, command, payload)

    def run_action_command(self, principal: Principal, action_item_id: str, command: str, payload: dict[str, Any]) -> ActionEnvelopeResult:
        with self._session_factory() as session:
            try:
                result = self._action_center(session).execute(principal, action_item_id, command, payload)
                hooks, refresh_receipt, receipt_pending, receipt_owner = self._commit_action_session(
                    session, action_item_id
                )
            except Exception:
                self._rollback_action_session(session)
                raise
        receipt_patch, receipt_hook_failed = self._run_committed_action_hooks(hooks)
        refreshed = None
        if refresh_receipt and not receipt_hook_failed:
            refreshed = self._finalize_post_commit_meeting_receipt(
                action_item_id,
                receipt_patch,
                expected_owner=receipt_owner,
            )
        if refreshed is None and receipt_pending:
            refreshed = self._wait_for_post_commit_meeting_receipt(action_item_id)
        if refreshed is not None:
            result = {**result, "execution_result": refreshed}
        return result

    def _commit_action_session(
        self,
        session: Any,
        action_item_id: str,
    ) -> tuple[list[tuple[Any, bool]], bool, bool, str | None]:
        refresh_receipt = bool(session.info.pop("scax_refresh_meeting_receipt", False))
        try:
            action = session.get(ActionItemRecord, UUID(action_item_id))
        except ValueError:
            action = None
        receipt_owner = str(uuid4()) if refresh_receipt else None
        if receipt_owner is not None and action is not None and isinstance(action.result, dict):
            action.result = {
                **action.result,
                POST_COMMIT_RECEIPT_PENDING: {
                    "owner": receipt_owner,
                    "expires_at": (
                        datetime.now(UTC) + timedelta(seconds=self._meeting_receipt_lease_seconds())
                    ).isoformat(),
                },
            }
        receipt_pending = bool(
            action is not None
            and isinstance(action.result, dict)
            and action.result.get(POST_COMMIT_RECEIPT_PENDING)
        )
        session.commit()
        hooks = session.info.pop("scax_after_commit", [])
        session.info.pop("scax_after_rollback", None)
        return hooks, refresh_receipt, receipt_pending, receipt_owner

    @staticmethod
    def _rollback_action_session(session: Any) -> None:
        session.rollback()
        session.info.pop("scax_after_commit", None)
        session.info.pop("scax_refresh_meeting_receipt", None)
        for hook in reversed(session.info.pop("scax_after_rollback", [])):
            try:
                hook()
            except Exception:
                logger.exception("승인 실행 rollback 후 외부 보상에 실패했습니다")

    @staticmethod
    def _run_committed_action_hooks(
        hooks: list[tuple[Any, bool]],
    ) -> tuple[dict[str, Any] | None, bool]:
        receipt_patch = None
        receipt_hook_failed = False
        for hook, owns_meeting_receipt in hooks:
            try:
                outcome = hook()
                if owns_meeting_receipt:
                    receipt_patch = outcome if isinstance(outcome, dict) else {}
            except Exception:
                # 업무 반영과 승인 기록은 이미 함께 커밋됐다. 후속 push/외부 동기화 실패가
                # 그 사실을 실패로 바꾸지는 않는다; 각 후속 경로가 원장 상태로 복구한다.
                logger.exception("승인 실행 commit 후 회의 후속 처리에 실패했습니다")
                if owns_meeting_receipt:
                    receipt_hook_failed = True
        return receipt_patch, receipt_hook_failed

    def _finalize_post_commit_meeting_receipt(
        self,
        action_item_id: str,
        receipt_patch: dict[str, Any] | None,
        *,
        expected_owner: str | None,
    ) -> dict[str, Any] | None:
        """Finish the durable receipt fence with only fields returned by the room hook."""
        try:
            identifier = UUID(action_item_id)
        except ValueError:
            return None
        with self._session_factory() as session:
            action = session.scalar(
                select(ActionItemRecord).where(ActionItemRecord.id == identifier).with_for_update()
            )
            if action is None or action.state != "approved" or action.action_type not in {
                "meeting.info.update",
                "meeting.update",
            }:
                return None
            source = dict(action.result or {})
            marker = source.get(POST_COMMIT_RECEIPT_PENDING)
            if (
                not isinstance(marker, dict)
                or not expected_owner
                or marker.get("owner") != expected_owner
            ):
                return None
            source.pop(POST_COMMIT_RECEIPT_PENDING, None)
            nested = source.get("meeting")
            owned = {
                key: value
                for key, value in (receipt_patch or {}).items()
                if key in {"location", "room_reservation"}
            }
            refreshed = (
                {**source, "meeting": {**nested, **owned}}
                if isinstance(nested, dict) and owned
                else source
            )
            if action.result != refreshed:
                action.result = refreshed
                session.commit()
            return refreshed

    def _wait_for_post_commit_meeting_receipt(
        self,
        action_item_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Wait for the owner, or take over an expired durable receipt lease."""
        identifier = UUID(action_item_id)
        timeout_seconds = timeout_seconds or self._meeting_receipt_lease_seconds() * 2
        deadline = monotonic() + timeout_seconds
        while True:
            with self._session_factory() as session:
                action = session.get(ActionItemRecord, identifier)
                result = dict(action.result or {}) if action is not None else {}
            marker = result.get(POST_COMMIT_RECEIPT_PENDING)
            if not marker:
                return action_result_view(result) or {}
            receipt_owner = (
                self._claim_expired_meeting_receipt(identifier)
                if self._meeting_receipt_lease_expired(marker, datetime.now(UTC))
                else None
            )
            if receipt_owner is not None:
                try:
                    receipt_patch = self._recover_meeting_receipt_patch(identifier)
                except Exception:
                    logger.exception("만료된 회의 승인 영수증 후속 처리를 복구하지 못했습니다")
                else:
                    recovered = self._finalize_post_commit_meeting_receipt(
                        action_item_id,
                        receipt_patch,
                        expected_owner=receipt_owner,
                    )
                    if recovered is not None:
                        return recovered
            if monotonic() >= deadline:
                raise ActionError("approval receipt is still being finalized")
            sleep(0.01)

    def _claim_expired_meeting_receipt(self, action_item_id: UUID) -> str | None:
        now = datetime.now(UTC)
        with self._session_factory() as session:
            action = session.scalar(
                select(ActionItemRecord).where(ActionItemRecord.id == action_item_id).with_for_update()
            )
            if action is None or not isinstance(action.result, dict):
                return None
            result = dict(action.result)
            marker = result.get(POST_COMMIT_RECEIPT_PENDING)
            if not self._meeting_receipt_lease_expired(marker, now):
                return None
            owner = str(uuid4())
            result[POST_COMMIT_RECEIPT_PENDING] = {
                "owner": owner,
                "expires_at": (
                    now + timedelta(seconds=self._meeting_receipt_lease_seconds())
                ).isoformat(),
            }
            action.result = result
            session.commit()
            return owner

    @staticmethod
    def _meeting_receipt_lease_expired(marker: Any, now: datetime) -> bool:
        if not isinstance(marker, dict):
            return bool(marker)
        try:
            expires_at = datetime.fromisoformat(str(marker["expires_at"]))
        except (KeyError, TypeError, ValueError):
            return True
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at <= now

    def _meeting_receipt_lease_seconds(self) -> float:
        """Outlive the configured gateway deadline so a live owner is not taken over mid-call."""
        return max(
            _MEETING_RECEIPT_MIN_LEASE_SECONDS,
            self._settings.room_booking_timeout_seconds + _MEETING_RECEIPT_LEASE_MARGIN_SECONDS,
        )

    def _recover_meeting_receipt_patch(self, action_item_id: UUID) -> dict[str, Any] | None:
        with self._session_factory() as session:
            action = session.get(ActionItemRecord, action_item_id)
            meeting_id = (action.payload or {}).get("meeting_id") if action is not None else None
        if not meeting_id:
            return {}
        with self._session_factory() as session:
            source = self._meetings(session).reservation_input(UUID(str(meeting_id)))
        reservation = source.get("reservation")
        if (
            isinstance(reservation, RoomReservation)
            and reservation.status == STATUS_NEEDS_VERIFICATION
        ):
            return {
                "location": reservation.room_name,
                "room_reservation": reservation.view(),
            }
        return self._sync_room_reservation(UUID(str(meeting_id)), cancelled=False)

    @staticmethod
    def _after_session_commit(
        session: Any,
        hook: Any,
        *,
        refresh_meeting_receipt: bool = False,
    ) -> None:
        session.info.setdefault("scax_after_commit", []).append(
            (hook, refresh_meeting_receipt)
        )
        if refresh_meeting_receipt:
            session.info["scax_refresh_meeting_receipt"] = True

    @staticmethod
    def _after_session_rollback(session: Any, hook: Any) -> None:
        session.info.setdefault("scax_after_rollback", []).append(hook)

    def _execute_meeting_action(
        self, session: Any, principal: Principal, operation: str, payload: dict[str, Any]
    ) -> Any:
        """Run confirmed meeting commands in the Action transaction used by every other owning command."""
        meetings = self._meetings(session)

        def identifier(name: str = "meeting_id") -> UUID:
            return UUID(str(payload[name]))

        if operation == "meeting.reservation.create":
            request = MeetingReservationInput.model_validate(
                {key: value for key, value in payload.items() if not key.startswith("_")}
            )
            reservation = None
            if request.room_id is not None:
                reservation = RoomReservation.restored(payload.get("_room_creation_reservation"))
                if reservation is None:
                    raise ActionError("회의실 예약 생성 시도가 준비되지 않았습니다")
            result = meetings.create(principal, **request.values())
            if reservation is not None:
                meeting_id = UUID(result["meeting"]["meeting_id"])
                meetings.attach_reservation(meeting_id, reservation, location=reservation.room_name)
                attempt = session.scalar(
                    select(MeetingRoomCreationAttemptRecord)
                    .where(
                        MeetingRoomCreationAttemptRecord.owner_id == str(principal.id),
                        MeetingRoomCreationAttemptRecord.request_key
                        == str(payload.get("_room_creation_attempt_key") or ""),
                    )
                    .with_for_update()
                )
                if attempt is not None:
                    attempt.meeting_id = meeting_id
                    attempt.updated_at = datetime.now(UTC)
                result = meetings.get(principal, meeting_id)
            return result
        if operation == "meeting.quick_start":
            result = meetings.quick_start(principal)
            meeting_id = str(result["meeting"]["meeting_id"])
            self._after_session_commit(
                session, lambda meeting_id=meeting_id: self._meeting_batch.launch_warm_start(meeting_id)
            )
            return result
        if operation == "meeting.info.update":
            meeting_id = identifier()
            changes = MeetingInfoPatch.model_validate(payload.get("changes") or {}).changes()
            result = meetings.update_info(principal, meeting_id, changes)
            if {"starts_at", "ends_at"} & set(changes):
                self._after_session_commit(
                    session,
                    lambda meeting_id=meeting_id: self._sync_room_reservation(meeting_id, cancelled=False),
                    refresh_meeting_receipt=True,
                )
            return result
        if operation == "meeting.update":
            request = MeetingUpdateCommand.model_validate(payload)
            changes = request.changes()
            if "description" in changes:
                changes["purpose"] = changes.pop("description")
            visibility = changes.pop("visibility", None)
            result = meetings.update_info(
                principal,
                request.meeting_id,
                changes,
                expected_version=request.expected_version,
            )
            if {"starts_at", "ends_at"} & set(changes):
                self._after_session_commit(
                    session,
                    lambda meeting_id=request.meeting_id: self._sync_room_reservation(
                        meeting_id, cancelled=False
                    ),
                    refresh_meeting_receipt=True,
                )
            if visibility is not None:
                organizations = SqlAlchemyOrganizationRepository(session)
                organization_id = session.get(MeetingRecord, request.meeting_id).organization_id
                member_ids = (
                    organizations.member_ids_in(
                        organizations.unit_descendants(organization_id)
                    )
                    if visibility == "public"
                    else []
                )
                result = meetings.apply_legacy_visibility(
                    principal,
                    request.meeting_id,
                    visibility,
                    sorted(member_ids),
                )
            return result
        if operation == "meeting.cancel":
            meeting_id = identifier()
            meetings.cancel(principal, meeting_id)
            self._after_session_commit(
                session, lambda meeting_id=meeting_id: self._sync_room_reservation(meeting_id, cancelled=True)
            )
            return {"meeting_id": str(meeting_id), "state": "cancelled"}
        if operation == "meeting.note.delete":
            meeting_id = identifier()
            meetings.delete_note(principal, meeting_id)
            return {"meeting_id": str(meeting_id), "state": "note_deleted"}
        if operation == "meeting.note.create":
            request = MeetingNoteCreateCommand.model_validate(payload)
            self._validate_legacy_note_cutover(session, request.meeting_id, operation, None)
            return meetings.apply_legacy_note(
                principal,
                request.meeting_id,
                body=request.body,
                operation="create",
                finalized=False,
            )
        if operation == "meeting.note.save":
            request = MeetingNoteSaveCommand.model_validate(payload)
            self._validate_legacy_note_cutover(
                session,
                request.meeting_id,
                operation,
                request.expected_version,
            )
            return meetings.apply_legacy_note(
                principal,
                request.meeting_id,
                body=request.body,
                operation="save",
                finalized=False,
            )
        if operation == "meeting.note.finalize":
            request = MeetingNoteFinalizeCommand.model_validate(payload)
            self._validate_legacy_note_cutover(
                session,
                request.meeting_id,
                operation,
                request.expected_version,
            )
            return meetings.apply_legacy_note(
                principal,
                request.meeting_id,
                body=None,
                operation="finalize",
                finalized=True,
            )
        if operation == "meeting.start":
            meeting_id = identifier()
            result = meetings.start(principal, meeting_id)
            self._after_session_commit(
                session, lambda meeting_id=str(meeting_id): self._meeting_batch.launch_warm_start(meeting_id)
            )
            return result
        if operation == "meeting.end":
            meeting_id = identifier()
            result = meetings.end(principal, meeting_id)
            self._enqueue_finalize(session, meeting_id)
            self._after_session_commit(
                session,
                lambda meeting_id=meeting_id: asyncio.run(self.close_meeting_stream(meeting_id)),
            )
            return result
        if operation == "meeting.finalize.retry":
            meeting_id = identifier()
            result = meetings.retry_finalize(principal, meeting_id)
            self._enqueue_finalize(session, meeting_id)
            return result
        if operation == "meeting.todo.promote":
            meeting_id, todo_id = identifier(), identifier("todo_id")
            request = MeetingTodoPromotionInput.model_validate(
                {key: value for key, value in payload.items() if key not in {"meeting_id", "todo_id"}}
            )
            meeting, todo = meetings.todo_for_promotion(principal, meeting_id, todo_id)
            reference = dict(todo.reference or {})
            created = self._work_requests(session).create(
                principal,
                (request.title or todo.title).strip(),
                request.assignee_id,
                None,
                description=request.description if request.description is not None else todo.description,
                due_date=request.due_date if request.due_date is not None else todo.due_candidate,
                checklist=request.checklist if request.checklist is not None else list(todo.checklist_candidate or []),
                source_meeting_id=meeting.id,
                source_agenda_id=UUID(str(reference.get("agenda_id") or todo.agenda_id)),
                allow_self_assignment=True,
                promoted_by_member_id=str(principal.id),
            )
            return meetings.link_promoted_todo(todo, work_request_id=UUID(str(created["request_id"])))
        if operation == "meeting.todo.remove":
            meeting_id, todo_id = identifier(), identifier("todo_id")
            meetings.remove_todo(principal, meeting_id, todo_id)
            return {"meeting_id": str(meeting_id), "todo_id": str(todo_id), "state": "removed"}
        if operation == "meeting.agenda.add":
            meeting_id = identifier()
            request = MeetingAgendaDraftInput.model_validate({"title": payload.get("title")})
            result = meetings.add_agenda(principal, meeting_id, request.title)
            self._after_session_commit(
                session,
                lambda meeting_id=str(meeting_id), result=result: self._meeting_stream.push_agenda_added_threadsafe(
                    meeting_id, agenda=result
                ),
            )
            return result
        if operation == "meeting.agenda.update":
            request = MeetingAgendaPatch.model_validate(payload.get("changes") or {})
            return meetings.update_agenda(
                principal, identifier(), identifier("agenda_id"), request.changes()
            )
        if operation == "meeting.agenda.remove":
            meeting_id, agenda_id = identifier(), identifier("agenda_id")
            meetings.remove_agenda(principal, meeting_id, agenda_id)
            return {"meeting_id": str(meeting_id), "agenda_id": str(agenda_id), "state": "removed"}
        if operation == "meeting.memo.write":
            meeting_id, agenda_id = identifier(), identifier("agenda_id")
            request = MeetingMemoInput.model_validate({"text": payload.get("text")})
            result = meetings.write_memo(principal, meeting_id, agenda_id, request.text)
            self._after_session_commit(
                session,
                lambda meeting_id=str(meeting_id), agenda_id=str(agenda_id), result=result: (
                    self._meeting_stream.push_memo_line_threadsafe(meeting_id, agenda_id=agenda_id, line=result),
                    self._meeting_batch.schedule(meeting_id, CAUSE_AGENDA_SWITCH),
                ),
            )
            return result
        if operation == "meeting.material.remove":
            meeting_id, material_id = identifier(), identifier("material_id")
            self._meeting_materials(session).detach(principal, meeting_id, material_id)
            return {"meeting_id": str(meeting_id), "material_id": str(material_id), "state": "detached"}
        if operation == "meeting.share_many":
            request = MeetingShareManyInput.model_validate({"member_ids": payload.get("member_ids")})
            return {"viewers": meetings.share_many(principal, identifier(), request.member_ids)}
        if operation == "meeting.share":
            request = MeetingShareCommand.model_validate(payload)
            return meetings.share(
                principal,
                request.meeting_id,
                request.member_id,
                expected_version=request.expected_version,
            )
        if operation == "meeting.share.revoke":
            return {"viewers": meetings.revoke_share(principal, identifier(), str(payload["member_id"]))}
        if operation == "meeting.revoke_share":
            request = MeetingShareCommand.model_validate(payload)
            return {
                "viewers": meetings.revoke_share(
                    principal,
                    request.meeting_id,
                    request.member_id,
                    expected_version=request.expected_version,
                )
            }
        raise ValueError(f"unsupported meeting action: {operation}")

    def _prepare_action_effect(
        self,
        action_session: Any,
        principal: Principal,
        action_id: str,
        operation: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist the room-create fence before an Action transaction calls the provider."""
        if operation != "meeting.reservation.create" or payload.get("_room_creation_reservation"):
            return payload
        request = MeetingReservationInput.model_validate(
            {key: value for key, value in payload.items() if not key.startswith("_")}
        )
        if request.room_id is None:
            return payload
        durable_payload = {
            **request.model_dump(mode="json"),
            "attachment_draft_ids": list(payload.get("_attachment_draft_ids") or []),
        }
        fingerprint = self._room_creation_payload_fingerprint(durable_payload)
        request_key = f"action:{action_id}:{fingerprint}"
        unresolved_keys = set(
            action_session.scalars(
                select(MeetingRoomCreationAttemptRecord.request_key).where(
                    MeetingRoomCreationAttemptRecord.owner_id == str(principal.id),
                    MeetingRoomCreationAttemptRecord.request_key.like(f"action:{action_id}:%"),
                    MeetingRoomCreationAttemptRecord.meeting_id.is_(None),
                    MeetingRoomCreationAttemptRecord.status.in_(
                        ("pending", STATUS_BOOKED, STATUS_NEEDS_VERIFICATION)
                    ),
                )
            ).all()
        )
        if unresolved_keys - {request_key}:
            raise ActionError(
                "이 제안의 이전 회의실 예약 결과를 먼저 확인해야 합니다. "
                "예약을 시도했던 원래 내용으로 확정을 다시 실행하세요"
            )
        with self._session_factory() as validation_session:
            meetings = self._meetings(validation_session)
            meetings.validate_creation(principal, **request.values())
            source = meetings.reservation_draft(
                principal,
                title=request.title,
                starts_at=request.starts_at,
                ends_at=request.ends_at,
                attendee_ids=request.attendee_ids,
                external_attendees=request.external_attendees,
            )
        reservation = self._resolve_room_creation_attempt(
            principal,
            request_key,
            request,
            source,
            payload_fingerprint=fingerprint,
            request_payload=durable_payload,
        )
        if reservation.status == STATUS_BOOKED and reservation.external_id:
            self._after_session_rollback(
                action_session,
                lambda owner_id=str(principal.id), request_key=request_key, reservation=reservation: (
                    self._compensate_room_creation_attempt(owner_id, request_key, reservation)
                ),
            )
        return {
            **payload,
            "_room_creation_reservation": reservation.stored(),
            "_room_creation_attempt_key": request_key,
        }

    @staticmethod
    def _validate_action_rejection(
        session: Any,
        principal: Principal,
        action_id: str,
        operation: str,
    ) -> None:
        if operation != "meeting.reservation.create":
            return
        unresolved = session.scalar(
            select(MeetingRoomCreationAttemptRecord.id).where(
                MeetingRoomCreationAttemptRecord.owner_id == str(principal.id),
                MeetingRoomCreationAttemptRecord.request_key.like(f"action:{action_id}:%"),
                MeetingRoomCreationAttemptRecord.meeting_id.is_(None),
                MeetingRoomCreationAttemptRecord.status.in_(
                    ("pending", STATUS_BOOKED, STATUS_NEEDS_VERIFICATION)
                ),
            )
        )
        if unresolved is not None:
            raise ActionError(
                "회의실 예약 결과 확인이 필요해 이 제안을 거절할 수 없습니다. "
                "확정을 다시 실행해 확인 필요 회의로 기록하세요"
            )

    @staticmethod
    def _validate_legacy_note_cutover(
        session: Any,
        meeting_id: UUID,
        operation: str,
        expected_version: int | None,
    ) -> None:
        """Continue the removed note aggregate's version from approved compatibility actions.

        The pre-cutover note version was independent from Meeting.version and its table no longer exists. The first
        surviving pending save/finalize therefore establishes the cutover baseline; later approvals advance from the
        immutable Action payloads that were actually approved.
        """
        session.scalar(select(MeetingRecord).where(MeetingRecord.id == meeting_id).with_for_update())
        rows = session.scalars(
            select(ActionItemRecord)
            .where(
                ActionItemRecord.state == "approved",
                ActionItemRecord.action_type.in_(
                    ("meeting.note.create", "meeting.note.save", "meeting.note.finalize")
                ),
            )
            .order_by(ActionItemRecord.decided_at, ActionItemRecord.id)
            .with_for_update()
        ).all()
        current_version: int | None = None
        finalized = False
        for row in rows:
            source = dict(row.payload or {})
            if str(source.get("meeting_id") or "") != str(meeting_id):
                continue
            if row.action_type == "meeting.note.create":
                current_version, finalized = 1, False
            elif row.action_type == "meeting.note.save":
                current_version, finalized = int(source["expected_version"]) + 1, False
            else:
                current_version, finalized = int(source["expected_version"]), True
        if operation == "meeting.note.create":
            if current_version is not None:
                raise MeetingError("meeting note already exists")
            return
        if current_version is not None and current_version != expected_version:
            raise MeetingVersionConflict("meeting note version is stale")
        if operation == "meeting.note.save" and finalized:
            raise MeetingError("finalized meeting notes cannot be edited")

    def stage_action_material_link(
        self, principal: Principal, action_item_id: UUID, *, url: str, label: str
    ) -> ActionMaterialDraftView:
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
    ) -> ActionMaterialDraftView:
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
    ) -> ActionMaterialDraftView:
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
                services=self._action_services(session),
                evidence_reader=lambda principal, turn_id: self._action_material_evidence(session, principal, turn_id),
                work_requests=work_requests,
                actions=self._actions(session, work_requests=work_requests),
                assignments=self._assignments(session),
                tasks=self._tasks(session),
                material_drafts=self._action_materials(session),
            )
        )

    def work_request_timeline(self, principal: Principal, request_id: UUID) -> WorkRequestHistoryResult:
        with self._session_factory() as session:
            return self._work_requests(session).timeline(principal, request_id)

    def negotiate_work_request(
        self, principal: Principal, request_id: UUID, expected_version: int, conditions: dict[str, Any]
    ) -> WorkRequestMutationResult:
        with self._session_factory() as session:
            result = self._work_requests(session).negotiate(
                principal, request_id, expected_version, conditions
            )
            session.commit()
            return result

    def work_request_inbox(self, principal: Principal) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return self._work_requests(session).inbox(principal)

    def create_conversation(self, principal: Principal, title: str) -> ConversationView:
        with self._session_factory() as session:
            result = self._conversations(session).create(principal, title)
            session.commit()
            return result

    def conversations(self, principal: Principal, *, limit: int = DEFAULT_CONVERSATION_LIST_LIMIT) -> list[ConversationView]:
        with self._session_factory() as session:
            return self._conversations(session).list(principal, limit=limit)

    def conversation(
        self,
        principal: Principal,
        conversation_id: UUID,
        *,
        message_limit: int = DEFAULT_MESSAGE_WINDOW,
        before_sequence: int | None = None,
    ) -> ConversationView:
        with self._session_factory() as session:
            return self._conversations(session).get(
                principal, conversation_id, message_limit=message_limit, before_sequence=before_sequence,
            )

    def accept_conversation_message(
        self,
        principal: Principal,
        body: str,
        conversation_id: UUID,
        context: list[Any],
        idempotency_key: str | None,
        follow_up_candidate_id: UUID | None = None,
    ) -> ConversationMessageResult:
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

    def retry_conversation_turn(self, principal: Principal, conversation_id: UUID, turn_id: UUID) -> ConversationRetryResult:
        with self._session_factory() as session:
            result = self._conversations(session).retry(principal, conversation_id, turn_id)
            session.commit()
            return result

    def cancel_conversation_turn(
        self, principal: Principal, conversation_id: UUID, expected_version: int
    ) -> ConversationView:
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
    ) -> ActionProposalResult:
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

    def actions(self, principal: Principal) -> list[ActionProposalResult]:
        with self._session_factory() as session:
            return self._actions(session).list(principal)

    def decide_action(
        self,
        principal: Principal,
        action_id: UUID,
        expected_version: int,
        decision: str,
    ) -> ActionProposalResult:
        with self._session_factory() as session:
            try:
                action = self._action_repository(session).action(action_id, str(principal.id))
                # The old chat endpoint remains a compatibility surface. A Task proposal raised under the canonical
                # ledger still goes through its atomic confirm operation, so this route cannot create or assign a Task
                # without the selected Submission and ReviewDecision lineage.
                if (
                    action is not None
                    and action.action_type in {
                        "task.create_self", "task.assign", "meeting.reservation.create"
                    }
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
                    result = self._action_repository(session).view(action, principal)
                else:
                    result = self._actions(session).decide(
                        principal,
                        action_id,
                        expected_version,
                        decision,
                    )
                hooks, refresh_receipt, receipt_pending, receipt_owner = self._commit_action_session(
                    session, str(action_id)
                )
            except Exception:
                self._rollback_action_session(session)
                raise
        receipt_patch, receipt_hook_failed = self._run_committed_action_hooks(hooks)
        refreshed = None
        if refresh_receipt and not receipt_hook_failed:
            refreshed = self._finalize_post_commit_meeting_receipt(
                str(action_id),
                receipt_patch,
                expected_owner=receipt_owner,
            )
        if refreshed is None and receipt_pending:
            refreshed = self._wait_for_post_commit_meeting_receipt(str(action_id))
        return {**result, **({"result": refreshed} if refreshed is not None else {})}

    def _action_services(self, session: Any) -> ActionServices:
        """Every delayed dependency remains bound to this transaction's session."""
        return ActionServices(
            tasks=lambda: self._tasks(session),
            assignments=lambda: self._assignments(session),
            meetings=lambda: self._meetings(session),
            prepare_action=lambda principal, action_id, operation, payload: self._prepare_action_effect(
                session, principal, action_id, operation, payload
            ),
            validate_action_rejection=lambda principal, action_id, operation: (
                self._validate_action_rejection(session, principal, action_id, operation)
            ),
            meeting_action=lambda principal, operation, payload: self._execute_meeting_action(
                session, principal, operation, payload
            ),
            reports=lambda: self._reports(session),
            projects=lambda: self._projects(session),
            organization=lambda: OrganizationApplication(SqlAlchemyOrganizationRepository(session)),
            action_center=lambda: self._action_center(session),
            material_folders=lambda: self._material_folders(session),
            materials=lambda: self._materials(session),
            meeting_followups=lambda: self._meeting_followups(session),
            notifications=lambda: self._notifications(session),
            work_requests=lambda: self._work_requests(session),
            conversations=lambda: self._conversations(session),
            action_materials=lambda: self._action_materials(session),
        )

    def _action_repository(self, session: Any) -> SqlAlchemyActionRepository:
        return SqlAlchemyActionRepository(
            session,
            services=self._action_services(session),
            evidence_reader=lambda principal, turn_id: self._action_material_evidence(session, principal, turn_id),
        )

    def _tasks(self, session: Any) -> TaskApplication:
        """Tasks with the request module attached, so a Task's origin can name a requester it is allowed to name."""
        return TaskApplication(
            SqlAlchemyTaskRepository(session),
            SqlAlchemyWorkRequestRepository(session),
            self._action_repository(session),
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
                actions=self._action_repository(session),
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
            self._action_repository(session),
            SqlAlchemyActionExecutor(
                session,
                self._action_materials(session),
                services=self._action_services(session),
                work_requests=work_requests,
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
    ) -> ChecklistMutationResult:
        with self._session_factory() as session:
            result = self._tasks(session).add_checklist_item(principal, task_id, text, expected_task_version=expected_task_version)
            session.commit()
            return result

    def update_task_checklist_item(self, principal: Principal, task_id: UUID, item_id: UUID, **fields: Any) -> ChecklistMutationResult:
        with self._session_factory() as session:
            result = self._tasks(session).update_checklist_item(principal, task_id, item_id, **fields)
            session.commit()
            return result

    def archive_task_checklist_item(self, principal: Principal, task_id: UUID, item_id: UUID, **fields: Any) -> ChecklistMutationResult:
        with self._session_factory() as session:
            result = self._tasks(session).archive_checklist_item(principal, task_id, item_id, **fields)
            session.commit()
            return result

    def reorder_task_checklist(self, principal: Principal, task_id: UUID, item_ids: list[UUID], **fields: Any) -> ChecklistOrderResult:
        with self._session_factory() as session:
            result = self._tasks(session).reorder_checklist(principal, task_id, item_ids, **fields)
            session.commit()
            return result

    def transition_task(self, task_id: UUID, principal: Principal, target: TaskState, reason: str | None = None, expected_version: int = 0) -> TaskMutationResult:
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


def create_scax_mcp_server(
    settings: Settings, *, enabled_tools: tuple[str, ...] = ()
) -> CodexCliMcpServer:
    """Address the same MCP entrypoint from source Python or the protected dispatcher.

    `enabled_tools` 가 비면 서버가 노출하는 도구 전부다. 회의 배치는 레지스트리가 준 목록으로 좁힌다
    (SCAX-SPEC-004 §7.2-3) — 「부르지 마라」고 말하는 대신 도구를 주지 않는다. 좁히는 규칙은 어느
    런타임에서 열든 같아야 하므로, 보호 이미지로 들어가는 이 자리에 함께 둔다.
    """
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
            "AX_WEB_ORIGIN": settings.web_origin,
        },
        enabled_tools=tuple(enabled_tools),
    )


def create_codex_cli_provider(
    settings: Settings, *, enabled_tools: tuple[str, ...] = ()
) -> CodexCliProviderAdapter:
    """Compose the isolated CLI adapter with exactly one server-bound SCAX MCP."""
    return CodexCliProviderAdapter(
        scax_mcp_server=create_scax_mcp_server(settings, enabled_tools=enabled_tools)
    )


def create_claude_cli_provider(
    settings: Settings, *, enabled_tools: tuple[str, ...] = ()
) -> ClaudeCliProviderAdapter:
    """Compose the Claude Code CLI adapter with the same server-bound SCAX MCP binding as Codex."""
    return ClaudeCliProviderAdapter(
        scax_mcp_server=create_scax_mcp_server(settings, enabled_tools=enabled_tools)
    )


#: `Settings.ai_provider` -> its factory. Both return an `AiProvider`; callers never branch on which.
_AI_PROVIDER_FACTORIES = {
    "codex": create_codex_cli_provider,
    "claude": create_claude_cli_provider,
}


def create_conversation_provider(
    settings: Settings, *, enabled_tools: tuple[str, ...] = ()
) -> AiProvider:
    """The one place a caller picks a provider — by `Settings.ai_provider`, never by importing an adapter."""
    return _AI_PROVIDER_FACTORIES[settings.ai_provider](settings, enabled_tools=enabled_tools)
