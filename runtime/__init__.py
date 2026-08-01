"""Composition root for task execution."""

from runtime.app import AgentRuntime
from runtime.factory import build_runtime

__all__ = ["AgentRuntime", "build_runtime"]
