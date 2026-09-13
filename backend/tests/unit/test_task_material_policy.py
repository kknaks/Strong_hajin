import pytest

from ax_workspace.modules.work.material_values import MaterialError, MaterialLink, MaterialReference, MaterialRole


def test_material_link_and_role_are_normalized_value_objects() -> None:
    role = MaterialRole.create("input")
    link = MaterialLink.create("  https://docs.example.com/spec  ", "  설계 문서  ")

    assert (role.value, link.url, link.label) == ("input", "https://docs.example.com/spec", "설계 문서")


def test_material_link_rejects_non_links_credentials_and_empty_labels() -> None:
    invalid_links = (
        (("", "문서"), "url is required"),
        (("ftp://files.example.com/a", "문서"), "http"),
        (("docs.example.com/a", "문서"), "http"),
        (("javascript:alert(1)", "문서"), "http"),
        (("https://user:secret@docs.example.com/a", "문서"), "credentials"),
        (("https://docs.example.com/a", "   "), "label is required"),
    )
    for values, message in invalid_links:
        with pytest.raises(MaterialError, match=message):
            MaterialLink.create(*values)

    with pytest.raises(MaterialError, match="input or output"):
        MaterialRole.create("evidence")


def test_material_link_preserves_url_validation_precedence() -> None:
    with pytest.raises(MaterialError, match="url is required"):
        MaterialLink.create("", "")


def test_material_reference_keeps_internal_resources_separate_from_task_references() -> None:
    role = MaterialRole.create("output")
    reference = MaterialReference.create("meeting", "meeting-1")

    assert (role.value, reference.resource_type, reference.resource_id) == ("output", "meeting", "meeting-1")

    invalid = (
        ("task", "참고 업무"),
        ("workflow", "reference type"),
    )
    for resource_type, message in invalid:
        with pytest.raises(MaterialError, match=message):
            MaterialReference.create(resource_type, "resource-1")
