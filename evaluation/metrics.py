"""Raw-dimension aggregation; intentionally no composite score."""

from __future__ import annotations

from collections import defaultdict

from evaluation.models import CaseResult, DimensionSummary


def aggregate_results(results: list[CaseResult]) -> list[DimensionSummary]:
    grouped: dict[str, list[CaseResult]] = defaultdict(list)
    for result in results:
        grouped[result.experiment.id].append(result)

    summaries: list[DimensionSummary] = []
    for experiment_id, rows in sorted(grouped.items()):
        count = len(rows)
        verifier_rows = [row for row in rows if row.metrics.verifier_passed is not None]
        selector_rows = [row for row in rows if row.metrics.selector_correct is not None]

        trials_by_case: dict[str, list[bool]] = defaultdict(list)
        for row in rows:
            trials_by_case[row.case_id].append(row.metrics.success)
        stability_values = []
        for outcomes in trials_by_case.values():
            if len(outcomes) < 2:
                continue
            pass_rate = sum(outcomes) / len(outcomes)
            stability_values.append(1.0 - 4.0 * pass_rate * (1.0 - pass_rate))

        summaries.append(
            DimensionSummary(
                experiment_id=experiment_id,
                cases=len(trials_by_case),
                success_rate=sum(row.metrics.success for row in rows) / count,
                verifier_rate=(
                    sum(bool(row.metrics.verifier_passed) for row in verifier_rows)
                    / len(verifier_rows)
                    if verifier_rows else None
                ),
                average_tokens=sum(row.metrics.tokens for row in rows) / count,
                average_latency_ms=sum(row.metrics.latency_ms for row in rows) / count,
                average_tool_calls=sum(row.metrics.tool_calls for row in rows) / count,
                average_iterations=sum(row.metrics.iterations for row in rows) / count,
                average_replans=sum(row.metrics.replans for row in rows) / count,
                stability=(
                    sum(stability_values) / len(stability_values)
                    if stability_values else None
                ),
                selector_accuracy=(
                    sum(bool(row.metrics.selector_correct) for row in selector_rows)
                    / len(selector_rows)
                    if selector_rows else None
                ),
            )
        )
    return summaries
