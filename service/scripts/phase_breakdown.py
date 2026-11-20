"""Per-phase latency percentiles from worker JSON logs ("job succeeded" records).

Usage: docker compose -f service/docker-compose.yml logs worker --no-log-prefix \
           | python service/scripts/phase_breakdown.py
"""

import json
import statistics
import sys

TOP_LEVEL_PHASES = ("download", "pipeline", "process_exit", "upload", "complete")


def percentile(values: list[float], q: float) -> float:
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[round(q * 100) - 1]


def main() -> None:
    phases: dict[str, list[float]] = {}
    for line in sys.stdin:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("msg") != "job succeeded":
            continue
        for phase, ms in record["phases_ms"].items():
            phases.setdefault(phase, []).append(ms)
        phases.setdefault("stages (inside pipeline)", []).append(record["stages_ms"])
        # "pipeline" already contains "process_start" and the stages.
        total = sum(record["phases_ms"].get(p, 0) for p in TOP_LEVEL_PHASES)
        phases.setdefault("attempt total", []).append(total)

    print("| phase | n | p50 ms | p95 ms | max ms |")
    print("|---|---|---|---|---|")
    for phase, values in phases.items():
        print(
            f"| {phase} | {len(values)} | {percentile(values, 0.5):.1f} | "
            f"{percentile(values, 0.95):.1f} | {max(values):.1f} |"
        )


if __name__ == "__main__":
    main()
