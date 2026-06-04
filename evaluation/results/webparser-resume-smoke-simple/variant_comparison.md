# Evaluation Variant Comparison: webparser-resume-smoke-simple

- Suite: `smoke_reasoning`
- Baseline variant: `react_auto_baseline`
- Task count: `2`

## Summary

| Variant | Mode | Success | Verifier | Score | Tokens | Reasoning Tokens | Calls | Wall Time | ΔSuccess | Token Ratio | Time Ratio | Recommendation |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| react_auto_baseline | simple | 100.0% | 100.0% | 0.890 | 16694 | 650 | 7.5 | 19584ms | +0.0% | 1.00x | 1.00x | baseline |
| reasoning_auto | simple | 50.0% | 50.0% | 0.736 | 45562 | 1692 | 17.0 | 53990ms | -50.0% | 2.73x | 2.76x | efficiency_regression |

## Decision Rules

- Correctness is primary. A variant needs at least +8pp success rate with <=2x tokens/time to be a default candidate.
- High effort is only justified for hard/emergent workloads when correctness improves materially.
- Live web suites should be treated as observational because external content can drift.

## Failures

- `reasoning_auto` / `simple` / `resume_001`: tool_execution_error: Verifier 'composite_and' failed: AND failed at 'regex_match': Regex '(?i)(density|人口密度|density_per_km2)' not matched
