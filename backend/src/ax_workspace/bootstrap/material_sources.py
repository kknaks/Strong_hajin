"""Compose owner read policies without copying them into the content index."""
from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from ax_workspace.modules.organization_access.domain import TASK_READ, WORK_REQUEST_READ, MEETING_READ, DAILY_REPORT_READ
from ax_workspace.modules.work.material_search import ReadableMaterialSource
from ax_workspace.modules.work.materials import MaterialNotFound
from ax_workspace.platform.work_tasks import SqlAlchemyAttachmentRepository
from ax_workspace.platform.native_materials import NativeMaterialRepository, material_id_for
from ax_workspace.platform.organization_access import SqlAlchemyOrganizationRepository


class SessionMaterialOwners:
    def __init__(self, application, session):
        self._application, self._session = application, session

    def sources(self, principal, resource_types, *, resource_type=None, resource_id=None, material_id=None, material_ids=None):
        selected = {material_id} if material_id is not None else material_ids
        current = SqlAlchemyOrganizationRepository(self._session).principal_for(str(principal.id))
        if current is None:
            if resource_type is not None:
                raise MaterialNotFound("resource was not found")
            return []
        # Refresh revoked membership/grants without widening an intentionally attenuated caller.
        principal = replace(current, capabilities=current.capabilities & principal.capabilities,
                            organization_scope=current.organization_scope & principal.organization_scope,
                            grants=tuple(replace(grant, units=grant.units & principal.scope_for(grant.capability),
                                                 projects=grant.projects & principal.projects_for(grant.capability)) for grant in current.grants
                                         if grant.capability in principal.capabilities))
        readable = {}
        if "task" in resource_types and TASK_READ in principal.capabilities:
            tasks = self._application._tasks(self._session).list_for(principal, include_closed=True, include_organization=True)
            readable = {row["task_id"]: row for row in tasks}
        if resource_type == "task":
            if resource_id not in readable:
                raise MaterialNotFound("resource was not found")
            readable = {resource_id: readable[resource_id]}
        sources = []
        attachments = SqlAlchemyAttachmentRepository(self._session)
        for binding, attachment in attachments.bindings_for_many("task", list(readable)):
            if binding.unbound_at is not None:
                continue
            sources.append(ReadableMaterialSource(attachment, {
                "resource_type": "task", "resource_id": binding.context_id, "title": readable[binding.context_id]["title"],
                "binding_id": str(binding.id), "role": binding.role,
                "origin": f"/api/tasks/{binding.context_id}/materials/{attachment.id}/content",
            }))
        requests = self._application._work_requests(self._session)
        readable_requests = {}
        if "work_request" in resource_types and WORK_REQUEST_READ in principal.capabilities:
            readable_requests = {row["request_id"]: row for row in requests.list(principal)}
        if resource_type == "work_request":
            if resource_id not in readable_requests:
                raise MaterialNotFound("resource was not found")
            readable_requests = {resource_id: readable_requests[resource_id]}
        for request_id, request in readable_requests.items():
            for binding, attachment in requests.material_bindings(principal, UUID(request_id)):
                sources.append(ReadableMaterialSource(attachment, {
                    "resource_type": "work_request", "resource_id": request_id, "title": request["title"],
                    "binding_id": str(binding.id), "role": binding.role,
                    "binding_context_type": binding.context_type, "binding_context_id": binding.context_id,
                    "origin": f"/api/work-requests/{request_id}/attachments/{attachment.id}/content",
                }))
        readable_folders = {}
        if resource_types & {"personal_folder", "team_folder"}:
            folders = self._application._material_folders(self._session).list_for(principal)
            readable_folders = {folder["folder_id"]: folder for folder in folders if f"{folder['kind']}_folder" in resource_types}
        if resource_type in {"personal_folder", "team_folder"}:
            if resource_id not in readable_folders:
                raise MaterialNotFound("resource was not found")
            readable_folders = {resource_id: readable_folders[resource_id]}
        for binding, attachment in attachments.bindings_for_many("material_folder", list(readable_folders)):
            folder = readable_folders[binding.context_id]
            sources.append(ReadableMaterialSource(attachment, {
                "resource_type": f"{folder['kind']}_folder", "resource_id": binding.context_id, "title": folder["title"],
                "binding_id": str(binding.id), "role": binding.role,
                "origin": f"/api/material-folders/{binding.context_id}/materials/{attachment.id}/content",
            }))
        meetings = self._application._meetings(self._session)
        readable_meetings = {}
        if "meeting" in resource_types and MEETING_READ in principal.capabilities:
            readable_meetings = {row["meeting_id"]: row for row in meetings.list(principal) if row.get("kind") == "meeting"}
        if resource_type == "meeting":
            if resource_id not in readable_meetings:
                raise MaterialNotFound("resource was not found")
            readable_meetings = {resource_id: readable_meetings[resource_id]}
        native = NativeMaterialRepository(self._session)
        for meeting_id, meeting in readable_meetings.items():
            for binding, attachment in attachments.bindings_for("meeting", meeting_id):
                if binding.unbound_at is not None:
                    continue
                if selected is not None and attachment.id not in selected:
                    continue
                sources.append(ReadableMaterialSource(attachment, {
                    "resource_type": "meeting", "resource_id": meeting_id, "title": meeting["title"],
                    "binding_id": str(binding.id), "role": binding.role,
                    "origin": f"/api/meetings/{meeting_id}/materials/{attachment.id}/content",
                }))
            audio_materials = {}
            for revision in meetings.material_revisions(principal, UUID(meeting_id), include_history=selected is not None):
                if revision["kind"] != "meeting_recording" and selected is not None and material_id_for(revision["kind"], UUID(revision["revision_id"])) not in selected:
                    continue
                binding, attachment = native.ensure(revision["kind"], UUID(revision["revision_id"]))
                if binding.unbound_at is not None or attachment.lifecycle == "purged":
                    continue
                origin = f"/api/meetings/{meeting_id}/materials/{attachment.id}/content"
                if revision["kind"] == "meeting_recording":
                    audio_materials[revision["recording_id"]] = {"material_id": str(attachment.id), "integrity_ref": attachment.integrity_ref, "origin": origin}
                if selected is not None and attachment.id not in selected:
                    continue
                context = {
                    "resource_type": "meeting", "resource_id": meeting_id, "title": meeting["title"],
                    "binding_id": str(binding.id), "role": binding.role, "source_layer": revision["source_layer"],
                    "source_revision_id": revision["revision_id"], "source_revision": revision["revision"],
                    "is_current_revision": revision["is_current_revision"],
                    "origin": origin,
                }
                if revision["kind"] == "meeting_note":
                    context.update(note_id=revision["note_id"], note_lifecycle=revision["note_lifecycle"])
                else:
                    context.update(recording_id=revision["recording_id"], recording_integrity_ref=revision["recording_integrity_ref"])
                    if "recording_state" in revision:
                        context["recording_state"] = revision["recording_state"]
                    if revision["recording_id"] in audio_materials:
                        context["recording_material"] = audio_materials[revision["recording_id"]]
                sources.append(ReadableMaterialSource(attachment, context))
        report_revisions = []
        if "report" in resource_types and DAILY_REPORT_READ in principal.capabilities:
            try:
                report_revisions = self._application._reports(self._session).material_revisions(
                    principal, report_id=resource_id if resource_type == "report" else None, include_history=selected is not None)
            except ValueError as error:
                if resource_type == "report":
                    raise MaterialNotFound("resource was not found") from error
                raise
        elif resource_type == "report":
            raise MaterialNotFound("resource was not found")
        for revision in report_revisions:
            identifier = UUID(revision["revision_id"])
            if selected is not None and material_id_for("report_submission", identifier) not in selected:
                continue
            binding, attachment = native.ensure("report_submission", identifier)
            if binding.unbound_at is not None or attachment.lifecycle == "purged":
                continue
            sources.append(ReadableMaterialSource(attachment, {
                "resource_type": "report", "resource_id": revision["report_id"], "title": f"일일 보고 · {revision['report_date']}",
                "binding_id": str(binding.id), "role": binding.role, "source_layer": "submission",
                "source_revision_id": revision["revision_id"], "source_revision": revision["revision"],
                "is_current_revision": revision["is_current_revision"],
                "origin": f"/api/daily-reports/{revision['report_id']}/materials/{attachment.id}/content",
            }))
        if resource_type is not None and resource_type not in {"task", "work_request", "personal_folder", "team_folder", "meeting", "report"}:
            raise MaterialNotFound("resource was not found")
        return sources


