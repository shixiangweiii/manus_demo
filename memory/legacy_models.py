"""Small compatibility record used by retained local memory stores."""

import time

from pydantic import BaseModel, Field


class MemoryEntry(BaseModel):
    task: str
    summary: str
    learnings: list[str] = Field(default_factory=list)
    timestamp: float = Field(default_factory=time.time)
