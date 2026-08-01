"""LLM client and usage models."""

__all__ = ["LLMClient"]


def __getattr__(name):
    if name != "LLMClient":
        raise AttributeError(name)
    from llm.client import LLMClient

    return LLMClient
