"""PB: all Cat2 workflow steps are reachable in the intended order."""

from src.graph.domain_workflow_graph import DomainWorkflowGraph


def test_domain_workflow_topology_order() -> None:
    graph = DomainWorkflowGraph(config={"llm": {}})
    graph.compile()
    compiled = graph._compiled
    assert compiled is not None
    edges = {(edge[0], edge[1]) for edge in compiled.get_graph().edges}
    expected = [
        ("__start__", "workflow_input"),
        ("workflow_input", "baseline_compare"),
        ("baseline_compare", "anomaly_classify"),
        ("anomaly_classify", "runbook_search"),
        ("runbook_search", "recommend_generate"),
        ("recommend_generate", "__end__"),
    ]
    assert all(edge in edges for edge in expected)
