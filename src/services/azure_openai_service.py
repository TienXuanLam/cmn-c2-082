"""Azure OpenAI client construction for CMN-C2-082."""

from typing import Any

from framework.schemas.invocation_context import InvocationContext
from shared.services.llm.azure_openai_client import AzureOpenAIClient

_PASSTHROUGH_KEYS = ("temperature", "max_tokens")


class AzureOpenAIService:
    """Build the AgentCore Azure OpenAI client from invocation-scoped secrets."""

    def __init__(self, llm_config: dict[str, Any] | None = None) -> None:
        self._llm_config = dict(llm_config or {})

    def create_client(self, state: dict[str, Any]) -> AzureOpenAIClient:
        """Create a client without reading secrets from process environment variables."""
        ctx = InvocationContext.from_state(state)
        config: dict[str, Any] = {key: value for key, value in self._llm_config.items() if key in _PASSTHROUGH_KEYS}
        config["api_key"] = ctx.secrets.require("AZURE_OPENAI_API_KEY")
        config["azure_endpoint"] = ctx.secrets.require("AZURE_OPENAI_ENDPOINT")
        config["azure_deployment"] = ctx.secrets.require("AZURE_OPENAI_DEPLOYMENT")
        return AzureOpenAIClient(config)
