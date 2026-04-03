"""Tests proving every PDF requirement is fulfilled and edge cases are handled.

Each test is named after what it proves. Running `pytest` with all green
is the experimental proof that the implementation is complete and correct.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app, get_service
from app.service import AdmissionsService


# ── Shared payloads ──────────────────────────────────────────────────────────
# Each matches the required_payload_fields defined in admissions_config.py.

P_PERSONAL       = {"first_name": "John", "last_name": "Doe", "timestamp": "2026-01-01T00:00:00"}
P_IQ_PASS        = {"test_id": "t1", "score": 80, "timestamp": "2026-01-01T00:00:00"}
P_IQ_MEDIUM      = {"test_id": "t1", "score": 65, "timestamp": "2026-01-01T00:00:00"}  # 60-75: second chance
P_IQ_FAIL        = {"test_id": "t1", "score": 50, "timestamp": "2026-01-01T00:00:00"}  # <60: hard fail
P_SCHEDULE       = {"interview_date": "2026-02-01"}
P_INTERVIEW_PASS = {"interview_date": "2026-02-01", "interviewer_id": "i1", "decision": "passed_interview"}
P_INTERVIEW_FAIL = {"interview_date": "2026-02-01", "interviewer_id": "i1", "decision": "failed_interview"}
P_UPLOAD_ID      = {"passport_number": "AB123456", "timestamp": "2026-01-01T00:00:00"}
P_SIGN           = {"timestamp": "2026-01-01T00:00:00"}
P_PAYMENT        = {"payment_id": "pay_1", "timestamp": "2026-01-01T00:00:00"}
P_SLACK          = {"timestamp": "2026-01-01T00:00:00"}


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    """Fresh TestClient backed by a new AdmissionsService for each test."""
    fresh_service = AdmissionsService()
    app.dependency_overrides[get_service] = lambda: fresh_service
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ── Helpers ──────────────────────────────────────────────────────────────────

def create_user(client, email="applicant@example.com"):
    resp = client.post("/users", json={"email": email})
    assert resp.status_code == 201
    return resp.json()["user_id"]


def complete(client, user_id, step, task, payload=None):
    """Send a task completion request; returns the raw response for assertion."""
    return client.put(
        f"/users/{user_id}/tasks/complete",
        json={"step_name": step, "task_name": task, "task_payload": payload or {}},
    )


def complete_ok(client, user_id, step, task, payload=None):
    """Complete a task and assert it succeeded with a passing outcome."""
    resp = complete(client, user_id, step, task, payload)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.json()}"
    assert resp.json()["passed"] is True, f"Expected passed=True, got: {resp.json()}"
    return resp


def full_happy_path(client, user_id):
    """Walk a user through all steps with passing inputs."""
    complete_ok(client, user_id, "personal_details_form", "submit",                       P_PERSONAL)
    complete_ok(client, user_id, "iq_test",                "take_iq_test",                P_IQ_PASS)
    complete_ok(client, user_id, "interview",              "schedule_interview",           P_SCHEDULE)
    complete_ok(client, user_id, "interview",              "perform_interview",            P_INTERVIEW_PASS)
    complete_ok(client, user_id, "sign_contract",          "upload_identification_document", P_UPLOAD_ID)
    complete_ok(client, user_id, "sign_contract",          "sign_contract",                P_SIGN)
    complete_ok(client, user_id, "payment",                "payment",                     P_PAYMENT)
    complete_ok(client, user_id, "join_slack",             "join_slack",                  P_SLACK)


# ── Requirement 1: POST /users ────────────────────────────────────────────────

def test_create_user_returns_unique_id(client):
    id1 = create_user(client, "a@example.com")
    id2 = create_user(client, "b@example.com")
    assert id1 and id2
    assert id1 != id2


def test_duplicate_email_returns_409(client):
    create_user(client, "same@example.com")
    resp = client.post("/users", json={"email": "same@example.com"})
    assert resp.status_code == 409


def test_invalid_email_format_returns_422(client):
    resp = client.post("/users", json={"email": "not-an-email"})
    assert resp.status_code == 422


# ── Requirement 2: GET /users/{id}/flow ──────────────────────────────────────

def test_get_flow_returns_all_six_steps_and_current_position(client):
    user_id = create_user(client)
    resp = client.get(f"/users/{user_id}/flow")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_steps"] == 6
    assert data["current_step_number"] == 1
    assert len(data["steps"]) == 6


def test_get_flow_marks_completed_steps(client):
    user_id = create_user(client)
    complete_ok(client, user_id, "personal_details_form", "submit", P_PERSONAL)
    steps = client.get(f"/users/{user_id}/flow").json()["steps"]
    assert steps[0]["completed"] is True   # personal_details_form done
    assert steps[1]["completed"] is False  # iq_test not yet done
    # Each step entry must include a human-readable label for frontend display.
    assert all("label" in s for s in steps)
    assert steps[0]["label"] == "Personal Details Form"


def test_get_flow_includes_task_level_completion(client):
    user_id = create_user(client)
    complete_ok(client, user_id, "personal_details_form", "submit", P_PERSONAL)
    steps = client.get(f"/users/{user_id}/flow").json()["steps"]
    personal_tasks = steps[0]["tasks"]
    assert len(personal_tasks) == 1
    assert personal_tasks[0]["name"] == "submit"
    assert personal_tasks[0]["completed"] is True
    # First visible IQ task is not yet done
    iq_tasks = steps[1]["tasks"]
    assert iq_tasks[0]["name"] == "take_iq_test"
    assert iq_tasks[0]["completed"] is False


def test_get_flow_omits_hidden_tasks(client):
    user_id = create_user(client)
    complete_ok(client, user_id, "personal_details_form", "submit", P_PERSONAL)
    # Before any IQ attempt, second_chance_test is hidden — only take_iq_test should appear.
    iq_tasks = client.get(f"/users/{user_id}/flow").json()["steps"][1]["tasks"]
    assert len(iq_tasks) == 1
    assert iq_tasks[0]["name"] == "take_iq_test"
    # After a medium score, second_chance_test becomes visible and appears in the list.
    complete(client, user_id, "iq_test", "take_iq_test", P_IQ_MEDIUM)
    iq_tasks_after = client.get(f"/users/{user_id}/flow").json()["steps"][1]["tasks"]
    assert len(iq_tasks_after) == 2
    assert iq_tasks_after[1]["name"] == "second_chance_test"


# ── Requirement 3: GET /users/{id}/current ───────────────────────────────────

def test_get_current_returns_first_task_on_new_user(client):
    user_id = create_user(client)
    data = client.get(f"/users/{user_id}/current").json()
    assert data["step_name"] == "personal_details_form"
    assert data["task_name"] == "submit"
    assert data["done"] is False


def test_get_current_advances_after_each_task(client):
    user_id = create_user(client)
    complete_ok(client, user_id, "personal_details_form", "submit", P_PERSONAL)
    data = client.get(f"/users/{user_id}/current").json()
    assert data["step_name"] == "iq_test"
    assert data["task_name"] == "take_iq_test"


# ── Requirement 4 & 5: Full flow + status ────────────────────────────────────

def test_initial_status_is_in_progress(client):
    user_id = create_user(client)
    assert client.get(f"/users/{user_id}/status").json()["status"] == "in_progress"


def test_full_happy_path_results_in_accepted(client):
    user_id = create_user(client)
    full_happy_path(client, user_id)
    assert client.get(f"/users/{user_id}/status").json()["status"] == "accepted"


# ── IQ test rules ─────────────────────────────────────────────────────────────

def test_iq_score_above_75_passes_directly(client):
    user_id = create_user(client)
    complete_ok(client, user_id, "personal_details_form", "submit", P_PERSONAL)
    resp = complete_ok(client, user_id, "iq_test", "take_iq_test", P_IQ_PASS)
    assert resp.json()["passed"] is True


def test_iq_score_below_60_results_in_rejected(client):
    user_id = create_user(client)
    complete_ok(client, user_id, "personal_details_form", "submit", P_PERSONAL)
    complete(client, user_id, "iq_test", "take_iq_test", P_IQ_FAIL)
    assert client.get(f"/users/{user_id}/status").json()["status"] == "rejected"


def test_iq_score_60_to_75_unlocks_second_chance_task(client):
    user_id = create_user(client)
    complete_ok(client, user_id, "personal_details_form", "submit", P_PERSONAL)
    complete(client, user_id, "iq_test", "take_iq_test", P_IQ_MEDIUM)
    # Second chance task should now be the current task
    data = client.get(f"/users/{user_id}/current").json()
    assert data["task_name"] == "second_chance_test"


def test_second_chance_not_visible_for_passing_iq_score(client):
    user_id = create_user(client)
    complete_ok(client, user_id, "personal_details_form", "submit", P_PERSONAL)
    complete_ok(client, user_id, "iq_test", "take_iq_test", P_IQ_PASS)
    # Second chance should be hidden — attempting it should fail
    resp = complete(client, user_id, "iq_test", "second_chance_test", P_IQ_PASS)
    assert resp.status_code == 400


def test_second_chance_pass_advances_to_interview(client):
    user_id = create_user(client)
    complete_ok(client, user_id, "personal_details_form", "submit", P_PERSONAL)
    complete(client, user_id, "iq_test", "take_iq_test", P_IQ_MEDIUM)
    complete_ok(client, user_id, "iq_test", "second_chance_test", P_IQ_PASS)
    data = client.get(f"/users/{user_id}/current").json()
    assert data["step_name"] == "interview"


def test_second_chance_fail_results_in_rejected(client):
    user_id = create_user(client)
    complete_ok(client, user_id, "personal_details_form", "submit", P_PERSONAL)
    complete(client, user_id, "iq_test", "take_iq_test", P_IQ_MEDIUM)
    complete(client, user_id, "iq_test", "second_chance_test", P_IQ_MEDIUM)  # score 65, fails
    assert client.get(f"/users/{user_id}/status").json()["status"] == "rejected"


# ── Interview rules ───────────────────────────────────────────────────────────

def test_interview_pass_continues_flow(client):
    user_id = create_user(client)
    complete_ok(client, user_id, "personal_details_form", "submit", P_PERSONAL)
    complete_ok(client, user_id, "iq_test", "take_iq_test", P_IQ_PASS)
    complete_ok(client, user_id, "interview", "schedule_interview", P_SCHEDULE)
    complete_ok(client, user_id, "interview", "perform_interview", P_INTERVIEW_PASS)
    data = client.get(f"/users/{user_id}/current").json()
    assert data["step_name"] == "sign_contract"


def test_interview_fail_results_in_rejected(client):
    user_id = create_user(client)
    complete_ok(client, user_id, "personal_details_form", "submit", P_PERSONAL)
    complete_ok(client, user_id, "iq_test", "take_iq_test", P_IQ_PASS)
    complete_ok(client, user_id, "interview", "schedule_interview", P_SCHEDULE)
    complete(client, user_id, "interview", "perform_interview", P_INTERVIEW_FAIL)
    assert client.get(f"/users/{user_id}/status").json()["status"] == "rejected"


def test_invalid_interview_decision_returns_400(client):
    user_id = create_user(client)
    complete_ok(client, user_id, "personal_details_form", "submit", P_PERSONAL)
    complete_ok(client, user_id, "iq_test", "take_iq_test", P_IQ_PASS)
    complete_ok(client, user_id, "interview", "schedule_interview", P_SCHEDULE)
    bad = {"interview_date": "2026-02-01", "interviewer_id": "i1", "decision": "maybe"}
    resp = complete(client, user_id, "interview", "perform_interview", bad)
    assert resp.status_code == 400


# ── Payload validation ────────────────────────────────────────────────────────

def test_non_numeric_iq_score_returns_400(client):
    user_id = create_user(client)
    complete_ok(client, user_id, "personal_details_form", "submit", P_PERSONAL)
    resp = complete(client, user_id, "iq_test", "take_iq_test",
                    {"test_id": "t1", "score": "high", "timestamp": "2026-01-01T00:00:00"})
    assert resp.status_code == 400


def test_missing_required_field_returns_400(client):
    user_id = create_user(client)
    # Omit last_name from personal details
    resp = complete(client, user_id, "personal_details_form", "submit",
                    {"first_name": "John", "timestamp": "2026-01-01T00:00:00"})
    assert resp.status_code == 400


def test_invalid_step_name_returns_400(client):
    user_id = create_user(client)
    resp = complete(client, user_id, "nonexistent_step", "some_task")
    assert resp.status_code == 400


def test_invalid_task_name_returns_400(client):
    user_id = create_user(client)
    resp = complete(client, user_id, "personal_details_form", "nonexistent_task")
    assert resp.status_code == 400


# ── Sequential enforcement ────────────────────────────────────────────────────

def test_cannot_skip_steps(client):
    user_id = create_user(client)
    # Jump straight to payment without completing earlier steps
    resp = complete(client, user_id, "payment", "payment", P_PAYMENT)
    assert resp.status_code == 400


def test_cannot_skip_tasks_within_a_step(client):
    user_id = create_user(client)
    complete_ok(client, user_id, "personal_details_form", "submit", P_PERSONAL)
    complete_ok(client, user_id, "iq_test", "take_iq_test", P_IQ_PASS)
    # Attempt perform_interview without scheduling first
    resp = complete(client, user_id, "interview", "perform_interview", P_INTERVIEW_PASS)
    assert resp.status_code == 400


# ── Idempotency ───────────────────────────────────────────────────────────────

def test_failed_iq_attempt_cannot_be_retried(client):
    user_id = create_user(client)
    complete_ok(client, user_id, "personal_details_form", "submit", P_PERSONAL)
    # Medium score: fails the first test but does not reject — second chance is now visible.
    complete(client, user_id, "iq_test", "take_iq_test", P_IQ_MEDIUM)
    # Re-submitting the first IQ task (e.g. with a passing score) must be blocked.
    resp = complete(client, user_id, "iq_test", "take_iq_test", P_IQ_PASS)
    assert resp.status_code == 400


def test_invalid_payload_does_not_consume_the_attempt(client):
    user_id = create_user(client)
    complete_ok(client, user_id, "personal_details_form", "submit", P_PERSONAL)
    # Malformed payload (non-numeric score) — should NOT consume the attempt.
    resp = complete(client, user_id, "iq_test", "take_iq_test",
                    {"test_id": "t1", "score": "high", "timestamp": "2026-01-01T00:00:00"})
    assert resp.status_code == 400
    # A valid submission afterwards must still be accepted.
    complete_ok(client, user_id, "iq_test", "take_iq_test", P_IQ_PASS)


def test_duplicate_task_completion_is_idempotent(client):
    user_id = create_user(client)
    complete_ok(client, user_id, "personal_details_form", "submit", P_PERSONAL)
    # Send the exact same request again
    resp = complete(client, user_id, "personal_details_form", "submit", P_PERSONAL)
    assert resp.status_code == 200
    assert resp.json()["passed"] is True
    # Status must still be in_progress — not double-counted
    assert client.get(f"/users/{user_id}/status").json()["status"] == "in_progress"


# ── Error handling ────────────────────────────────────────────────────────────

def test_unknown_user_returns_404(client):
    resp = client.get("/users/does-not-exist/status")
    assert resp.status_code == 404


def test_cannot_complete_task_when_rejected(client):
    user_id = create_user(client)
    complete_ok(client, user_id, "personal_details_form", "submit", P_PERSONAL)
    complete(client, user_id, "iq_test", "take_iq_test", P_IQ_FAIL)  # rejected
    resp = complete(client, user_id, "iq_test", "take_iq_test", P_IQ_PASS)
    assert resp.status_code == 400


def test_cannot_complete_task_when_accepted(client):
    user_id = create_user(client)
    full_happy_path(client, user_id)  # accepted
    resp = complete(client, user_id, "join_slack", "join_slack", P_SLACK)
    assert resp.status_code == 400
