"""Standalone HTTP adapter for WebServiceAnomalyDetectionAgent."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.context import bound_secrets
from framework.utils.config_loader import load_config
from shared.secrets import factory as secrets_factory

from src.graph.graph import AnomalyDetectionGraph

app = FastAPI(title="WebServiceAnomalyDetectionAgent")

# LLM construction is not wired here: RecommendGenerateNode builds its own
# secret-bound AzureOpenAIClient per invocation via _build_llm(state) (see
# its docstring) -- config["llm"] stays the config/config.yaml tuning dict
# (temperature/max_tokens) all the way down; no client is ever assigned to
# it. STG_MOCK_MODE LLM mocking is controlled by RecommendGenerateNode
# itself, not here.

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"
_config = load_config(str(_CONFIG_PATH)) if _CONFIG_PATH.exists() else {}
_secrets_provider = secrets_factory(namespace="cmn", agent_name="cmn-c2-082")

agent = AnomalyDetectionGraph(config=_config)
agent.compile()
agent.provision_secrets(_secrets_provider)


class InvokeRequest(BaseModel):
    # Optional: metrics may instead be supplied as a real JSON object via
    # input_context.metrics_input (see the /invoke handler below) -- callers
    # are no longer required to double-encode metrics as a JSON string here.
    input: str = Field(default="", max_length=4096)
    session_id: str = Field(default="", max_length=256)
    input_context: dict[str, Any] = Field(default_factory=dict)


def _bearer_matches(supplied: str, expected: str) -> bool:
    return secrets.compare_digest(supplied.encode(), f"Bearer {expected}".encode())


def _resolve_standalone_trust(
    current: TrustLevel,
    authorization: str,
    invoke_auth_token: str | None,
    internal_runner_token: str | None,
) -> TrustLevel:
    if current is not TrustLevel.ANONYMOUS:
        return current
    if internal_runner_token and _bearer_matches(authorization, internal_runner_token):
        return TrustLevel.INTERNAL
    if invoke_auth_token and _bearer_matches(authorization, invoke_auth_token):
        return TrustLevel.VERIFIED_EXTERNAL
    if internal_runner_token or invoke_auth_token:
        raise HTTPException(status_code=401, detail="Token is invalid or expired.")
    return TrustLevel.ANONYMOUS


@app.post("/invoke")
async def invoke(req: InvokeRequest, request: Request) -> dict[str, Any]:
    trust = _resolve_standalone_trust(
        getattr(request.state, "trust_level", TrustLevel.ANONYMOUS),
        request.headers.get("authorization", ""),
        os.environ.get("INVOKE_AUTH_TOKEN"),
        os.environ.get("STG_INTERNAL_RUNNER_TOKEN"),
    )
    with bound_secrets(agent._secrets_provider):
        context = dict(req.input_context)
        # A caller-supplied input_context.metrics_input (a real JSON object,
        # the friendlier path) takes precedence; input is the legacy
        # string-encoded fallback, only used if metrics_input wasn't given.
        if "metrics_input" not in context:
            context["metrics_input"] = req.input
        ctx = InvocationContext(
            session_id=req.session_id or str(uuid4()),
            caller_trust_level=trust,
            caller_id=getattr(request.state, "caller_id", ""),
        )
        return cast("dict[str, Any]", agent.invoke(req.input, ctx=ctx, input_context=context))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "agent": "WebServiceAnomalyDetectionAgent"}
