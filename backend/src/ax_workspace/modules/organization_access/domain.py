from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PersonaId(StrEnum):
    MINA = "mina"
    JIHO = "jiho"
    SORA = "sora"
    MINSEOK = "minseok"
    DEMO_ADMIN = "demo-admin"


@dataclass(frozen=True, slots=True)
class Principal:
    id: PersonaId
    display_name: str
    organization_scope: frozenset[str]
    capabilities: frozenset[str]


TASK_READ = "task.read"
TASK_SELF_MANAGE = "task.self_manage"
TASK_ASSIGN = "task.assign"
WORK_REQUEST_READ = "work_request.read"
WORK_REQUEST_CREATE = "work_request.create"
WORK_REQUEST_DECIDE = "work_request.decide"
DAILY_REPORT_READ = "daily_report.read"
DAILY_REPORT_GENERATE = "daily_report.generate"
DAILY_REPORT_EDIT = "daily_report.edit"
DAILY_REPORT_SUBMIT = "daily_report.submit"
ACTION_READ = "action.read"
ACTION_DECIDE = "action.decide"
MEETING_READ = "meeting.read"
MEETING_READ_PRIVATE = "meeting.read.private"
MEETING_MANAGE = "meeting.manage"
MEETING_SHARE = "meeting.share"


SEED_PERSONAS: dict[PersonaId, Principal] = {
    PersonaId.MINA: Principal(
        PersonaId.MINA,
        "민아 (구성원)",
        frozenset({"scax", "product"}),
        frozenset({
            "work.read",
            DAILY_REPORT_READ,
            DAILY_REPORT_GENERATE,
            DAILY_REPORT_EDIT,
            DAILY_REPORT_SUBMIT,
            "meeting.followup.request",
            MEETING_READ,
            MEETING_MANAGE,
            MEETING_SHARE,
            "task.accept",
            TASK_READ,
            TASK_SELF_MANAGE,
            WORK_REQUEST_READ,
            WORK_REQUEST_CREATE,
            ACTION_READ,
            ACTION_DECIDE,
        }),
    ),
    PersonaId.JIHO: Principal(
        PersonaId.JIHO,
        "지호 (팀장)",
        frozenset({"scax", "product"}),
        frozenset({
            "work.read",
            WORK_REQUEST_READ,
            WORK_REQUEST_DECIDE,
            TASK_READ,
            TASK_SELF_MANAGE,
            "report.review",
            "meeting.followup.assign",
            MEETING_READ,
            MEETING_MANAGE,
            MEETING_SHARE,
            "team.manage",
            TASK_ASSIGN,
            ACTION_READ,
            ACTION_DECIDE,
        }),
    ),
    PersonaId.SORA: Principal(
        PersonaId.SORA,
        "소라 (법무)",
        frozenset({"scax", "legal"}),
        frozenset({"contract.legal_review", MEETING_READ}),
    ),
    PersonaId.MINSEOK: Principal(
        PersonaId.MINSEOK,
        "민석 (재무)",
        frozenset({"scax", "finance"}),
        frozenset({"contract.finance_review", MEETING_READ}),
    ),
    PersonaId.DEMO_ADMIN: Principal(
        PersonaId.DEMO_ADMIN,
        "데모 관리자",
        frozenset({"scax", "product", "legal", "finance", "people"}),
        frozenset({
            "work.read", DAILY_REPORT_READ, DAILY_REPORT_GENERATE, DAILY_REPORT_EDIT,
            DAILY_REPORT_SUBMIT, "meeting.followup.request",
            "meeting.followup.assign", MEETING_READ, MEETING_READ_PRIVATE, MEETING_MANAGE, MEETING_SHARE,
            "task.accept", TASK_READ, TASK_SELF_MANAGE, TASK_ASSIGN,
            WORK_REQUEST_READ, WORK_REQUEST_CREATE, "report.review", "team.manage",
            ACTION_READ, ACTION_DECIDE,
            "contract.legal_review", "contract.finance_review", "demo.admin",
        }),
    ),
}


def seeded_principal(persona: str) -> Principal:
    """Resolve an allow-listed development principal; callers never supply privileges."""
    try:
        return SEED_PERSONAS[PersonaId(persona)]
    except ValueError as error:
        raise ValueError("Select one of the seeded demo personas.") from error
