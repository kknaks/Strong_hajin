"""Notification delivery/read state, authorized through the current owning resource."""
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import TypedDict

from ax_workspace.modules.errors import ResourceNotFound
from ax_workspace.modules.organization_access.domain import Principal
from ax_workspace.modules.meetings.application import MeetingApplication
from ax_workspace.modules.work.requests import WorkRequestApplication, WorkRequestAccessDenied


class NotificationNotFound(RuntimeError, ResourceNotFound):
    pass


class NotificationReadCommand(BaseModel):
    model_config = ConfigDict(extra='forbid')
    notification_id: UUID = Field(title='읽음 처리할 알림')


class NotificationResource(TypedDict):
    type: str
    id: str
    version: int | None
    title: str


class NotificationView(TypedDict):
    notification_id: str
    kind: str
    summary: str
    actor_id: str
    resource: NotificationResource
    created_at: str
    read_at: str | None


class NotificationRepository(Protocol):
    def list_for(self, recipient_member_id: str) -> list[Any]: ...
    def for_recipient(self, notification_id: UUID, recipient_member_id: str, *, lock: bool = False) -> Any | None: ...
    def mark_read(self, record: Any) -> None: ...


def notification_view(record: Any, *, title: str, version: int | None) -> NotificationView:
    return {
        'notification_id': str(record.id), 'kind': record.kind, 'summary': record.safe_summary,
        'actor_id': record.actor_member_id,
        'resource': {'type': record.resource_type, 'id': record.resource_id, 'version': version, 'title': title},
        'created_at': record.created_at.isoformat(),
        'read_at': record.read_at.isoformat() if record.read_at else None,
    }


class NotificationApplication:
    def __init__(self, repository: NotificationRepository, meetings: MeetingApplication, requests: WorkRequestApplication) -> None:
        self._repository, self._meetings, self._requests = repository, meetings, requests

    def list(self, principal: Principal) -> list[NotificationView]:
        return [view for row in self._repository.list_for(str(principal.id)) if (view := self._authorized_view(principal, row)) is not None]

    def mark_read(self, principal: Principal, notification_id: UUID) -> NotificationView:
        row = self._repository.for_recipient(notification_id, str(principal.id), lock=True)
        if row is None:
            raise NotificationNotFound('notification was not found')
        view = self._authorized_view(principal, row)
        if view is None:
            raise NotificationNotFound('notification was not found')
        self._repository.mark_read(row)
        return notification_view(row, title=view['resource']['title'], version=view['resource']['version'])

    def _authorized_view(self, principal: Principal, row: Any) -> NotificationView | None:
        try:
            if row.resource_type == 'meeting':
                resource = self._meetings.get(principal, UUID(row.resource_id))
            elif row.resource_type == 'work_request':
                resource = self._requests.get(principal, UUID(row.resource_id))
            else:
                return None
        except (ResourceNotFound, WorkRequestAccessDenied):
            return None
        return notification_view(row, title=resource['title'], version=resource['version'])
