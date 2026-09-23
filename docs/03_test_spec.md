# Test specification — CMN-C2-082

The suite validates this project as an independent Cat 2 use case.

## Unit coverage

- Reject NaN, infinity, booleans, negative values, and metric percentages outside 0–100.
- Reject missing/zero invalid baselines and non-positive/non-finite stddev values.
- Reject unknown, non-finite, out-of-range, or ordering-inverting threshold overrides after merging defaults.
- Verify statistical percentage and sigma calculations.
- Verify inclusive P3/P2/P1 threshold boundaries and absolute availability/error-rate floors.
- Require canonical LLM responses with exactly 2–5 valid recommendation objects.
- Enforce escalation as the first P1/P2 recommendation.
- Verify runbook disabled behavior, HTTPS/allowlist enforcement, private DNS rejection, redirect blocking policy, bounded response handling, and advisory failure behavior.
- Verify malformed upstream output fails closed and P1/P2 escalation cannot be suppressed.

## Integration and proof-of-boundary coverage

- Compile and invoke the full outer graph plus inner workflow for P4 and P1 scenarios.
- Verify anonymous invocation is denied.
- Verify the outer `main` slot is `GraphNode` and returns `DomainWorkflowGraph(BaseGraph)`.
- Verify all inner steps are reachable in the documented order.
- Exercise the standalone FastAPI endpoint with deterministic STG LLM injection and bearer authentication.
- Verify import isolation, flat/checkpoint-safe state, framework security-gate usage, and absence of legacy `_invoke_impl` hooks.

## Required local gates

Run `.claude/common-scripts/check-security.sh` and `.claude/common-scripts/check-local.sh` with `AGENTCORE_WHEEL_SPEC=agenticstar-agentcore[marketplace,openai]==1.0.3`. A release candidate requires Ruff format/lint, strict mypy, tests, proof-of-boundary tests, security checks, and Stage 5 Overall to pass.
