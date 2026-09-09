"""Folder metadata selected using live membership ids from Organization & Access."""
from sqlalchemy import and_, or_, select

from ax_workspace.platform.persistence import MaterialFolderRecord, OrganizationUnitRecord


class SqlAlchemyMaterialFolderRepository:
    def __init__(self, session):
        self._session = session

    def active_organization(self, organization_id):
        return self._session.scalar(select(OrganizationUnitRecord.id).where(
            OrganizationUnitRecord.id == organization_id, OrganizationUnitRecord.lifecycle == "active",
            OrganizationUnitRecord.abolished_at.is_(None))) is not None

    def create(self, *, kind, title, owner_member_id, organization_id, created_by):
        folder = MaterialFolderRecord(kind=kind, title=title, owner_member_id=owner_member_id,
                                      organization_id=organization_id, created_by=created_by)
        self._session.add(folder)
        self._session.flush()
        return folder

    def readable(self, member_id, organization_ids):
        return list(self._session.scalars(select(MaterialFolderRecord).outerjoin(
            OrganizationUnitRecord, OrganizationUnitRecord.id == MaterialFolderRecord.organization_id).where(
                MaterialFolderRecord.archived_at.is_(None),
                or_(and_(MaterialFolderRecord.kind == "personal", MaterialFolderRecord.owner_member_id == member_id),
                    and_(MaterialFolderRecord.kind == "team", MaterialFolderRecord.organization_id.in_(organization_ids),
                         OrganizationUnitRecord.lifecycle == "active", OrganizationUnitRecord.abolished_at.is_(None))),
            ).order_by(MaterialFolderRecord.created_at, MaterialFolderRecord.id)))
