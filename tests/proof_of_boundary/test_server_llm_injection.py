"""PB: standalone STG server injects a canonical LLM and authenticated trust."""

from __future__ import annotations

import importlib
import json
import sys

from fastapi.testclient import TestClient


def test_stg_server_full_invoke(monkeypatch) -> None:
    monkeypatch.setenv("STG_MOCK_MODE", "true")
    monkeypatch.setenv("INVOKE_AUTH_TOKEN", "integration-token")
    sys.modules.pop("src.api.server", None)
    server = importlib.import_module("src.api.server")
    client = TestClient(server.app)
    response = client.post(
        "/invoke",
        headers={"Authorization": "Bearer integration-token"},
        json={
            "input": json.dumps({"response_time_ms": 1200.0, "error_rate_pct": 15.0, "availability_pct": 96.0}),
            "input_context": {
                "baseline_input": {
                    "response_time_ms": 200.0,
                    "error_rate_pct": 1.0,
                    "availability_pct": 99.9,
                },
                "operator_config": {},
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert json.loads(body["output"])["escalation_required"] is True


def test_standalone_auth_rejects_invalid_token(monkeypatch) -> None:
    monkeypatch.setenv("STG_MOCK_MODE", "true")
    monkeypatch.setenv("INVOKE_AUTH_TOKEN", "expected-token")
    sys.modules.pop("src.api.server", None)
    server = importlib.import_module("src.api.server")
    response = TestClient(server.app).post(
        "/invoke",
        headers={"Authorization": "Bearer wrong-token"},
        json={"input": "{}", "input_context": {}},
    )
    assert response.status_code == 401
