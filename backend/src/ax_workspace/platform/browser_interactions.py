"""Same-session storage for actor-bound browser requests."""
from sqlalchemy import select

from ax_workspace.platform.persistence import BrowserInteractionRecord, MemberRecord


class SqlAlchemyBrowserInteractionRepository:
    def __init__(self, session):
        self._session = session

    def find_request(self, owner_id, request_key):
        # Serialize first acceptance for this actor before testing the unique request key.
        self._session.scalar(select(MemberRecord).where(MemberRecord.id == owner_id).with_for_update())
        return self._session.scalar(select(BrowserInteractionRecord).where(
            BrowserInteractionRecord.owner_id == owner_id, BrowserInteractionRecord.request_key == request_key))

    def get(self, owner_id, interaction_id, *, lock=False):
        query = select(BrowserInteractionRecord).where(
            BrowserInteractionRecord.id == interaction_id, BrowserInteractionRecord.owner_id == owner_id)
        return self._session.scalar(query.with_for_update() if lock else query)

    def add(self, **fields):
        row = BrowserInteractionRecord(**fields)
        self._session.add(row)
        self._session.flush()
        return row
