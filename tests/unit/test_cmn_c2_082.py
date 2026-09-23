"""Domain unit tests for CMN-C2-082."""

from __future__ import annotations

import json
import math

import pytest

from src.nodes.anomaly_classify import AnomalyClassifyNode
from src.nodes.escalation_gate import EscalationGateNode
from src.nodes.metrics_validate import MetricsValidateNode
from src.nodes.recommend_generate import RecommendGenerateNode
from src.nodes.runbook_search import RunbookSearchNode, _query_kb
from src.services.statistical_baseline_comparator import StatisticalBaselineComparator


def _metrics(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "response_time_ms": 250.0,
        "error_rate_pct": 1.0,
        "availability_pct": 99.9,
    }
    value.update(updates)
    return value


def _baseline(**updates: object) -> dict[str, object]:
    value = _metrics(response_time_ms=200.0)
    value.update(updates)
    return value


def _gate_state(**updates: object) -> dict[str, object]:
    state: dict[str, object] = {
        "user_input": json.dumps(_metrics()),
        "input_context": {
            "baseline_input": _baseline(),
            "operator_config": {},
        },
        "caller_trust_level": "VERIFIED_EXTERNAL",
        "node_history": [],
        "error_log": [],
        "execution_time": {},
    }
    state.update(updates)
    return state


@pytest.mark.parametrize(
    "metrics",
    [
        _metrics(response_time_ms=float("nan")),
        _metrics(error_rate_pct=float("inf")),
        _metrics(error_rate_pct=101.0),
        _metrics(availability_pct=-0.1),
        _metrics(response_time_ms=True),
    ],
)
def test_metrics_gate_rejects_non_finite_boolean_and_out_of_range(metrics: dict[str, object]) -> None:
    result = MetricsValidateNode()(
        _gate_state(user_input=json.dumps(metrics), input_context={"baseline_input": _baseline()})
    )
    assert result["status"] == "error"
    assert result["error_code"] == "S1_INVALID_METRIC_VALUE"


def test_metrics_gate_rejects_credential_material() -> None:
    bearer = "Bearer " + "eyJhbGciOiJIUzI1NiJ9" + ".payload.signature123"
    result = MetricsValidateNode()(
        _gate_state(input_context={"baseline_input": _baseline(), "operator_config": {"authorization": bearer}})
    )
    assert result["error_code"] == "S2_CREDENTIAL_DETECTED"


@pytest.mark.parametrize(
    "thresholds",
    [
        {"degraded_pct": 30.0},
        {"critical_pct": 60.0},
        {"error_rate_critical_pct": 20.0},
        {"availability_outage_pct": 101.0},
        {"degraded_pct": float("nan")},
    ],
)
def test_metrics_gate_rejects_invalid_or_inverted_thresholds(thresholds: dict[str, float]) -> None:
    result = MetricsValidateNode()(
        _gate_state(input_context={"baseline_input": _baseline(), "operator_config": {"thresholds": thresholds}})
    )
    assert result["status"] == "error"


def test_metrics_gate_accepts_metrics_input_as_real_json_object() -> None:
    # Friendlier path: metrics_input given as a real object in input_context,
    # no double-encoding into a JSON string, and no `user_input` at all.
    result = MetricsValidateNode()(
        _gate_state(
            user_input="",
            input_context={"metrics_input": _metrics(), "baseline_input": _baseline(), "operator_config": {}},
        )
    )
    assert result["status"] == "success"
    assert json.loads(result["validated_metrics"]) == _metrics()


def test_metrics_gate_rejects_empty_metrics_input_with_actionable_message() -> None:
    result = MetricsValidateNode()(_gate_state(user_input="", input_context={"baseline_input": _baseline()}))
    assert result["error_code"] == "S1_EMPTY_INPUT"
    assert "input_context.metrics_input" in result["error_message"]
    assert "legacy" in result["error_message"]


def test_metrics_gate_reports_missing_and_unexpected_fields_by_name() -> None:
    result = MetricsValidateNode()(
        _gate_state(
            user_input=json.dumps(_metrics(bogus_field=1)),
            input_context={"baseline_input": _baseline()},
        )
    )
    assert result["error_code"] == "S1_INVALID_METRIC_VALUE"
    assert "missing:" not in result["error_message"]
    assert "unexpected:" in result["error_message"]
    assert "bogus_field" in result["error_message"]


