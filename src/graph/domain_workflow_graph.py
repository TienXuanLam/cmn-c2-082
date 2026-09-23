"""Inner Cat2 anomaly-analysis workflow."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_status import AgentStatus

from src.nodes.anomaly_classify import AnomalyClassifyNode
from src.nodes.baseline_compare import BaselineCompareNode
from src.nodes.recommend_generate import RecommendGenerateNode
from src.nodes.runbook_search import RunbookSearchNode
from src.nodes.workflow_input import WorkflowInputNode
from src.schemas.state import WebServiceAnomalyDetectionState


class DomainWorkflowGraph(BaseGraph):
    @property
    def name(self) -> str:
        return "cmn_c2_082_anomaly_workflow"

    @property
    def state_schema(self) -> type:
        return WebServiceAnomalyDetectionState

    def _validate_config(self) -> None:
        thresholds = self.config.get("thresholds", {})
        if not isinstance(thresholds, dict):
            raise ValueError("thresholds must be an object")
        allowed_hosts = self.config.get("runbook_kb_allowed_hosts", [])
        if not isinstance(allowed_hosts, list) or not all(isinstance(item, str) for item in allowed_hosts):
            raise ValueError("runbook_kb_allowed_hosts must be a list of hostnames")

    def register_nodes(self) -> None:
        self._nodes = {
            "workflow_input": WorkflowInputNode(),
            "baseline_compare": BaselineCompareNode(),
            "anomaly_classify": AnomalyClassifyNode(self.config.get("thresholds")),
            "runbook_search": RunbookSearchNode(self.config.get("runbook_kb_allowed_hosts", [])),
            "recommend_generate": RecommendGenerateNode(self.config.get("llm")),
            # ^ "llm" here is config/config.yaml's tuning dict (temperature/
            # max_tokens), not an LLM client instance -- RecommendGenerateNode
            # builds its own secret-bound AzureOpenAIClient per invocation via
            # _build_llm(state); see its docstring for why constructor
            # injection of a client is structurally unreachable on the real
            # Marketplace path and would leak credentials across callers.
        }

    def add_edges(self) -> None:
        self._sg.add_edge(START, "workflow_input")
        self._sg.add_edge("workflow_input", "baseline_compare")
        self._sg.add_edge("baseline_compare", "anomaly_classify")
        self._sg.add_edge("anomaly_classify", "runbook_search")
        self._sg.add_edge("runbook_search", "recommend_generate")
        self._sg.add_edge("recommend_generate", END)

    def route(self, state: WebServiceAnomalyDetectionState) -> str:
        return END

    def get_output(self, state: WebServiceAnomalyDetectionState) -> dict[str, Any]:
        output = {
            key: state.get(key)
            for key in (
                "deviation_scores",
                "anomaly_class",
                "severity_level",
                "runbook_matches",
                "recommendations",
                "warning_code",
                "error_code",
                "error_message",
            )
            if state.get(key) is not None
        }
        return {
            "output": output,
            "status": state.get("status", AgentStatus.SUCCESS.value),
            "trace_id": state.get("trace_id", ""),
            "correlation_id": state.get("correlation_id", ""),
            "node_history": state.get("node_history", []),
            "error_log": state.get("error_log", []),
        }
