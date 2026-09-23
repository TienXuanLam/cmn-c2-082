"""Outer ingress validation for web-service metrics and baseline data."""

from __future__ import annotations

import json
import math
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.security import detect_credentials_in_value, detect_injection
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import WebServiceAnomalyDetectionState
from src.services.nl_metrics_extraction_service import NLMetricsExtractionError, NLMetricsExtractionService

REQUIRED_METRICS = ("response_time_ms", "error_rate_pct", "availability_pct")
MAX_INPUT_CHARS = 4096
MAX_OPERATOR_CONFIG_CHARS = 4096
_METRIC_RANGES = {
    "response_time_ms": (0.0, 3_600_000.0),
    "error_rate_pct": (0.0, 100.0),
    "availability_pct": (0.0, 100.0),
}
_THRESHOLD_KEYS = {
    "degraded_pct",
    "critical_pct",
    "outage_pct",
    "error_rate_critical_pct",
    "error_rate_outage_pct",
    "availability_outage_pct",
}
_VALID_OUTPUT_FORMATS = ("json", "markdown")
_DEFAULT_THRESHOLDS = {
    "degraded_pct": 10.0,
    "critical_pct": 25.0,
    "outage_pct": 50.0,
    "error_rate_critical_pct": 5.0,
    "error_rate_outage_pct": 10.0,
    "availability_outage_pct": 99.0,
}


class MetricsValidateNode(FunctionNode):
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, llm_config: dict[str, Any] | None = None) -> None:
        self._nl_extraction_service = NLMetricsExtractionService(llm_config)

    def _extra_security_gate_input(self, state: WebServiceAnomalyDetectionState) -> WebServiceAnomalyDetectionState:
        context = state.get("input_context") or {}
        if not isinstance(context, dict):
            return _reject(state, "S1_INVALID_JSON", "input_context must be a JSON object")

        candidate = dict(state)
        metrics_raw = context.get("metrics_input", state.get("user_input", ""))
        baseline_raw = context.get("baseline_input", state.get("baseline_input"))

        if _looks_like_natural_language(metrics_raw):
            try:
                extracted = self._nl_extraction_service.extract(candidate, str(metrics_raw))
            except NLMetricsExtractionError as exc:
                return _reject(candidate, exc.code, exc.message)
            candidate["metrics_input"] = _json_string(extracted["metrics_input"])
            # An explicit JSON baseline (from input_context/state) always wins
            # over a baseline the LLM guessed from the same free-form text.
            if baseline_raw in (None, "") and extracted.get("baseline_input"):
                baseline_raw = extracted["baseline_input"]
        else:
            candidate["metrics_input"] = _json_string(metrics_raw)

        candidate["baseline_input"] = _json_string(baseline_raw)
        candidate["operator_config"] = _json_string(context.get("operator_config", state.get("operator_config") or {}))
        candidate["output_format"] = context.get("output_format", "json")

        error = _validate_candidate(candidate)
        if error is not None:
            return _reject(candidate, error[0], error[1])
        return candidate  # type: ignore[return-value]

    def execute(self, state: WebServiceAnomalyDetectionState) -> dict[str, Any]:
        emit_trace_event("metrics_validate_start", {"node": type(self).__name__}, state)
        metrics = _parse_object(state.get("metrics_input"), "metrics_input")
        validated = {field: float(metrics[field]) for field in REQUIRED_METRICS}
        emit_trace_event("metrics_validate_complete", {"node": type(self).__name__}, state)
        return {
            "metrics_input": state.get("metrics_input"),
            "baseline_input": state.get("baseline_input"),
            "operator_config": state.get("operator_config"),
            "output_format": state.get("output_format", "json"),
            "validated_metrics": json.dumps(validated, allow_nan=False),
            "status": AgentStatus.SUCCESS.value,
        }


def _looks_like_natural_language(raw: Any) -> bool:
    """True when `raw` is non-empty but NOT a JSON object -- i.e. neither the
    legacy JSON-string path nor the JSON-object path applies, so natural-
    language extraction is the only remaining option. Empty/missing input is
    left to _validate_candidate's existing S1_EMPTY_INPUT check untouched."""
    if isinstance(raw, dict):
        return False
    text = str(raw or "").strip()
    if not text:
        return False
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return True
    return not isinstance(parsed, dict)


