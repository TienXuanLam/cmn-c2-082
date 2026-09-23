"""Cat2 topology contract tests."""

from framework.graph.base_graph import BaseGraph
from framework.nodes.graph_node import GraphNode

from src.graph.domain_workflow_graph import DomainWorkflowGraph
from src.graph.graph import AnomalyDetectionGraph
from src.graph.anomaly_workflow_node import AnomalyWorkflowGraphNode


def test_main_slot_is_graph_node() -> None:
    graph = AnomalyDetectionGraph(config={"llm": {}})
    graph.register_nodes()
    assert isinstance(graph._nodes["main"], GraphNode)
    assert isinstance(graph._nodes["main"], AnomalyWorkflowGraphNode)


def test_graph_node_returns_domain_base_graph() -> None:
    node = AnomalyWorkflowGraphNode(config={"llm": {}})
    assert isinstance(node.get_subgraph(), BaseGraph)
    assert isinstance(node.get_subgraph(), DomainWorkflowGraph)


def test_inner_graph_registers_all_domain_steps() -> None:
    graph = DomainWorkflowGraph(config={"llm": {}})
    graph.register_nodes()
    assert list(graph._nodes) == [
        "workflow_input",
        "baseline_compare",
        "anomaly_classify",
        "runbook_search",
        "recommend_generate",
    ]
