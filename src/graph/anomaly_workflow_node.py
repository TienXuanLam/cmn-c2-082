"""Cat2 GraphNode boundary for the anomaly-analysis workflow."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from framework.schemas.trust_level import TrustLevel

from src.graph.domain_workflow_graph import DomainWorkflowGraph


class AnomalyWorkflowGraphNode(GraphNode):
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL
    error_strategy: ClassVar[str] = "propagate"
    propagate_hitl: ClassVar[bool] = False

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = dict(config or {})

    def get_subgraph(self) -> DomainWorkflowGraph:
        return DomainWorkflowGraph(config=self._config)

    def extract_input(self, state: AgentState) -> str:
        return json.dumps(
            {
                key: state.get(key)
                for key in ("metrics_input", "baseline_input", "operator_config", "validated_metrics")
            },
            allow_nan=False,
        )

    def merge_output(self, state: AgentState, sub_result: dict[str, Any]) -> dict[str, Any]:
        domain_output = sub_result.get("output")
        if not isinstance(domain_output, dict):
            return {
                "status": "error",
                "error_code": "SUBGRAPH_INVALID_OUTPUT",
                "error_message": "Anomaly workflow returned a malformed output",
                "error_log": ["SUBGRAPH_INVALID_OUTPUT: output must be an object"],
            }
        merged = dict(domain_output)
        merged["status"] = sub_result.get("status")
        if sub_result.get("error_log"):
            merged["error_log"] = sub_result["error_log"]
        return merged
