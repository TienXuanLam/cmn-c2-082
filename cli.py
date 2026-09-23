"""AGENTIC STAR Marketplace entrypoint — one-shot Pod process.

Referenced by this repo's Dockerfile as the image `CMD`. Compiles the agent,
provisions its secrets, then hands off to shared.bootstrap.marketplace_app
for the Marketplace lifecycle (identity, input, events, terminal delivery,
exit). Mirrors agentcore's own `agents/base/chat_agent/cli.py` (the pattern
this file was copied from).

`namespace=` here is the Marketplace secret-provisioning namespace — a different
concept from `config/agent.yaml`'s AgentRegistry `namespace:` key that happens to
share its value. Mirrors
`src/api/server.py`'s existing `secrets_factory(namespace="cmn",
agent_name="cmn-c2-082")` call shape rather than a per-template value: one
Marketplace Pod deploys exactly one template, so there is no cross-template
secret-path collision to guard against.

No `config["llm"]` seam to fill here: RecommendGenerateNode builds its own
secret-bound AzureOpenAIClient per invocation (see
`src/nodes/recommend_generate.py::_build_llm`) -- constructor/module-scope
injection would be structurally unreachable on this real Marketplace path
anyway (`run_agent_marketplace` never populates `config["llm"]`; it's the
caller's own seam to fill, and this template chooses not to use it at all).
"""

from pathlib import Path
from typing import Any

from framework.utils.config_loader import load_agent_config
from shared.bootstrap.marketplace_app import run_agent_marketplace
from src.graph.graph import AnomalyDetectionGraph

# Add config overrides here to set values without touching config/config.yaml.
extend_config: dict[str, Any] = {}

if __name__ == "__main__":
    run_agent_marketplace(
        AnomalyDetectionGraph,
        agent_name="cmn-c2-082",
        namespace="cmn",
        config={**load_agent_config(Path(__file__).resolve().parent), **extend_config},
    )
