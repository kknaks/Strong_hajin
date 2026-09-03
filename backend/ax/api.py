from __future__ import annotations

from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from ax.auth import DeveloperAuthAdapter, Principal, SEED_PERSONAS, developer_principal
from ax.database import make_session_factory
from ax.runtime import (
    AccessDenied,
    InvalidDecision,
    InvalidWorkflowInput,
    RunNotFound,
    SqlAlchemyUnitOfWork,
    WorkflowRunStarter,
)
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


class StartRunRequest(BaseModel):
    input: dict[str, object]


class DecisionRequest(BaseModel):
    decision: str
    rationale: str | None = None
    payload: dict[str, object] = Field(default_factory=dict)


def _runtime_error(error: Exception) -> HTTPException:
    if isinstance(error, RunNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))
    if isinstance(error, AccessDenied):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error))
    if isinstance(error, (InvalidDecision, InvalidWorkflowInput)):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))
    raise error


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_environment()
    app = FastAPI(title="SCAX Workflow Catalog API", version="0.1.0")
    if settings.developer_auth_enabled:
        app.state.developer_auth = DeveloperAuthAdapter(settings)
        app.state.session_factory = make_session_factory(settings.database_url)
        app.state.uow_factory = lambda: SqlAlchemyUnitOfWork(app.state.session_factory)

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

        @app.post("/api/runs/{workflow_id}", status_code=status.HTTP_201_CREATED)
        def start_run(
            workflow_id: str,
            request: StartRunRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                with app.state.uow_factory() as uow:
                    assert uow.workflows is not None
                    return WorkflowRunStarter(uow.workflows).start(workflow_id, principal, request.input)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/runs/{run_id}")
        def get_run(run_id: UUID, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            try:
                with app.state.uow_factory() as uow:
                    assert uow.workflows is not None
                    service = WorkflowRunStarter(uow.workflows)
                    result = service.summary(run_id)
                    if result["workflow_id"] not in {item.workflow_id for item in catalog_definitions() if item.is_visible_to(principal)}:
                        raise AccessDenied("Principal cannot view this workflow run")
                    return result
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/inbox")
        def inbox(principal: Principal = Depends(developer_principal)) -> list[dict[str, object]]:
            with app.state.uow_factory() as uow:
                assert uow.workflows is not None
                return WorkflowRunStarter(uow.workflows).inbox(principal)

        @app.post("/api/runs/{run_id}/decisions/{node_id}")
        def decide(
            run_id: UUID,
            node_id: str,
            request: DecisionRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                with app.state.uow_factory() as uow:
                    assert uow.workflows is not None
                    return WorkflowRunStarter(uow.workflows).decide(
                        run_id, node_id, principal, request.decision, request.rationale, request.payload
                    )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/my-work")
        def my_work(principal: Principal = Depends(developer_principal)) -> list[dict[str, object]]:
            with app.state.uow_factory() as uow:
                assert uow.workflows is not None
                return WorkflowRunStarter(uow.workflows).my_work(principal)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "profile": settings.profile}

    return app


app = create_app()
