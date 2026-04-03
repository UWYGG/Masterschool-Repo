from typing import Annotated, Any

from fastapi import Depends, FastAPI
from pydantic import BaseModel, EmailStr, Field

from app.service import AdmissionsService

app = FastAPI(title="Masterschool Admissions API")

# Production singleton — one instance for the lifetime of the server process.
_service = AdmissionsService()


def get_service() -> AdmissionsService:
    return _service


ServiceDep = Annotated[AdmissionsService, Depends(get_service)]


class CreateUserRequest(BaseModel):
    # EmailStr validates format (e.g. rejects "notanemail") before the request reaches the service.
    email: EmailStr


class CompleteTaskRequest(BaseModel):
    step_name: str
    task_name: str
    # Free-form dict matching the webhook payload for the given task.
    # Defaults to empty dict; tasks with no required fields pass with no payload.
    task_payload: dict[str, Any] = Field(default_factory=dict)


@app.get("/health")
def health() -> dict[str, str]:
    # Lightweight liveness check — useful for deployment health probes.
    return {"status": "ok"}


@app.post("/users", status_code=201)
def create_user(payload: CreateUserRequest, service: ServiceDep) -> dict[str, str]:
    # Returns the new user's unique ID — all subsequent requests use this ID.
    user_id = service.create_user(payload.email)
    return {"user_id": user_id}


@app.get("/users/{user_id}/status")
def get_user_status(user_id: str, service: ServiceDep) -> dict[str, str]:
    # Returns one of: "in_progress", "accepted", "rejected".
    return service.get_status(user_id)


@app.get("/users/{user_id}/flow")
def get_flow(user_id: str, service: ServiceDep) -> dict[str, object]:
    # Returns the full step list with completion flags and current position.
    # Enables UI copy like "You are on step 3 of 6".
    return service.get_flow(user_id)


@app.get("/users/{user_id}/current")
def get_current(user_id: str, service: ServiceDep) -> dict[str, object]:
    # Returns the specific step and task the user should act on next.
    return service.get_current(user_id)


@app.put("/users/{user_id}/tasks/complete")
def complete_task(user_id: str, payload: CompleteTaskRequest, service: ServiceDep) -> dict[str, Any]:
    # Receives a webhook-style completion event and advances the user's progress.
    return service.complete_task(
        user_id,
        payload.step_name,
        payload.task_name,
        payload.task_payload,
    )
