from __future__ import annotations

from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from ax_workspace.modules.organization_access.domain import Principal, SEED_PERSONAS
from ax_workspace.entrypoints.http_auth import DeveloperAuthAdapter, developer_principal
from ax_workspace.bootstrap.application import create_workflow_application
from ax_workspace.modules.ax_execution.application import (
    AccessDenied,
    InvalidDecision,
    InvalidWorkflowInput,
    RunNotFound,
)
from ax_workspace.modules.work.application import InvalidTaskTransition, TaskError, TaskNotFound, TaskState
from ax_workspace.bootstrap.settings import Settings
from ax_workspace.modules.ax_execution.domain import catalog_definitions


class PersonaResponse(BaseModel):
    id: str
    display_name: str


class CatalogItemResponse(BaseModel):
    workflow_id: str
    version: str
    title: str
    description: str
    input_schema: dict[str, object]
    graph: dict[str, object]


class StartRunRequest(BaseModel):
    input: dict[str, object]


class DecisionRequest(BaseModel):
    decision: str
    rationale: str | None = None
    payload: dict[str, object] = Field(default_factory=dict)


class CreateTaskRequest(BaseModel):
    title: str


class BlockTaskRequest(BaseModel):
    reason: str


def _runtime_error(error: Exception) -> HTTPException:
    if isinstance(error, RunNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))
    if isinstance(error, AccessDenied):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error))
    if isinstance(error, (InvalidDecision, InvalidWorkflowInput)):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))
    if isinstance(error, TaskNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))
    if isinstance(error, (TaskError, InvalidTaskTransition)):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))
    raise error


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_environment()
    app = FastAPI(title="SCAX Workflow Catalog API", version="0.1.0")
    if settings.developer_auth_enabled:
        app.state.developer_auth = DeveloperAuthAdapter(settings)
        app.state.workflow_application = create_workflow_application(settings)

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
                    input_schema=definition.input_schema,
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
                return app.state.workflow_application.start(workflow_id, principal, request.input)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/runs/{run_id}")
        def get_run(run_id: UUID, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            try:
                return app.state.workflow_application.run(run_id, principal)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/inbox")
        def inbox(principal: Principal = Depends(developer_principal)) -> list[dict[str, object]]:
            return app.state.workflow_application.inbox(principal)

        @app.get("/api/meeting-assignment-candidates", response_model=list[PersonaResponse])
        def meeting_assignment_candidates(
            principal: Principal = Depends(developer_principal),
        ) -> list[PersonaResponse]:
            try:
                return [PersonaResponse(**candidate) for candidate in app.state.workflow_application.meeting_assignment_candidates(principal)]
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/runs/{run_id}/decisions/{node_id}")
        def decide(
            run_id: UUID,
            node_id: str,
            request: DecisionRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.decide(run_id, node_id, principal, request.decision, request.rationale, request.payload)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/my-work")
        def my_work(principal: Principal = Depends(developer_principal)) -> list[dict[str, object]]:
            return app.state.workflow_application.my_work(principal)

        @app.post("/api/tasks", status_code=status.HTTP_201_CREATED)
        def create_self_task(request: CreateTaskRequest, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            try:
                return app.state.workflow_application.create_self_task(principal, request.title)
            except Exception as error:
                raise _runtime_error(error) from error

        def task_transition(task_id: UUID, target: TaskState, principal: Principal, reason: str | None = None) -> dict[str, object]:
            try:
                return app.state.workflow_application.transition_task(task_id, principal, target, reason)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/tasks/{task_id}/start")
        def start_task(task_id: UUID, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            return task_transition(task_id, TaskState.IN_PROGRESS, principal)

        @app.post("/api/tasks/{task_id}/block")
        def block_task(task_id: UUID, request: BlockTaskRequest, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            return task_transition(task_id, TaskState.BLOCKED, principal, request.reason)

        @app.post("/api/tasks/{task_id}/resume")
        def resume_task(task_id: UUID, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            return task_transition(task_id, TaskState.IN_PROGRESS, principal)

        @app.post("/api/tasks/{task_id}/complete")
        def complete_task(task_id: UUID, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            return task_transition(task_id, TaskState.COMPLETED, principal)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "profile": settings.profile}

    return app


app = create_app()
