"""Central safety policy for local shell execution and guardrails."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path


SHELL_MODES = {"disabled", "restricted", "trusted"}

_RESTRICTED_COMMANDS = {
    "pwd",
    "echo",
    "printf",
    "ls",
    "cat",
    "head",
    "tail",
    "wc",
    "rg",
    "grep",
    "find",
    "stat",
    "du",
}

_SHELL_SYNTAX = ("\n", "\r", ";", "&", "|", ">", "<", "`", "$", "~", "\\")
_FIND_BLOCKED_PREFIXES = ("-delete", "-exec", "-ok", "-fprint", "-fprintf", "-fls")
_RG_BLOCKED = ("--pre", "--pre-glob", "--follow", "-L")


@dataclass(frozen=True)
class ShellAssessment:
    allowed: bool
    reason: str = ""
    argv: list[str] = field(default_factory=list)


def _parse(command: str) -> tuple[list[str], str]:
    try:
        argv = shlex.split(command, posix=True)
    except ValueError as exc:
        return [], f"invalid shell syntax: {exc}"
    if not argv:
        return [], "empty command"
    return argv, ""


def _path_reason(token: str, sandbox: Path) -> str:
    candidate_text = token.split("=", 1)[-1] if "=" in token else token
    candidate = Path(candidate_text)
    if candidate.is_absolute():
        return f"absolute paths are not allowed: {candidate_text}"
    if ".." in candidate.parts:
        return f"parent traversal is not allowed: {candidate_text}"
    local = sandbox / candidate
    if local.exists() and not local.resolve().is_relative_to(sandbox):
        return f"path escapes sandbox: {candidate_text}"
    return ""


def assess_shell_command(command: str, sandbox_dir: str, mode: str) -> ShellAssessment:
    """Validate a command and return the exact argv used for execution."""
    if mode not in SHELL_MODES:
        return ShellAssessment(False, f"unknown shell mode: {mode}")
    if mode == "disabled":
        return ShellAssessment(False, "shell execution is disabled")

    argv, error = _parse(command)
    if error:
        return ShellAssessment(False, error)

    if mode == "trusted":
        return ShellAssessment(True, argv=["bash", "-c", command])

    if any(marker in command for marker in _SHELL_SYNTAX):
        return ShellAssessment(False, "shell operators, expansion, and escaping are not allowed")
    command_name = argv[0]
    if command_name not in _RESTRICTED_COMMANDS:
        return ShellAssessment(False, f"command is not allowed in restricted mode: {command_name}")

    lowered = [token.lower() for token in argv[1:]]
    if command_name == "find" and any(
        token.startswith(prefix) for token in lowered for prefix in _FIND_BLOCKED_PREFIXES
    ):
        return ShellAssessment(False, "find execution and write actions are not allowed")
    if command_name == "rg" and any(
        token == blocked or token.startswith(blocked + "=")
        for token in lowered
        for blocked in _RG_BLOCKED
    ):
        return ShellAssessment(False, "rg preprocessing and symlink following are not allowed")

    sandbox = Path(os.path.realpath(os.path.expanduser(sandbox_dir)))
    for token in argv[1:]:
        if token.startswith("-") and "=" not in token:
            continue
        reason = _path_reason(token, sandbox)
        if reason:
            return ShellAssessment(False, reason)
    return ShellAssessment(True, argv=argv)
