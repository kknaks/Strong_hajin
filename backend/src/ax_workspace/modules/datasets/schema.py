"""사람이 채우는 dataset의 모양.

A dataset is a folder someone keeps outside this repository: a manifest, one CSV per kind of fact, and the original
files. This module is that contract and nothing else — the columns, the keys rows use to point at each other, and
what counts as a well-formed value. It reads no files and knows no database.

Rows refer to each other by keys a person writes and can read back (`unit:product`, `member:kim-dohyung`), never by
database identifiers. Turning those into canonical ids is the importer's job, later, through the product's own
commands.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ax_workspace.modules.organization_access.catalog import ROLE_TEMPLATES


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    required: bool = True
    #: `text` | `key` | `date` | `int` | `enum`
    kind: str = "text"
    values: tuple[str, ...] = ()
    #: The table this column points at, when it is a reference.
    references: str | None = None


@dataclass(frozen=True, slots=True)
class Table:
    name: str
    #: The column whose value identifies a row to a person. Absent for tables that are pure links.
    key: str | None
    columns: tuple[Column, ...]
    note: str = ""
    #: Rows that must be unique together, for link tables that have no single key.
    unique: tuple[str, ...] = field(default_factory=tuple)

    @property
    def filename(self) -> str:
        return f"{self.name}.csv"

    @property
    def header(self) -> list[str]:
        return [column.name for column in self.columns]


UNIT_TYPES = ("company", "division", "office", "team", "part")
MEMBERSHIP_KINDS = ("primary", "additional")
APPOINTMENT_KINDS = ("primary", "concurrent")
EMPLOYMENT_STATES = ("active", "ended")
#: 어떤 형태로 일하는지. 원문이 사람별로 말하지 않으면 비워 둔다.
EMPLOYMENT_TYPES = ("regular", "part_time", "contract")
PROJECT_STATES = ("active", "closed")
PROJECT_ASSIGNMENT_KINDS = ("lead", "member")
#: 이 제품이 실제로 설치할 수 있는 역할. 여기 없는 이름은 dataset을 검사할 때 걸린다.
ROLE_KEYS = tuple(template.key for template in ROLE_TEMPLATES)

TABLES: tuple[Table, ...] = (
    Table(
        "organization_units",
        "key",
        (
            Column("key", kind="key"),
            Column("name"),
            Column("unit_type", kind="enum", values=UNIT_TYPES),
            Column("parent_key", required=False, kind="key", references="organization_units"),
            Column("display_order", required=False, kind="int"),
        ),
        note="조직 단위. parent_key가 비어 있으면 최상위다.",
    ),
    Table("grades", "key", (Column("key", kind="key"), Column("name"), Column("display_order", required=False, kind="int")), note="직급"),
    Table("jobs", "key", (Column("key", kind="key"), Column("name")), note="직무"),
    Table(
        "positions",
        "key",
        (
            Column("key", kind="key"),
            Column("name"),
            Column("unit_type", kind="enum", values=UNIT_TYPES),
            Column("slot", required=False, kind="enum", values=("head", "deputy")),
            Column("role_key", required=False, kind="enum", values=ROLE_KEYS),
        ),
        note="보직 정의. 대표이사·부서장·팀장·부팀장처럼 조직 단위 종류에 붙는다. role_key를 비우면 그 사람 자신의 역할을 쓴다.",
    ),
    Table(
        "members",
        "key",
        (
            Column("key", kind="key"),
            Column("display_name"),
            Column("employment_state", kind="enum", values=EMPLOYMENT_STATES),
            Column("employment_type", required=False, kind="enum", values=EMPLOYMENT_TYPES),
            Column("primary_unit_key", kind="key", references="organization_units"),
            Column("role_key", kind="enum", values=ROLE_KEYS),
            Column("grade_key", required=False, kind="key", references="grades"),
            Column("employed_from", required=False, kind="date"),
            Column("employed_until", required=False, kind="date"),
            #: 원문이 사람별로 말할 때만 적는 인사 정보. 열을 통째로 쓰지 않은 예전 폴더도 그대로 통과한다.
            Column("phone", required=False),
            Column("birth_date", required=False, kind="date"),
        ),
        note="구성원. 원문에 없는 발령 효력일은 비워 둔다 — 만들어 내지 않는다.",
    ),
    Table(
        "memberships",
        None,
        (
            Column("member_key", kind="key", references="members"),
            Column("unit_key", kind="key", references="organization_units"),
            Column("kind", kind="enum", values=MEMBERSHIP_KINDS),
            Column("valid_from", required=False, kind="date"),
            Column("valid_until", required=False, kind="date"),
        ),
        note="소속. 복수 소속은 여러 행으로 쓴다.",
        unique=("member_key", "unit_key"),
    ),
    Table(
        "appointments",
        None,
        (
            Column("member_key", kind="key", references="members"),
            Column("unit_key", kind="key", references="organization_units"),
            Column("position_key", kind="key", references="positions"),
            Column("kind", kind="enum", values=APPOINTMENT_KINDS),
            Column("valid_from", required=False, kind="date"),
            Column("valid_until", required=False, kind="date"),
        ),
        note="보직. 공동 대표와 겸임 책임자는 여러 행이며, 보직이 없는 사람에게는 행을 만들지 않는다.",
        unique=("member_key", "unit_key", "position_key"),
    ),
    Table(
        "job_assignments",
        None,
        (
            Column("member_key", kind="key", references="members"),
            Column("job_key", kind="key", references="jobs"),
            Column("kind", kind="enum", values=MEMBERSHIP_KINDS),
        ),
        note="직무 배정",
        unique=("member_key", "job_key"),
    ),
    Table(
        "projects",
        "key",
        (
            Column("key", kind="key"),
            Column("name"),
            Column("state", required=False, kind="enum", values=PROJECT_STATES),
            Column("starts_on", required=False, kind="date"),
            Column("ends_on", required=False, kind="date"),
            Column("description", required=False),
        ),
        note="프로젝트. 소유 조직을 두지 않는다 — 누가 참여하는지가 `project_assignments`이고 그것이 열리는 유일한 길이다. 기간은 없을 수 있다.",
    ),
    Table(
        "project_assignments",
        None,
        (
            Column("member_key", kind="key", references="members"),
            Column("project_key", kind="key", references="projects"),
            Column("kind", kind="enum", values=PROJECT_ASSIGNMENT_KINDS),
            Column("valid_from", required=False, kind="date"),
            Column("valid_until", required=False, kind="date"),
        ),
        note="누가 이 프로젝트를 함께 하는가. 조직 단위를 묻지 않으며, 담당 기간은 없을 수 있다.",
        unique=("member_key", "project_key"),
    ),
    Table(
        "logins",
        "member_key",
        (
            Column("member_key", kind="key", references="members"),
            Column("email"),
        ),
        note="로그인을 만들 구성원만 적는다. 권한은 조직이 정하므로 여기 없다. 비밀번호도 여기 두지 않고 실행 환경에서 준다.",
    ),
)

TABLES_BY_NAME = {table.name: table for table in TABLES}

SCHEMA_VERSION = 6
