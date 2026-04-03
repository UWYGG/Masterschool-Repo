"""Admissions funnel business logic for the Masterschool exercise API.

The flow (steps, tasks, pass/fail rules, and which tasks are visible) is defined
in :mod:`app.admissions_config` and loaded via :func:`app.admissions_config.build_flow`.
This module applies that configuration to per-user state.

**User record** (in-memory ``users`` dict)::

    {
        "email": str,
        "status": "in_progress" | "accepted" | "rejected",
        "completed_tasks": list[str],  # keys "step_name.task_name" that passed
        "attempted_tasks": set[str],   # keys submitted with a valid payload (pass or fail)
        "context": dict,               # e.g. iq_test_score for visibility rules
    }

**Task keys** identify a task uniquely as ``"{step_name}.{task_name}"`` (e.g.
``iq_test.take_iq_test``). Only tasks that satisfy their ``pass_rule`` are
appended to ``completed_tasks``.

**IQ step:** When both the first IQ task and the second-chance task are visible,
completing either one with a passing score satisfies that step (OR semantics).
"""

from __future__ import annotations

from typing import Any, NamedTuple
from uuid import uuid4

from fastapi import HTTPException

from app.admissions_config import (
    STEP_IQ_TEST,
    TASK_SECOND_CHANCE_TEST,
    TASK_TAKE_IQ_TEST,
    StepDefinition,
    TaskDefinition,
    build_flow,
)


class StepProgress(NamedTuple):
    """Result of ``_step_progress``.

    ``current_step_number`` is the 1-based index of the first step with outstanding
    work, or ``None`` when there is none.  ``is_terminal`` distinguishes the two
    ``None`` cases: a terminal user (accepted/rejected) vs an in-progress user with
    no remaining visible work (edge case that should not normally occur).
    """
    current_step_number: int | None
    total_steps: int
    is_terminal: bool


