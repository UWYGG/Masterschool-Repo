"""Tests demonstrating that PM-driven flow changes require only config modifications.

Each test defines a modified flow using the same primitives from admissions_config
(StepDefinition, TaskDefinition, pass_rule, etc.) and passes it directly into a
fresh AdmissionsService. No changes to service.py or main.py are needed.

This is the architectural guarantee: the service is a flow-agnostic engine.
"""

import pytest
from fastapi import HTTPException

from app.admissions_config import (
    DEFINITION_INTERVIEW,
    DEFINITION_JOIN_SLACK,
    DEFINITION_PAYMENT,
    DEFINITION_PERSONAL_DETAILS_FORM,
    DEFINITION_SIGN_CONTRACT,
    DEFINITION_IQ_TEST,
    TASK_TAKE_IQ_TEST,
    TASK_SECOND_CHANCE_TEST,
    StepDefinition,
    TaskDefinition,
    default_step_completion_rule,
    iq_context_extractor,
    iq_pass_rule,
    iq_score_validator,
    iq_step_completion_rule,
    second_chance_visibility_rule,
)
from app.service import AdmissionsService


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_service(flow):
    """Instantiate a fresh service with a custom flow — no server needed."""
    return AdmissionsService(flow=flow)


def complete(svc, user_id, step, task, payload=None):
    """Call complete_task directly; returns the result dict or raises HTTPException."""
    return svc.complete_task(user_id, step, task, payload or {})


def complete_ok(svc, user_id, step, task, payload=None):
    result = complete(svc, user_id, step, task, payload)
    assert result["passed"] is True, f"Expected pass, got: {result}"
    return result


# Shared minimal payloads reused across tests.
P_PERSONAL       = {"first_name": "Jane", "last_name": "Doe", "timestamp": "2026-01-01T00:00:00"}
P_IQ_PASS        = {"test_id": "t1", "score": 80, "timestamp": "2026-01-01T00:00:00"}
P_IQ_MEDIUM      = {"test_id": "t1", "score": 65, "timestamp": "2026-01-01T00:00:00"}
P_SCHEDULE       = {"interview_date": "2026-02-01"}
P_INTERVIEW_PASS = {"interview_date": "2026-02-01", "interviewer_id": "i1", "decision": "passed_interview"}
P_UPLOAD_ID      = {"passport_number": "AB123456", "timestamp": "2026-01-01T00:00:00"}
P_SIGN           = {"timestamp": "2026-01-01T00:00:00"}
P_PAYMENT        = {"payment_id": "pay_1", "timestamp": "2026-01-01T00:00:00"}
P_SLACK          = {"timestamp": "2026-01-01T00:00:00"}


# ── PM Change 1: Remove the IQ test step entirely ────────────────────────────
#
# PM request: "The IQ test is causing a 40% drop-off. Let's remove it and
# trust the interview process instead."

def test_pm_removes_iq_test_and_users_are_accepted_without_it():
    flow = [
        DEFINITION_PERSONAL_DETAILS_FORM,
        # IQ test intentionally excluded
        DEFINITION_INTERVIEW,
        DEFINITION_SIGN_CONTRACT,
        DEFINITION_PAYMENT,
        DEFINITION_JOIN_SLACK,
    ]
    svc = make_service(flow)
    uid = svc.create_user("noiq@example.com")

    complete_ok(svc, uid, "personal_details_form", "submit", P_PERSONAL)
    complete_ok(svc, uid, "interview", "schedule_interview", P_SCHEDULE)
    complete_ok(svc, uid, "interview", "perform_interview", P_INTERVIEW_PASS)
    complete_ok(svc, uid, "sign_contract", "upload_identification_document", P_UPLOAD_ID)
    complete_ok(svc, uid, "sign_contract", "sign_contract", P_SIGN)
    complete_ok(svc, uid, "payment", "payment", P_PAYMENT)
    complete_ok(svc, uid, "join_slack", "join_slack", P_SLACK)

    assert svc.get_status(uid)["status"] == "accepted"
    assert svc.get_flow(uid)["total_steps"] == 5  # one fewer step


def test_pm_removes_iq_test_and_iq_step_is_blocked():
    flow = [
        DEFINITION_PERSONAL_DETAILS_FORM,
        DEFINITION_INTERVIEW,  # no IQ test
        DEFINITION_SIGN_CONTRACT,
        DEFINITION_PAYMENT,
        DEFINITION_JOIN_SLACK,
    ]
    svc = make_service(flow)
    uid = svc.create_user("noiq2@example.com")

    complete_ok(svc, uid, "personal_details_form", "submit", P_PERSONAL)

    # Attempting IQ test on a flow that doesn't have it should raise 400.
    with pytest.raises(HTTPException) as exc:
        complete(svc, uid, "iq_test", "take_iq_test", P_IQ_PASS)
    assert exc.value.status_code == 400


# ── PM Change 2: Reorder steps — interview before IQ test ────────────────────
#
# PM request: "Let's filter on personality first — move the interview
# before the IQ test so we meet people before testing them."

