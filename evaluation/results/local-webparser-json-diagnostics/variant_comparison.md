# Evaluation Variant Comparison: local-webparser-json-diagnostics

- Suite: `smoke_reasoning`
- Baseline variant: `react_auto_baseline`
- Task count: `1`

## Summary

| Variant | Mode | Success | Verifier | Score | Tokens | Reasoning Tokens | Calls | Wall Time | ΔSuccess | Token Ratio | Time Ratio | Recommendation |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| react_auto_baseline | simple | 100.0% | 100.0% | 0.915 | 14367 | 801 | 8.0 | 19932ms | +0.0% | 1.00x | 1.00x | baseline |
| reasoning_auto | simple | 100.0% | 100.0% | 0.921 | 14690 | 882 | 8.0 | 19378ms | +0.0% | 1.02x | 0.97x | neutral |

## Decision Rules

- Correctness is primary. A variant needs at least +8pp success rate with <=2x tokens/time to be a default candidate.
- High effort is only justified for hard/emergent workloads when correctness improves materially.
- Live web suites should be treated as observational because external content can drift.

## Failures

- No failed tasks recorded.
