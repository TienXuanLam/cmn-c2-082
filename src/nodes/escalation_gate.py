"""Non-suppressible escalation decision and final envelope assembly."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import WebServiceAnomalyDetectionState

_SEVERITIES = {"P1", "P2", "P3", "P4"}
_ESCALATION_SEVERITIES = {"P1", "P2"}


class EscalationGateNode(FunctionNode):
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: WebServiceAnomalyDetectionState) -> dict[str, Any]:
        emit_trace_event("escalation_gate_start", {"node": type(self).__name__}, state)
        if state.get("error_code"):
            return {}
        severity = state.get("severity_level")
        if severity not in _SEVERITIES:
            return _output_error("severity_level is missing or invalid")
        try:
            scores = _required_object(state.get("deviation_scores"), "deviation_scores")
            runbook_matches = _required_list(state.get("runbook_matches"), "runbook_matches")
            recommendations = _required_list(state.get("recommendations"), "recommendations")
        except ValueError as exc:
            return _output_error(str(exc))

        deviation_summary: dict[str, Any] = {}
        for metric, score in scores.items():
            if not isinstance(score, dict) or not isinstance(score.get("pct_change"), (int, float)):
                return _output_error("deviation_scores contains a malformed entry")
            pct = float(score["pct_change"])
            deviation_summary[metric] = {"pct_change": pct, "assessment": _assess_metric(metric, pct)}

        metrics_raw = state.get("metrics_input")
        if not isinstance(metrics_raw, str):
            return _output_error("metrics_input is missing from validated state")
        escalation_required = severity in _ESCALATION_SEVERITIES
        anomaly_class = state.get("anomaly_class", "normal")
        audit = {
            "metrics_ref_hash": hashlib.sha256(metrics_raw.encode()).hexdigest(),
            "utc_timestamp": datetime.now(timezone.utc).isoformat(),
        }
        envelope = {
            "anomaly_class": anomaly_class,
            "severity_level": severity,
            "escalation_required": escalation_required,
            "deviation_summary": deviation_summary,
            "runbook_matches": runbook_matches,
            "recommendations": recommendations,
            "audit": audit,
        }
        # final_output is always JSON (machine-consumption / audit trail);
        # formatted_output (what the caller actually receives) renders as
        # Markdown when the caller asked for it via input_context.output_format.
        envelope_json = json.dumps(envelope, ensure_ascii=False, allow_nan=False)
        if state.get("output_format") == "markdown":
            formatted_output = _build_markdown_envelope(
                anomaly_class=anomaly_class,
                severity=severity,
                escalation_required=escalation_required,
                deviation_summary=deviation_summary,
                runbook_matches=runbook_matches,
                recommendations=recommendations,
                audit=audit,
            )
        else:
            formatted_output = envelope_json
        return {
            "escalation_required": escalation_required,
            "final_output": envelope_json,
            "formatted_output": formatted_output,
            "status": AgentStatus.SUCCESS.value,
        }


def _required_object(raw: Any, label: str) -> dict[str, Any]:
    parsed = _parse_json(raw, label)
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must be a JSON object")
    return parsed


def _required_list(raw: Any, label: str) -> list[Any]:
    parsed = _parse_json(raw, label)
    if not isinstance(parsed, list):
        raise ValueError(f"{label} must be a JSON array")
    return parsed


def _parse_json(raw: Any, label: str) -> Any:
    if not isinstance(raw, str):
        raise ValueError(f"{label} must be a JSON string")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} contains invalid JSON") from exc


def _build_markdown_envelope(
    *,
    anomaly_class: str,
    severity: str,
    escalation_required: bool,
    deviation_summary: dict[str, Any],
    runbook_matches: list[Any],
    recommendations: list[Any],
    audit: dict[str, Any],
) -> str:
    lines = [f"# Anomaly Report — {severity} ({anomaly_class})", ""]
    if escalation_required:
        lines.append("**Escalation required: YES** — page on-call immediately.")
    else:
        lines.append("**Escalation required: no**")
    lines.append("")

    lines.append("## Deviation Summary")
    lines.append("| Metric | % Change | Assessment |")
    lines.append("|---|---|---|")
    for metric, info in deviation_summary.items():
        pct = info.get("pct_change", 0.0)
        assessment = info.get("assessment", "")
        lines.append(f"| {metric} | {pct:+.1f}% | {assessment} |")
    lines.append("")

    lines.append("## Recommendations")
    if recommendations:
        for idx, item in enumerate(recommendations, start=1):
            if isinstance(item, dict) and "action" in item:
                action = item["action"]
                rationale = item.get("rationale", "")
                lines.append(f"{idx}. **{action}** — {rationale}" if rationale else f"{idx}. **{action}**")
            else:
                lines.append(f"{idx}. {item}")
    else:
        lines.append("(none)")
    lines.append("")

    lines.append("## Runbook Matches")
    if runbook_matches:
        for item in runbook_matches:
            title = item.get("title", item) if isinstance(item, dict) else item
            lines.append(f"- {title}")
    else:
        lines.append("(none)")
    lines.append("")

    lines.append("---")
    ref = audit.get("metrics_ref_hash", "")
    timestamp = audit.get("utc_timestamp", "")
    lines.append(f"_Audit ref: {ref[:12]}... · {timestamp}_")
    return "\n".join(lines)


def _output_error(message: str) -> dict[str, Any]:
    return {
        "error_code": "OUTPUT_INVALID",
        "error_message": message,
        "error_log": [f"OUTPUT_INVALID: {message}"],
        "status": AgentStatus.ERROR.value,
    }


def _assess_metric(metric: str, pct_change: float) -> str:
    degradation = -pct_change if metric == "availability_pct" else pct_change
    if degradation >= 50.0:
        return "outage"
    if degradation >= 25.0:
        return "critical"
    if degradation >= 10.0:
        return "degraded"
    return "normal"