def test_pm_reorders_steps_new_order_is_enforced():
    flow = [
        DEFINITION_PERSONAL_DETAILS_FORM,
        DEFINITION_INTERVIEW,   # interview now comes BEFORE IQ test
        DEFINITION_IQ_TEST,
        DEFINITION_SIGN_CONTRACT,
        DEFINITION_PAYMENT,
        DEFINITION_JOIN_SLACK,
    ]
    svc = make_service(flow)
    uid = svc.create_user("reorder@example.com")

    complete_ok(svc, uid, "personal_details_form", "submit", P_PERSONAL)

    # Attempting IQ test before the interview now blocked by sequential enforcement.
    with pytest.raises(HTTPException) as exc:
        complete(svc, uid, "iq_test", "take_iq_test", P_IQ_PASS)
    assert exc.value.status_code == 400

    # Completing in the new order works fine.
    complete_ok(svc, uid, "interview", "schedule_interview", P_SCHEDULE)
    complete_ok(svc, uid, "interview", "perform_interview", P_INTERVIEW_PASS)
    complete_ok(svc, uid, "iq_test", "take_iq_test", P_IQ_PASS)

    assert svc.get_status(uid)["status"] == "in_progress"  # still more steps to go


# ── PM Change 3: Lower the IQ pass threshold ─────────────────────────────────
#
# PM request: "After reviewing the data, a score of 60 is good enough.
# Update the IQ pass threshold from 75 to 60."

def _lowered_iq_pass_rule(payload):
    score = payload.get("score")
    return isinstance(score, (int, float)) and score >= 60  # lowered from > 75


DEFINITION_IQ_TEST_LOWERED = StepDefinition(
    name="iq_test",
    label="IQ Test (updated threshold)",
    completion_rule=iq_step_completion_rule,
    tasks=(
        TaskDefinition(
            name=TASK_TAKE_IQ_TEST,
            label="Take IQ Test",
            pass_rule=_lowered_iq_pass_rule,  # only change from the original
            required_payload_fields=["test_id", "score", "timestamp"],
        ),
    ),
)


def test_pm_lowers_iq_threshold_score_65_now_passes_directly():
    flow = [
        DEFINITION_PERSONAL_DETAILS_FORM,
        DEFINITION_IQ_TEST_LOWERED,
        DEFINITION_INTERVIEW,
        DEFINITION_SIGN_CONTRACT,
        DEFINITION_PAYMENT,
        DEFINITION_JOIN_SLACK,
    ]
    svc = make_service(flow)
    uid = svc.create_user("lowthreshold@example.com")

    complete_ok(svc, uid, "personal_details_form", "submit", P_PERSONAL)

    # Score of 65 failed under the old threshold (> 75). Under the new one (>= 60) it passes.
    resp = complete_ok(svc, uid, "iq_test", "take_iq_test", P_IQ_MEDIUM)
    assert resp["passed"] is True

    # No second chance task needed — user moves straight to interview.
    current = svc.get_current(uid)
    assert current["step_name"] == "interview"


# ── PM Change 4: Capricious PM — name-based IQ exception ─────────────────────
#
# PM: "I will not tolerate anyone else named Ofeer in this company! Reject them immediately."
# Head of R&D: "Hold on — let them take the IQ test first. If they score above 90, they stay."
#
# Implementation: a single modified IQ step definition.
#   - first_name is added to the IQ payload so the rule can inspect it at evaluation time.
#   - Pass rule: Ofeer → must score > 90; everyone else → standard threshold > 75.
#   - Reject on fail: Ofeer → always reject immediately (no second chance reprieve);
#     everyone else → normal hard-fail rule (score < 60 → reject).
#   - The second-chance task remains in the step for everyone else who scores in the 60–75 range.

BANNED_NAME = "Ofeer"  


def _capricious_pm_iq_pass_rule(payload):
    score = payload.get("score", 0)
    if payload.get("first_name") == BANNED_NAME:
        return score > 90       # R&D exception: banned name needs 90+ to survive
    return score > 75           # standard threshold for everyone else


def _capricious_pm_iq_reject_rule(payload):
    if payload.get("first_name") == BANNED_NAME:
        return True             # any fail by the banned name is terminal — no second chance
    score = payload.get("score", 0)
    return score < 60           # normal hard-fail rule for everyone else


DEFINITION_IQ_CAPRICIOUS_PM = StepDefinition(
    name="iq_test",
    label="IQ Test (Capricious PM Policy)",
    completion_rule=iq_step_completion_rule,
    tasks=(
        TaskDefinition(
            name=TASK_TAKE_IQ_TEST,
            label="Take IQ Test",
            pass_rule=_capricious_pm_iq_pass_rule,
            reject_on_fail=_capricious_pm_iq_reject_rule,
            # first_name added so the rule knows who it is dealing with.
            required_payload_fields=["test_id", "score", "timestamp", "first_name"],
            payload_validator=iq_score_validator,
            context_extractor=iq_context_extractor,
        ),
        TaskDefinition(
            name=TASK_SECOND_CHANCE_TEST,
            label="Second Chance IQ Test",
            pass_rule=iq_pass_rule,     # everyone else who reaches this uses the standard threshold
            reject_on_fail=True,
            required_payload_fields=["test_id", "score", "timestamp"],
            visibility_rule=second_chance_visibility_rule,
            payload_validator=iq_score_validator,
        ),
    ),
)

