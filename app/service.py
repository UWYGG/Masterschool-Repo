"""Admissions funnel business logic for the Masterschool exercise API.

The flow (steps, tasks, pass/fail rules, and which tasks are visible) is defined
in :mod:`app.admissions_config` and loaded via :func:`app.admissions_config.build_flow`.
This module applies that configuration to per-user state.

**User record** (in-memory ``users`` dict)::

    {
        "email": str,
        "status": "in_progress" | "accepted" | "rejected",
        "completed_tasks": list[str],  # keys "step_name.task_name" that passed
        "context": dict,               # e.g. iq_test_score for visibility rules
    }

**Task keys** identify a task uniquely as ``"{step_name}.{task_name}"`` (e.g.
``iq_test.take_iq_test``). Only tasks that satisfy their ``pass_rule`` are
appended to ``completed_tasks``.

**IQ step:** When both the first IQ task and the second-chance task are visible,
completing either one with a passing score satisfies that step (OR semantics).
"""

from __future__ import annotations

from typing import Any
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


class AdmissionsService:
    """Holds the configured flow and an in-memory map of users.

    Public methods correspond to the REST API: create user, read flow/progress,
    read status, and complete tasks with webhook-style payloads.
    """

    def __init__(self) -> None:
        self.flow: list[StepDefinition] = build_flow()
        self._steps: dict[str, StepDefinition] = {s.name: s for s in self.flow}
        self.users: dict[str, dict[str, Any]] = {}

    def create_user(self, email: str) -> str:
        """Create a user in ``in_progress`` with empty progress; return a new unique id."""
        user_id = str(uuid4())
        self.users[user_id] = {
            "email": email,
            "status": "in_progress",
            "completed_tasks": [],
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
        """Whether every visible task in ``step`` is satisfied for this user.

        The IQ step is special: if both the initial and second-chance tasks are
        visible, only one of them needs to appear in ``done`` (passing score).
        """
        visible = self._visible_task_names(step, user)
        if not visible:
            return True
        if (
            step.name == STEP_IQ_TEST
            and TASK_TAKE_IQ_TEST in visible
            and TASK_SECOND_CHANCE_TEST in visible
        ):
            k1 = f"{step.name}.{TASK_TAKE_IQ_TEST}"
            k2 = f"{step.name}.{TASK_SECOND_CHANCE_TEST}"
            return k1 in done or k2 in done
        return all(f"{step.name}.{n}" in done for n in visible)

    def _all_flow_requirements_met(self, user: dict[str, Any], done: set[str]) -> bool:
        """True when every step in the flow is fully done for this user."""
        return all(self._step_fully_done(s, user, done) for s in self.flow)

    def _recompute_user_status(self, user: dict[str, Any]) -> None:
        """Set ``accepted`` if all steps are done; otherwise ``in_progress``. No-op if ``rejected``."""
        if user["status"] == "rejected":
            return
        done = set(user["completed_tasks"])
        user["status"] = "accepted" if self._all_flow_requirements_met(user, done) else "in_progress"

    def _step_progress(self, user: dict[str, Any]) -> tuple[int | None, int]:
        """1-based index of the first step that still has incomplete work, and total step count.

        Returns ``(None, total)`` when the user is not ``in_progress`` or there is
        no remaining work under the current rules.
        """
        total = len(self.flow)
        if user["status"] != "in_progress":
            return None, total
        done = set(user["completed_tasks"])
        for i, step in enumerate(self.flow):
            if self._step_fully_done(step, user, done):
                continue
            for name in self._visible_task_names(step, user):
                key = f"{step.name}.{name}"
                if key not in done:
                    return i + 1, total
        return None, total

    def get_flow(self, user_id: str) -> dict[str, Any]:
        """Return all steps with completion flags plus ``current_step_number`` and ``total_steps``."""
        user = self._get_user(user_id)
        done = set(user["completed_tasks"])
        steps_out: list[dict[str, Any]] = []
        for step in self.flow:
            step_done = self._step_fully_done(step, user, done)
            steps_out.append({"name": step.name, "completed": step_done})
        current_n, total = self._step_progress(user)
        return {
            "user_id": user_id,
            "total_steps": total,
            "current_step_number": current_n,
            "steps": steps_out,
        }

    def get_current(self, user_id: str) -> dict[str, Any]:
        """Return the next incomplete visible task, or ``done: True`` if there is none.

        Includes ``current_step_number`` and ``total_steps`` for UI copy such as
        "step x of y" when the user is still in progress.
        """
        user = self._get_user(user_id)
        current_n, total = self._step_progress(user)
        if user["status"] != "in_progress":
            return {
                "user_id": user_id,
                "step_name": None,
                "task_name": None,
                "done": True,
                "current_step_number": current_n,
                "total_steps": total,
            }
        done = set(user["completed_tasks"])
        for step in self.flow:
            if self._step_fully_done(step, user, done):
                continue
            visible = self._visible_task_names(step, user)

            # Special behavior for the IQ step:
            # - when both IQ tasks are visible (medium score => second chance available),
            #   we want the "current task" to point to the second-chance task, not keep
            #   returning the first IQ attempt that may have failed its pass condition.
            # - step completion still depends on passing, so we only change "current".
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
                        "current_step_number": current_n,
                        "total_steps": total,
                    }

            for name in visible:
                key = f"{step.name}.{name}"
                if key not in done:
                    return {
                        "user_id": user_id,
                        "step_name": step.name,
                        "task_name": name,
                        "done": False,
                        "current_step_number": current_n,
                        "total_steps": total,
                    }
        return {
            "user_id": user_id,
            "step_name": None,
            "task_name": None,
            "done": True,
            "current_step_number": current_n,
            "total_steps": total,
        }

    def get_status(self, user_id: str) -> dict[str, str]:
        """Return ``accepted``, ``rejected``, or ``in_progress``."""
        user = self._get_user(user_id)
        return {"user_id": user_id, "status": user["status"]}

    def _touch_iq_context(self, user: dict[str, Any], step_name: str, task_name: str, payload: dict[str, Any]) -> None:
        """Store ``payload["score"]`` on the user when completing an IQ task so visibility rules can run."""
        if step_name != "iq_test" or task_name not in ("take_iq_test", "second_chance_test"):
            return
        score = payload.get("score")
        if isinstance(score, (int, float)):
            user.setdefault("context", {})["iq_test_score"] = score

    def complete_task(
        self, user_id: str, step_name: str, task_name: str, payload: dict[str, Any]
    ) -> dict[str, str]:
        """Apply webhook-style completion: validate, evaluate pass/reject rules, update user state when applicable.

        Raises ``HTTPException`` for unknown user (404), bad step/task or hidden task (400),
        missing required payload fields (400), or completion when already ``accepted``/``rejected`` (400).

        On success or idempotent replay of an already-passed task, returns a dict including
        ``passed`` as the strings ``"true"`` or ``"false"`` (whether this attempt satisfied
        ``pass_rule`` and was or was already counted as completed).
        """
        user = self._get_user(user_id)
        if user["status"] in ("rejected", "accepted"):
            raise HTTPException(status_code=400, detail="Cannot complete tasks in current status")

        step = self._steps.get(step_name)
        if step is None:
            raise HTTPException(status_code=400, detail="Invalid step_name")
        task_def = self._task_def(step_name, task_name)
        if task_def is None:
            raise HTTPException(status_code=400, detail="Invalid task_name for the given step_name")
        if task_name not in self._visible_task_names(step, user):
            raise HTTPException(status_code=400, detail="Task is not currently available")

        key = f"{step_name}.{task_name}"
        if key in user["completed_tasks"]:
            return {
                "message": f"Task {key} already completed",
                "user_id": user_id,
                "step_name": step_name,
                "task_name": task_name,
                "task_key": key,
                "passed": "true",
            }

        required = task_def.required_payload_fields
        if required:
            for fname in required:
                if fname not in payload or payload[fname] is None:
                    raise HTTPException(status_code=400, detail=f"Missing required field: {fname}")

        self._touch_iq_context(user, step_name, task_name, payload)

        passed = bool(task_def.pass_rule(payload))
        if not passed:
            policy = task_def.reject_on_fail
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
                "passed": "false",
            }

        user["completed_tasks"].append(key)
        self._recompute_user_status(user)

        return {
            "message": f"Task {key} marked as completed",
            "user_id": user_id,
            "step_name": step_name,
            "task_name": task_name,
            "task_key": key,
            "passed": "true",
        }
