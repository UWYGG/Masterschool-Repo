"""Admissions flow configuration: step/task definitions, display labels, and rule callables.

Holds only data and rule definitions for the funnel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


# Step name constants — used as stable string identifiers throughout the codebase.
STEP_PERSONAL_DETAILS_FORM = "personal_details_form"
STEP_IQ_TEST = "iq_test"
STEP_INTERVIEW = "interview"
STEP_SIGN_CONTRACT = "sign_contract"
STEP_PAYMENT = "payment"
STEP_JOIN_SLACK = "join_slack"


# Task name constants — each maps to a specific task within its parent step.
TASK_SUBMIT = "submit"
TASK_TAKE_IQ_TEST = "take_iq_test"
TASK_SECOND_CHANCE_TEST = "second_chance_test"
TASK_SCHEDULE_INTERVIEW = "schedule_interview"
TASK_PERFORM_INTERVIEW = "perform_interview"
TASK_UPLOAD_IDENTIFICATION_DOCUMENT = "upload_identification_document"
TASK_SIGN_CONTRACT = "sign_contract"
TASK_PAYMENT = "payment"
TASK_JOIN_SLACK = "join_slack"


# Type aliases for the callable fields on TaskDefinition.
TaskRule = Callable[[dict[str, Any]], bool]       # receives task payload, returns pass/fail
VisibilityRule = Callable[[dict[str, Any]], bool]  # receives user context, returns visible/hidden
RejectRule = Callable[[dict[str, Any]], bool]      # receives task payload, returns should-reject
RejectOnFailPolicy = bool | RejectRule             # True = always reject; callable = conditional reject
# Raises ValueError for invalid field values; returns None when payload is valid.
PayloadValidator = Callable[[dict[str, Any]], None]
# Receives (step_name, visible_task_names, completed_task_keys) and returns whether the step is done.
StepCompletionRule = Callable[[str, list[str], set[str]], bool]
# Receives the task payload; returns a dict of {context_key: value} to merge into user["context"].
ContextExtractor = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class TaskDefinition:
    name: str
    label: str
    # Default pass rule: task passes once a completion payload is received.
    pass_rule: TaskRule = lambda _task_payload: True
    # Can be static bool or payload-based reject rule.
    reject_on_fail: RejectOnFailPolicy = False
    # Default is None, meaning no mandatory payload fields for this task.
    required_payload_fields: Optional[list[str]] = None
    # Can be a visibility function OR None (no condition). Default is None.
    visibility_rule: Optional[VisibilityRule] = None
    # Optional strict validation of payload values — runs before pass_rule and raises 400 on bad input.
    payload_validator: Optional[PayloadValidator] = None
    # Optional extraction of values from the payload and merges them into user["context"].
    # Runs after payload_validator and before pass_rule so visibility rules see the updated context
    # on the same request (e.g. IQ score stored so second-chance visibility rule evaluates immediately).
    context_extractor: Optional[ContextExtractor] = None


def default_step_completion_rule(step_name: str, visible: list[str], done: set[str]) -> bool:
    # A step with no visible tasks is trivially complete.
    if not visible:
        return True
    # All visible tasks must be in completed_tasks.
    return all(f"{step_name}.{n}" in done for n in visible)


def iq_step_completion_rule(step_name: str, visible: list[str], done: set[str]) -> bool:
    # Passing either the main IQ test or the second chance test satisfies the step.
    k1 = f"{step_name}.{TASK_TAKE_IQ_TEST}"
    k2 = f"{step_name}.{TASK_SECOND_CHANCE_TEST}"
    return k1 in done or k2 in done


@dataclass(frozen=True)
class StepDefinition:
    name: str
    label: str
    # Task order matters: completion is enforced sequentially within a step.
    tasks: tuple[TaskDefinition, ...] = field(default_factory=tuple)
    # Determines when all tasks in this step are considered satisfied.
    # Defaults to "all visible tasks must be completed".
    completion_rule: StepCompletionRule = field(default=default_step_completion_rule)


def iq_score_validator(task_payload: dict[str, Any]) -> None:
    # Reject non-numeric scores before any pass/fail logic runs.
    score = task_payload.get("score")
    if not isinstance(score, (int, float)):
        raise ValueError(f"Invalid score value '{score}': must be a number")


def iq_pass_rule(task_payload: dict[str, Any]) -> bool:
    # Strictly greater than 75 — a score of exactly 75 does not pass.
    score = task_payload.get("score")
    return isinstance(score, (int, float)) and score > 75


def iq_hard_fail_rule(task_payload: dict[str, Any]) -> bool:
    # Hard fail for first IQ attempt — score below 60 means no second chance.
    score = task_payload.get("score")
    return isinstance(score, (int, float)) and score < 60


def second_chance_visibility_rule(user_context: dict[str, Any]) -> bool:
    # Second chance is shown only to users who scored in the medium range (60–75 inclusive).
    iq_score = user_context.get("iq_test_score")
    return isinstance(iq_score, (int, float)) and 60 <= iq_score <= 75


def iq_context_extractor(task_payload: dict[str, Any]) -> dict[str, Any]:
    # Persists the numeric score into user context so visibility rules can read it immediately.
    score = task_payload.get("score")
    return {"iq_test_score": score} if isinstance(score, (int, float)) else {}


def interview_decision_validator(task_payload: dict[str, Any]) -> None:
    # Only "passed_interview" and "failed_interview" are valid — anything else is a bad request.
    decision = task_payload.get("decision")
    valid = {"passed_interview", "failed_interview"}
    if decision not in valid:
        raise ValueError(f"Invalid decision value '{decision}': must be one of {sorted(valid)}")


def interview_pass_rule(task_payload: dict[str, Any]) -> bool:
    # Only the exact string "passed_interview" counts as a pass.
    return task_payload.get("decision") == "passed_interview"


def interview_reject_rule(task_payload: dict[str, Any]) -> bool:
    # Only "failed_interview" triggers rejection — other values are caught by the validator first.
    return task_payload.get("decision") == "failed_interview"


DEFINITION_PERSONAL_DETAILS_FORM = StepDefinition(
    name=STEP_PERSONAL_DETAILS_FORM,
    label="Personal Details Form",
    tasks=(
        TaskDefinition(
            name=TASK_SUBMIT,
            label="Submit Personal Details",
            # email is intentionally omitted — it is already known from the user record via user_id.
            required_payload_fields=["first_name", "last_name", "timestamp"],
        ),
    ),
)

DEFINITION_IQ_TEST = StepDefinition(
    name=STEP_IQ_TEST,
    label="IQ Test",
    completion_rule=iq_step_completion_rule,
    tasks=(
        TaskDefinition(
            name=TASK_TAKE_IQ_TEST,
            label="Take IQ Test",
            pass_rule=iq_pass_rule,
            # Score < 60 rejects immediately; score 60-75 unlocks second chance task.
            reject_on_fail=iq_hard_fail_rule,
            required_payload_fields=["test_id", "score", "timestamp"],
            payload_validator=iq_score_validator,
            # Stores the score in user context so second_chance visibility rule evaluates immediately.
            context_extractor=iq_context_extractor,
        ),
        TaskDefinition(
            name=TASK_SECOND_CHANCE_TEST,
            label="Second Chance IQ Test",
            pass_rule=iq_pass_rule,
            # Failing the second chance is always a hard rejection — no further retries.
            reject_on_fail=True,
            required_payload_fields=["test_id", "score", "timestamp"],
            # Only visible when the user's IQ score is in the medium range (60–75).
            visibility_rule=second_chance_visibility_rule,
            payload_validator=iq_score_validator,
            context_extractor=iq_context_extractor,
        ),
    ),
)

DEFINITION_INTERVIEW = StepDefinition(
    name=STEP_INTERVIEW,
    label="Interview",
    tasks=(
        TaskDefinition(
            name=TASK_SCHEDULE_INTERVIEW,
            label="Schedule Interview",
            required_payload_fields=["interview_date"],
        ),
        TaskDefinition(
            name=TASK_PERFORM_INTERVIEW,
            label="Perform Interview",
            pass_rule=interview_pass_rule,
            # Only "failed_interview" rejects — other unexpected values are caught by the validator.
            reject_on_fail=interview_reject_rule,
            required_payload_fields=["interview_date", "interviewer_id", "decision"],
            # Raises 400 for any decision value other than "passed_interview" or "failed_interview".
            payload_validator=interview_decision_validator,
        ),
    ),
)

DEFINITION_SIGN_CONTRACT = StepDefinition(
    name=STEP_SIGN_CONTRACT,
    label="Sign Contract",
    tasks=(
        TaskDefinition(
            name=TASK_UPLOAD_IDENTIFICATION_DOCUMENT,
            label="Upload Identification Document",
            required_payload_fields=["passport_number", "timestamp"],
        ),
        TaskDefinition(
            name=TASK_SIGN_CONTRACT,
            label="Sign the Contract",
            required_payload_fields=["timestamp"],
        ),
    ),
)

DEFINITION_PAYMENT = StepDefinition(
    name=STEP_PAYMENT,
    label="Payment",
    tasks=(
        TaskDefinition(
            name=TASK_PAYMENT,
            label="Complete Payment",
            required_payload_fields=["payment_id", "timestamp"],
        ),
    ),
)

DEFINITION_JOIN_SLACK = StepDefinition(
    name=STEP_JOIN_SLACK,
    label="Join Slack",
    tasks=(
        TaskDefinition(
            name=TASK_JOIN_SLACK,
            label="Join Slack Workspace",
            # email is intentionally omitted — it is already known from the user record via user_id.
            required_payload_fields=["timestamp"],
        ),
    ),
)


def build_flow() -> list[StepDefinition]:
    # The order here defines the enforced progression sequence for all users.
    return [
        DEFINITION_PERSONAL_DETAILS_FORM,
        DEFINITION_IQ_TEST,
        DEFINITION_INTERVIEW,
        DEFINITION_SIGN_CONTRACT,
        DEFINITION_PAYMENT,
        DEFINITION_JOIN_SLACK,
    ]
