from __future__ import annotations

from typing import Any
from uuid import UUID

from datetime import UTC, datetime

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ax_workspace.modules.organization_access.credentials import LocalCredential, normalize_email
from ax_workspace.modules.organization_access.domain import ORGANIZATION_ACTIVITY_AXES, TASK_ASSIGN, Grant, Principal
from ax_workspace.modules.organization_access.application import AssistantCharacterPreferenceConflict
from ax_workspace.platform.persistence import (
    AccessGrantRecord,
    AssistantCharacterPreferenceRecord,
    ActivityEventRecord,
    AppointmentRecord,
    EmploymentPeriodRecord,
    GradeAssignmentRecord,
    GradeRecord,
    JobAssignmentRecord,
    JobRecord,
    MemberCredentialRecord,
    MemberRecord,
    MembershipRecord,
    OrganizationUnitRecord,
    OrganizationUnitTypeRecord,
    PositionDefinitionRecord,
    RoleCapabilityRecord,
    RoleRecord,
    StandardGrantRuleRecord,
)


class SqlAlchemyOrganizationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def profile_for(self, member_id: str) -> dict[str, Any] | None:
        member = self._session.get(MemberRecord, member_id)
        now = datetime.now(UTC)
        employment = self._session.scalar(
            select(EmploymentPeriodRecord)
            .where(
                EmploymentPeriodRecord.member_id == member_id,
                EmploymentPeriodRecord.state == "active",
                EmploymentPeriodRecord.ended_at.is_(None),
            )
            .order_by(EmploymentPeriodRecord.started_at.desc())
        )
        if member is None or member.employment_state != "active" or employment is None or employment.state != "active":
            return None
        organizations = self._session.execute(
            select(OrganizationUnitRecord.id, OrganizationUnitRecord.name)
            .join(MembershipRecord, MembershipRecord.organization_id == OrganizationUnitRecord.id)
            .where(
                MembershipRecord.member_id == member_id,
                MembershipRecord.valid_from <= now,
                or_(MembershipRecord.valid_until.is_(None), MembershipRecord.valid_until > now),
            )
            .order_by(OrganizationUnitRecord.id)
        ).all()
        appointments = list(
            self._session.scalars(
                select(AppointmentRecord).where(
                    AppointmentRecord.member_id == member_id,
                    AppointmentRecord.valid_from <= now,
                    or_(AppointmentRecord.valid_until.is_(None), AppointmentRecord.valid_until > now),
                )
            )
        )
        appointed_roles = {item.role_id for item in appointments}
        grants = list(
            self._session.scalars(
                select(AccessGrantRecord)
                .where(
                    AccessGrantRecord.member_id == member_id,
                    AccessGrantRecord.valid_from <= now,
                    AccessGrantRecord.revoked_at.is_(None),
                    or_(AccessGrantRecord.valid_until.is_(None), AccessGrantRecord.valid_until > now),
                )
                .order_by(AccessGrantRecord.valid_from)
            )
        )
        # 보직이 만든 grant는 그 보직과 함께 끝나고, 소속이 만든 grant는 소속이 있는 동안 남는다. 어느 쪽인지는
        # 그 grant를 만든 규칙이 말한다 — grant에 규칙이 적혀 있다는 사실만으로 보직을 요구하지 않는다.
        rule_ids = {str(grant.origin_rule_id) for grant in grants if grant.origin_rule_id}
        appointment_rules = {
            rule.id
            for rule in self._session.scalars(
                select(StandardGrantRuleRecord).where(StandardGrantRuleRecord.id.in_(rule_ids))
            )
            if rule.trigger_kind == "appointment"
        } if rule_ids else set()
        capabilities: set[str] = set()
        effective_grants: list[AccessGrantRecord] = []
        granted_capabilities: dict[UUID, set[str]] = {}
        for grant in grants:
            if grant.origin_rule_id in appointment_rules and grant.role_id not in appointed_roles:
                continue
            effective_grants.append(grant)
            carried = granted_capabilities.setdefault(grant.id, set())
            if grant.capability_id:
                capabilities.add(grant.capability_id)
                carried.add(grant.capability_id)
            if grant.role_id:
                # Role snapshot: only mappings that existed at the pinned role_capability_version apply.
                pinned = grant.role_capability_version or 1
                mapped = set(
                    self._session.scalars(
                        select(RoleCapabilityRecord.capability_id).where(
                            RoleCapabilityRecord.role_id == grant.role_id, RoleCapabilityRecord.mapping_version <= pinned
                        )
                    )
                )
                capabilities.update(mapped)
                carried.update(mapped)
        role_ids = sorted(appointed_roles | {grant.role_id for grant in effective_grants if grant.role_id})
        role_labels = {item.id: item.label for item in self._session.scalars(select(RoleRecord).where(RoleRecord.id.in_(role_ids)))} if role_ids else {}
        roles = [role_labels[role_id] for role_id in role_ids if role_id in role_labels]
        unit_names = {item.id: item.name for item in self._session.scalars(select(OrganizationUnitRecord))}
        capabilities = sorted(capabilities)
        return {
            "member_id": member.id,
            "display_name": member.display_name,
            "organizations": [{"id": item.id, "name": item.name} for item in organizations],
            "roles": roles,
            "capabilities": capabilities,
            "grants": [
                {
                    "grant_id": str(grant.id),
                    "role_id": grant.role_id,
                    "role_label": role_labels.get(grant.role_id or "", None),
                    "capability_id": grant.capability_id,
                    "role_capability_version": grant.role_capability_version,
                    "scope_kind": grant.scope_kind,
                    "scope_ref": grant.scope_ref,
                    "scope_name": unit_names.get(grant.scope_ref or "", grant.scope_ref),
                    "include_descendants": grant.include_descendants,
                    "capabilities": sorted(granted_capabilities.get(grant.id, set())),
                    "origin_rule_id": grant.origin_rule_id,
                    "granted_by": grant.granted_by_member_id,
                    "valid_from": grant.valid_from.isoformat(),
                    "valid_until": grant.valid_until.isoformat() if grant.valid_until else None,
                }
                for grant in effective_grants
            ],
        }

    def assistant_character_preference(self, member_id: str) -> dict[str, Any] | None:
        record = self._session.get(AssistantCharacterPreferenceRecord, member_id)
        if record is None:
            return None
        return {"character_key": record.character_key, "version": record.version}

    def save_assistant_character_preference(
        self, member_id: str, character_key: str, expected_version: int
    ) -> dict[str, Any]:
        if expected_version == 0:
            record = AssistantCharacterPreferenceRecord(
                member_id=member_id,
                character_key=character_key,
                version=1,
            )
            self._session.add(record)
            try:
                self._session.flush()
            except IntegrityError as error:
                self._session.rollback()
                raise AssistantCharacterPreferenceConflict("AX 캐릭터 설정이 이미 변경됐습니다.") from error
            return {"character_key": record.character_key, "version": record.version}

        row = self._session.execute(
            update(AssistantCharacterPreferenceRecord)
            .where(
                AssistantCharacterPreferenceRecord.member_id == member_id,
                AssistantCharacterPreferenceRecord.version == expected_version,
            )
            .values(character_key=character_key, version=expected_version + 1, updated_at=datetime.now(UTC))
            .returning(
                AssistantCharacterPreferenceRecord.character_key,
                AssistantCharacterPreferenceRecord.version,
            )
        ).one_or_none()
        if row is None:
            raise AssistantCharacterPreferenceConflict("AX 캐릭터 설정이 다른 곳에서 변경됐습니다.")
        return {"character_key": row.character_key, "version": row.version}

    def member_detail(self, member_id: str) -> dict[str, Any] | None:
        """한 사람을 여섯 축으로 한 번에 읽는다 — 지금 값과, 지금 닿지 않게 된 권한까지.

        SPEC-005 §2의 축들은 서로 다른 사실이라 한 질문에 함께 답해도 섞이지 않는다. 여기서는 **누가 볼 수
        있는지를 정하지 않는다** — 원장은 있는 그대로 말하고, 무엇을 비울지는 application이 정한다. 그래야 권한
        규칙이 두 곳에 흩어지지 않는다.
        """
        profile = self.profile_for(member_id)
        if profile is None:
            return None
        member = self._session.get(MemberRecord, member_id)
        assert member is not None  # profile_for가 이미 확인했다
        now = datetime.now(UTC)
        units = {unit.id: unit for unit in self._session.scalars(select(OrganizationUnitRecord))}
        type_names = {item.id: item.name for item in self._session.scalars(select(OrganizationUnitTypeRecord))}

        memberships = list(
            self._session.scalars(
                select(MembershipRecord)
                .where(
                    MembershipRecord.member_id == member_id,
                    MembershipRecord.valid_from <= now,
                    or_(MembershipRecord.valid_until.is_(None), MembershipRecord.valid_until > now),
                )
                .order_by(MembershipRecord.is_primary.desc(), MembershipRecord.organization_id)
            )
        )
        primary = next((row.organization_id for row in memberships if row.is_primary), None)
        if primary is None and memberships:
            primary = memberships[0].organization_id

        # 직책 축은 직책을 말한다. 자리 이름이 없는 발령 행은 그 사람의 역할을 조직에 붙들어 두는 것이라
        # 권한 축(`grants`)이 이미 말하고 있다 — 여기 함께 실으면 직책이 없는 사람에게도 직책이 있는 것처럼 보인다.
        appointments = self._session.execute(
            select(AppointmentRecord, PositionDefinitionRecord.name)
            .join(PositionDefinitionRecord, PositionDefinitionRecord.id == AppointmentRecord.position_definition_id)
            .where(
                AppointmentRecord.member_id == member_id,
                AppointmentRecord.valid_from <= now,
                or_(AppointmentRecord.valid_until.is_(None), AppointmentRecord.valid_until > now),
            )
            .order_by(AppointmentRecord.valid_from)
        ).all()

        grade = self._session.execute(
            select(GradeRecord.id, GradeRecord.name)
            .join(GradeAssignmentRecord, GradeAssignmentRecord.grade_id == GradeRecord.id)
            .where(
                GradeAssignmentRecord.member_id == member_id,
                GradeAssignmentRecord.valid_from <= now,
                or_(GradeAssignmentRecord.valid_until.is_(None), GradeAssignmentRecord.valid_until > now),
            )
            .order_by(GradeAssignmentRecord.valid_from.desc())
        ).first()

        jobs = self._session.execute(
            select(JobRecord.id, JobRecord.name, JobAssignmentRecord.assignment_kind)
            .join(JobAssignmentRecord, JobAssignmentRecord.job_id == JobRecord.id)
            .where(
                JobAssignmentRecord.member_id == member_id,
                JobAssignmentRecord.valid_from <= now,
                or_(JobAssignmentRecord.valid_until.is_(None), JobAssignmentRecord.valid_until > now),
            )
            .order_by(JobRecord.id)
        ).all()

        # 회수된 권한은 지워진 것이 아니다. 지금 닿지 않는다는 사실과 언제 거두었는지가 화면에 남아야 한다.
        revoked = list(
            self._session.scalars(
                select(AccessGrantRecord)
                .where(AccessGrantRecord.member_id == member_id, AccessGrantRecord.revoked_at.is_not(None))
                .order_by(AccessGrantRecord.revoked_at.desc())
            )
        )
        role_labels = {
            item.id: item.label
            for item in self._session.scalars(
                select(RoleRecord).where(RoleRecord.id.in_({grant.role_id for grant in revoked if grant.role_id}))
            )
        } if revoked else {}

        return {
            "member_id": member.id,
            "display_name": member.display_name,
            "employment_state": member.employment_state,
            "employment_type": member.employment_type,
            # 계정이 있다는 것은 권한이 아니라 들어올 문이 있다는 뜻이다.
            "has_account": bool(member.account_ref),
            "phone": member.phone,
            "birth_date": member.birth_date.isoformat() if member.birth_date else None,
            "hierarchy_path": [
                {"unit_id": unit.id, "name": unit.name, "unit_type": type_names.get(unit.unit_type_id, unit.unit_type_id)}
                for unit in self._unit_path(primary, units)
            ],
            "memberships": [
                {
                    "unit_id": row.organization_id,
                    "unit_name": units[row.organization_id].name if row.organization_id in units else row.organization_id,
                    "kind": row.membership_kind,
                    "valid_from": row.valid_from.isoformat(),
                    "valid_until": row.valid_until.isoformat() if row.valid_until else None,
                }
                for row in memberships
            ],
            "appointments": [
                {
                    "unit_id": row.organization_id,
                    "unit_name": units[row.organization_id].name if row.organization_id in units else row.organization_id,
                    "position": position,
                    "role_id": row.role_id,
                    "kind": row.appointment_kind,
                    "valid_from": row.valid_from.isoformat(),
                    "valid_until": row.valid_until.isoformat() if row.valid_until else None,
                }
                for row, position in appointments
            ],
            "grade": {"id": grade.id, "name": grade.name} if grade else None,
            "jobs": [{"id": job_id, "name": name, "kind": kind} for job_id, name, kind in jobs],
            "grants": profile["grants"],
            "revoked_grants": [
                {
                    "grant_id": str(grant.id),
                    "role_id": grant.role_id,
                    "role_label": role_labels.get(grant.role_id or "", None),
                    "scope_kind": grant.scope_kind,
                    "scope_ref": grant.scope_ref,
                    "scope_name": units[grant.scope_ref].name if grant.scope_ref in units else grant.scope_ref,
                    "valid_from": grant.valid_from.isoformat(),
                    "revoked_at": grant.revoked_at.isoformat() if grant.revoked_at else None,
                }
                for grant in revoked
            ],
        }

    def member_axis_history(self, member_id: str, axis: str) -> list[dict[str, Any]]:
        """한 축이 지나온 기간들, 최신순. 지금 값도 여기 한 행으로 들어 있다 — 현재는 아직 끝나지 않은 기간이다."""
        units = {item.id: item.name for item in self._session.scalars(select(OrganizationUnitRecord))}
        rows: list[dict[str, Any]] = []

        if axis == "membership":
            for row in self._session.scalars(select(MembershipRecord).where(MembershipRecord.member_id == member_id)):
                rows.append(
                    {
                        "value": units.get(row.organization_id, row.organization_id),
                        "unit_name": units.get(row.organization_id, row.organization_id),
                        "kind": row.membership_kind,
                        "valid_from": row.valid_from,
                        "valid_until": row.valid_until,
                        "reason": row.change_reason_ref,
                        "actor": None,
                    }
                )
        elif axis == "appointment":
            for row, position in self._session.execute(
                select(AppointmentRecord, PositionDefinitionRecord.name)
                .join(PositionDefinitionRecord, PositionDefinitionRecord.id == AppointmentRecord.position_definition_id)
                .where(AppointmentRecord.member_id == member_id)
            ).all():
                rows.append(
                    {
                        "value": position,
                        "unit_name": units.get(row.organization_id, row.organization_id),
                        "kind": row.appointment_kind,
                        "valid_from": row.valid_from,
                        "valid_until": row.valid_until,
                        "reason": row.change_reason_ref,
                        "actor": None,
                    }
                )
        elif axis == "grade":
            for row, name in self._session.execute(
                select(GradeAssignmentRecord, GradeRecord.name)
                .join(GradeRecord, GradeRecord.id == GradeAssignmentRecord.grade_id)
                .where(GradeAssignmentRecord.member_id == member_id)
            ).all():
                rows.append(
                    {"value": name, "unit_name": None, "kind": None, "valid_from": row.valid_from,
                     "valid_until": row.valid_until, "reason": None, "actor": None}
                )
        elif axis == "job":
            for row, name in self._session.execute(
                select(JobAssignmentRecord, JobRecord.name)
                .join(JobRecord, JobRecord.id == JobAssignmentRecord.job_id)
                .where(JobAssignmentRecord.member_id == member_id)
            ).all():
                rows.append(
                    {"value": name, "unit_name": None, "kind": row.assignment_kind, "valid_from": row.valid_from,
                     "valid_until": row.valid_until, "reason": None, "actor": None}
                )
        elif axis == "grant":
            grants = list(
                self._session.scalars(select(AccessGrantRecord).where(AccessGrantRecord.member_id == member_id))
            )
            labels = {
                item.id: item.label
                for item in self._session.scalars(
                    select(RoleRecord).where(RoleRecord.id.in_({grant.role_id for grant in grants if grant.role_id}))
                )
            } if grants else {}
            # 부여한 이유는 grant 자신이 아니라 그때 남은 사건이 갖고 있다. 그 사건을 grant로 되짚는다.
            reasons = self._grant_reasons({grant.id for grant in grants})
            actors = self._display_names({grant.granted_by_member_id for grant in grants if grant.granted_by_member_id})
            for grant in grants:
                rows.append(
                    {
                        "value": labels.get(grant.role_id or "", grant.role_id or grant.capability_id),
                        "unit_name": units.get(grant.scope_ref or "", grant.scope_ref),
                        "kind": grant.scope_kind,
                        "valid_from": grant.valid_from,
                        # 예정 만료일이 있어도 그 전에 거두었다면 거둔 때가 끝이다 — 먼저 오는 쪽을 쓴다.
                        "valid_until": _earliest(grant.valid_until, grant.revoked_at),
                        "reason": reasons.get(grant.id),
                        "actor": actors.get(grant.granted_by_member_id or ""),
                    }
                )
        else:
            raise ValueError(axis)

        rows.sort(key=lambda row: row["valid_from"], reverse=True)
        for row in rows:
            row["valid_from"] = row["valid_from"].isoformat()
            row["valid_until"] = row["valid_until"].isoformat() if row["valid_until"] else None
        return rows

    def _grant_reasons(self, grant_ids: set[UUID]) -> dict[UUID, str | None]:
        if not grant_ids:
            return {}
        refs = {f"access_grant:{grant_id}": grant_id for grant_id in grant_ids}
        found: dict[UUID, str | None] = {}
        for event in self._session.scalars(
            select(ActivityEventRecord)
            .where(ActivityEventRecord.after_ref.in_(refs))
            .order_by(ActivityEventRecord.occurred_at)
        ):
            found[refs[str(event.after_ref)]] = event.reason
        return found

    def _display_names(self, member_ids: set[str]) -> dict[str, str]:
        if not member_ids:
            return {}
        return {
            member.id: member.display_name
            for member in self._session.scalars(select(MemberRecord).where(MemberRecord.id.in_(member_ids)))
        }

    def organization_activity(
        self, *, member_ids: frozenset[str] | None, limit: int, cursor: str | None
    ) -> list[dict[str, Any]]:
        """조직 축에서 무슨 일이 있었는지, 최신순.

        업무·요청·회의의 사건은 여기 오르지 않는다 — 축 매핑표에 있는 kind만 조직의 사건이다. `member_ids`가
        주어지면 그 사람들에 대한 사건으로 좁힌다(그 조직 아래를 물었다는 뜻이다).
        """
        if not member_ids:
            return []
        query = select(ActivityEventRecord).where(
            ActivityEventRecord.event_kind.in_(ORGANIZATION_ACTIVITY_AXES),
            # 사람에 대한 사건은 그 사람이 이 범위에 있어야 하고, 역할·조직처럼 사람이 아닌 대상의 사건은
            # 그것을 바꾼 사람이 이 범위에 있어야 한다. 역할은 제품의 것이라 어느 회사의 것도 아니지만,
            # 바꾼 행위는 한 사람의 것이고 그 사람은 한 회사에 속한다.
            or_(
                and_(ActivityEventRecord.target_type == "member", ActivityEventRecord.target_id.in_(member_ids)),
                and_(ActivityEventRecord.target_type != "member", ActivityEventRecord.actor_id.in_(member_ids)),
            ),
        )
        if cursor:
            # 정렬이 (occurred_at, id)이므로 경계도 그 둘이어야 한다. 시각만 쓰면 같은 시각의 나머지가
            # 다음 페이지에서 통째로 빠진다(PR #2 F4).
            moment, _, last_id = cursor.partition("|")
            at = datetime.fromisoformat(moment)
            query = (
                query.where(
                    or_(
                        ActivityEventRecord.occurred_at < at,
                        and_(ActivityEventRecord.occurred_at == at, ActivityEventRecord.id < UUID(last_id)),
                    )
                )
                if last_id
                # 시각만 적힌 옛 커서도 계속 받는다 — 같은 시각을 건너뛰는 예전 동작 그대로이며, 새 커서를
                # 쓰는 쪽은 행마다 실려 오는 `cursor`를 그대로 되돌려 주면 된다.
                else query.where(ActivityEventRecord.occurred_at < at)
            )
        events = list(
            self._session.scalars(
                query.order_by(ActivityEventRecord.occurred_at.desc(), ActivityEventRecord.id.desc()).limit(limit)
            )
        )
        actors = self._display_names({event.actor_id for event in events})
        return [
            {
                "occurred_at": event.occurred_at.isoformat(),
                "axis": ORGANIZATION_ACTIVITY_AXES[event.event_kind],
                "event_kind": event.event_kind,
                "summary": event.safe_summary,
                "reason": event.reason,
                "actor_id": event.actor_id,
                "actor_name": actors.get(event.actor_id, event.actor_id),
                "target_id": event.target_id,
                "target_type": event.target_type,
                # 이 행 다음부터 읽으려면 그대로 돌려주면 되는 자리표.
                "cursor": f"{event.occurred_at.isoformat()}|{event.id}",
            }
            for event in events
        ]

    def _unit_path(self, unit_id: str | None, units: dict[str, Any]) -> list[Any]:
        """회사에서 이 자리까지 내려오는 길. 위로 올라가며 모으고 뒤집는다."""
        path: list[Any] = []
        seen: set[str] = set()
        current = unit_id
        while current and current in units and current not in seen:
            seen.add(current)
            path.append(units[current])
            current = units[current].parent_id
        return list(reversed(path))

    def principal_for(self, member_id: str) -> Principal | None:
        profile = self.profile_for(member_id)
        if profile is None:
            return None
        descendants = self._unit_descendants()
        grants: list[Grant] = []
        for row in profile["grants"]:
            scope_ref = str(row["scope_ref"] or "")
            projects: frozenset[str] = frozenset()
            if row["scope_kind"] == "project":
                # 프로젝트는 조직 단위가 아니다. 조직 축은 비어 있고 프로젝트 축만 닿는다 — 그래서 프로젝트에
                # 붙었다는 이유로 그 사람의 부서 밖 업무가 함께 열리지 않는다.
                units = frozenset()
                projects = frozenset({scope_ref})
            elif row["scope_kind"] == "organization":
                # 조직 전체는 이 사람의 조직 전체다. 한 데이터베이스에 회사가 둘 있어도 한쪽의 대표가 다른 쪽을
                # 읽지 않는다.
                root = self.organization_root(near=scope_ref) or scope_ref
                units = descendants.get(root, frozenset({root}))
            elif row["include_descendants"]:
                units = descendants.get(scope_ref, frozenset({scope_ref}))
            else:
                units = frozenset({scope_ref})
            for capability in row["capabilities"]:
                grants.append(
                    Grant(
                        capability=capability,
                        scope_kind=str(row["scope_kind"]),
                        scope_ref=scope_ref,
                        units=units,
                        projects=projects,
                        role_id=row["role_id"],
                        role_capability_version=row["role_capability_version"],
                        origin_rule_id=row["origin_rule_id"],
                    )
                )
        return Principal(
            id=member_id,
            display_name=str(profile["display_name"]),
            organization_scope=frozenset(item["id"] for item in profile["organizations"]),
            capabilities=frozenset(profile["capabilities"]),
            grants=tuple(grants),
        )

    def _unit_descendants(self) -> dict[str, frozenset[str]]:
        """Each unit with everything under it, so a scoped grant can be resolved to the units it actually reaches."""
        children: dict[str, list[str]] = {}
        unit_ids: list[str] = []
        for unit_id, parent_id in self._session.execute(
            select(OrganizationUnitRecord.id, OrganizationUnitRecord.parent_id)
        ).all():
            unit_ids.append(unit_id)
            if parent_id is not None:
                children.setdefault(parent_id, []).append(unit_id)
        resolved: dict[str, frozenset[str]] = {}
        for unit_id in unit_ids:
            reach = {unit_id}
            stack = [unit_id]
            while stack:
                for child in children.get(stack.pop(), []):
                    if child not in reach:
                        reach.add(child)
                        stack.append(child)
            resolved[unit_id] = frozenset(reach)
        return resolved

    def credential_for_email(self, email: str) -> LocalCredential | None:
        record = self._session.scalar(
            select(MemberCredentialRecord).where(MemberCredentialRecord.email == normalize_email(email))
        )
        if record is None:
            return None
        return LocalCredential(member_id=record.member_id, email=record.email, password_hash=record.password_hash)

    # ---- access administration (ERD ACCESS_GRANT / ROLE_CAPABILITY writes) ----

    def member_units(self, member_id: str) -> frozenset[str]:
        now = datetime.now(UTC)
        return frozenset(
            self._session.scalars(
                select(MembershipRecord.organization_id).where(
                    MembershipRecord.member_id == member_id,
                    MembershipRecord.valid_from <= now,
                    or_(MembershipRecord.valid_until.is_(None), MembershipRecord.valid_until > now),
                )
            )
        )

    def installed_roles(self) -> list[dict[str, Any]]:
        return [
            {
                "role_id": role.id,
                "label": role.label,
                "version": role.version,
                "template_key": role.template_key,
                "customized": role.customized_at is not None,
                "capabilities": sorted(
                    self._session.scalars(
                        select(RoleCapabilityRecord.capability_id).where(
                            RoleCapabilityRecord.role_id == role.id,
                            RoleCapabilityRecord.mapping_version <= role.version,
                        )
                    )
                ),
            }
            for role in self._session.scalars(select(RoleRecord).order_by(RoleRecord.id))
        ]

    def role_exists(self, role_id: str) -> bool:
        return self._session.get(RoleRecord, role_id) is not None

    def role_version(self, role_id: str) -> int | None:
        role = self._session.get(RoleRecord, role_id)
        return None if role is None else role.version

    def add_role_grant(
        self,
        *,
        member_id: str,
        role_id: str,
        scope_kind: str,
        scope_ref: str,
        include_descendants: bool,
        granted_by: str,
    ) -> dict[str, Any]:
        role = self._session.get(RoleRecord, role_id)
        grant = AccessGrantRecord(
            member_id=member_id,
            role_id=role_id,
            # The grant pins the role as it is now: a later change to the role does not travel backwards into it.
            role_capability_version=role.version if role is not None else 1,
            scope_kind=scope_kind,
            # 조직 전체에 주는 것이면 그 조직의 실제 꼭대기에 붙는다. 이름을 미리 알고 있지 않는다.
            scope_organization_id=scope_ref if scope_kind == "unit" else self.organization_root(near=scope_ref),
            scope_ref=scope_ref,
            include_descendants=include_descendants,
            granted_by_member_id=granted_by,
        )
        self._session.add(grant)
        self._session.flush()
        return {
            "grant_id": str(grant.id),
            "member_id": member_id,
            "role_id": role_id,
            "scope_kind": scope_kind,
            "scope_ref": scope_ref,
            "include_descendants": include_descendants,
            "role_capability_version": grant.role_capability_version,
        }

    def grant(self, grant_id: UUID) -> AccessGrantRecord | None:
        return self._session.get(AccessGrantRecord, grant_id)

    def revoke_grant(self, grant_id: UUID) -> None:
        grant = self._session.get(AccessGrantRecord, grant_id)
        if grant is not None and grant.revoked_at is None:
            grant.revoked_at = datetime.now(UTC)
            self._session.flush()

    def replace_role_capabilities(self, role_id: str, capabilities: list[str]) -> int:
        """A new mapping version, and the role marked as the organization's own from now on."""
        role = self._session.get(RoleRecord, role_id)
        if role is None:
            raise LookupError(role_id)
        version = role.version + 1
        self._session.execute(delete(RoleCapabilityRecord).where(RoleCapabilityRecord.role_id == role_id))
        for capability in capabilities:
            self._session.add(
                RoleCapabilityRecord(role_id=role_id, capability_id=capability, mapping_version=version)
            )
        role.version = version
        role.customized_at = datetime.now(UTC)
        # Grants that pinned an older snapshot would otherwise keep the previous meaning forever; the organization
        # changed what the role means, so the grants that carry that role move with it.
        self._session.execute(
            update(AccessGrantRecord)
            .where(AccessGrantRecord.role_id == role_id, AccessGrantRecord.revoked_at.is_(None))
            .values(role_capability_version=version)
        )
        self._session.flush()
        return version

    def members_administering(self, unit: str) -> set[str]:
        """Everyone who can still administer access covering this unit, as the ledger stands right now."""
        administering: set[str] = set()
        for member_id in self._session.scalars(select(MemberRecord.id)):
            principal = self.principal_for(member_id)
            if principal is not None and principal.allows("organization.manage", unit=unit):
                administering.add(member_id)
        return administering

    def append_access_audit(
        self,
        *,
        target_type: str,
        target_id: str,
        event_kind: str,
        actor_id: str,
        summary: str,
        reason: str | None,
        before_ref: str | None = None,
        after_ref: str | None = None,
    ) -> None:
        self._session.add(
            ActivityEventRecord(
                target_type=target_type,
                target_id=target_id,
                event_kind=event_kind,
                actor_id=actor_id,
                before_ref=before_ref,
                after_ref=after_ref,
                reason=reason,
                safe_summary=summary[:300],
                occurred_at=datetime.now(UTC),
            )
        )
        self._session.flush()

    def unit_descendants(self, unit_id: str) -> frozenset[str]:
        """이 조직과 그 아래 전부. 한 조직을 물었다는 것은 그 아래를 함께 물었다는 뜻이다."""
        return self._unit_descendants().get(unit_id, frozenset({unit_id}))

    def member_ids_in(self, units: frozenset[str]) -> frozenset[str]:
        """Active members whose current membership sits in one of these units."""
        if not units:
            return frozenset()
        now = datetime.now(UTC)
        active = set(
            self._session.scalars(
                select(EmploymentPeriodRecord.member_id).where(
                    EmploymentPeriodRecord.state == "active", EmploymentPeriodRecord.ended_at.is_(None)
                )
            )
        )
        rows = self._session.scalars(
            select(MembershipRecord.member_id).where(
                MembershipRecord.organization_id.in_(units),
                MembershipRecord.valid_from <= now,
                or_(MembershipRecord.valid_until.is_(None), MembershipRecord.valid_until > now),
            )
        )
        return frozenset(member_id for member_id in rows if member_id in active)

    def demo_accounts(self, email_domain: str) -> list[dict[str, str]]:
        """The local demo's own accounts, for the sign-in shortcut on a developer machine.

        Only credentials at the demo domain are listed, so a real account someone adds to a local database is not
        enumerated alongside them. Everything here is what `reset-demo` installed and the README already prints.
        """
        active = set(
            self._session.scalars(
                select(EmploymentPeriodRecord.member_id).where(
                    EmploymentPeriodRecord.state == "active", EmploymentPeriodRecord.ended_at.is_(None)
                )
            )
        )
        rows = self._session.execute(
            select(MemberCredentialRecord.member_id, MemberCredentialRecord.email, MemberRecord.display_name)
            .join(MemberRecord, MemberRecord.id == MemberCredentialRecord.member_id)
            .where(
                MemberCredentialRecord.email.endswith(f"@{email_domain}"),
                MemberRecord.employment_state == "active",
            )
            .order_by(MemberRecord.id)
        ).all()
        return [
            {"member_id": row.member_id, "email": row.email, "display_name": row.display_name}
            for row in rows
            if row.member_id in active
        ]

    def organization_root(self, *, near: str | None = None) -> str | None:
        """이 조직의 꼭대기. 회사 이름이 무엇이든, 위로 더 올라갈 곳이 없는 단위가 그 자리다.

        `near`를 주면 그 단위에서 위로 올라가 만나는 꼭대기를 돌려준다. 한 데이터베이스에 뿌리가 여럿일 수
        있으므로 — 예시 회사 옆에 실제 조직이 들어온 경우 — 누구의 꼭대기인지 물을 수 있어야 한다.
        """
        if near:
            seen: set[str] = set()
            current: str | None = near
            while current and current not in seen:
                seen.add(current)
                unit = self._session.get(OrganizationUnitRecord, current)
                if unit is None:
                    return None
                if unit.parent_id is None:
                    return unit.id
                current = unit.parent_id
            return near
        return self._session.scalar(
            select(OrganizationUnitRecord.id)
            .where(OrganizationUnitRecord.parent_id.is_(None))
            .order_by(OrganizationUnitRecord.display_order, OrganizationUnitRecord.id)
        )

    def _active_member_names(self) -> list[dict[str, Any]]:
        """재직 중인 사람의 이름. 조직의 꼭대기가 무엇으로 불리든 이 목록은 같다.

        전화·생년월일은 여기서 함께 읽고 **누구에게 보일지는 정하지 않는다** — 그 판단은 원장이 아니라
        application의 몫이라, 권한 규칙이 두 곳에 흩어지지 않는다.
        """
        rows = self._session.execute(
            select(
                MemberRecord.id,
                MemberRecord.display_name,
                MemberRecord.phone,
                MemberRecord.birth_date,
                MemberRecord.account_ref,
            )
            .join(EmploymentPeriodRecord, EmploymentPeriodRecord.member_id == MemberRecord.id)
            .where(
                MemberRecord.employment_state == "active",
                EmploymentPeriodRecord.state == "active",
                EmploymentPeriodRecord.ended_at.is_(None),
            )
            .order_by(MemberRecord.id)
        ).all()
        return [
            {
                "id": str(member_id),
                "display_name": str(name),
                "phone": phone or None,
                "birth_date": born.isoformat() if born else None,
                # 계정이 있다는 것은 권한이 아니라 들어올 문이 있다는 뜻이다 — 화면의 「계정 없음」 배지가 읽는다.
                "has_account": bool(account_ref),
            }
            for member_id, name, phone, born, account_ref in rows
        ]

    def member_directory(self) -> list[dict[str, Any]]:
        """Every active member's name, so the product can say who did what. It carries no capability."""
        return self._active_member_names()

    def _can_answer(self, member_id: str) -> bool:
        """이 사람이 요청을 받아 스스로 답할 수 있는가 — 들어올 문이 있는가.

        조직 전체가 원장에 있어도 로그인은 일부만 갖는다. 답할 수 없는 사람에게 판단을 맡기면 그 요청은 영영
        기다린다. 명부·과거 업무·회의 참석자·graph node로는 그대로 보이고, 여기서만 후보에서 빠진다.
        """
        member = self._session.get(MemberRecord, member_id)
        return member is not None and bool(member.account_ref)

    def work_request_assignee_candidates(self, principal: Principal) -> list[dict[str, str]]:
        """Return active decision-capable peers whose current org scope overlaps the requester."""
        candidates: list[dict[str, str]] = []
        member_ids = self._session.scalars(select(MemberRecord.id).order_by(MemberRecord.id))
        for member_id in member_ids:
            if member_id == str(principal.id) or not self._can_answer(member_id):
                continue
            candidate = self.principal_for(member_id)
            if candidate is None:
                continue
            if "work_request.decide" not in candidate.capabilities:
                continue
            if not principal.organization_scope.intersection(candidate.organization_scope):
                continue
            candidates.append({"id": str(candidate.id), "display_name": candidate.display_name})
        return candidates


    def member_candidates(self, principal: Principal) -> list[dict[str, str]]:
        """Every active member other than the principal (참조자 후보); the org tree stays the navigation boundary.

        후보는 **고를 수 있을 만큼만** 말한다 — id와 이름이다. 명부와 같은 행을 돌려쓰면, 명부에 열이 하나 늘 때
        마스킹을 지나지 않는 이 길로 함께 새어 나간다. 실제로 그렇게 전화·생년월일이 나갔다(PR #2 F1).
        """
        rows = self._session.execute(
            select(MemberRecord.id, MemberRecord.display_name)
            .join(EmploymentPeriodRecord, EmploymentPeriodRecord.member_id == MemberRecord.id)
            .where(
                MemberRecord.employment_state == "active",
                EmploymentPeriodRecord.state == "active",
                EmploymentPeriodRecord.ended_at.is_(None),
                MemberRecord.id != str(principal.id),
            )
            .order_by(MemberRecord.id)
        ).all()
        return [{"id": str(member_id), "display_name": str(name)} for member_id, name in rows]

    def task_assignment_candidates(self, principal: Principal) -> list[dict[str, str]]:
        """Active members inside the scope this person's assign authority was granted at, who can run a Task themselves.

        Belonging to a team is not the same as having authority over it: the reach comes from the grant, so a lead of
        one team never gains the ability to put work on another team by also being a member of it.
        """
        units = sorted(principal.scope_for(TASK_ASSIGN))
        seen: dict[str, str] = {}
        for unit_id in units:
            for member in self.unit_members(unit_id, include_descendants=True):
                member_id = str(member["member_id"])
                if member_id == str(principal.id) or member_id in seen or not self._can_answer(member_id):
                    continue
                candidate = self.principal_for(member_id)
                if candidate is None or "task.self_manage" not in candidate.capabilities:
                    continue
                seen[member_id] = candidate.display_name
        return [{"id": member_id, "display_name": name} for member_id, name in sorted(seen.items())]

    # ---- read-only organization navigation (ERD member_organization_view projection) ----

    def organization_tree(self) -> list[dict[str, Any]]:
        now = datetime.now(UTC)
        units = list(self._session.scalars(select(OrganizationUnitRecord).order_by(OrganizationUnitRecord.display_order, OrganizationUnitRecord.id)))
        types = {item.id: item for item in self._session.scalars(select(OrganizationUnitTypeRecord))}
        active_member_ids = set(
            self._session.scalars(
                select(EmploymentPeriodRecord.member_id).where(EmploymentPeriodRecord.state == "active", EmploymentPeriodRecord.ended_at.is_(None))
            )
        )
        memberships = self._session.execute(
            select(MembershipRecord.organization_id, MembershipRecord.member_id).where(
                MembershipRecord.valid_from <= now,
                or_(MembershipRecord.valid_until.is_(None), MembershipRecord.valid_until > now),
            )
        ).all()
        direct: dict[str, set[str]] = {}
        for organization_id, member_id in memberships:
            if member_id in active_member_ids:
                direct.setdefault(organization_id, set()).add(member_id)
        children: dict[str | None, list[OrganizationUnitRecord]] = {}
        for unit in units:
            children.setdefault(unit.parent_id, []).append(unit)

        def subtree_members(unit_id: str) -> set[str]:
            members = set(direct.get(unit_id, set()))
            for child in children.get(unit_id, []):
                members |= subtree_members(child.id)
            return members

        heads = self._session.execute(
            select(AppointmentRecord.organization_id, MemberRecord.display_name, PositionDefinitionRecord.name, AppointmentRecord.appointment_kind)
            .join(MemberRecord, MemberRecord.id == AppointmentRecord.member_id)
            .join(PositionDefinitionRecord, PositionDefinitionRecord.id == AppointmentRecord.position_definition_id)
            .where(AppointmentRecord.valid_from <= now, or_(AppointmentRecord.valid_until.is_(None), AppointmentRecord.valid_until > now))
        ).all()
        leaders: dict[str, list[dict[str, str]]] = {}
        for organization_id, display_name, position_name, kind in heads:
            leaders.setdefault(organization_id, []).append({"display_name": display_name, "position": position_name, "kind": kind})
        return [
            {
                "id": unit.id,
                "name": unit.name,
                "parent_id": unit.parent_id,
                "unit_type": types[unit.unit_type_id].name if unit.unit_type_id in types else None,
                "lifecycle": unit.lifecycle,
                "display_order": unit.display_order,
                "member_count": len(subtree_members(unit.id)),
                "direct_member_count": len(direct.get(unit.id, set())),
                "leaders": leaders.get(unit.id, []),
            }
            for unit in units
        ]

    def unit_members(self, unit_id: str, *, include_descendants: bool = True) -> list[dict[str, Any]]:
        now = datetime.now(UTC)
        units = list(self._session.scalars(select(OrganizationUnitRecord)))
        children: dict[str | None, list[str]] = {}
        for unit in units:
            children.setdefault(unit.parent_id, []).append(unit.id)
        scope = {unit_id}
        if include_descendants:
            stack = [unit_id]
            while stack:
                current = stack.pop()
                for child in children.get(current, []):
                    if child not in scope:
                        scope.add(child)
                        stack.append(child)
        unit_names = {unit.id: unit.name for unit in units}
        rows = self._session.execute(
            select(MembershipRecord.member_id, MembershipRecord.organization_id, MembershipRecord.membership_kind)
            .where(
                MembershipRecord.organization_id.in_(scope),
                MembershipRecord.valid_from <= now,
                or_(MembershipRecord.valid_until.is_(None), MembershipRecord.valid_until > now),
            )
        ).all()
        member_ids = sorted({row.member_id for row in rows})
        result: list[dict[str, Any]] = []
        for member_id in member_ids:
            profile = self.profile_for(member_id)
            if profile is None:
                continue
            memberships = [
                {"organization_id": row.organization_id, "organization_name": unit_names.get(row.organization_id, row.organization_id), "kind": row.membership_kind}
                for row in rows
                if row.member_id == member_id
            ]
            positions = self._session.execute(
                select(PositionDefinitionRecord.name, OrganizationUnitRecord.name, AppointmentRecord.appointment_kind)
                .join(PositionDefinitionRecord, PositionDefinitionRecord.id == AppointmentRecord.position_definition_id)
                .join(OrganizationUnitRecord, OrganizationUnitRecord.id == AppointmentRecord.organization_id)
                .where(
                    AppointmentRecord.member_id == member_id,
                    AppointmentRecord.valid_from <= now,
                    or_(AppointmentRecord.valid_until.is_(None), AppointmentRecord.valid_until > now),
                )
            ).all()
            grade = self._session.scalar(
                select(GradeRecord.name)
                .join(GradeAssignmentRecord, GradeAssignmentRecord.grade_id == GradeRecord.id)
                .where(GradeAssignmentRecord.member_id == member_id, or_(GradeAssignmentRecord.valid_until.is_(None), GradeAssignmentRecord.valid_until > now))
            )
            jobs = list(
                self._session.scalars(
                    select(JobRecord.name)
                    .join(JobAssignmentRecord, JobAssignmentRecord.job_id == JobRecord.id)
                    .where(JobAssignmentRecord.member_id == member_id, or_(JobAssignmentRecord.valid_until.is_(None), JobAssignmentRecord.valid_until > now))
                )
            )
            result.append(
                {
                    "member_id": member_id,
                    "display_name": profile["display_name"],
                    "memberships": memberships,
                    "positions": [{"position": name, "organization_name": unit, "kind": kind} for name, unit, kind in positions],
                    "grade": grade,
                    "jobs": jobs,
                }
            )
        return result


def _earliest(*moments: datetime | None) -> datetime | None:
    """적힌 것들 중 가장 먼저 오는 때. 아무것도 적히지 않았으면 아직 끝나지 않은 것이다."""
    stated = [moment for moment in moments if moment is not None]
    return min(stated) if stated else None