def readable_content_evidence(application, session, principal, evidence):
    """Retain only observed contexts that still authorize the same immutable artifact."""
    identifiers = set()
    for row in evidence:
        try:
            identifiers.add(UUID(row["material_id"]))
        except (ValueError, TypeError, KeyError):
            continue
    if not identifiers:
        return []
    from ax_workspace.modules.work.material_search import RESOURCE_TYPES
    kinds = {context.get("resource_type") for row in evidence for context in row.get("source_contexts", [])} & RESOURCE_TYPES
    sources = SessionMaterialOwners(application, session).sources(principal, kinds, material_ids=identifiers)
    current = {}
    for source in sources:
        if source.attachment.id in identifiers:
            current.setdefault(str(source.attachment.id), []).append(source)
    readable = []
    for row in evidence:
        observed = {(context.get("resource_type"), context.get("resource_id"), context.get("binding_id")) for context in row.get("source_contexts", [])}
        contexts = [source.context for source in current.get(row.get("material_id"), [])
                    if source.attachment.integrity_ref == row.get("integrity_ref")
                    and source.attachment.lifecycle != "purged"
                    and (source.context["resource_type"], source.context["resource_id"], source.context["binding_id"]) in observed]
        if contexts:
            primary = contexts[0]
            readable.append({**row, "name": application._material_name(session, principal, current[row["material_id"]][0].attachment), "source_contexts": contexts, "origin": primary["origin"],
                "source_resource_type": primary["resource_type"], "source_resource_id": primary["resource_id"], "source_resource_title": primary["title"]})
    return readable
