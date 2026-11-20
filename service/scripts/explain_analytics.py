"""Run EXPLAIN (ANALYZE, BUFFERS) for each query in sql/analytics and summarise the plans.

Usage: python service/scripts/explain_analytics.py DATABASE_URL OUT_DIR [--runs 5] [--jit on|off]
Writes one text plan per query to OUT_DIR and prints a markdown summary table.
"""

import argparse
import json
import statistics
from pathlib import Path

import psycopg

SQL_DIR = Path(__file__).resolve().parents[1] / "sql" / "analytics"


def scans(node: dict) -> list[str]:
    found = []
    if "Relation Name" in node:
        index = f" using {node['Index Name']}" if "Index Name" in node else ""
        found.append(f"{node['Relation Name']}: {node['Node Type']}{index}")
    elif node["Node Type"] == "Bitmap Index Scan":
        found.append(f"(bitmap index {node['Index Name']})")
    for child in node.get("Plans", []):
        found.extend(scans(child))
    return found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database_url")
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--jit", choices=["on", "off"], default="on")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    url = args.database_url.replace("postgresql+psycopg://", "postgresql://")
    print("| query | median exec ms | median JIT ms | scans |")
    print("|---|---|---|---|")
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(f"SET jit = {args.jit}")
        for path in sorted(SQL_DIR.glob("*.sql")):
            sql = path.read_text()
            exec_ms, jit_ms = [], []
            for _ in range(args.runs):
                [[plan]] = conn.execute(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}").fetchall()
                plan = plan[0] if isinstance(plan, list) else json.loads(plan)[0]
                exec_ms.append(plan["Execution Time"])
                jit_ms.append(plan.get("JIT", {}).get("Timing", {}).get("Total", 0.0))
            text = conn.execute(f"EXPLAIN (ANALYZE, BUFFERS) {sql}").fetchall()
            (args.out_dir / f"{path.stem}.txt").write_text("\n".join(r[0] for r in text) + "\n")
            print(
                f"| {path.stem} | {statistics.median(exec_ms):.1f} | "
                f"{statistics.median(jit_ms):.1f} | {'; '.join(scans(plan['Plan']))} |"
            )


if __name__ == "__main__":
    main()
