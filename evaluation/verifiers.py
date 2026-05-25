"""
Deterministic outcome verifiers for benchmark task evaluation.

Replaces keyword-only matching with structured verifiers:
  - file_exists: check file exists in sandbox
  - file_contains: check file content for exact/fuzzy match
  - json_field: check JSON output has expected field with expected value
  - numeric_range: check numeric output falls in range
  - regex_match: check output matches regex pattern
  - keyword_include / keyword_exclude: legacy keyword checks
  - composite_and / composite_or: combine verifiers

确定性结果验证器 —— 替代纯关键词匹配，减少假阳性/假阴性。

Design: registry pattern + dataclass verifier specs attached to BenchmarkTask.
参考 SWE-bench execution-based verification 和 GAIA exact-match 模式。
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class VerifierType(str, Enum):
    FILE_EXISTS = "file_exists"
    FILE_CONTAINS = "file_contains"
    JSON_FIELD = "json_field"
    NUMERIC_RANGE = "numeric_range"
    REGEX_MATCH = "regex_match"
    KEYWORD_INCLUDE = "keyword_include"
    KEYWORD_EXCLUDE = "keyword_exclude"
    COMPOSITE_AND = "composite_and"
    COMPOSITE_OR = "composite_or"


@dataclass
class VerifierSpec:
    """A single verifier specification and its result."""
    type: VerifierType
    params: dict[str, Any] = field(default_factory=dict)
    passed: bool | None = None
    detail: str = ""


@dataclass
class VerifierResult:
    """Aggregate result from running all verifiers for a task."""
    total: int = 0
    passed_count: int = 0
    all_passed: bool = False
    details: list[VerifierSpec] = field(default_factory=list)


# ======================================================================
# Verifier Registry
# ======================================================================

VerifierFunc = Callable[[VerifierSpec, str, str], VerifierSpec]

_REGISTRY: dict[VerifierType, VerifierFunc] = {}


def register(vtype: VerifierType) -> Callable:
    """Decorator to register a verifier function for a given type."""
    def decorator(fn: VerifierFunc) -> VerifierFunc:
        _REGISTRY[vtype] = fn
        return fn
    return decorator


def _read_file(path: str, sandbox_dir: str = "") -> str | None:
    """Read file content, returning None if file doesn't exist."""
    full_path = os.path.join(sandbox_dir, path) if sandbox_dir else path
    if not os.path.isfile(full_path):
        return None
    try:
        with open(full_path, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def _extract_json(text: str) -> Any:
    """Try to parse JSON from text, tolerating surrounding prose."""
    text = text.strip()
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try extracting from ```json ... ``` block
    m = re.search(r'```(?:json)?\s*\n?(.*?)```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    # Try finding first { ... } or [ ... ]
    for start_char, end_char in [('{', '}'), ('[', ']')]:
        start = text.find(start_char)
        if start >= 0:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == start_char:
                    depth += 1
                elif text[i] == end_char:
                    depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
    return None


# ======================================================================
# Verifier Implementations
# ======================================================================

@register(VerifierType.KEYWORD_INCLUDE)
def _keyword_include(spec: VerifierSpec, output: str, sandbox_dir: str = "") -> VerifierSpec:
    keywords = spec.params.get("keywords", [])
    case_sensitive = spec.params.get("case_sensitive", False)
    text = output if case_sensitive else output.lower()
    missed = []
    for kw in keywords:
        check_kw = kw if case_sensitive else kw.lower()
        if check_kw not in text:
            missed.append(kw)
    spec.passed = len(missed) == 0
    spec.detail = f"All keywords present" if spec.passed else f"Missed: {missed}"
    return spec


@register(VerifierType.KEYWORD_EXCLUDE)
def _keyword_exclude(spec: VerifierSpec, output: str, sandbox_dir: str = "") -> VerifierSpec:
    keywords = spec.params.get("keywords", [])
    case_sensitive = spec.params.get("case_sensitive", False)
    text = output if case_sensitive else output.lower()
    found = []
    for kw in keywords:
        check_kw = kw if case_sensitive else kw.lower()
        if check_kw in text:
            found.append(kw)
    spec.passed = len(found) == 0
    spec.detail = "No forbidden keywords found" if spec.passed else f"Found forbidden: {found}"
    return spec


@register(VerifierType.REGEX_MATCH)
def _regex_match(spec: VerifierSpec, output: str, sandbox_dir: str = "") -> VerifierSpec:
    pattern = spec.params.get("pattern", "")
    source = spec.params.get("source", "output")
    if source == "file":
        content = _read_file(spec.params.get("path", ""), sandbox_dir)
        if content is None:
            spec.passed = None  # Cannot verify — file not accessible
            spec.detail = f"File not found: {spec.params.get('path', '')}"
            return spec
        text = content
    else:
        text = output
    try:
        spec.passed = bool(re.search(pattern, text, re.IGNORECASE))
    except re.error as e:
        spec.passed = None
        spec.detail = f"Invalid regex: {e}"
        return spec
    spec.detail = f"Regex '{pattern}' {'matched' if spec.passed else 'not matched'}"
    return spec


@register(VerifierType.JSON_FIELD)
def _json_field(spec: VerifierSpec, output: str, sandbox_dir: str = "") -> VerifierSpec:
    field_path = spec.params.get("field", "")
    expected = spec.params.get("expected")
    source = spec.params.get("source", "output")

    if source == "file":
        content = _read_file(spec.params.get("path", ""), sandbox_dir)
        if content is None:
            spec.passed = None
            spec.detail = f"File not found: {spec.params.get('path', '')}"
            return spec
        text = content
    else:
        text = output

    data = _extract_json(text)
    if data is None:
        spec.passed = False
        spec.detail = "Output is not valid JSON"
        return spec

    # Navigate nested field path (e.g., "results.score")
    current = data
    for part in field_path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                spec.passed = False
                spec.detail = f"Cannot navigate field '{field_path}' (index error at '{part}')"
                return spec
        else:
            spec.passed = False
            spec.detail = f"Field '{field_path}' not found (failed at '{part}')"
            return spec

    if expected is not None:
        spec.passed = current == expected
        spec.detail = f"Field '{field_path}': got {current!r}, expected {expected!r}"
    else:
        spec.passed = True
        spec.detail = f"Field '{field_path}' exists with value: {current!r}"
    return spec


@register(VerifierType.NUMERIC_RANGE)
def _numeric_range(spec: VerifierSpec, output: str, sandbox_dir: str = "") -> VerifierSpec:
    min_val = spec.params.get("min")
    max_val = spec.params.get("max")
    tolerance = spec.params.get("tolerance", 0.01)
    field_path = spec.params.get("field", "")
    source = spec.params.get("source", "output")

    if source == "file":
        content = _read_file(spec.params.get("path", ""), sandbox_dir)
        if content is None:
            spec.passed = None
            spec.detail = f"File not found: {spec.params.get('path', '')}"
            return spec
        text = content
    else:
        text = output

    # Extract numeric value
    if field_path:
        data = _extract_json(text)
        if data is None:
            spec.passed = False
            spec.detail = "Cannot parse JSON for numeric extraction"
            return spec
        current = data
        for part in field_path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                spec.passed = False
                spec.detail = f"Field '{field_path}' not found"
                return spec
        value = current
    else:
        # Extract first number from text
        m = re.search(r'[-+]?\d*\.?\d+', text)
        if m is None:
            spec.passed = False
            spec.detail = "No numeric value found in output"
            return spec
        value = float(m.group())

    try:
        value = float(value)
    except (TypeError, ValueError):
        spec.passed = False
        spec.detail = f"Cannot convert {value!r} to number"
        return spec

    checks = []
    if min_val is not None:
        checks.append(value >= min_val - tolerance)
    if max_val is not None:
        checks.append(value <= max_val + tolerance)
    spec.passed = all(checks) if checks else True
    spec.detail = f"Value {value} in range [{min_val}, {max_val}]" if spec.passed else f"Value {value} outside range [{min_val}, {max_val}]"
    return spec


@register(VerifierType.FILE_EXISTS)
def _file_exists(spec: VerifierSpec, output: str, sandbox_dir: str = "") -> VerifierSpec:
    path = spec.params.get("path", "")
    full_path = os.path.join(sandbox_dir, path) if sandbox_dir else path
    if not sandbox_dir:
        # No sandbox available — cannot verify file existence from output alone
        # Check if filename is mentioned in output as a heuristic
        spec.passed = path.lower() in output.lower() if path else None
        spec.detail = f"No sandbox; checking output for '{path}': {'found' if spec.passed else 'not found'}"
        return spec
    spec.passed = os.path.isfile(full_path)
    spec.detail = f"File '{path}' {'exists' if spec.passed else 'not found'}"
    return spec


@register(VerifierType.FILE_CONTAINS)
def _file_contains(spec: VerifierSpec, output: str, sandbox_dir: str = "") -> VerifierSpec:
    path = spec.params.get("path", "")
    content_pattern = spec.params.get("content", "")
    fuzzy = spec.params.get("fuzzy", False)
    threshold = spec.params.get("threshold", 0.8)

    content = _read_file(path, sandbox_dir)
    if content is None:
        if not sandbox_dir:
            # Fallback: check if content pattern appears in output
            spec.passed = content_pattern.lower() in output.lower() if content_pattern else None
            spec.detail = f"No sandbox; checking output for content: {'found' if spec.passed else 'not found'}"
            return spec
        spec.passed = False
        spec.detail = f"File '{path}' not found"
        return spec

    if fuzzy:
        # Simple fuzzy: check if enough of the expected words appear
        expected_words = set(content_pattern.lower().split())
        actual_words = set(content.lower().split())
        overlap = expected_words & actual_words
        ratio = len(overlap) / len(expected_words) if expected_words else 1.0
        spec.passed = ratio >= threshold
        spec.detail = f"Fuzzy match: {ratio:.0%} word overlap (threshold={threshold:.0%})"
    else:
        spec.passed = content_pattern.lower() in content.lower()
        spec.detail = f"Content {'found' if spec.passed else 'not found'} in file '{path}'"
    return spec


@register(VerifierType.COMPOSITE_AND)
def _composite_and(spec: VerifierSpec, output: str, sandbox_dir: str = "") -> VerifierSpec:
    sub_specs = spec.params.get("verifiers", [])
    results = []
    for sub_dict in sub_specs:
        sub = _dict_to_spec(sub_dict)
        sub = run_verifier(sub, output, sandbox_dir)
        results.append(sub)
        if sub.passed is False:
            spec.passed = False
            spec.detail = f"AND failed at '{sub.type.value}': {sub.detail}"
            return spec
    all_none = all(r.passed is None for r in results)
    if all_none:
        spec.passed = None
        spec.detail = "All sub-verifiers skipped"
    else:
        spec.passed = all(r.passed is not False for r in results)
        spec.detail = f"All {len(results)} sub-verifiers passed"
    return spec


@register(VerifierType.COMPOSITE_OR)
def _composite_or(spec: VerifierSpec, output: str, sandbox_dir: str = "") -> VerifierSpec:
    sub_specs = spec.params.get("verifiers", [])
    results = []
    for sub_dict in sub_specs:
        sub = _dict_to_spec(sub_dict)
        sub = run_verifier(sub, output, sandbox_dir)
        results.append(sub)
        if sub.passed is True:
            spec.passed = True
            spec.detail = f"OR passed at '{sub.type.value}'"
            return spec
    all_none = all(r.passed is None for r in results)
    if all_none:
        spec.passed = None
        spec.detail = "All sub-verifiers skipped"
    else:
        spec.passed = any(r.passed is True for r in results)
        spec.detail = f"No sub-verifier passed ({len(results)} tried)"
    return spec


# ======================================================================
# Public API
# ======================================================================

def _dict_to_spec(d: dict[str, Any]) -> VerifierSpec:
    """Convert a dict (from BenchmarkTask.verifiers) to a VerifierSpec."""
    return VerifierSpec(
        type=VerifierType(d["type"]),
        params=d.get("params", {}),
    )


def run_verifier(spec: VerifierSpec, output: str, sandbox_dir: str = "") -> VerifierSpec:
    """Run a single verifier and return the spec with result populated."""
    fn = _REGISTRY.get(spec.type)
    if fn is None:
        spec.passed = None
        spec.detail = f"Unknown verifier type: {spec.type}"
        return spec
    return fn(spec, output, sandbox_dir)


def run_verifiers(
    specs: list[dict[str, Any]],
    output: str,
    sandbox_dir: str = "",
) -> VerifierResult:
    """Run all verifiers from a list of spec dicts. Returns aggregate result."""
    if not specs:
        return VerifierResult()

    results: list[VerifierSpec] = []
    passed_count = 0
    for d in specs:
        spec = _dict_to_spec(d)
        spec = run_verifier(spec, output, sandbox_dir)
        results.append(spec)
        if spec.passed is True:
            passed_count += 1

    # all_passed requires all verifiers to pass (None/skipped doesn't fail)
    actionable = [r for r in results if r.passed is not None]
    all_passed = all(r.passed is True for r in actionable) if actionable else False

    return VerifierResult(
        total=len(results),
        passed_count=passed_count,
        all_passed=all_passed,
        details=results,
    )
