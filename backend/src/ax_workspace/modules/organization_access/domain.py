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


SEED_PERSONAS: dict[PersonaId, Principal] = {
    PersonaId.MINA: Principal(
        PersonaId.MINA,
        "민아 (구성원)",
        frozenset({"scax", "product"}),
        frozenset({"work.read", "daily_report.submit", "meeting.followup.request", "task.accept"}),
    ),
    PersonaId.JIHO: Principal(
        PersonaId.JIHO,
        "지호 (팀장)",
        frozenset({"scax", "product"}),
        frozenset({"work.read", "work_request.decide", "report.review", "meeting.followup.assign", "team.manage"}),
    ),
    PersonaId.SORA: Principal(
        PersonaId.SORA,
        "소라 (법무)",
        frozenset({"scax", "legal"}),
        frozenset({"contract.legal_review"}),
    ),
    PersonaId.MINSEOK: Principal(
        PersonaId.MINSEOK,
        "민석 (재무)",
        frozenset({"scax", "finance"}),
        frozenset({"contract.finance_review"}),
    ),
    PersonaId.DEMO_ADMIN: Principal(
        PersonaId.DEMO_ADMIN,
        "데모 관리자",
        frozenset({"scax", "product", "legal", "finance", "people"}),
        frozenset({
            "work.read", "daily_report.submit", "meeting.followup.request",
            "meeting.followup.assign", "task.accept", "report.review", "team.manage",
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
