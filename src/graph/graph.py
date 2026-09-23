"""AgentCore Platform v1.0"""

from framework.graph.agent_base_graph import AgentBaseGraph

from src.nodes.metrics_validate import MetricsValidateNode
from src.graph.anomaly_workflow_node import AnomalyWorkflowGraphNode
from src.nodes.escalation_gate import EscalationGateNode
from src.schemas.state import WebServiceAnomalyDetectionState


class AnomalyDetectionGraph(AgentBaseGraph):
    """Fixed-pipeline graph for CMN-C2-082 WebServiceAnomalyDetectionAgent.

    Slot mapping (6 conceptual nodes → 3 SDK slots):
      pre_process  → MetricsValidateNode   (S-1/S-2 gates, input validation)
      main         → MainNode              (BaselineCompare → AnomalyClassify → RunbookSearch → RecommendGenerate)
      post_process → EscalationGateNode    (S-3 non-suppressible escalation, output assembly)
    """

    @property
    def name(self) -> str:
        return "cmn_c2_anomaly_detection_agent"

    @property
    def state_schema(self) -> type:
        return WebServiceAnomalyDetectionState

    def register_nodes(self) -> None:
        super().register_nodes()

        self._nodes["pre_process"] = MetricsValidateNode(self.config.get("llm"))
        self._nodes["main"] = AnomalyWorkflowGraphNode(config=self.config)
        self._nodes["post_process"] = EscalationGateNode()
