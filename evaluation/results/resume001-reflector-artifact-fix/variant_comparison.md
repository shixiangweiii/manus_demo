# Evaluation Variant Comparison: resume001-reflector-artifact-fix

- Suite: `smoke_reasoning`
- Baseline variant: `react_auto_baseline`
- Task count: `1`

## Summary

| Variant | Mode | Success | Verifier | Score | Tokens | Reasoning Tokens | Calls | Wall Time | ΔSuccess | Token Ratio | Time Ratio | Recommendation |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| react_auto_baseline | simple | 0.0% | 100.0% | 0.433 | 0 | 0 | 0.0 | 0ms | +0.0% | - | - | baseline |
| reasoning_auto | simple | 0.0% | 100.0% | 0.433 | 0 | 0 | 0.0 | 0ms | +0.0% | - | - | neutral |

## Decision Rules

- Correctness is primary. A variant needs at least +8pp success rate with <=2x tokens/time to be a default candidate.
- High effort is only justified for hard/emergent workloads when correctness improves materially.
- Live web suites should be treated as observational because external content can drift.

## Failures

- `react_auto_baseline` / `simple` / `resume_001`: tool_execution_error: result.success=False; tool_errors=0; output=LLM call failed: Error code: 401 - {'error': {'message': 'Authentication Fails, Your api key: ****4626 is invalid', 'type': 'authentication_error', 'param': None, 'code': 'invalid_; llm_call_failure: Error code: 401 - {'error': {'message': 'Authentication Fails, Your api key: ****4626 is invalid', 'type': 'authentication_error', 'param': None, 'code': 'invalid_request_error'}}
- `reasoning_auto` / `simple` / `resume_001`: tool_execution_error: result.success=False; tool_errors=0; output=LLM call failed: Error code: 401 - {'error': {'message': 'Authentication Fails, Your api key: ****4626 is invalid', 'type': 'authentication_error', 'param': None, 'code': 'invalid_; llm_call_failure: Error code: 401 - {'error': {'message': 'Authentication Fails, Your api key: ****4626 is invalid', 'type': 'authentication_error', 'param': None, 'code': 'invalid_request_error'}}
