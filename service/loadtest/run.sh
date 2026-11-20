#!/usr/bin/env bash
# One load-test run on a fresh local stack.
# Usage: service/loadtest/run.sh LABEL RATE DURATION WORKERS PDF
#   e.g. service/loadtest/run.sh w1-r5 5 60s 1 path/to/paper.pdf
# Writes service/loadtest/results/LABEL/{k6-summary.json,k6.txt,backlog.tsv,db-summary.txt}.
set -euo pipefail

label="$1" rate="$2" duration="$3" workers="$4" pdf="$5"
here="$(cd "$(dirname "$0")" && pwd)"
out="$here/results/$label"
compose=(docker compose -f "$here/../docker-compose.yml")
mkdir -p "$out"

"${compose[@]}" down -v >/dev/null 2>&1
"${compose[@]}" up --build -d --scale "worker=$workers" >/dev/null 2>&1
for _ in $(seq 1 120); do curl -sf localhost:8080/health >/dev/null && break; sleep 1; done

# Sample the queue backlog from the API's /metrics once per second.
(
  while true; do
    backlog=$(curl -s localhost:8080/metrics | awk '/^p2s_queue_backlog /{print $2}')
    printf '%s\t%s\n' "$(date +%s)" "${backlog:-NA}"
    sleep 1
  done
) > "$out/backlog.tsv" &
sampler=$!
trap 'kill $sampler 2>/dev/null || true' EXIT

pdf_dir="$(cd "$(dirname "$pdf")" && pwd)"
docker run --rm --network p2s_default \
  -v "$here:/scripts:ro" -v "$pdf_dir:/pdf:ro" -v "$out:/out" \
  -e BASE_URL=http://api:8080 -e PDF_PATH="/pdf/$(basename "$pdf")" \
  -e RATE="$rate" -e DURATION="$duration" \
  grafana/k6:1.3.0 run --quiet --summary-export /out/k6-summary.json /scripts/submit.js \
  > "$out/k6.txt" 2>&1 || echo "k6 exited non-zero (thresholds crossed?); see $out/k6.txt"

for _ in $(seq 1 900); do
  left=$("${compose[@]}" exec -T db psql -U p2s -d p2s -tAc \
    "SELECT count(*) FROM jobs WHERE status IN ('queued', 'running')")
  [[ "$left" == 0 ]] && break
  sleep 1
done
sleep 2
kill "$sampler" 2>/dev/null || true
wait "$sampler" 2>/dev/null || true

"${compose[@]}" exec -T db psql -U p2s -d p2s -x < "$here/run_summary.sql" > "$out/db-summary.txt"
"${compose[@]}" logs api --no-log-prefix > "$out/api.log" 2>&1
"${compose[@]}" logs worker --no-log-prefix > "$out/worker.log" 2>&1
printf 'label=%s rate=%s duration=%s workers=%s pdf=%s\n' \
  "$label" "$rate" "$duration" "$workers" "$(basename "$pdf")" > "$out/params.txt"
echo "max backlog: $(cut -f2 "$out/backlog.tsv" | grep -v NA | sort -n | tail -1)" >> "$out/params.txt"
cat "$out/params.txt" "$out/db-summary.txt"
