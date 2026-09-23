"""Security and architectural proof-of-boundary tests."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from framework.nodes.graph_node import GraphNode

from src.graph.domain_workflow_graph import DomainWorkflowGraph
from src.graph.anomaly_workflow_node import AnomalyWorkflowGraphNode
from src.nodes.metrics_validate import MetricsValidateNode

ROOT = Path(__file__).resolve().parents[2]


def _trusted_state(operator: dict[str, object]) -> dict[str, object]:
    return {
        "user_input": json.dumps({"response_time_ms": 200.0, "error_rate_pct": 1.0, "availability_pct": 99.9}),
        "input_context": {
            "baseline_input": {
                "response_time_ms": 200.0,
                "error_rate_pct": 1.0,
                "availability_pct": 99.9,
            },
            "operator_config": operator,
        },
        "caller_trust_level": "VERIFIED_EXTERNAL",
        "node_history": [],
        "error_log": [],
        "execution_time": {},
    }


def test_pb_credential_rejected_through_public_gate_chain() -> None:
    bearer = "Bearer " + "eyJhbGciOiJIUzI1NiJ9" + ".payload.signature123"
    result = MetricsValidateNode()(_trusted_state({"authorization": bearer}))
    assert result["status"] == "error"
    assert result["error_code"] == "S2_CREDENTIAL_DETECTED"


def test_pb_cat2_uses_graphnode_and_custom_inner_graph() -> None:
    node = AnomalyWorkflowGraphNode(config={"llm": {}})
    assert isinstance(node, GraphNode)
    inner = node.get_subgraph()
    assert isinstance(inner, DomainWorkflowGraph)
    inner.compile()
    assert inner._compiled is not None


def test_pb_no_level_zero_imports_or_legacy_invoke_hook() -> None:
    violations: list[str] = []
    for path in (ROOT / "src").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        if "_invoke_impl" in source:
            violations.append(f"{path}: legacy _invoke_impl")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                violations.extend(
                    f"{path}: {alias.name}" for alias in node.names if alias.name.startswith("agenticstar")
                )
            elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("agenticstar"):
                violations.append(f"{path}: {node.module}")
    assert violations == []


def test_pb_protected_docs_are_not_replaced_by_scaffold_placeholders() -> None:
    # Built at runtime, not spelled out literally, so this source file itself
    # never contains an unfilled-looking "{{...}}" token (publish.sh's B-3
    # scan flags that pattern anywhere in the published tree, including test
    # sources — it can't tell a negated assertion from a real placeholder).
    unfilled_placeholder = "{{" + "AGENT_NAME" + "}}"
    for name in ("01_proposal.md", "04_security_review.md", "05_review_log.md", "09_process_metrics.md"):
        content = (ROOT / "docs" / name).read_text(encoding="utf-8")
        assert unfilled_placeholder not in content
