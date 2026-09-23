"""Full outer-and-inner graph integration tests."""

from __future__ import annotations

import json
from typing import Any

import pytest

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import AnomalyDetectionGraph
from src.nodes.recommend_generate import RecommendGenerateNode


class _LLM:
    def complete(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        return {
            "content": json.dumps(
                [
                    {"priority": 1, "action": "Escalate to on-call", "rationale": "Coordinate response"},
                    {"priority": 2, "action": "Inspect recent changes", "rationale": "Isolate cause"},
                ]
            )
        }


@pytest.fixture(autouse=True)
def _fake_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    # RecommendGenerateNode builds its LLM client per-invocation via
    # _build_llm(state) -- see its docstring. The node instance here is
    # created inside register_nodes(), not directly accessible from the
    # test, so the fake is installed at the class level instead of
    # monkeypatching one instance.
    monkeypatch.setattr(RecommendGenerateNode, "_build_llm", lambda self, state: _LLM())


def _invoke(metrics: dict[str, float]) -> dict[str, Any]:
    agent = AnomalyDetectionGraph(config={"thresholds": {}, "runbook_kb_allowed_hosts": []})
    agent.compile()
    raw = json.dumps(metrics)
    return agent.invoke(
        raw,
        input_context={
            "baseline_input": {
                "response_time_ms": 200.0,
                "error_rate_pct": 1.0,
                "availability_pct": 99.9,
            },
            "operator_config": {},
        },
        ctx=InvocationContext(caller_id="integration", caller_trust_level=TrustLevel.VERIFIED_EXTERNAL),
    )


def test_full_cat2_pipeline_normal() -> None:
    result = _invoke({"response_time_ms": 205.0, "error_rate_pct": 1.0, "availability_pct": 99.9})
    output = json.loads(result["output"])
    assert result["status"] == "success"
    assert output["severity_level"] == "P4"
    assert output["escalation_required"] is False
    assert "AnomalyWorkflowGraphNode" in result["node_history"]


def test_full_cat2_pipeline_p1() -> None:
    result = _invoke({"response_time_ms": 1200.0, "error_rate_pct": 15.0, "availability_pct": 96.0})
    output = json.loads(result["output"])
    assert result["status"] == "success"
    assert output["severity_level"] == "P1"
    assert output["escalation_required"] is True
    assert "escalat" in output["recommendations"][0]["action"].lower()


def test_anonymous_invocation_is_denied() -> None:
    agent = AnomalyDetectionGraph(config={})
    result = agent.invoke(
        json.dumps({"response_time_ms": 205.0, "error_rate_pct": 1.0, "availability_pct": 99.9}),
        input_context={"baseline_input": {}, "operator_config": {}},
    )
    assert result["status"] == "error"
    assert result["output"] is None
    assert "MetricsValidateNode" in result["node_history"]