def test_metrics_gate_rejects_invalid_output_format() -> None:
    result = MetricsValidateNode()(_gate_state(input_context={"baseline_input": _baseline(), "output_format": "xml"}))
    assert result["status"] == "error"
    assert result["error_code"] == "S1_INVALID_JSON"
    assert "output_format" in result["error_message"]


def test_metrics_gate_defaults_and_propagates_output_format() -> None:
    default_result = MetricsValidateNode()(_gate_state(input_context={"baseline_input": _baseline()}))
    assert default_result["output_format"] == "json"

    markdown_result = MetricsValidateNode()(
        _gate_state(input_context={"baseline_input": _baseline(), "output_format": "markdown"})
    )
    assert markdown_result["output_format"] == "markdown"


def test_metrics_gate_accepts_natural_language_text_via_stg_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STG_MOCK_MODE", "true")
    result = MetricsValidateNode()(
        _gate_state(
            user_input="response time 850ms, error rate 7.5%, availability 99.2%, baseline 300ms/1%/99.95%",
            input_context={},
        )
    )
    assert result["status"] == "success"
    assert json.loads(result["validated_metrics"])["response_time_ms"] == 850.0


def test_metrics_gate_prefers_explicit_json_baseline_over_nl_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STG_MOCK_MODE", "true")
    result = MetricsValidateNode()(
        _gate_state(
            user_input="response time 850ms, error rate 7.5%, availability 99.2%",
            input_context={"baseline_input": _baseline(response_time_ms=123.0)},
        )
    )
    assert result["status"] == "success"
    assert json.loads(result["baseline_input"])["response_time_ms"] == 123.0


def test_metrics_gate_json_input_never_triggers_nl_path() -> None:
    # No STG_MOCK_MODE set and no real LLM secrets available -- if the NL
    # path were wrongly triggered for valid JSON input, this would fail
    # closed with S2_LLM_ERROR instead of succeeding.
    result = MetricsValidateNode()(_gate_state())
    assert result["status"] == "success"


def test_metrics_gate_nl_extraction_llm_error_surfaces_actionable_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STG_MOCK_MODE", raising=False)
    result = MetricsValidateNode()(
        _gate_state(user_input="response time 850ms, error rate 7.5%, availability 99.2%", input_context={})
    )
    assert result["status"] == "error"
    assert result["error_code"] == "S2_LLM_ERROR"


def test_metrics_gate_rejects_oversized_natural_language_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STG_MOCK_MODE", "true")
    huge_text = "response time 850ms " * 300
    result = MetricsValidateNode()(_gate_state(user_input=huge_text, input_context={}))
    assert result["error_code"] == "S1_INPUT_TOO_LONG"


def test_comparator_calculates_deviation_and_sigma() -> None:
    comparator = StatisticalBaselineComparator(
        _baseline(),
        {"response_time_ms": 25.0, "error_rate_pct": 0.5, "availability_pct": 0.1},
    )
    scores = comparator.compare(_metrics())
    assert scores["response_time_ms"].pct_change == 25.0
    assert scores["response_time_ms"].sigma == 2.0


@pytest.mark.parametrize(
    ("pct", "severity"),
    [(0.0, "P4"), (10.0, "P3"), (25.0, "P2"), (50.0, "P1")],
)
def test_classifier_uses_inclusive_threshold_boundaries(pct: float, severity: str) -> None:
    scores = {
        metric: {"delta": 0.0, "pct_change": 0.0, "sigma": None}
        for metric in ("response_time_ms", "error_rate_pct", "availability_pct")
    }
    scores["response_time_ms"]["pct_change"] = pct
    result = AnomalyClassifyNode().execute(
        {
            "deviation_scores": json.dumps(scores),
            "baseline_input": json.dumps(_baseline()),
            "operator_config": "{}",
        }
    )
    assert result["severity_level"] == severity


class _LLM:
    def __init__(self, output: object) -> None:
        self.output = output

    def complete(self, messages: list[dict[str, str]]) -> object:
        return self.output


def _recommend_state(severity: str = "P4") -> dict[str, object]:
    return {
        "severity_level": severity,
        "anomaly_class": "critical",
        "deviation_scores": "{}",
        "runbook_matches": "[]",
    }


def _node_with_llm(llm: _LLM) -> RecommendGenerateNode:
    # RecommendGenerateNode builds its LLM client per-invocation via
    # _build_llm(state) -- see its docstring. Tests inject a fake client by
    # monkeypatching the instance method rather than constructor injection.
    node = RecommendGenerateNode()
    node._build_llm = lambda state: llm
    return node


