# Evaluation Variant Comparison: resume001-fix-diagnostics

- Suite: `smoke_reasoning`
- Baseline variant: `react_auto_baseline`
- Task count: `1`

## Summary

| Variant | Mode | Success | Verifier | Score | Tokens | Reasoning Tokens | Calls | Wall Time | ΔSuccess | Token Ratio | Time Ratio | Recommendation |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| react_auto_baseline | simple | 100.0% | 100.0% | 0.883 | 10237 | 462 | 5.0 | 17031ms | +0.0% | 1.00x | 1.00x | baseline |
| reasoning_auto | simple | 100.0% | 100.0% | 0.882 | 11239 | 572 | 5.0 | 18800ms | +0.0% | 1.10x | 1.10x | neutral |

## Decision Rules

- Correctness is primary. A variant needs at least +8pp success rate with <=2x tokens/time to be a default candidate.
- High effort is only justified for hard/emergent workloads when correctness improves materially.
- Live web suites should be treated as observational because external content can drift.

## Failures

- No failed tasks recorded.
