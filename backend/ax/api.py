from __future__ import annotations

from fastapi import Depends, FastAPI
from pydantic import BaseModel

from ax.auth import DeveloperAuthAdapter, Principal, SEED_PERSONAS, developer_principal
from ax.settings import Settings
from ax.workflows import catalog_definitions


class PersonaResponse(BaseModel):
    id: str
    display_name: str


class CatalogItemResponse(BaseModel):
    workflow_id: str
    version: str
    title: str
    description: str
    graph: dict[str, object]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_environment()
    app = FastAPI(title="SCAX Workflow Catalog API", version="0.1.0")
    if settings.developer_auth_enabled:
        app.state.developer_auth = DeveloperAuthAdapter(settings)

        @app.get("/api/developer/personas", response_model=list[PersonaResponse])
        def personas() -> list[PersonaResponse]:
            return [PersonaResponse(id=persona.id, display_name=persona.display_name) for persona in SEED_PERSONAS.values()]

        @app.get("/api/catalog", response_model=list[CatalogItemResponse])
        def catalog(principal: Principal = Depends(developer_principal)) -> list[CatalogItemResponse]:
            return [
                CatalogItemResponse(
                    workflow_id=definition.workflow_id,
                    version=definition.version,
                    title=definition.title,
                    description=definition.description,
                    graph={
                        "nodes": [node.model_dump(mode="json") for node in definition.nodes],
                        "edges": [edge.model_dump(mode="json") for edge in definition.edges],
                    },
                )
                for definition in catalog_definitions()
                if definition.is_visible_to(principal)
            ]

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "profile": settings.profile}

    return app


app = create_app()

