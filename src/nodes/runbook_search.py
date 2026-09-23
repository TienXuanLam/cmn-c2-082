"""Allowlisted, SSRF-resistant runbook retrieval."""

from __future__ import annotations

import ipaddress
import json
import math
import socket
import urllib.request
from typing import Any, ClassVar
from urllib.parse import urlparse

from framework.nodes.function_node import FunctionNode
from framework.security import evaluate_untrusted_content
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import WebServiceAnomalyDetectionState
from src.services.runbook_http import NoRedirect

_DEFAULT_TOP_K = 3
_KB_TIMEOUT_S = 5
_MAX_BODY_BYTES = 1_048_576


class RunbookSearchNode(FunctionNode):
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, allowed_hosts: list[str] | None = None) -> None:
        self._allowed_hosts = {host.lower().rstrip(".") for host in (allowed_hosts or []) if host}

    def execute(self, state: WebServiceAnomalyDetectionState) -> dict[str, Any]:
        emit_trace_event("runbook_search_start", {"node": type(self).__name__}, state)
        if state.get("error_code"):
            return {}
        kb_url, top_k = _parse_kb_config(state.get("operator_config"))
        if not kb_url:
            return {"runbook_matches": "[]"}
        try:
            matches = _query_kb(
                kb_url,
                json.dumps(
                    {
                        "anomaly_class": state.get("anomaly_class", "normal"),
                        "severity_level": state.get("severity_level", "P4"),
                        "top_k": top_k,
                    }
                ),
                self._allowed_hosts,
            )
            safe_matches = _sanitise_matches(matches, top_k)
            checked = evaluate_untrusted_content(
                json.dumps(safe_matches, ensure_ascii=False),
                source="runbook_kb",
                state=state,
                node_name=type(self).__name__,
            )
            if checked.get("status") == "error":
                raise ValueError("unsafe runbook content")
        except Exception as exc:  # noqa: BLE001
            emit_trace_event(
                "runbook_search_unavailable",
                {"node": type(self).__name__, "reason": type(exc).__name__},
                state,
            )
            return {
                "warning_code": "RUNBOOK_KB_UNAVAILABLE",
                "error_message": "Runbook KB is unavailable; recommendations use metric evidence only",
                "runbook_matches": "[]",
            }
        return {"runbook_matches": json.dumps(safe_matches, allow_nan=False)}


def _parse_kb_config(operator_config_raw: Any) -> tuple[str, int]:
    try:
        cfg = json.loads(operator_config_raw) if isinstance(operator_config_raw, str) else operator_config_raw
    except (json.JSONDecodeError, TypeError):
        return "", _DEFAULT_TOP_K
    if not isinstance(cfg, dict):
        return "", _DEFAULT_TOP_K
    url = cfg.get("runbook_kb_url", "")
    raw_top_k = cfg.get("runbook_kb_top_k", _DEFAULT_TOP_K)
    try:
        top_k = int(raw_top_k)
    except (TypeError, ValueError):
        top_k = _DEFAULT_TOP_K
    return (url.strip() if isinstance(url, str) else ""), max(1, min(top_k, 10))


def _query_kb(kb_url: str, query_payload: str, allowed_hosts: set[str]) -> list[Any]:
    parsed = urlparse(kb_url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not hostname or parsed.username or parsed.password:
        raise ValueError("runbook URL must be an HTTPS URL without embedded credentials")
    if hostname not in allowed_hosts:
        raise ValueError("runbook host is not in the static allowlist")
    port = parsed.port or 443
    addresses = {item[4][0] for item in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)}
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("runbook host does not resolve exclusively to public addresses")

    request = urllib.request.Request(
        kb_url,
        data=query_payload.encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(NoRedirect())
    with opener.open(request, timeout=_KB_TIMEOUT_S) as response:  # noqa: S310
        body = response.read(_MAX_BODY_BYTES + 1)
    if len(body) > _MAX_BODY_BYTES:
        raise ValueError("runbook response exceeds size limit")
    result = json.loads(body.decode("utf-8"))
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        matches = result.get("matches", result.get("results", []))
        return matches if isinstance(matches, list) else []
    return []


def _sanitise_matches(raw_matches: list[Any], top_k: int) -> list[dict[str, Any]]:
    safe: list[dict[str, Any]] = []
    for item in raw_matches[:top_k]:
        if not isinstance(item, dict):
            continue
        relevance = _safe_float(item.get("relevance_score"))
        safe.append(
            {
                "id": str(item.get("id", ""))[:128],
                "title": str(item.get("title", ""))[:256],
                "relevance_score": relevance,
                "summary": str(item.get("summary", ""))[:512],
            }
        )
    return safe


def _safe_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(number, 4) if math.isfinite(number) else 0.0
