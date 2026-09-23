# Design — CMN-C2-082

CMN-C2-082 detects web-service anomalies from current metrics and a caller-supplied baseline, classifies severity, optionally retrieves allowlisted runbook references, and generates bounded incident-response recommendations.

## Architecture

- Category: Cat 2
- L1 Base: `AgentBaseGraph`
- Outer pipeline: initialize → `MetricsValidateNode` → `AnomalyWorkflowGraphNode` → `EscalationGateNode` → finalize
- Inner `DomainWorkflowGraph(BaseGraph)`: workflow input → baseline comparison → anomaly classification → runbook search → recommendation generation
- `AnomalyWorkflowGraphNode` is a real SDK `GraphNode`; domain steps are not manually composed in a `FunctionNode`.

All structured state fields are JSON strings so the state remains flat and checkpoint-safe. `formatted_output` is also a JSON string.

## Security boundaries

- Every domain node requires `VERIFIED_EXTERNAL`; the SDK propagates the invocation context into the inner graph.
- Metrics, baseline values, stddev values, and threshold overrides must be finite and within domain ranges. Boolean numeric values are rejected.
- Credential and high-confidence injection detection uses the framework/shared security implementation.
- Operator-supplied runbook URLs require HTTPS, an exact static host allowlist match, no embedded credentials, public-only DNS results, no redirects, and a 1 MiB response limit.
- Retrieved runbook text is untrusted context. The recommendation prompt explicitly forbids following instructions from it.
- LLM responses must use the canonical SDK dictionary shape and contain 2–5 exact-schema recommendations.
- P1/P2 outputs always require escalation. If the LLM omits escalation as the first action, a deterministic escalation action is inserted.
- The framework S-3 output gate performs credential scanning; the domain does not duplicate it with private regex helpers.

## Inputs and outputs

Metrics may be supplied three ways. `MetricsValidateNode._extra_security_gate_input` tries them in order and the first one that applies wins — no LLM call happens unless the first two both fail to apply:
- **Marketplace chat UI (primary)**: free-form text in `input` (e.g. `"response time 850ms, error rate 7.5%, availability 99.2%, baseline 300ms/1%/99.95%"`). When `metrics_input` isn't valid JSON, `NLMetricsExtractionService` (`src/services/nl_metrics_extraction_service.py`) calls the LLM once to extract `metrics_input`/`baseline_input` as JSON, with one corrective retry if the first attempt has the wrong shape. The extracted JSON is **not trusted** — it re-enters the exact same `_validate_candidate` range/shape/injection/credential checks as the other two paths. An explicit JSON `baseline_input` (from `input_context`) always wins over one the LLM guessed from the same sentence. `operator_config`/thresholds are never accepted via natural language, only via `input_context.operator_config`.
- **Programmatic — JSON object**: `input_context.metrics_input` as a real JSON object (e.g. `{"response_time_ms": 850, ...}`) — no string-escaping required.
- **Programmatic — legacy JSON string**: `agent.invoke()`'s `user_input` (the HTTP adapter's `input` field) as a JSON-encoded string of the same object, for backward compatibility with existing callers.

There is no fourth outer-graph slot to run natural-language extraction as a separate node before `MetricsValidateNode` — `AgentBaseGraph` hard-codes exactly three slots (`pre_process`/`main`/`post_process`). Extraction therefore runs as a preprocessing step inside `MetricsValidateNode`, still in the `pre_process` slot, using the same per-invocation `AzureOpenAIService` client-construction pattern as `RecommendGenerateNode` (`config/config.yaml`'s `llm` block is threaded into `MetricsValidateNode(llm_config)` via `graph.py`'s `register_nodes()`).

`input_context` also supplies `baseline_input` (required), optional `operator_config`, and an optional `output_format` (`"json"`, the default, or `"markdown"`) that controls only how `formatted_output` is rendered.

The final `final_output` is always a JSON string containing anomaly class, P1–P4 severity, non-suppressible escalation flag, deviation summary, runbook matches, recommendations, and an audit hash/timestamp — this is the stable, machine-consumable contract regardless of `output_format`. `formatted_output` mirrors `final_output` when `output_format` is `"json"`, or renders the same data as human-readable Markdown (headline, deviation table, numbered recommendations, runbook list, audit footer) when `output_format` is `"markdown"`.

Runbook unavailability is advisory (`warning_code=RUNBOOK_KB_UNAVAILABLE`) and does not suppress metric-based recommendation generation. Validation, graph, or LLM contract failures are fatal and fail closed, with error messages naming the specific missing/unexpected field or invalid value where possible.
