"""Decode the validated outer payload at the Cat2 subgraph boundary."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import WebServiceAnomalyDetectionState


class WorkflowInputNode(FunctionNode):
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: WebServiceAnomalyDetectionState) -> dict[str, Any]:
        emit_trace_event("workflow_input_start", {"node": type(self).__name__}, state)
        raw = state.get("user_input", "")
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            payload = None
        if not isinstance(payload, dict):
            return {
                "status": AgentStatus.ERROR.value,
                "error_code": "SUBGRAPH_INVALID_INPUT",
                "error_message": "Validated subgraph payload must be a JSON object",
                "error_log": ["SUBGRAPH_INVALID_INPUT: malformed workflow payload"],
            }
        required = ("metrics_input", "baseline_input", "operator_config", "validated_metrics")
        if any(not isinstance(payload.get(key), str) for key in required):
            return {
                "status": AgentStatus.ERROR.value,
                "error_code": "SUBGRAPH_INVALID_INPUT",
                "error_message": "Validated subgraph payload is missing required string fields",
                "error_log": ["SUBGRAPH_INVALID_INPUT: missing workflow field"],
            }
        return {
            **{key: payload[key] for key in required},
            "status": AgentStatus.SUCCESS.value,
        }