FLOW_CAPRICIOUS_PM = [
    DEFINITION_PERSONAL_DETAILS_FORM,
    DEFINITION_IQ_CAPRICIOUS_PM,
    DEFINITION_INTERVIEW,
    DEFINITION_SIGN_CONTRACT,
    DEFINITION_PAYMENT,
    DEFINITION_JOIN_SLACK,
]

# IQ payloads that include first_name for the capricious-PM step.
P_IQ_BANNED_PASS  = {"test_id": "t1", "score": 95, "timestamp": "2026-01-01T00:00:00", "first_name": BANNED_NAME}
P_IQ_BANNED_FAIL  = {"test_id": "t1", "score": 80, "timestamp": "2026-01-01T00:00:00", "first_name": BANNED_NAME}
P_IQ_ALLOWED_PASS = {"test_id": "t1", "score": 80, "timestamp": "2026-01-01T00:00:00", "first_name": "Jane"}
P_IQ_ALLOWED_MED  = {"test_id": "t1", "score": 65, "timestamp": "2026-01-01T00:00:00", "first_name": "Jane"}


def test_capricious_pm_banned_name_scoring_above_90_is_accepted():
    """R&D exception: Ofeer who scores > 90 passes the IQ step and can complete the flow."""
    svc = make_service(FLOW_CAPRICIOUS_PM)
    uid = svc.create_user("ofeer.genius@example.com")

    complete_ok(svc, uid, "personal_details_form", "submit",            P_PERSONAL)
    complete_ok(svc, uid, "iq_test",               "take_iq_test",      P_IQ_BANNED_PASS)
    complete_ok(svc, uid, "interview",             "schedule_interview", P_SCHEDULE)
    complete_ok(svc, uid, "interview",             "perform_interview",  P_INTERVIEW_PASS)
    complete_ok(svc, uid, "sign_contract",         "upload_identification_document", P_UPLOAD_ID)
    complete_ok(svc, uid, "sign_contract",         "sign_contract",      P_SIGN)
    complete_ok(svc, uid, "payment",               "payment",            P_PAYMENT)
    complete_ok(svc, uid, "join_slack",            "join_slack",         P_SLACK)

    assert svc.get_status(uid)["status"] == "accepted"


def test_capricious_pm_banned_name_scoring_80_is_rejected_despite_clearing_normal_threshold():
    """PM policy: score of 80 normally passes (> 75), but Ofeer's bar is 90."""
    svc = make_service(FLOW_CAPRICIOUS_PM)
    uid = svc.create_user("ofeer.average@example.com")

    complete_ok(svc, uid, "personal_details_form", "submit", P_PERSONAL)
    result = complete(svc, uid, "iq_test", "take_iq_test", P_IQ_BANNED_FAIL)

    assert result["passed"] is False
    assert svc.get_status(uid)["status"] == "rejected"


def test_capricious_pm_banned_name_rejected_immediately_no_second_chance():
    """Ofeer who fails the IQ test is rejected on the spot — second-chance task is irrelevant."""
    svc = make_service(FLOW_CAPRICIOUS_PM)
    uid = svc.create_user("ofeer.retry@example.com")

    complete_ok(svc, uid, "personal_details_form", "submit", P_PERSONAL)
    complete(svc, uid, "iq_test", "take_iq_test", P_IQ_BANNED_FAIL)   # score 80, rejected

    # Attempting any further task on a rejected user is blocked.
    with pytest.raises(HTTPException) as exc:
        complete(svc, uid, "iq_test", "second_chance_test",
                 {"test_id": "t2", "score": 95, "timestamp": "2026-01-01T00:00:00"})
    assert exc.value.status_code == 400


def test_capricious_pm_allowed_name_uses_standard_threshold_and_gets_second_chance():
    """The capricious PM policy leaves everyone else completely unaffected."""
    svc = make_service(FLOW_CAPRICIOUS_PM)
    uid = svc.create_user("jane@example.com")

    complete_ok(svc, uid, "personal_details_form", "submit", P_PERSONAL)
    # Score 65: fails standard threshold (> 75) but is in the 60–75 second-chance band.
    result = complete(svc, uid, "iq_test", "take_iq_test", P_IQ_ALLOWED_MED)
    assert result["passed"] is False
    assert svc.get_status(uid)["status"] == "in_progress"   # not rejected — second chance awaits

    # Second chance now visible; passing it advances the flow.
    complete_ok(svc, uid, "iq_test", "second_chance_test",
                {"test_id": "t2", "score": 80, "timestamp": "2026-01-01T00:00:00"})
    assert svc.get_status(uid)["status"] == "in_progress"   # still more steps, but IQ is cleared