def test_recommendation_requires_canonical_response_and_two_items() -> None:
    assert _node_with_llm(_LLM("legacy")).execute(_recommend_state())["error_code"] == "S2_LLM_ERROR"
    one_item = {"content": '[{"priority":1,"action":"A","rationale":"B"}]'}
    assert _node_with_llm(_LLM(one_item)).execute(_recommend_state())["error_code"] == "S2_LLM_ERROR"


def test_p1_recommendation_forces_escalation_first() -> None:
    output = {
        "content": json.dumps(
            [
                {"priority": 1, "action": "Inspect dashboards", "rationale": "Find scope"},
                {"priority": 2, "action": "Check deployments", "rationale": "Find cause"},
            ]
        )
    }
    result = _node_with_llm(_LLM(output)).execute(_recommend_state("P1"))
    first = json.loads(result["recommendations"])[0]
    assert "escalat" in first["action"].lower()


def test_runbook_disabled_is_nonfatal_and_invalid_url_is_warning() -> None:
    disabled = RunbookSearchNode().execute({"operator_config": "{}"})
    assert disabled == {"runbook_matches": "[]"}
    blocked = RunbookSearchNode(["kb.example.com"]).execute(
        {"operator_config": json.dumps({"runbook_kb_url": "http://kb.example.com/runbooks"})}
    )
    assert blocked["warning_code"] == "RUNBOOK_KB_UNAVAILABLE"
    assert "error_code" not in blocked


def test_query_kb_rejects_non_allowlisted_and_embedded_credentials() -> None:
    with pytest.raises(ValueError):
        _query_kb("https://other.example/runbooks", "{}", {"kb.example.com"})
    with pytest.raises(ValueError):
        embedded = "https://" + "user" + ":" + "pass" + "@kb.example.com/runbooks"
        _query_kb(embedded, "{}", {"kb.example.com"})


def test_query_kb_rejects_allowlisted_host_resolving_private(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.nodes.runbook_search.socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(ValueError, match="public"):
        _query_kb("https://kb.example.com/runbooks", "{}", {"kb.example.com"})


@pytest.mark.parametrize(("severity", "expected"), [("P1", True), ("P2", True), ("P3", False), ("P4", False)])
def test_escalation_is_non_suppressible(severity: str, expected: bool) -> None:
    scores = {"response_time_ms": {"pct_change": 10.0}}
    result = EscalationGateNode().execute(
        {
            "severity_level": severity,
            "anomaly_class": "critical",
            "deviation_scores": json.dumps(scores),
            "runbook_matches": "[]",
            "recommendations": "[]",
            "metrics_input": json.dumps(_metrics()),
            "operator_config": json.dumps({"suppress_escalation": True}),
        }
    )
    assert result["escalation_required"] is expected
    assert isinstance(result["formatted_output"], str)
    assert math.isfinite(json.loads(result["formatted_output"])["deviation_summary"]["response_time_ms"]["pct_change"])


def test_escalation_renders_markdown_when_requested() -> None:
    scores = {"response_time_ms": {"pct_change": 183.3}}
    result = EscalationGateNode().execute(
        {
            "severity_level": "P1",
            "anomaly_class": "outage",
            "deviation_scores": json.dumps(scores),
            "runbook_matches": "[]",
            "recommendations": json.dumps([{"priority": 1, "action": "Escalate now", "rationale": "P1"}]),
            "metrics_input": json.dumps(_metrics()),
            "output_format": "markdown",
        }
    )
    formatted = result["formatted_output"]
    assert isinstance(formatted, str)
    with pytest.raises(json.JSONDecodeError):
        json.loads(formatted)
    assert formatted.startswith("# Anomaly Report — P1")
    assert "Escalation required: YES" in formatted
    assert "Escalate now" in formatted
    # final_output stays machine-readable JSON regardless of output_format.
    assert json.loads(result["final_output"])["severity_level"] == "P1"


def test_escalation_fails_closed_on_malformed_upstream_state() -> None:
    result = EscalationGateNode().execute(
        {"severity_level": "P1", "deviation_scores": "not-json", "runbook_matches": "[]", "recommendations": "[]"}
    )
    assert result["status"] == "error"
    assert result["error_code"] == "OUTPUT_INVALID"
