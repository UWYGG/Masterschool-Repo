# Design Decisions

This document captures key implementation decisions for the admissions exercise, including rationale and tradeoffs.

## 1) Configuration-driven funnel
- **Context:** PM-driven changes should be easy.
- **Decision:** Keep steps/tasks/rules in `app/admissions_config.py`; execute them in `app/service.py`.
- **Why:** Flow changes without rewriting core logic.
- **Tradeoff:** Slightly more indirection.

## 2) Python + FastAPI
- **Context:** Choice of language and framework.
- **Decision:** Python with FastAPI and Pydantic.
- **Why:** FastAPI gives automatic request validation, clear route definitions, and free interactive docs (`/docs`) with minimal boilerplate. Pydantic enforces payload types at the boundary so bad input never reaches business logic.
- **Tradeoff:** Async overhead is unnecessary at this scale, but the clarity it adds to routing justifies the choice.

## 3) Rule-based completion
- **Context:** Some tasks pass by payload existence, others by payload values.
- **Decision:** Apply `required_payload_fields`, `pass_rule`, and `reject_on_fail` from config in `complete_task`.
- **Why:** Single policy mechanism across all tasks.
- **Tradeoff:** More branching paths to test.

## 4) Only passed tasks advance progression
- **Context:** Failed attempts should not count toward completion.
- **Decision:** Append to `completed_tasks` only when `pass_rule(payload)` is true.
- **Why:** Prevent incorrect advancement.
- **Tradeoff:** Failed-attempt history is not persisted.

## 5) IQ second-chance visibility via context
- **Context:** Second chance should appear only for medium first score (60-75).
- **Decision:** Store score in `user["context"]["iq_test_score"]`; visibility rule reads it.
- **Why:** Keeps branching behavior config-driven.
- **Tradeoff:** Requires context updates before follow-up queries.

## 6) IQ step OR semantics
- **Context:** If both IQ tasks are visible, requiring both to pass is too strict — passing either one should satisfy the step.
- **Decision:** Mark IQ step complete when either IQ task passed, expressed as a `completion_rule` on the step definition (`iq_step_completion_rule` in `admissions_config.py`).
- **Why:** Keeps the OR logic in config alongside the rest of the IQ rules; `service.py` calls `step.completion_rule(...)` generically with no awareness of IQ.
- **Tradeoff:** Each non-default completion rule adds a callable to maintain in config.

## 7) Progress numbers, not presentation text
- **Context:** Sheet asks to support "step x of y" and also says no frontend required.
- **Decision:** Return `current_step_number` + `total_steps`, leave text formatting to clients.
- **Why:** Keeps API presentation-agnostic.
- **Tradeoff:** Consumer formats the final message.


## 8) Terminal statuses are non-mutable
- **Context:** Completing tasks after accepted/rejected can create inconsistent state.
- **Decision:** Block task completion in terminal states.
- **Why:** Deterministic state transitions.
- **Tradeoff:** No reopen/retry flow yet.

## 9) `user_id` in the URL, not the request body
- **Context:** The exercise lists `user_id` as part of the PUT task-completion payload.
- **Decision:** Place `user_id` in the path (`/users/{user_id}/tasks/complete`) instead of the body.
- **Why:** REST convention — the resource being acted on belongs in the URL; repeating it in the body creates a surface for mismatched values.
- **Tradeoff:** Slight deviation from the literal payload spec in the exercise sheet.

## 10) Idempotent task completion
- **Context:** Webhooks are routinely retried by the sender on network failure.
- **Decision:** If the same step + task is submitted a second time, return the same success response without mutating state.
- **Why:** Prevents double-counting a passing event as two completions; safe by default.
- **Tradeoff:** A genuinely different payload on the second call is silently ignored.

## 11) Sequential step and task enforcement
- **Context:** The PDF does not explicitly forbid out-of-order submissions.
- **Decision:** Reject any task completion where a prior step is incomplete, or a prior task within the same step is incomplete.
- **Why:** Prevents nonsensical states (e.g. signing a contract before interviewing); matches the obvious product intent.
- **Tradeoff:** Stricter than the spec; a frontend that sends events in order will never notice.

## 12) One account per email
- **Context:** The PDF does not mention uniqueness constraints on registration.
- **Decision:** Return 409 Conflict if a second `POST /users` arrives with an already-registered email.
- **Why:** Prevents accidental double-registration and the ambiguity of two users sharing an identity.
- **Tradeoff:** No account-recovery or merge flow if a user genuinely needs a second attempt.

## 13) Email omitted from task payloads
- **Context:** The PDF lists `email` in the Personal Details and Join Slack payloads.
- **Decision:** Drop `email` from `required_payload_fields` for those tasks.
- **Why:** `user_id` in the URL already uniquely identifies the user; requiring `email` again creates a surface for mismatched data with no added value.
- **Tradeoff:** Minor divergence from the literal payload spec, documented with a comment in config.

## 14) Payload validator separate from pass_rule
- **Context:** Some tasks receive inputs that can be structurally invalid regardless of pass/fail (e.g. a non-numeric IQ score, an unrecognised interview decision).
- **Decision:** Add an optional `payload_validator` to `TaskDefinition` that raises 400 before `pass_rule` runs.
- **Why:** Separates "is the input well-formed?" from "did the applicant pass?" — bad input is a client error, not a failed attempt.
- **Tradeoff:** Two callables to define per task instead of one.

## 15) `context_extractor` on `TaskDefinition` for config-driven context updates
- **Context:** Some tasks need to store payload values in `user["context"]` after completion so that subsequent visibility rules on the same request evaluate correctly (e.g. the IQ score must be stored before `second_chance_visibility_rule` runs).
- **Decision:** Add an optional `context_extractor: Callable[[payload], dict]` field to `TaskDefinition`. It returns `{key: value}` pairs that are merged into `user["context"]`. `service.py` calls it generically after payload validation and before `pass_rule`.
- **Why:** Keeps the service a flow-agnostic engine. IQ-specific knowledge (which payload field to read, which context key to write) stays in `admissions_config.py` alongside the rest of the IQ rules. Any future task that needs context side-effects follows the same pattern without touching the service.
- **Tradeoff:** Callers building custom step definitions (e.g. in `test_pm_modifiability.py`) must remember to wire up `context_extractor` when their rules depend on context values — omitting it silently breaks visibility.

## 16) Validators raise `ValueError`; service converts to HTTP errors
- **Context:** `payload_validator` callables live in `admissions_config.py`, which should be a pure business-rules layer with no knowledge of the web framework.
- **Decision:** Validators raise `ValueError` on bad input. `service.py` catches `ValueError` from the validator and re-raises it as `HTTPException(400)`.
- **Why:** Keeps the config layer framework-agnostic — the rule callables can be imported, tested, and reasoned about without FastAPI installed. The web-framework concern (HTTP status codes) stays in the service layer where it belongs.
- **Tradeoff:** One extra `try/except` in `complete_task`; callers of the validator outside the service must handle `ValueError` themselves.


