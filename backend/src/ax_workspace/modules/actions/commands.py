"""Shared caller payload; each owning handler still normalizes and authorizes its command."""
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, JsonValue


class ActionCommandInput(BaseModel):
    """What a command needs from the caller; the server decides which command is available at all."""

    model_config = ConfigDict(extra="forbid")

    #: Always required: a command answers the version it was shown, so a stale write cannot slip through.
    expected_version: int
    #: Editable AX confirmations also answer the immutable Submission they were opened from.
    base_submission_version: int | None = None
    #: Raw editor values. The owning server operation normalizes and authorizes them again before executing.
    draft: dict[str, JsonValue] | None = None
    #: Expiring Action-bound material identities selected for this confirmation, separate from typed Task fields.
    attachment_draft_ids: list[UUID] | None = None
    reason: str | None = Field(default=None, max_length=4000)
    changes: dict[str, JsonValue] | None = None
