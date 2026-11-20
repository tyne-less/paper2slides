#!/usr/bin/env bash
# Submit every PDF listed in a file (one path per line) in both pdftotext modes to a running
# stack, then wait until no job is queued or running.
# Usage: service/scripts/run_pdf_batch.sh pdf-list.txt [base_url]
set -euo pipefail

list="$1"
base="${2:-http://localhost:8080}"
compose=(docker compose -f "$(dirname "$0")/../docker-compose.yml")

submitted=0
while IFS= read -r pdf || [[ -n "$pdf" ]]; do
  [[ -n "$pdf" ]] || continue
  for mode in default hybrid; do
    code=$(curl -s -o /dev/null -w '%{http_code}' -F "file=@$pdf" -F "pdftotext_mode=$mode" "$base/jobs")
    echo "$code $mode $pdf"
    submitted=$((submitted + 1))
  done
done < "$list"
echo "submitted $submitted requests"

for _ in $(seq 1 600); do
  left=$("${compose[@]}" exec -T db psql -U p2s -d p2s -tAc \
    "SELECT count(*) FROM jobs WHERE status IN ('queued', 'running')")
  [[ "$left" == 0 ]] && break
  sleep 1
done
"${compose[@]}" exec -T db psql -U p2s -d p2s -c "SELECT status, count(*) FROM jobs GROUP BY 1"
