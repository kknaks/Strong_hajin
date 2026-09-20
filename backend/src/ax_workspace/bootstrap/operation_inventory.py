"""Machine-readable HTTP-to-AX operation equivalence for coverage audits."""

from types import MappingProxyType

from ax_workspace.modules.ax_execution.tool_catalog import TOOL_CATALOG


# Routes whose screen transport and AX tool deliberately enter the shared domain
# through different facade methods. Every other route maps by adapter_operation.
HTTP_TOOL_TARGET_OVERRIDES = MappingProxyType(
    {
        'DELETE /api/tasks/{task_id}/checklist/{item_id}': ('task_checklist_archive',),
        'GET /api/actions': ('action_item_list',),
        'GET /api/meetings': ('meeting_list',),
        'GET /api/meetings/{meeting_id}/export': ('meeting_export',),
        'GET /api/meetings/{meeting_id}/materials': ('meeting_materials_list',),
        'GET /api/work-requests/{request_id}/timeline': ('work_request_history',),
        'PATCH /api/tasks/{task_id}/checklist/{item_id}': ('task_checklist_update',),
        'POST /api/action-items/{action_item_id}/material-drafts/files': ('file_attachment_request',),
        'POST /api/actions/{action_id}/decide': ('action_item_command',),
        'POST /api/daily-reports/generate-draft': ('daily_report_generate_draft',),
        'POST /api/material-folders/{folder_id}/materials': ('file_attachment_request',),
        'POST /api/meetings': ('meeting_create',),
        'POST /api/meetings/{meeting_id}/materials': ('file_attachment_request',),
        'PATCH /api/meetings/{meeting_id}': ('meeting_update',),
        'DELETE /api/meetings/{meeting_id}': ('meeting_cancel', 'meeting_note_delete'),
        'POST /api/meetings/{meeting_id}/start': ('meeting_start',),
        'POST /api/meetings/{meeting_id}/end': ('meeting_end',),
        'POST /api/meetings/{meeting_id}/finalize': ('meeting_finalize_retry',),
        'POST /api/meetings/{meeting_id}/todos/{todo_id}/promote': ('meeting_todo_promote',),
        'DELETE /api/meetings/{meeting_id}/todos/{todo_id}': ('meeting_todo_remove',),
        'POST /api/meetings/{meeting_id}/agendas': ('meeting_agenda_add',),
        'PATCH /api/meetings/{meeting_id}/agendas/{agenda_id}': ('meeting_agenda_update',),
        'POST /api/meetings/{meeting_id}/agendas/{agenda_id}/lines': ('meeting_memo_write',),
        'DELETE /api/meetings/{meeting_id}/agendas/{agenda_id}': ('meeting_agenda_remove',),
        'DELETE /api/meetings/{meeting_id}/materials/{material_id}': ('meeting_material_detach',),
        'POST /api/meetings/{meeting_id}/shares': ('meeting_share',),
        'DELETE /api/meetings/{meeting_id}/shares/{member_id}': ('meeting_revoke_share',),
        # 생성 라우트와 도구가 같은 명령을 다른 facade 이름으로 지난다 — 라우트는 `create_task`,
        # 도구는 `create_self_task` 다(도구 이름은 외부 계약이라 W1 이 바꾸지 않는다).
        'POST /api/tasks': ('task_create_self',),
        # 같은 질문이 두 이름으로 선다 — REST 는 `children`, 도구는 이미 있던 `task_subtask_list` 다.
        # 같은 판정(`_hierarchy_view`)을 지나므로 새 도구를 만들지 않는다.
        'GET /api/tasks/{task_id}/children': ('task_subtask_list',),
        'POST /api/task-assignments/{assignment_id}/accept': ('action_item_command',),
        'POST /api/task-assignments/{assignment_id}/decline': ('action_item_command',),
        'POST /api/tasks/{task_id}/block': ('task_block',),
        'POST /api/tasks/{task_id}/cancel': ('task_cancel',),
        'POST /api/tasks/{task_id}/checklist': ('task_checklist_add',),
        'POST /api/tasks/{task_id}/checklist/order': ('task_checklist_reorder',),
        'POST /api/tasks/{task_id}/complete': ('task_complete',),
        'POST /api/tasks/{task_id}/materials': ('file_attachment_request',),
        'POST /api/tasks/{task_id}/resume': ('task_resume',),
        'POST /api/tasks/{task_id}/start': ('task_start',),
        'POST /api/work-requests/{request_id}/accept': ('action_item_command',),
        'POST /api/work-requests/{request_id}/comments/{comment_id}/attachments': (
            'file_attachment_request',
        ),
        'POST /api/work-requests/{request_id}/evidence': ('file_attachment_request',),
        'POST /api/work-requests/{request_id}/negotiate': ('action_item_command',),
        'POST /api/work-requests/{request_id}/reject': ('action_item_command',),
        'POST /api/work-requests/{request_id}/resubmit': ('action_item_command',),
        'POST /api/browser-interactions/{interaction_id}/file': ('file_attachment_request',),
        'PATCH /api/browser-interactions/{interaction_id}': ('file_attachment_request',),
        'POST /api/browser-interactions/{interaction_id}/recording/start': ('recording_request',),
        'POST /api/browser-interactions/{interaction_id}/recording/stop': ('recording_request',),
    }
)

if unknown_http_targets := {
    tool_id
    for tool_ids in HTTP_TOOL_TARGET_OVERRIDES.values()
    for tool_id in tool_ids
    if tool_id not in TOOL_CATALOG
}:
    raise RuntimeError(f"unknown HTTP target tools: {sorted(unknown_http_targets)}")


def tool_targets_for_http(
    route: str, application_operations: tuple[str, ...] | list[str]
) -> tuple[str, ...]:
    override = HTTP_TOOL_TARGET_OVERRIDES.get(route)
    if override is not None:
        return override
    operations = set(application_operations)
    return tuple(
        sorted(
            tool_id
            for tool_id, definition in TOOL_CATALOG.items()
            if definition.adapter_operation in operations
        )
    )
