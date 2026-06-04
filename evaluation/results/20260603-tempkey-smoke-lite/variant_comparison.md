# Evaluation Variant Comparison: 20260603-tempkey-smoke-lite

- Suite: `smoke_reasoning`
- Baseline variant: `react_auto_baseline`
- Task count: `3`

## Summary

| Variant | Mode | Success | Verifier | Score | Tokens | Reasoning Tokens | Calls | Wall Time | ΔSuccess | Token Ratio | Time Ratio | Recommendation |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| react_auto_baseline | simple | 100.0% | 100.0% | 0.917 | 14299 | 840 | 7.0 | 20029ms | +0.0% | 1.00x | 1.00x | baseline |
| reasoning_auto | simple | 100.0% | 100.0% | 0.903 | 13958 | 976 | 7.7 | 21321ms | +0.0% | 0.98x | 1.06x | neutral |

## Decision Rules

- Correctness is primary. A variant needs at least +8pp success rate with <=2x tokens/time to be a default candidate.
- High effort is only justified for hard/emergent workloads when correctness improves materially.
- Live web suites should be treated as observational because external content can drift.

## Failures

- No failed tasks recorded.
