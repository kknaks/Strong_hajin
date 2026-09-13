import pytest

from ax_workspace.modules.work.material_folder_policy import (
    FolderCreationContext,
    FolderTitle,
    decide_folder_creation,
    ensure_folder_archivable,
    ensure_folder_detachable,
    ensure_folder_organization_active,
)
from ax_workspace.modules.work.material_values import MaterialError, MaterialNotFound


def _decide(kind: str, title: str, organization_id: str | None = None):
    return decide_folder_creation(
        FolderCreationContext(
            actor_id="mina",
            kind=kind,
            title=FolderTitle.create(title),
            organization_id=organization_id,
            member_organization_ids=frozenset({"product"}),
        )
    )


def test_folder_creation_assigns_the_explicit_owner_and_uses_a_normalized_title_value() -> None:
    personal = _decide("personal", "  내 자료  ")
    team = _decide("team", "  제품팀 자료  ", "product")

    assert (personal.title.value, personal.owner_member_id, personal.organization_id) == ("내 자료", "mina", None)
    assert (team.title.value, team.owner_member_id, team.organization_id) == ("제품팀 자료", None, "product")


def test_folder_creation_rejects_invalid_shape_or_a_team_outside_current_membership() -> None:
    with pytest.raises(MaterialError):
        _decide("shared", "자료")
    with pytest.raises(MaterialError):
        FolderTitle.create(" ")
    with pytest.raises(MaterialError):
        _decide("personal", "자료", "product")
    with pytest.raises(MaterialNotFound):
        _decide("team", "자료", "other")

    ensure_folder_organization_active(organization_active=True)
    with pytest.raises(MaterialNotFound, match="organization was not found"):
        ensure_folder_organization_active(organization_active=False)


def test_folder_detach_and_archive_are_owned_by_uploader_and_creator() -> None:
    ensure_folder_detachable(actor_id="mina", uploaded_by_member_ids=("mina", "mina"))
    ensure_folder_archivable(actor_id="mina", created_by_member_id="mina")

    with pytest.raises(MaterialError, match="only the uploader"):
        ensure_folder_detachable(actor_id="jiho", uploaded_by_member_ids=("mina",))
    with pytest.raises(MaterialError, match="only the folder creator"):
        ensure_folder_archivable(actor_id="jiho", created_by_member_id="mina")
