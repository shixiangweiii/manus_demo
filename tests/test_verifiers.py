"""
Tests for deterministic evaluation verifiers.
All tests are local and do not require LLM/API access.
"""

from __future__ import annotations

from evaluation.verifiers import run_verifiers


def test_keyword_include_and_exclude_pass():
    result = run_verifiers(
        [
            {"type": "keyword_include", "params": {"keywords": ["Python", "agent"]}},
            {"type": "keyword_exclude", "params": {"keywords": ["secret"]}},
        ],
        "A Python agent completed the task safely.",
    )

    assert result.total == 2
    assert result.passed_count == 2
    assert result.all_passed is True


def test_regex_match_invalid_pattern_is_skipped():
    result = run_verifiers(
        [{"type": "regex_match", "params": {"pattern": "["}}],
        "anything",
    )

    assert result.total == 1
    assert result.passed_count == 0
    assert result.all_passed is None
    assert result.details[0].passed is None
    assert "Invalid regex" in result.details[0].detail


def test_json_field_from_output():
    output = """
    Here is the result:
    ```json
    {"slideshow": {"title": "Sample Slide Show"}}
    ```
    """

    result = run_verifiers(
        [
            {
                "type": "json_field",
                "params": {
                    "field": "slideshow.title",
                    "expected": "Sample Slide Show",
                },
            }
        ],
        output,
    )

    assert result.all_passed is True
    assert result.details[0].passed is True


def test_numeric_range_from_output():
    result = run_verifiers(
        [{"type": "numeric_range", "params": {"min": 40, "max": 45}}],
        "The final answer is 42.",
    )

    assert result.all_passed is True
    assert result.details[0].passed is True


def test_file_exists_and_file_contains_with_sandbox(tmp_path):
    target = tmp_path / "answer.txt"
    target.write_text("Hello World\n", encoding="utf-8")

    result = run_verifiers(
        [
            {"type": "file_exists", "params": {"path": "answer.txt"}},
            {"type": "file_contains", "params": {"path": "answer.txt", "content": "Hello World"}},
        ],
        "",
        sandbox_dir=str(tmp_path),
    )

    assert result.total == 2
    assert result.passed_count == 2
    assert result.all_passed is True


def test_composite_and_fails_on_failed_child():
    result = run_verifiers(
        [
            {
                "type": "composite_and",
                "params": {
                    "verifiers": [
                        {"type": "keyword_include", "params": {"keywords": ["ok"]}},
                        {"type": "keyword_include", "params": {"keywords": ["missing"]}},
                    ],
                },
            }
        ],
        "ok",
    )

    assert result.all_passed is False
    assert result.details[0].passed is False


def test_composite_or_passes_on_any_child():
    result = run_verifiers(
        [
            {
                "type": "composite_or",
                "params": {
                    "verifiers": [
                        {"type": "keyword_include", "params": {"keywords": ["missing"]}},
                        {"type": "keyword_include", "params": {"keywords": ["present"]}},
                    ],
                },
            }
        ],
        "present",
    )

    assert result.all_passed is True
    assert result.details[0].passed is True


def test_all_skipped_verifiers_return_none_all_passed(tmp_path):
    result = run_verifiers(
        [
            {
                "type": "regex_match",
                "params": {
                    "source": "file",
                    "path": "missing.txt",
                    "pattern": "anything",
                },
            }
        ],
        "fallback answer",
        sandbox_dir=str(tmp_path),
    )

    assert result.total == 1
    assert result.passed_count == 0
    assert result.all_passed is None
    assert result.details[0].passed is None
