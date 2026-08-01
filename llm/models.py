"""LLM usage accounting models."""

from pydantic import BaseModel, Field


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    model: str = ""


class LLMCallRecord(BaseModel):
    call_type: str = ""
    prompt_summary: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    model: str = ""
    caller_tag: str = ""


class TokenUsageSummary(BaseModel):
    call_records: list[LLMCallRecord] = Field(default_factory=list)
    by_model: dict[str, TokenUsage] = Field(default_factory=dict)
    by_caller: dict[str, TokenUsage] = Field(default_factory=dict)
    total: TokenUsage = Field(default_factory=TokenUsage)