def _validate_candidate(state: dict[str, Any]) -> tuple[str, str] | None:
    metrics_raw = state.get("metrics_input")
    if not metrics_raw:
        return (
            "S1_EMPTY_INPUT",
            "metrics_input is empty — supply it either as input_context.metrics_input "
            '(a JSON object, e.g. {"response_time_ms": 850, "error_rate_pct": 7.5, '
            '"availability_pct": 99.2}) or as the legacy JSON-encoded string in `input`',
        )
    if len(str(metrics_raw)) > MAX_INPUT_CHARS:
        return "S1_INPUT_TOO_LONG", f"metrics_input exceeds {MAX_INPUT_CHARS} characters"

    output_format = state.get("output_format", "json")
    if output_format not in _VALID_OUTPUT_FORMATS:
        return (
            "S1_INVALID_JSON",
            f"output_format must be one of {list(_VALID_OUTPUT_FORMATS)}, got {output_format!r}",
        )

    try:
        metrics = _parse_object(metrics_raw, "metrics_input")
        baseline = _parse_object(state.get("baseline_input"), "baseline_input")
        operator = _parse_object(state.get("operator_config") or "{}", "operator_config")
    except ValueError as exc:
        code = "S1_INVALID_BASELINE" if "baseline" in str(exc) else "S1_INVALID_JSON"
        return code, str(exc)

    if len(json.dumps(operator, ensure_ascii=False)) > MAX_OPERATOR_CONFIG_CHARS:
        return "S1_INPUT_TOO_LONG", "operator_config exceeds its safety limit"
    if any(detect_credentials_in_value(value) for value in (metrics, baseline, operator)):
        return "S2_CREDENTIAL_DETECTED", "Credential material is not allowed in domain input"
    for value in (json.dumps(metrics), json.dumps(baseline), json.dumps(operator)):
        if any(item.get("confidence") == "high" for item in detect_injection(value)):
            return "S1_INJECTION_DETECTED", "High-confidence injection content detected"

    error = _validate_metric_object(metrics, baseline=False)
    if error:
        return "S1_INVALID_METRIC_VALUE", error
    error = _validate_metric_object(baseline, baseline=True)
    if error:
        return "S1_INVALID_BASELINE", error

    if set(metrics) != set(REQUIRED_METRICS):
        missing = set(REQUIRED_METRICS) - set(metrics)
        unexpected = set(metrics) - set(REQUIRED_METRICS)
        detail = "; ".join(
            part
            for part in (
                f"missing: {sorted(missing)}" if missing else "",
                f"unexpected: {sorted(unexpected)}" if unexpected else "",
            )
            if part
        )
        return (
            "S1_INVALID_METRIC_VALUE",
            f"metrics_input field mismatch ({detail}) — required: {list(REQUIRED_METRICS)}",
        )
    if set(baseline) - {*REQUIRED_METRICS, "stddev"}:
        unexpected_baseline_fields = sorted(set(baseline) - {*REQUIRED_METRICS, "stddev"})
        return "S1_INVALID_BASELINE", f"baseline_input contains unexpected field(s): {unexpected_baseline_fields}"

    stddev = baseline.get("stddev")
    if stddev is not None:
        if not isinstance(stddev, dict):
            return "S1_INVALID_BASELINE", "baseline stddev must be an object"
        for metric in REQUIRED_METRICS:
            if metric in stddev and not _finite_number(stddev[metric], minimum=0.0, strict_minimum=True):
                return "S1_INVALID_BASELINE", f"stddev.{metric} must be finite and greater than zero"

    thresholds = operator.get("thresholds", {})
    if not isinstance(thresholds, dict) or any(key not in _THRESHOLD_KEYS for key in thresholds):
        return "S1_INVALID_JSON", "operator_config.thresholds contains an invalid key or shape"
    parsed_thresholds = dict(_DEFAULT_THRESHOLDS)
    for key, value in thresholds.items():
        if not _finite_number(value, minimum=0.0, maximum=1000.0):
            return "S1_INVALID_JSON", f"threshold {key} must be a finite non-negative number"
        parsed_thresholds[key] = float(value)
    for low, high in (("degraded_pct", "critical_pct"), ("critical_pct", "outage_pct")):
        if parsed_thresholds[low] >= parsed_thresholds[high]:
            return "S1_INVALID_JSON", f"threshold {low} must be lower than {high}"
    for key in ("error_rate_critical_pct", "error_rate_outage_pct", "availability_outage_pct"):
        if parsed_thresholds[key] > 100.0:
            return "S1_INVALID_JSON", f"threshold {key} must not exceed 100"
    if parsed_thresholds["error_rate_critical_pct"] >= parsed_thresholds["error_rate_outage_pct"]:
        return "S1_INVALID_JSON", "error-rate critical threshold must be lower than outage threshold"
    return None


def _validate_metric_object(data: dict[str, Any], *, baseline: bool) -> str | None:
    for metric, (minimum, maximum) in _METRIC_RANGES.items():
        value = data.get(metric)
        strict_minimum = baseline and metric in {"response_time_ms", "availability_pct"}
        if not _finite_number(value, minimum=minimum, maximum=maximum, strict_minimum=strict_minimum):
            qualifier = " greater than zero" if strict_minimum else ""
            return f"{metric} must be finite,{qualifier} and within [{minimum}, {maximum}]"
    return None


def _finite_number(
    value: Any,
    *,
    minimum: float,
    maximum: float | None = None,
    strict_minimum: bool = False,
) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    number = float(value)
    if not math.isfinite(number):
        return False
    below_minimum = number <= minimum if strict_minimum else number < minimum
    if below_minimum:
        return False
    return maximum is None or number <= maximum


_FIELD_EXAMPLES = {
    "metrics_input": '{"response_time_ms": 850, "error_rate_pct": 7.5, "availability_pct": 99.2}',
    "baseline_input": '{"response_time_ms": 300, "error_rate_pct": 1.0, "availability_pct": 99.95}',
    "operator_config": '{"thresholds": {"degraded_pct": 10.0}}',
}


def _parse_object(raw: Any, label: str) -> dict[str, Any]:
    example = _FIELD_EXAMPLES.get(label, "{}")
    if raw is None or raw == "":
        raise ValueError(f"{label} is missing — expected a JSON object, e.g. {example}")
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON — expected a JSON object, e.g. {example}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must be a JSON object, e.g. {example}")
    return parsed


def _json_string(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, allow_nan=False)


def _reject(state: dict[str, Any], code: str, message: str) -> WebServiceAnomalyDetectionState:
    rejected = dict(state)
    rejected.update(
        {
            "status": AgentStatus.ERROR.value,
            "error_code": code,
            "error_message": message,
            "error_log": [f"{code}: {message}"],
        }
    )
    return rejected  # type: ignore[return-value]