class AdmissionsService:
    """Holds the configured flow and an in-memory map of users.

    Public methods correspond to the REST API: create user, read flow/progress,
    read status, and complete tasks with webhook-style payloads.
    """

    def __init__(self, flow: list[StepDefinition] | None = None) -> None:
        # Accepts a custom flow for testing or PM-driven overrides; defaults to the production config.
        self.flow: list[StepDefinition] = flow if flow is not None else build_flow()
        # Lookup dict for O(1) step resolution by name.
        self._steps: dict[str, StepDefinition] = {s.name: s for s in self.flow}
        # All user state lives here; keyed by UUID user_id.
        self.users: dict[str, dict[str, Any]] = {}

    def create_user(self, email: str) -> str:
        """Create a user in ``in_progress`` with empty progress; return a new unique id."""
        # One account per email address — reject duplicates before creating.
        if any(u["email"] == email for u in self.users.values()):
            raise HTTPException(status_code=409, detail="A user with this email already exists")
        user_id = str(uuid4())
        self.users[user_id] = {
            "email": email,
            "status": "in_progress",
            "completed_tasks": [],
            "attempted_tasks": set(),
            "context": {},
        }
        return user_id

    def _get_user(self, user_id: str) -> dict[str, Any]:
        """Return the user dict or raise 404."""
        user = self.users.get(user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        return user

    def _visible_task_names(self, step: StepDefinition, user: dict[str, Any]) -> list[str]:
        """Task names in this step that are visible for ``user`` (see each task's ``visibility_rule``)."""
        ctx = user.get("context") or {}
        out: list[str] = []
        for task in step.tasks:
            # Tasks with no visibility_rule are always visible.
            if task.visibility_rule is None or task.visibility_rule(ctx):
                out.append(task.name)
        return out

    def _task_def(self, step_name: str, task_name: str) -> TaskDefinition | None:
        """Resolve configuration for a task, or ``None`` if the pair is invalid."""
        step = self._steps.get(step_name)
        if not step:
            return None
        for task in step.tasks:
            if task.name == task_name:
                return task
        return None

    def _step_fully_done(self, step: StepDefinition, user: dict[str, Any], done: set[str]) -> bool:
        """Whether the step is satisfied for this user, according to its completion_rule."""
        visible = self._visible_task_names(step, user)
        return step.completion_rule(step.name, visible, done)

    def _all_flow_requirements_met(self, user: dict[str, Any], done: set[str]) -> bool:
        """True when every step in the flow is fully done for this user."""
        return all(self._step_fully_done(s, user, done) for s in self.flow)

    def _recompute_user_status(self, user: dict[str, Any]) -> None:
        """Set ``accepted`` if all steps are done; otherwise ``in_progress``. No-op if ``rejected``."""
        # Rejection is terminal — once rejected, status never changes back.
        if user["status"] == "rejected":
            return
        done = set(user["completed_tasks"])
        user["status"] = "accepted" if self._all_flow_requirements_met(user, done) else "in_progress"

    def _step_progress(self, user: dict[str, Any]) -> StepProgress:
        """Return progress metadata for the given user.

        ``is_terminal=True`` when the user is accepted or rejected — callers should
        hide the step counter in that case.  ``is_terminal=False`` with
        ``current_step_number=None`` is an edge case meaning the user is still
        ``in_progress`` but no visible incomplete task was found (should not normally
        occur once all completion rules and visibility rules are consistent).
        """
        total = len(self.flow)
        if user["status"] != "in_progress":
            return StepProgress(current_step_number=None, total_steps=total, is_terminal=True)
        done = set(user["completed_tasks"])
        for i, step in enumerate(self.flow):
            if self._step_fully_done(step, user, done):
                continue
            for name in self._visible_task_names(step, user):
                if f"{step.name}.{name}" not in done:
                    return StepProgress(current_step_number=i + 1, total_steps=total, is_terminal=False)
        return StepProgress(current_step_number=None, total_steps=total, is_terminal=False)

    def get_flow(self, user_id: str) -> dict[str, Any]:
        """Return all steps with per-task completion flags plus ``current_step_number`` and ``total_steps``.

        Only currently visible tasks are included in each step's ``tasks`` list.
        Hidden tasks (e.g. second_chance_test before a medium IQ score is recorded) are omitted.
        """
        user = self._get_user(user_id)
        done = set(user["completed_tasks"])
        steps_out: list[dict[str, Any]] = []
        for step in self.flow:
            step_done = self._step_fully_done(step, user, done)
            visible = set(self._visible_task_names(step, user))
            tasks_out = [
                {
                    "name": t.name,
                    "label": t.label,
                    "completed": f"{step.name}.{t.name}" in done,
                }
                for t in step.tasks
                if t.name in visible
            ]
            steps_out.append({
                "name": step.name,
                "label": step.label,
                "completed": step_done,
                "tasks": tasks_out,
            })
        progress = self._step_progress(user)
        return {
            "user_id": user_id,
            "total_steps": progress.total_steps,
            "current_step_number": progress.current_step_number,
            "steps": steps_out,
        }

    def get_current(self, user_id: str) -> dict[str, Any]:
        """Return the next incomplete visible task, or ``done: True`` if there is none.

        Includes ``current_step_number`` and ``total_steps`` for UI copy such as
        "step x of y" when the user is still in progress.
        """
        user = self._get_user(user_id)
        progress = self._step_progress(user)
        # User is accepted or rejected — no current task to show.
        if progress.is_terminal:
            return {
                "user_id": user_id,
                "step_name": None,
                "task_name": None,
                "done": True,
                "current_step_number": progress.current_step_number,
                "total_steps": progress.total_steps,
            }
        done = set(user["completed_tasks"])
        for step in self.flow:
            if self._step_fully_done(step, user, done):
                continue
            visible = self._visible_task_names(step, user)

            if (
                step.name == STEP_IQ_TEST
                and TASK_TAKE_IQ_TEST in visible
                and TASK_SECOND_CHANCE_TEST in visible
            ):
                second_key = f"{step.name}.{TASK_SECOND_CHANCE_TEST}"
                if second_key not in done:
                    return {
                        "user_id": user_id,
                        "step_name": step.name,
                        "task_name": TASK_SECOND_CHANCE_TEST,
                        "done": False,
                        "current_step_number": progress.current_step_number,
                        "total_steps": progress.total_steps,
                    }

            for name in visible:
                key = f"{step.name}.{name}"
                if key not in done:
                    return {
                        "user_id": user_id,
                        "step_name": step.name,
                        "task_name": name,
                        "done": False,
                        "current_step_number": progress.current_step_number,
                        "total_steps": progress.total_steps,
                    }
        # All visible tasks are done but status hasn't flipped yet — shouldn't normally happen.
        return {
            "user_id": user_id,
            "step_name": None,
            "task_name": None,
            "done": True,
            "current_step_number": progress.current_step_number,
            "total_steps": progress.total_steps,
        }

    def get_status(self, user_id: str) -> dict[str, str]:
        """Return ``accepted``, ``rejected``, or ``in_progress``."""
        user = self._get_user(user_id)
        return {"user_id": user_id, "status": user["status"]}

    def complete_task(
        self, user_id: str, step_name: str, task_name: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Apply webhook-style completion: validate, evaluate pass/reject rules, update user state when applicable.

        Raises ``HTTPException`` for unknown user (404), bad step/task or hidden task (400),
        missing required payload fields (400), or completion when already ``accepted``/``rejected`` (400).

        On success or idempotent replay of an already-passed task, returns a dict including
        ``passed`` as a boolean (whether this attempt satisfied ``pass_rule`` and was or was
        already counted as completed).
        """
        user = self._get_user(user_id)
        # Accepted and rejected are terminal states — no further task completions allowed.
        if user["status"] in ("rejected", "accepted"):
            raise HTTPException(status_code=400, detail=f"Cannot complete tasks for a user with status '{user['status']}'")

        # --- Validate step and task identity ---
        step = self._steps.get(step_name)
        if step is None:
            raise HTTPException(status_code=400, detail="Invalid step_name")
        task_def = self._task_def(step_name, task_name)
        if task_def is None:
            raise HTTPException(status_code=400, detail="Invalid task_name for the given step_name")
        # Blocks access to tasks hidden by a visibility_rule (e.g. second chance before IQ attempt).
        if task_name not in self._visible_task_names(step, user):
            raise HTTPException(status_code=400, detail="Task is not currently available")

        done = set(user["completed_tasks"])

        # --- Enforce sequential step order ---
        # All steps before this one must be fully complete before proceeding.
        step_index = next(i for i, s in enumerate(self.flow) if s.name == step_name)
        for prev_step in self.flow[:step_index]:
            if not self._step_fully_done(prev_step, user, done):
                raise HTTPException(
                    status_code=400,
                    detail=f"Step '{prev_step.name}' must be completed before '{step_name}'",
                )

        # --- Enforce sequential task order within the step ---
        # All visible tasks listed before this one must be complete first.
        visible = self._visible_task_names(step, user)
        task_index = visible.index(task_name)
        for prev_task_name in visible[:task_index]:
            if (
                step.name == STEP_IQ_TEST
                and prev_task_name == TASK_TAKE_IQ_TEST
                and task_name == TASK_SECOND_CHANCE_TEST
            ):
                continue
            if f"{step_name}.{prev_task_name}" not in done:
                raise HTTPException(
                    status_code=400,
                    detail=f"Task '{prev_task_name}' must be completed before '{task_name}'",
                )

        # --- Idempotency: duplicate webhook events are safe to replay ---
        key = f"{step_name}.{task_name}"
        if key in done:
            return {
                "message": f"Task {key} already completed",
                "user_id": user_id,
                "step_name": step_name,
                "task_name": task_name,
                "task_key": key,
                "passed": True,
            }

        # A task that was already attempted (but failed) cannot be retried.
        # Only valid payloads consume the attempt — see recording point below.
        if key in user.get("attempted_tasks", set()):
            raise HTTPException(status_code=400, detail="Task has already been attempted")

        # --- Validate required payload fields ---
        required = task_def.required_payload_fields
        if required:
            for fname in required:
                if fname not in payload or payload[fname] is None:
                    raise HTTPException(status_code=400, detail=f"Missing required field: {fname}")

        # Run task-specific payload validation (e.g. score must be a number, decision must be a known value).
        if task_def.payload_validator:
            try:
                task_def.payload_validator(payload)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        # Let the task update user context (e.g. storing a score) before pass_rule runs,
        # so visibility rules on the same request see the updated values immediately.
        if task_def.context_extractor:
            user["context"].update(task_def.context_extractor(payload))

        # Record the attempt now — payload is valid, so this submission counts regardless of outcome.
        user["attempted_tasks"].add(key)

        # --- Evaluate pass/reject rules ---
        passed = bool(task_def.pass_rule(payload))
        if not passed:
            policy = task_def.reject_on_fail
            # Static True means always reject on fail (e.g. second chance test); a callable makes rejection conditional on the payload.
            if policy is True:
                user["status"] = "rejected"
            elif callable(policy) and policy(payload):
                user["status"] = "rejected"
            msg = "Applicant rejected" if user["status"] == "rejected" else "Task did not pass"
            return {
                "message": msg,
                "user_id": user_id,
                "step_name": step_name,
                "task_name": task_name,
                "task_key": key,
                "passed": False,
            }

        # Task passed — record it and check if the whole flow is now complete.
        user["completed_tasks"].append(key)
        self._recompute_user_status(user)

        return {
            "message": f"Task {key} marked as completed",
            "user_id": user_id,
            "step_name": step_name,
            "task_name": task_name,
            "task_key": key,
            "passed": True,
        }
