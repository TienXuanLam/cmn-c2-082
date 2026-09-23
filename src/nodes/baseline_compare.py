"""AgentCore Platform v1.0"""

# Node contract:
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Never import from mediator/, api/, or other agents

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import WebServiceAnomalyDetectionState
from src.services.statistical_baseline_comparator import StatisticalBaselineComparator

_ADVISORY_ERRORS = {"RUNBOOK_KB_UNAVAILABLE"}


class BaselineCompareNode(FunctionNode):
    """Compare current metrics against baseline using StatisticalBaselineComparator."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: WebServiceAnomalyDetectionState) -> dict[str, Any]:
        emit_trace_event(
            event_type="baseline_compare_start",
            payload={"node": "BaselineCompareNode"},
            state=state,
        )

        error_code = state.get("error_code")
        if error_code and error_code not in _ADVISORY_ERRORS:
            return {}

        validated_raw = state.get("validated_metrics")
        if not validated_raw:
            emit_trace_event(
                event_type="baseline_compare_error",
                payload={"node": "BaselineCompareNode", "error_code": "UNEXPECTED_ERROR"},
                state=state,
            )
            return {
                "error_code": "UNEXPECTED_ERROR",
                "error_message": "validated_metrics is missing — MetricsValidateNode may have failed.",
                "status": AgentStatus.ERROR.value,
            }

        try:
            current = json.loads(validated_raw) if isinstance(validated_raw, str) else validated_raw
        except (json.JSONDecodeError, TypeError) as exc:
            emit_trace_event(
                event_type="baseline_compare_error",
                payload={"node": "BaselineCompareNode", "error_code": "S1_INVALID_JSON"},
                state=state,
            )
            return {
                "error_code": "S1_INVALID_JSON",
                "error_message": str(exc),
                "status": AgentStatus.ERROR.value,
            }

        baseline_raw = state.get("baseline_input")
        try:
            baseline_full = json.loads(baseline_raw) if isinstance(baseline_raw, str) else baseline_raw
        except (json.JSONDecodeError, TypeError):
            emit_trace_event(
                event_type="baseline_compare_error",
                payload={"node": "BaselineCompareNode", "error_code": "S1_INVALID_BASELINE"},
                state=state,
            )
            return {
                "error_code": "S1_INVALID_BASELINE",
                "error_message": "baseline_input could not be parsed.",
                "status": AgentStatus.ERROR.value,
            }

        if not isinstance(baseline_full, dict):
            return {
                "error_code": "S1_INVALID_BASELINE",
                "error_message": "baseline_input must be a JSON object",
                "status": AgentStatus.ERROR.value,
            }
        baseline = {
            k: v for k, v in baseline_full.items() if k in ("response_time_ms", "error_rate_pct", "availability_pct")
        }
        stddev = baseline_full.get("stddev")

        for field in ("response_time_ms", "error_rate_pct", "availability_pct"):
            if field not in baseline:
                emit_trace_event(
                    event_type="baseline_compare_error",
                    payload={"node": "BaselineCompareNode", "error_code": "BASELINE_MISSING_FIELD"},
                    state=state,
                )
                return {
                    "error_code": "BASELINE_MISSING_FIELD",
                    "error_message": f"baseline_input missing required field '{field}'.",
                    "status": AgentStatus.ERROR.value,
                }

        comparator = StatisticalBaselineComparator(baseline, stddev)
        try:
            scores = comparator.compare(current)
        except KeyError as exc:
            emit_trace_event(
                event_type="baseline_compare_error",
                payload={"node": "BaselineCompareNode", "error_code": "UNEXPECTED_ERROR"},
                state=state,
            )
            return {
                "error_code": "UNEXPECTED_ERROR",
                "error_message": f"validated_metrics missing expected key: {exc}",
                "status": AgentStatus.ERROR.value,
            }

        scores_dict = StatisticalBaselineComparator.scores_to_dict(scores)

        emit_trace_event(
            event_type="baseline_compare_complete",
            payload={"node": "BaselineCompareNode"},
            state=state,
        )

        return {"deviation_scores": json.dumps(scores_dict)}
