"""Admissions flow configuration: step/task definitions, display labels, and rule callables.

Holds only data and rule definitions for the funnel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional



STEP_PERSONAL_DETAILS_FORM = "personal_details_form"
STEP_IQ_TEST = "iq_test"
STEP_INTERVIEW = "interview"
STEP_SIGN_CONTRACT = "sign_contract"
STEP_PAYMENT = "payment"
STEP_JOIN_SLACK = "join_slack"


TASK_SUBMIT = "submit"
TASK_TAKE_IQ_TEST = "take_iq_test"
TASK_SECOND_CHANCE_TEST = "second_chance_test"
TASK_SCHEDULE_INTERVIEW = "schedule_interview"
TASK_PERFORM_INTERVIEW = "perform_interview"
TASK_UPLOAD_IDENTIFICATION_DOCUMENT = "upload_identification_document"
TASK_SIGN_CONTRACT = "sign_contract"
TASK_PAYMENT = "payment"
TASK_JOIN_SLACK = "join_slack"


TaskRule = Callable[[dict[str, Any]], bool]
VisibilityRule = Callable[[dict[str, Any]], bool]
RejectRule = Callable[[dict[str, Any]], bool]
RejectOnFailPolicy = bool | RejectRule


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


@dataclass(frozen=True)
class StepDefinition:
    name: str
    label: str
    tasks: list[TaskDefinition] = field(default_factory=list)


def iq_pass_rule(task_payload: dict[str, Any]) -> bool:
    score = task_payload.get("score")
    return isinstance(score, (int, float)) and score > 75


def iq_hard_fail_rule(task_payload: dict[str, Any]) -> bool:
    # Hard fail for first IQ attempt (no second chance below threshold).
    score = task_payload.get("score")
    return isinstance(score, (int, float)) and score < 60


def interview_pass_rule(task_payload: dict[str, Any]) -> bool:
    return task_payload.get("decision") == "passed_interview"


def second_chance_visibility_rule(user_context: dict[str, Any]) -> bool:
    iq_score = user_context.get("iq_test_score")
    return isinstance(iq_score, (int, float)) and 60 <= iq_score <= 75


DEFINITION_PERSONAL_DETAILS_FORM = StepDefinition(
    name=STEP_PERSONAL_DETAILS_FORM,
    label="Personal Details Form",
    tasks=[
        TaskDefinition(
            name=TASK_SUBMIT,
            label="Submit Personal Details",
        ),
    ],
)

DEFINITION_IQ_TEST = StepDefinition(
    name=STEP_IQ_TEST,
    label="IQ Test",
    tasks=[
        TaskDefinition(
            name=TASK_TAKE_IQ_TEST,
            label="Take IQ Test",
            pass_rule=iq_pass_rule,
            reject_on_fail=iq_hard_fail_rule,
            required_payload_fields=["score"],
        ),
        TaskDefinition(
            name=TASK_SECOND_CHANCE_TEST,
            label="Second Chance IQ Test",
            pass_rule=iq_pass_rule,
            reject_on_fail=True,
            required_payload_fields=["score"],
            visibility_rule=second_chance_visibility_rule,
        ),
    ],
)

DEFINITION_INTERVIEW = StepDefinition(
    name=STEP_INTERVIEW,
    label="Interview",
    tasks=[
        TaskDefinition(
            name=TASK_SCHEDULE_INTERVIEW,
            label="Schedule Interview",
        ),
        TaskDefinition(
            name=TASK_PERFORM_INTERVIEW,
            label="Perform Interview",
            pass_rule=interview_pass_rule,
            reject_on_fail=True,
            required_payload_fields=["decision"],
        ),
    ],
)

DEFINITION_SIGN_CONTRACT = StepDefinition(
    name=STEP_SIGN_CONTRACT,
    label="Sign Contract",
    tasks=[
        TaskDefinition(
            name=TASK_UPLOAD_IDENTIFICATION_DOCUMENT,
            label="Upload Identification Document",
        ),
        TaskDefinition(
            name=TASK_SIGN_CONTRACT,
            label="Sign the Contract",
        ),
    ],
)

DEFINITION_PAYMENT = StepDefinition(
    name=STEP_PAYMENT,
    label="Payment",
    tasks=[
        TaskDefinition(
            name=TASK_PAYMENT,
            label="Complete Payment",
        ),
    ],
)

DEFINITION_JOIN_SLACK = StepDefinition(
    name=STEP_JOIN_SLACK,
    label="Join Slack",
    tasks=[
        TaskDefinition(
            name=TASK_JOIN_SLACK,
            label="Join Slack Workspace",
        ),
    ],
)


def build_flow() -> list[StepDefinition]:
    return [
        DEFINITION_PERSONAL_DETAILS_FORM,
        DEFINITION_IQ_TEST,
        DEFINITION_INTERVIEW,
        DEFINITION_SIGN_CONTRACT,
        DEFINITION_PAYMENT,
        DEFINITION_JOIN_SLACK,
    ]





# TODO: Consider adding allow_retry for tasks that may be retried after failure.
# TODO: Consider adding is_optional for tasks that should not block acceptance.
# TODO: Consider adding flow_version for tracking and migrating PM-driven flow changes.
# TODO: Consider adding status_transition_policy for configurable rejection/acceptance behavior.