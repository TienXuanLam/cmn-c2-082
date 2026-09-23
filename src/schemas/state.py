"""Flat, checkpoint-safe state for CMN-C2-082."""

from framework.schemas.agent_state import AgentState


class WebServiceAnomalyDetectionState(AgentState, total=False):  # type: ignore[call-arg]
    # Input values are JSON strings after the outer pre-process gate.
    metrics_input: str | None
    baseline_input: str | None
    operator_config: str | None
    # Caller-selected rendering for formatted_output: "json" (default) or
    # "markdown" -- a runtime choice, not a config/config.yaml setting.
    output_format: str | None

    # Inner workflow values. Every structured value is JSON encoded.
    validated_metrics: str | None
    deviation_scores: str | None
    anomaly_class: str | None
    severity_level: str | None
    runbook_matches: str | None
    recommendations: str | None
    escalation_required: bool | None

    # Output and stable diagnostics.
    final_output: str | None
    formatted_output: str | None
    warning_code: str | None
    error_code: str | None
    error_message: str | None
