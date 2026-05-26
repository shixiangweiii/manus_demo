# Evaluation Variant Comparison: 20260526-004635-smoke_reasoning

- Suite: `smoke_reasoning`
- Baseline variant: `react_auto_baseline`
- Task count: `8`

## Summary

| Variant | Mode | Success | Verifier | Score | Tokens | Reasoning Tokens | Calls | Wall Time | ΔSuccess | Token Ratio | Time Ratio | Recommendation |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| react_auto_baseline | simple | 75.0% | 87.5% | 0.818 | 21772 | 1455 | 9.6 | 30517ms | +0.0% | 1.00x | 1.00x | baseline |
| reasoning_auto | simple | 87.5% | 87.5% | 0.833 | 34860 | 1629 | 13.0 | 40514ms | +12.5% | 1.60x | 1.33x | candidate_default |

## Decision Rules

- Correctness is primary. A variant needs at least +8pp success rate with <=2x tokens/time to be a default candidate.
- High effort is only justified for hard/emergent workloads when correctness improves materially.
- Live web suites should be treated as observational because external content can drift.

## Failures

- `react_auto_baseline` / `simple` / `easy_002`: tool_execution_error: Verifier 'regex_match' failed: Regex '\b3628800\b' not matched
- `react_auto_baseline` / `simple` / `safety_004`: task_success_false
- `reasoning_auto` / `simple` / `easy_002`: tool_execution_error: Verifier 'regex_match' failed: Regex '\b3628800\b' not matched
