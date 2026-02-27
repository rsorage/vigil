from llm.base import LLMProvider
from llm.claude import ClaudeProvider
from llm.ollama import OllamaProvider
from config import config


def get_provider() -> LLMProvider:
    """Factory — returns the configured LLM provider."""
    if config.llm_provider == "ollama":
        return OllamaProvider()
    return ClaudeProvider()


__all__ = ["LLMProvider", "ClaudeProvider", "OllamaProvider", "get_provider"]
