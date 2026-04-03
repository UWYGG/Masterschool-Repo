# Engineering Decisions

This document captures key implementation decisions for the admissions exercise, including rationale and tradeoffs.

## 1) Configuration-driven funnel
- **Context:** PM-driven changes should be easy.
- **Decision:** Keep steps/tasks/rules in `app/admissions_config.py`; execute them in `app/service.py`.
- **Why:** Flow changes without rewriting core logic.
- **Tradeoff:** Slightly more indirection.

## 2) In-memory state
- **Context:** Exercise scope does not require persistence.
- **Decision:** Keep users in process memory (`AdmissionsService.users`).
- **Why:** Fast iteration and focus on domain logic.
- **Tradeoff:** State resets on restart.

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
- **Context:** If both IQ tasks are visible, requiring both is too strict.
- **Decision:** Mark IQ step complete when either IQ task passed.
- **Why:** Matches alternative-path product intent.
- **Tradeoff:** One explicit special case in service logic.

## 7) Progress numbers, not presentation text
- **Context:** Sheet asks to support "step x of y" and also says no frontend required.
- **Decision:** Return `current_step_number` + `total_steps`, leave text formatting to clients.
- **Why:** Keeps API presentation-agnostic.
- **Tradeoff:** Consumer formats the final message.

## 8) Keep contracts simple for current scope
- **Context:** Functionality is prioritized over architecture ceremony.
- **Decision:** Keep request models in `app/main.py`; skip separate response DTO layer for now.
- **Why:** Faster delivery with less boilerplate.
- **Tradeoff:** Response typing/documentation is less strict.

## 9) Terminal statuses are non-mutable
- **Context:** Completing tasks after accepted/rejected can create inconsistent state.
- **Decision:** Block task completion in terminal states.
- **Why:** Deterministic state transitions.
- **Tradeoff:** No reopen/retry flow yet.

## 10) Revisit triggers
- Need persistence or multi-instance support.
- Need an audit trail of failed attempts.
- Need richer branching rules beyond current IQ special case.
- Want stricter response contracts and richer OpenAPI docs.
