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

_ADVISORY_ERRORS = {"RUNBOOK_KB_UNAVAILABLE"}
_REQUIRED_METRICS = ("response_time_ms", "error_rate_pct", "availability_pct")

_DEFAULT_THRESHOLDS = {
    "degraded_pct": 10.0,
    "critical_pct": 25.0,
    "outage_pct": 50.0,
    "error_rate_critical_pct": 5.0,
    "error_rate_outage_pct": 10.0,
    "availability_outage_pct": 99.0,
}


def _load_thresholds(operator_config_raw: Any, defaults: dict[str, float]) -> dict[str, float]:
    thresholds = dict(defaults)
    if not operator_config_raw:
        return thresholds
    try:
        cfg = json.loads(operator_config_raw) if isinstance(operator_config_raw, str) else operator_config_raw
        overrides = cfg.get("thresholds", {}) if isinstance(cfg, dict) else {}
        for key in _DEFAULT_THRESHOLDS:
            if key in overrides:
                try:
                    thresholds[key] = float(overrides[key])
                except (TypeError, ValueError):
                    pass
    except (json.JSONDecodeError, TypeError):
        pass
    return thresholds


def _parse_baseline(baseline_raw: Any) -> dict[str, float]:
    if not baseline_raw:
        return {}
    try:
        b = json.loads(baseline_raw) if isinstance(baseline_raw, str) else baseline_raw
        if not isinstance(b, dict):
            return {}
        result = {}
        for metric in _REQUIRED_METRICS:
            if metric in b:
                try:
                    result[metric] = float(b[metric])
                except (TypeError, ValueError):
                    pass
        return result
    except (json.JSONDecodeError, TypeError):
        return {}


class AnomalyClassifyNode(FunctionNode):
    """Classify anomaly severity from deviation scores."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, default_thresholds: dict[str, Any] | None = None) -> None:
        self._default_thresholds = dict(_DEFAULT_THRESHOLDS)
        if default_thresholds:
            for key in _DEFAULT_THRESHOLDS:
                if key in default_thresholds:
                    self._default_thresholds[key] = float(default_thresholds[key])

    def execute(self, state: WebServiceAnomalyDetectionState) -> dict[str, Any]:
        emit_trace_event(
            event_type="anomaly_classify_start",
            payload={"node": "AnomalyClassifyNode"},
            state=state,
        )

        error_code = state.get("error_code")
        if error_code and error_code not in _ADVISORY_ERRORS:
            return {}

        scores_raw = state.get("deviation_scores")
        if not scores_raw:
            emit_trace_event(
                event_type="anomaly_classify_error",
                payload={"node": "AnomalyClassifyNode", "error_code": "UNEXPECTED_ERROR"},
                state=state,
            )
            return {
                "error_code": "UNEXPECTED_ERROR",
                "error_message": "deviation_scores is missing — BaselineCompareNode may have failed.",
                "status": AgentStatus.ERROR.value,
            }

        try:
            scores = json.loads(scores_raw) if isinstance(scores_raw, str) else scores_raw
        except (json.JSONDecodeError, TypeError) as exc:
            emit_trace_event(
                event_type="anomaly_classify_error",
                payload={"node": "AnomalyClassifyNode", "error_code": "UNEXPECTED_ERROR"},
                state=state,
            )
            return {
                "error_code": "UNEXPECTED_ERROR",
                "error_message": f"deviation_scores could not be parsed: {exc}",
                "status": AgentStatus.ERROR.value,
            }

        baseline_values = _parse_baseline(state.get("baseline_input"))
        thresholds = _load_thresholds(state.get("operator_config"), self._default_thresholds)
        anomaly_class, severity_level = _classify(scores, baseline_values, thresholds)

        emit_trace_event(
            event_type="anomaly_classified",
            payload={
                "node": "AnomalyClassifyNode",
                "anomaly_class": anomaly_class,
                "severity_level": severity_level,
            },
            state=state,
        )

        return {
            "anomaly_class": anomaly_class,
            "severity_level": severity_level,
        }


def _classify(
    scores: dict[str, Any], baseline_values: dict[str, float], thresholds: dict[str, float]
) -> tuple[str, str]:
    degraded_pct = thresholds["degraded_pct"]
    critical_pct = thresholds["critical_pct"]
    outage_pct = thresholds["outage_pct"]
    error_rate_critical = thresholds["error_rate_critical_pct"]
    error_rate_outage = thresholds["error_rate_outage_pct"]
    avail_outage = thresholds["availability_outage_pct"]

    worst = 0

    for metric, score in scores.items():
        pct = score.get("pct_change", 0.0)
        delta = score.get("delta", 0.0)
        baseline_val = baseline_values.get(metric)
        current_val = (baseline_val + delta) if baseline_val is not None else None

        if metric == "availability_pct":
            deg = -pct
            if deg >= outage_pct:
                worst = max(worst, 3)
            elif deg >= critical_pct:
                worst = max(worst, 2)
            elif deg >= degraded_pct:
                worst = max(worst, 1)
            if current_val is not None and current_val < avail_outage:
                worst = max(worst, 3)
        elif metric == "error_rate_pct":
            if current_val is not None:
                # Absolute thresholds only — pct_change is unreliable for low baselines.
                # e.g. baseline=1%, current=4.9% → pct=390% would fire P1 via pct,
                # but absolute 4.9% is below the critical floor (5%) → no escalation.
                if current_val >= error_rate_outage:
                    worst = max(worst, 3)
                elif current_val >= error_rate_critical:
                    worst = max(worst, 2)
                # else: abs rate below critical floor — no escalation from error rate
            else:
                # No current_val — fall back to pct_change only
                deg = pct
                if deg >= outage_pct:
                    worst = max(worst, 3)
                elif deg >= critical_pct:
                    worst = max(worst, 2)
                elif deg >= degraded_pct:
                    worst = max(worst, 1)
        else:
            deg = pct
            if deg >= outage_pct:
                worst = max(worst, 3)
            elif deg >= critical_pct:
                worst = max(worst, 2)
            elif deg >= degraded_pct:
                worst = max(worst, 1)

    _SEVERITY_MAP = {
        0: ("normal", "P4"),
        1: ("degraded", "P3"),
        2: ("critical", "P2"),
        3: ("outage", "P1"),
    }
    return _SEVERITY_MAP[worst]
