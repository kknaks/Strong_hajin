import pytest

from ax_workspace.modules.organization_access.catalog import RoleTemplate, UnknownCapability, validate


def test_role_template_rejects_capabilities_outside_the_product_catalog() -> None:
    with pytest.raises(UnknownCapability, match="capability"):
        validate(RoleTemplate("invented", "만든 역할", 1, ("task.read", "budget.approve")))
