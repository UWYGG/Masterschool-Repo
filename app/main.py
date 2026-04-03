from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from app.service import AdmissionsService

app = FastAPI(title="Masterschool Admissions API")
service = AdmissionsService()


class CreateUserRequest(BaseModel):
    email: str


class CompleteTaskRequest(BaseModel):
    user_id: str
    step_name: str
    task_name: str
    task_payload: dict[str, Any] = Field(default_factory=dict)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/users", status_code=201)
def create_user(payload: CreateUserRequest) -> dict[str, str]:
    user_id = service.create_user(payload.email)
    return {"user_id": user_id}


@app.get("/users/{user_id}/status")
def get_user_status(user_id: str) -> dict[str, str]:
    return service.get_status(user_id)


@app.get("/flow/{user_id}")
def get_flow(user_id: str) -> dict[str, object]:
    return service.get_flow(user_id)


@app.get("/users/{user_id}/current")
def get_current(user_id: str) -> dict[str, object]:
    return service.get_current(user_id)


@app.put("/tasks/complete")
def complete_task(payload: CompleteTaskRequest) -> dict[str, str]:
    return service.complete_task(
        payload.user_id,
        payload.step_name,
        payload.task_name,
        payload.task_payload,
    )


# TODO: Keep main.py as the routing layer only (already mostly true).
# TODO: Standardize HTTP status codes (e.g., 201 for user creation, policy-based duplicates).
# TODO: Define idempotency behavior for duplicate task completion events.
# TODO: Main responsibility should be:
#       1) receive HTTP request
#       2) call service method
#       3) return service response
